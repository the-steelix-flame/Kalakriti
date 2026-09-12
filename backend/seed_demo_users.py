"""
Create the two demo accounts, one per user type, with passwords.

Why this exists
---------------
OTP delivery needs an SMS provider with credit on it. Until that is paid for, nobody
can sign in - which means nobody can be shown the product either. These two accounts
are the way in until then, and they are also the two halves of the cooperative model
standing next to each other, which is what makes a demo legible:

    the weaver         no GSTIN, joins a cluster, promises capacity, delivers
    the cluster owner  holds the GSTIN, runs the cluster, records what arrived

Both are real rows created through the real code path. The passwords are hashed with
scrypt like anybody else's; nothing here is a bypass, and there is no branch anywhere
that treats these accounts differently from a real one.

What this is NOT
----------------
These are demo credentials with memorable passwords, and memorable passwords are weak
passwords. That is a deliberate trade for a demo and an unacceptable one in
production. `preflight.py --production` fails if these accounts still exist with their
seeded passwords. Before anything real:

    python seed_demo_users.py --remove

Usage
-----
    python seed_demo_users.py                 create or update both accounts
    python seed_demo_users.py --show          print the credentials again
    python seed_demo_users.py --remove        delete them
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import auth  # noqa: E402
import db  # noqa: E402

# Numbers in the 99999-xxxxx range reserved for documentation, so these can never
# collide with a real artisan's number or send an SMS to a stranger.
DEMO = [
    {
        "phone": "9999900001",
        "password": "kalakriti-weaver",
        "role": db.ROLE_ARTISAN,
        "full_name": "Sunita Devi",
        "business_name": "Sunita Handloom",
        "language": "hi",
        "cluster_label": "Varanasi weavers",
        "gstin": "",
        "pan": "",
        "capacity_units": 40,
        "what": "Artisan with no GST. Joins a cluster to reach a marketplace.",
    },
    {
        "phone": "9999900002",
        "password": "kalakriti-cluster",
        "role": db.ROLE_CLUSTER_CREATOR,
        "full_name": "Ramesh Prasad",
        "business_name": "Banaras Craft Collective",
        "language": "hi",
        "cluster_label": "Varanasi weavers",
        # A structurally valid GSTIN for Uttar Pradesh (state code 09). It is not
        # registered to anybody - it exists so the readiness checks have something
        # real-shaped to validate, not to impersonate a business.
        "gstin": "09AAACH7409R1ZZ",
        "pan": "AAACH7409R",
        "capacity_units": 0,
        "what": "Cluster Creator. Holds the GSTIN, is the seller of record, "
                "records deliveries.",
    },
]


def upsert(s, spec: dict) -> db.Artisan:
    a = s.query(db.Artisan).filter(db.Artisan.phone == spec["phone"]).first()
    created = a is None
    if created:
        a = db.Artisan(id=db.nid("art"), phone=spec["phone"])
        s.add(a)

    # Verified, because these accounts skip the OTP that would otherwise set it, and
    # an unverified account is blocked from publishing further down.
    a.phone_verified = 1
    a.phone_verified_at = a.phone_verified_at or db.now()
    a.full_name = spec["full_name"]
    a.business_name = spec["business_name"]
    a.language = spec["language"]
    a.cluster = spec["cluster_label"]
    a.gstin = spec["gstin"]
    a.pan = spec["pan"]
    a.role = spec["role"]
    a.capacity_units = spec["capacity_units"]
    a.capacity_committed = a.capacity_committed or 0
    auth.set_password(s, a, spec["password"])
    s.flush()
    db.log_event(s, "artisan", a.id, "seed", "", spec["role"],
                 "demo account created" if created else "demo account updated")
    return a


def remove(s) -> int:
    n = 0
    for spec in DEMO:
        a = s.query(db.Artisan).filter(db.Artisan.phone == spec["phone"]).first()
        if a is None:
            continue
        for c in s.query(db.Cluster).filter(db.Cluster.owner_artisan_id == a.id).all():
            s.query(db.GoodsReceipt).filter(
                db.GoodsReceipt.cluster_id == c.id).delete()
            s.query(db.ClusterMembership).filter(
                db.ClusterMembership.cluster_id == c.id).delete()
            s.delete(c)
        s.query(db.ClusterMembership).filter(
            db.ClusterMembership.artisan_id == a.id).delete()
        s.query(db.GoodsReceipt).filter(db.GoodsReceipt.artisan_id == a.id).delete()
        s.query(db.Session).filter(db.Session.artisan_id == a.id).delete()
        s.delete(a)
        n += 1
    s.commit()
    return n


def show() -> None:
    print("\n  demo sign-in, on the app's Create account / Sign in screen\n")
    for spec in DEMO:
        print(f"    {spec['full_name']}  -  {spec['what']}")
        print(f"      phone     {spec['phone']}")
        print(f"      password  {spec['password']}")
        print()
    print("  Both are verified accounts with real scrypt-hashed passwords.")
    print("  Remove them before anything real: python seed_demo_users.py --remove\n")


def main() -> int:
    if "--show" in sys.argv:
        show()
        return 0

    s = db.session()
    try:
        if "--remove" in sys.argv:
            n = remove(s)
            print(f"removed {n} demo account(s)")
            return 0

        for spec in DEMO:
            a = upsert(s, spec)
            print(f"  {spec['phone']}  {a.full_name:<16} role={a.role}")
        s.commit()
        show()
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
