"""
Phase 2 over the wire: the same endpoints, but against the public HTTPS host the
phone actually talks to, rather than through an in-process test client.

`test_clusters.py` proves the logic. This proves the deployment: TLS, the tunnel, the
real uvicorn process, real JSON over a real network. The two catch different things -
a signing key that differs between the test process and the server shows up only here.

    python test_clusters_live.py [https://host]
"""
from __future__ import annotations

import os
import sys
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import requests  # noqa: E402

import auth  # noqa: E402
import db  # noqa: E402

TIMEOUT = 60
WEAVER, OWNER = "9000000201", "9000000202"
GSTIN = "09AAACH7409R1ZZ"


def base_url() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1].rstrip("/")
    url = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if not url:
        raise SystemExit("no PUBLIC_BASE_URL and no argument given")
    return url


def token_for(s, phone: str, *, gstin: str = "", capacity: int = 0) -> str:
    a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
    if a is None:
        a = db.Artisan(id=db.nid("art"), phone=phone, phone_verified=1)
        s.add(a)
    a.phone_verified = 1
    a.gstin = gstin
    a.capacity_units = capacity
    a.capacity_committed = 0
    a.role = db.ROLE_ARTISAN
    a.full_name = a.full_name or f"Live test {phone[-3:]}"
    s.flush()
    sess = db.Session(id=db.nid("ses"), artisan_id=a.id,
                      expires_at=db.now() + timedelta(days=1))
    s.add(sess)
    s.commit()
    return auth.issue_token(sess)


def wipe(s, phones: list[str]) -> None:
    for phone in phones:
        a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
        if a is None:
            continue
        for c in s.query(db.Cluster).filter(db.Cluster.owner_artisan_id == a.id).all():
            s.query(db.ClusterMembership).filter(
                db.ClusterMembership.cluster_id == c.id).delete()
            s.delete(c)
        s.query(db.ClusterMembership).filter(
            db.ClusterMembership.artisan_id == a.id).delete()
        s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
        s.delete(a)
    s.commit()


def main() -> int:
    url = base_url()
    s = db.session()
    wipe(s, [WEAVER, OWNER])
    wtok = token_for(s, WEAVER, capacity=40)
    otok = token_for(s, OWNER, gstin=GSTIN)

    def h(tok: str) -> dict:
        return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

    print(f"against {url}")
    print("every call below crosses the public internet over TLS\n")
    bad = 0

    r = requests.post(f"{url}/v1/clusters", headers=h(otok), timeout=TIMEOUT, json={
        "name": "Live Tunnel Weavers", "craftCategory": "handloom",
        "commissionPct": 9.5, "district": "Varanasi", "state": "UP"})
    c = r.json()
    ok = r.status_code == 200 and c.get("inviteCode")
    bad += not ok
    print(f"  create cluster         HTTP {r.status_code}  invite code {c.get('inviteCode')}")
    if not ok:
        print("   ", str(c)[:200])
        s.close()
        return 1

    r = requests.get(f"{url}/v1/clusters", params={"craftCategory": "handloom"},
                     timeout=TIMEOUT)
    found = [x for x in r.json().get("clusters", []) if x["id"] == c["id"]]
    f0 = found[0] if found else {}
    bad += not f0
    print(f"  browse, signed out     HTTP {r.status_code}  commission {f0.get('commissionPct')}%")
    print(f"  platforms named        {[p['name'] for p in f0.get('platforms', [])]}")

    rt = f0.get("rating", {})
    ok = rt.get("available") is False and rt.get("overall") is None
    bad += not ok
    print(f"  cold start, not 0 stars  available={rt.get('available')}  {rt.get('why')!r}")

    leaked = GSTIN in str(f0)
    bad += leaked
    print(f"  owner GSTIN            last4 {f0.get('ownerGstinLast4')!r}, "
          f"full number in payload: {leaked}")

    r = requests.post(f"{url}/v1/clusters/join", headers=h(wtok), timeout=TIMEOUT,
                      json={"inviteCode": c["inviteCode"].lower()})
    ok = r.status_code == 200
    bad += not ok
    print(f"  join by code           HTTP {r.status_code}  "
          f"{r.json().get('membership', {}).get('status')}")

    r = requests.post(f"{url}/v1/me/capacity/commit", headers=h(wtok), timeout=TIMEOUT,
                      json={"units": 30, "clusterId": c["id"]})
    ok = r.status_code == 200 and r.json().get("capacityAvailable") == 10
    bad += not ok
    print(f"  commit 30 of 40        HTTP {r.status_code}  "
          f"{r.json().get('capacityAvailable')} free")

    r = requests.post(f"{url}/v1/me/capacity/commit", headers=h(wtok), timeout=TIMEOUT,
                      json={"units": 30, "clusterId": c["id"]})
    d = r.json().get("detail", {})
    ok = r.status_code == 409 and d.get("error") == "insufficient_capacity"
    bad += not ok
    print(f"  commit 30 again        HTTP {r.status_code}  {d.get('error')}")
    print(f"     she is told:        {d.get('why', '')[:74]}")

    wipe(s, [WEAVER, OWNER])
    s.close()
    print(f"\n{'all checks passed' if not bad else f'{bad} check(s) failed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
