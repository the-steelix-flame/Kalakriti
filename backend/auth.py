"""
Authentication: phone + OTP, backed by real server-side sessions.

Design notes
------------
* No passwords. Artisans have phone numbers, not password managers.
* The OTP is six digits from `secrets`, stored only as a salted SHA-256 hash with a
  five-minute expiry, five verify attempts, and a per-phone send cooldown. A screen
  that accepted any six digits would be worse than useless here, since the phone
  number is what every marketplace and courier will actually call.
* A JWT carries the session id; the session row is authoritative, so logout and
  expiry genuinely invalidate rather than relying on the client discarding a token.
* An Artisan row is created only after a successful verification, so its existence
  always means a verified phone.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

import db
import sms

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALG = "HS256"
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "60"))
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "300"))
OTP_RESEND_COOLDOWN = int(os.getenv("OTP_RESEND_COOLDOWN", "45"))
_KEY_FILE = os.getenv("JWT_SECRET_FILE", "jwt_secret.txt")


def _secret() -> str:
    """Persisted so a restart does not log everybody out."""
    global JWT_SECRET
    if JWT_SECRET:
        return JWT_SECRET
    if os.path.exists(_KEY_FILE):
        JWT_SECRET = open(_KEY_FILE, encoding="utf-8").read().strip()
    else:
        JWT_SECRET = secrets.token_urlsafe(48)
        with open(_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(JWT_SECRET)
    return JWT_SECRET


def normalise_phone(raw: str) -> str | None:
    """Accepts +91XXXXXXXXXX, 91XXXXXXXXXX, 0XXXXXXXXXX, XXXXXXXXXX."""
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) != 10 or d[0] not in "6789":
        return None
    return d


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256((salt + code).encode()).hexdigest()


# ────────────────────────────────────────────────────────────────── OTP issue

def request_otp(s, phone_raw: str) -> dict:
    phone = normalise_phone(phone_raw)
    if not phone:
        return {"ok": False, "error": "invalid_phone",
                "message": "Enter a valid 10-digit Indian mobile number."}

    recent = (s.query(db.OtpChallenge)
              .filter(db.OtpChallenge.phone == phone)
              .order_by(db.OtpChallenge.created_at.desc()).first())
    if recent and recent.created_at:
        created = db.naive_utc(recent.created_at)
        age = (db.now() - created).total_seconds()
        if age < OTP_RESEND_COOLDOWN and not recent.consumed:
            return {"ok": False, "error": "cooldown",
                    "retryAfter": int(OTP_RESEND_COOLDOWN - age),
                    "message": f"Please wait {int(OTP_RESEND_COOLDOWN - age)}s "
                               f"before asking for another code."}

    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(8)
    ch = db.OtpChallenge(
        id=db.nid("otp"), phone=phone, code_hash=_hash(code, salt), salt=salt,
        expires_at=db.now() + timedelta(seconds=OTP_TTL_SECONDS),
    )

    provider, ref, err = sms.send_otp(phone, code)
    ch.delivery = provider
    ch.delivery_ref = ref
    s.add(ch)
    db.log_event(s, "otp", ch.id, "note",
                 detail=f"otp issued to {phone[:2]}****{phone[-2:]} via {provider}")
    s.commit()

    out = {
        "ok": provider != "not_configured",
        "challengeId": ch.id,
        "phone": phone,
        "delivery": provider,
        "expiresInSeconds": OTP_TTL_SECONDS,
    }
    if provider == "not_configured":
        out["error"] = "sms_not_configured"
        out["message"] = err
    elif err:
        out["ok"] = False
        out["error"] = "sms_failed"
        out["message"] = err
    # Development escape hatch only, and always labelled as such.
    if sms.dev_echo():
        out["devCode"] = code
        out["devNotice"] = ("SMS is not configured, so the code is shown here. "
                            "This only happens because OTP_DEV_ECHO is enabled.")
    return out


# ───────────────────────────────────────────────────────────────── OTP verify

def verify_otp(s, challenge_id: str, code: str, user_agent: str = "",
               guest_token: str = "") -> dict:
    ch = s.get(db.OtpChallenge, challenge_id)
    if not ch:
        return {"ok": False, "error": "unknown_challenge",
                "message": "That code request has expired. Please ask for a new code."}
    if ch.consumed:
        return {"ok": False, "error": "already_used",
                "message": "This code was already used. Please request a new one."}

    exp = db.naive_utc(ch.expires_at)
    if not exp or db.now() > exp:
        return {"ok": False, "error": "expired",
                "message": "The code has expired. Please request a new one."}
    if ch.attempts >= ch.max_attempts:
        return {"ok": False, "error": "too_many_attempts",
                "message": "Too many wrong attempts. Please request a new code."}

    ch.attempts += 1
    s.commit()

    if not secrets.compare_digest(_hash((code or "").strip(), ch.salt), ch.code_hash):
        left = max(ch.max_attempts - ch.attempts, 0)
        return {"ok": False, "error": "wrong_code", "attemptsLeft": left,
                "message": f"That code is not correct. {left} attempts left."}

    ch.consumed = 1

    artisan = s.query(db.Artisan).filter(db.Artisan.phone == ch.phone).first()
    created = False
    if not artisan:
        artisan = db.Artisan(id=db.nid("art"), phone=ch.phone)
        s.add(artisan)
        created = True
    artisan.phone_verified = 1
    artisan.phone_verified_at = db.now()

    sess = db.Session(id=db.nid("ses"), artisan_id=artisan.id,
                      expires_at=db.now() + timedelta(days=SESSION_DAYS),
                      user_agent=(user_agent or "")[:200])
    s.add(sess)
    db.log_event(s, "artisan", artisan.id, "status", "", "phone_verified",
                 "created" if created else "login")

    # Hand the guest's existing drafts to the account they just proved they own.
    claimed = 0
    if guest_token:
        rows = (s.query(db.Listing)
                .filter(db.Listing.guest_token == guest_token,
                        db.Listing.artisan_id.is_(None)).all())
        for r in rows:
            r.artisan_id = artisan.id
            claimed += 1
            db.log_event(s, "listing", r.id, "note", detail="claimed by artisan on login")
    s.commit()

    return {"ok": True, "token": issue_token(sess), "artisan": artisan.public(),
            "isNewAccount": created, "claimedDrafts": claimed}


# ───────────────────────────────────────────────────────────────────── tokens

def issue_token(sess: db.Session) -> str:
    exp = db.naive_utc(sess.expires_at)
    return jwt.encode(
        {"sid": sess.id, "aid": sess.artisan_id, "exp": int(exp.timestamp())},
        _secret(), algorithm=JWT_ALG)


def artisan_from_token(s, token: str | None) -> db.Artisan | None:
    """The session row is authoritative - a valid signature is not enough."""
    if not token:
        return None
    token = token.replace("Bearer ", "").strip()
    try:
        claims = jwt.decode(token, _secret(), algorithms=[JWT_ALG])
    except Exception:
        return None
    sess = s.get(db.Session, claims.get("sid", ""))
    if not sess or sess.revoked:
        return None
    exp = db.naive_utc(sess.expires_at)
    if not exp or db.now() > exp:
        return None
    return s.get(db.Artisan, sess.artisan_id)


def logout(s, token: str | None) -> bool:
    if not token:
        return False
    token = token.replace("Bearer ", "").strip()
    try:
        claims = jwt.decode(token, _secret(), algorithms=[JWT_ALG],
                            options={"verify_exp": False})
    except Exception:
        return False
    sess = s.get(db.Session, claims.get("sid", ""))
    if not sess:
        return False
    sess.revoked = 1
    s.commit()
    return True
