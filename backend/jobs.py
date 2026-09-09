"""
Server-side processing for slow connections.

The problem this solves: the analysis pipeline takes two to five minutes and the app
currently holds an HTTP request open for all of it. On a village 2G connection that
request will not survive - it times out, the artisan sees a failure, and the work she
did is gone. Retrying costs her another upload of the same photograph.

So the upload becomes a job. The app posts the photograph once, gets an id back
immediately, and can be closed. This process does the whole pipeline - OCR, detection,
matting, background, copy, price - and writes each stage into the job row as it
finishes. The app collects the results whenever it next has signal, cheapest first:

    stage 1  fields      a few hundred bytes: title, price, category, OCR text
    stage 2  thumbnail   ~15 KB
    stage 3  full image  ~200 KB, only when the artisan opens the product

That ordering is the point. An artisan on a bad connection gets a usable, editable
listing from the first few hundred bytes, and the pictures arrive when they can.

Jobs live in the database rather than in memory, so a deploy or a crash does not lose
somebody's upload. Anything left `running` when this process starts is put back to
`queued`, because a job that was mid-flight when the process died was not finished.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

import db

log = logging.getLogger("jobs")

# Two workers. The pipeline is a mix of local CPU (OCR, matting) and network waits
# (the vision and language models), so a little parallelism helps; more than two and
# the ONNX sessions contend for the same cores and everything gets slower.
POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")

STAGES = ["queued", "enhancing", "reading", "detecting", "writing", "pricing", "done"]


class Job(db.Base):
    """One unit of server-side work, owned by whoever created it."""
    __tablename__ = "jobs"
    id = Column(String, primary_key=True)
    kind = Column(String, default="analyze")
    artisan_id = Column(String, index=True, nullable=True)
    guest_token = Column(String, default="", index=True)
    listing_id = Column(String, default="", index=True)

    status = Column(String, default="queued")     # queued|running|done|failed|cancelled
    stage = Column(String, default="queued")
    progress = Column(Integer, default=0)         # 0-100, for a progress bar

    # db.JSONType is JSONB on Postgres and plain JSON on SQLite. Plain JSON on
    # Postgres is stored as text and reparsed on every read.
    payload = Column(db.JSONType, default=dict)          # inputs, minus the image bytes
    result = Column(db.JSONType, default=dict)           # the cheap fields, delivered first
    error = Column(Text, default="")

    # The upload, held only until the job finishes. Kept out of `payload` so a status
    # poll never serialises a megabyte of base64 by accident.
    image_b64 = Column(Text, default="")

    attempts = Column(Integer, default=0)
    seen = Column(Integer, default=0)             # has the artisan been shown the result
    created_at = Column(DateTime, default=db.now)
    updated_at = Column(DateTime, default=db.now, onupdate=db.now)
    finished_at = Column(DateTime, nullable=True)

    def public(self, with_result: bool = True) -> dict:
        out = {
            "id": self.id, "kind": self.kind, "status": self.status,
            "stage": self.stage, "progress": self.progress,
            "listingId": self.listing_id, "error": self.error,
            "seen": bool(self.seen),
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
            "finishedAt": self.finished_at.isoformat() if self.finished_at else None,
        }
        if with_result:
            out["result"] = self.result or {}
        return out


def owns(job: Job, artisan_id: str | None, guest_token: str | None) -> bool:
    if artisan_id and job.artisan_id == artisan_id:
        return True
    return bool(guest_token) and job.guest_token == guest_token and not job.artisan_id


def _set(s, job_id: str, **fields):
    """Update a job in its own short transaction, so progress is visible while running."""
    j = s.get(Job, job_id)
    if not j:
        return
    for k, v in fields.items():
        setattr(j, k, v)
    s.commit()


def create(artisan_id: str | None, guest_token: str, image_b64: str,
           payload: dict) -> dict:
    """Accept an upload and return immediately. The artisan can close the app."""
    s = db.session()
    try:
        j = Job(id=db.nid("job"), kind="analyze", artisan_id=artisan_id,
                guest_token=guest_token or "", image_b64=image_b64,
                payload=payload or {}, status="queued", stage="queued")
        s.add(j)
        s.commit()
        out = j.public()
        POOL.submit(_run, j.id)
        return out
    finally:
        s.close()


def _run(job_id: str) -> None:
    """
    Execute one job.

    Imports are local because the vision stack takes several seconds to load and this
    module is imported at startup; a worker can pay that cost on first use rather than
    delaying the first request the server ever answers.
    """
    import media
    import pipeline

    s = db.session()
    try:
        j = s.get(Job, job_id)
        if not j or j.status in ("cancelled", "done"):
            return
        j.status = "running"
        j.attempts = (j.attempts or 0) + 1
        s.commit()

        raw = base64.b64decode(j.image_b64)
        payload = j.payload or {}

        # The listing row exists before any processing, so the work is never orphaned
        # if this process dies halfway through - the artisan gets a draft with her
        # photograph rather than nothing.
        lst = s.get(db.Listing, j.listing_id) if j.listing_id else None
        if lst is None:
            lst = db.Listing(id=db.nid("lst"), artisan_id=j.artisan_id,
                             guest_token=j.guest_token or "", status="processing",
                             transcript=payload.get("transcript", ""))
            s.add(lst)
            s.commit()
        _set(s, job_id, listing_id=lst.id, stage="enhancing", progress=8)

        out = pipeline.analyse_into(
            s, lst, raw,
            transcript=payload.get("transcript", ""),
            background=payload.get("background", "studio"),
            save_media=media.save,
            on_stage=lambda stage, pct: _set(s, job_id, stage=stage, progress=pct),
        )
        s.commit()

        # Only the cheap half goes into `result`. Images are URLs, so the app decides
        # when to spend bandwidth on them - which is the whole point of this path.
        _set(s, job_id, status="done", stage="done", progress=100,
             finished_at=db.now(),
             result={
                 "listingId": lst.id,
                 "titleEn": lst.title_en, "titleHi": lst.title_hi,
                 "descEn": (lst.desc_en or "")[:600],
                 "category": lst.category, "hsn": lst.hsn,
                 "price": lst.price, "floorPrice": lst.floor_price,
                 "attributes": lst.attributes or {},
                 "ocrText": (out.get("ocr", {}).get("text") or "")[:800],
                 "confidence": out.get("confidence", {}),
                 "suggestions": out.get("suggestions", {}),
                 "notes": out.get("notes", ""),
                 "thumbUrl": out.get("thumbUrl", ""),
                 "imageUrl": out.get("imageUrl", ""),
                 "ms": out.get("ms", 0),
             })
        db.log_event(s, "listing", lst.id, "note",
                     detail=f"processed on the server as job {job_id}")
        s.commit()
        log.info("job %s finished for listing %s in %sms",
                 job_id, lst.id, out.get("ms"))

    except Exception as e:
        log.exception("job %s failed", job_id)
        _set(s, job_id, status="failed", error=f"{type(e).__name__}: {e}",
             finished_at=db.now())
    finally:
        # The upload is not needed once the pipeline has read it, and holding a
        # megabyte of base64 per job would bloat the database within a day.
        try:
            _set(s, job_id, image_b64="")
        except Exception:
            pass
        s.close()


def requeue_orphans() -> int:
    """
    Anything left `running` belongs to a process that is no longer alive. Put it back
    in the queue - the alternative is a job that says "working on it" forever.
    """
    s = db.session()
    try:
        rows = s.query(Job).filter(Job.status == "running").all()
        for j in rows:
            if (j.attempts or 0) >= 3:
                j.status = "failed"
                j.error = "gave up after three attempts"
            else:
                j.status = "queued"
                j.stage = "queued"
        s.commit()
        for j in rows:
            if j.status == "queued":
                POOL.submit(_run, j.id)
        return len(rows)
    finally:
        s.close()


def sweep(older_than_hours: int = 48) -> int:
    """Drop finished jobs and any leftover upload bytes."""
    s = db.session()
    try:
        cut = db.now() - timedelta(hours=older_than_hours)
        n = (s.query(Job)
             .filter(Job.status.in_(["done", "failed", "cancelled"]),
                     Job.finished_at < cut)
             .delete(synchronize_session=False))
        s.commit()
        return n
    finally:
        s.close()
