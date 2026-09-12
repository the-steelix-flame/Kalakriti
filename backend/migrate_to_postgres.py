#!/usr/bin/env python
"""
Copy an existing SQLite database into Postgres, row for row.

Everything built and demonstrated so far lives in `kalakriti.db` - verified phone
numbers, listings, publication attempts, orders, the event log. Pointing DATABASE_URL
at a hosted Postgres creates an empty schema and leaves all of that behind on the
laptop, so this script moves it across before the switch.

    python migrate_to_postgres.py --dry-run
    python migrate_to_postgres.py --sqlite kalakriti.db --target postgresql://...

The schema comes from db.py, never from SQL written here: the destination is created
with the same models the application uses, so the two cannot drift.

Safe to run more than once. Rows whose primary key already exists in the target are
skipped rather than re-inserted, so an interrupted run is finished by running it again.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

# Every other entry point reads .env before touching db, and this one did not - so
# `DATABASE_URL` set in the file was invisible here and the script reported "no
# destination" while the application connected to Supabase perfectly well.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from sqlalchemy import create_engine, func, insert, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError

import db

# Dependency order. Foreign keys are enforced on Postgres in a way SQLite by default is
# not, so a child row inserted before its parent fails outright - artisans before the
# addresses that reference them, listings before their orders, orders before shipments.
TABLE_ORDER: list = [
    db.Artisan,
    db.Address,
    db.MarketplaceAccount,
    db.Session,
    db.OtpChallenge,
    db.Listing,
    db.Publication,
    db.Order,
    db.Enquiry,
    db.ListingView,
    db.Shipment,
    db.Event,

    # The cooperative model. Clusters are owned by an artisan, memberships reference
    # both, and a goods receipt references a cluster plus the order or enquiry it was
    # produced against - so every one of these has to follow the tables above. A
    # settlement line cannot precede its settlement, and a review cannot precede the
    # settlement that proves the reviewer earned the right to leave it.
    db.HsnGstRate,
    db.Cluster,
    db.ClusterMembership,
    db.GoodsReceipt,
    db.Settlement,
    db.SettlementLine,
    db.Review,
]

try:
    # Job is declared in jobs.py against the same Base. Imported here so uploads that
    # were queued or finished before the migration come across too; the module pulls in
    # nothing heavy at import time.
    import jobs
    TABLE_ORDER.append(jobs.Job)
except Exception as exc:  # pragma: no cover - jobs is optional to this script
    print(f"note: the jobs table is not included ({exc})")

BATCH = 500


def _fail(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _source_url(path: str) -> str:
    if not os.path.exists(path):
        _fail(f"no SQLite database at {os.path.abspath(path)}")
    # Absolute, so the URL means the same thing regardless of where this was run from.
    return "sqlite:///" + os.path.abspath(path).replace("\\", "/")


def _count(conn, table) -> int:
    return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _pk_column(table):
    cols = list(table.primary_key.columns)
    if len(cols) != 1:
        _fail(f"{table.name} has a composite primary key, which this script cannot handle")
    return cols[0]


def _copy_table(model, src_engine, dst_engine, present: set) -> tuple[int, int]:
    """
    Copy one table. Returns (inserted, skipped).

    Rows are read and written through the same Table object, so each dialect applies
    its own type handling: SQLite hands back a parsed dict for a JSON column and psycopg
    writes it as JSONB, and DateTime columns arrive as datetimes rather than the strings
    SQLite stores them as. Copying raw SQL text instead would put strings into timestamp
    columns and quietly corrupt every date in the database.
    """
    table = model.__table__
    pk = _pk_column(table)
    inserted = skipped = 0
    pending: list[dict] = []

    with src_engine.connect() as src, dst_engine.begin() as dst:
        for row in src.execute(select(table)).mappings():
            if row[pk.name] in present:
                skipped += 1
                continue
            pending.append(dict(row))
            if len(pending) >= BATCH:
                dst.execute(insert(table), pending)
                inserted += len(pending)
                pending = []
        if pending:
            dst.execute(insert(table), pending)
            inserted += len(pending)

    return inserted, skipped


def _fix_sequence(dst_engine, table) -> None:
    """
    Move an identity sequence past the ids that were just copied.

    `events` and `listing_views` use an autoincrementing integer id. Inserting explicit
    ids does not advance Postgres's sequence, so the next row the application writes
    asks for id 1 and dies on a duplicate key: the migration appears to have worked and
    the first new event afterwards fails. SQLite has no such counter to carry over, so
    the sequence has to be set from the data that was copied.
    """
    pk = _pk_column(table)
    if dst_engine.name != "postgresql":
        return
    try:
        if pk.type.python_type is not int:
            return
    except NotImplementedError:
        return

    # Identifiers come from our own metadata, not from anything a user supplied.
    seq_sql = f"SELECT pg_get_serial_sequence('{table.name}', '{pk.name}')"
    set_sql = (f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pk.name}'), "
               f"GREATEST(COALESCE((SELECT MAX({pk.name}) FROM {table.name}), 0), 1))")
    try:
        with dst_engine.begin() as conn:
            if conn.execute(text(seq_sql)).scalar() is None:
                return   # a plain integer primary key, with no sequence behind it
            conn.execute(text(set_sql))
    except SQLAlchemyError as exc:
        print(f"  warning: could not reset the {table.name} id sequence: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Copy the SQLite database into Postgres.")
    ap.add_argument("--sqlite", default="kalakriti.db", help="source SQLite file")
    ap.add_argument("--target", default=db._env("DATABASE_URL", ""),
                    help="destination URL (defaults to DATABASE_URL)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be copied, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="copy even when the target already holds more rows than the source")
    args = ap.parse_args()

    src_url = _source_url(args.sqlite)
    if not args.target:
        _fail("no destination. Set DATABASE_URL, or pass --target postgresql://...")

    # The same normalisation the application uses, so a `postgres://` string copied
    # straight from a provider dashboard works here exactly as it does at runtime.
    dst_url = db._normalise_url(args.target)
    if dst_url == src_url:
        _fail("the source and the destination are the same database")
    if not dst_url.startswith("postgresql"):
        print(f"note: the destination is not Postgres ({dst_url.split(':', 1)[0]})")

    src_engine = create_engine(src_url, connect_args={"check_same_thread": False})
    dst_engine = create_engine(dst_url, pool_pre_ping=True)

    print(f"source      {src_url}")
    print(f"destination {dst_engine.url.render_as_string(hide_password=True)}")

    try:
        with dst_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        _fail(f"cannot reach the destination: {db._diagnose(exc)}. "
              f"Underlying error: {exc}")

    # Create anything missing at the destination, from the models themselves.
    if not args.dry_run:
        db.Base.metadata.create_all(dst_engine)
        print("schema ensured at the destination")

    src_tables = set(inspect(src_engine).get_table_names())
    dst_tables = set(inspect(dst_engine).get_table_names())

    # Read both sides before writing anything. A destination holding more rows than the
    # source is almost always the two arguments the wrong way round, which would mean
    # writing a laptop's stale copy over live data.
    plan: list[tuple] = []
    heavier: list[str] = []
    for model in TABLE_ORDER:
        table = model.__table__
        if table.name not in src_tables:
            plan.append((model, 0, 0))
            continue
        with src_engine.connect() as sc:
            n_src = _count(sc, table)
        n_dst = 0
        if table.name in dst_tables:
            with dst_engine.connect() as dc:
                n_dst = _count(dc, table)
        if n_dst > n_src:
            heavier.append(f"{table.name} (destination {n_dst}, source {n_src})")
        plan.append((model, n_src, n_dst))

    print(f"\n{'table':<22}{'source':>10}{'target before':>16}")
    for model, n_src, n_dst in plan:
        print(f"{model.__tablename__:<22}{n_src:>10}{n_dst:>16}")
    print(f"{'TOTAL':<22}{sum(p[1] for p in plan):>10}{sum(p[2] for p in plan):>16}")

    if heavier and not args.force:
        _fail("the destination already holds more rows than the source in: "
              + ", ".join(heavier)
              + ".\nThat usually means --sqlite and --target are the wrong way round. "
                "Check them, and pass --force if this really is what you intend.")

    if args.dry_run:
        print("\ndry run: nothing was written")
        return 0

    print()
    results: list[tuple[str, int, int]] = []
    for model, n_src, _ in plan:
        table = model.__table__
        if table.name not in src_tables or n_src == 0:
            results.append((table.name, 0, 0))
            continue
        pk = _pk_column(table)
        with dst_engine.connect() as dc:
            present = set(dc.execute(select(pk)).scalars())
        try:
            inserted, skipped = _copy_table(model, src_engine, dst_engine, present)
        except SQLAlchemyError as exc:
            _fail(f"copying {table.name} failed: {exc}")
        _fix_sequence(dst_engine, table)
        results.append((table.name, inserted, skipped))
        print(f"  {table.name:<22} copied {inserted:>7}   already present {skipped:>7}")

    # Verify by counting the destination again rather than trusting the numbers above:
    # a row that was inserted and then rolled back would still have been counted.
    dst_tables = set(inspect(dst_engine).get_table_names())
    print(f"\n{'table':<22}{'source':>10}{'target after':>16}   status")
    ok = True
    for model, n_src, _ in plan:
        table = model.__table__
        n_dst = 0
        if table.name in dst_tables:
            with dst_engine.connect() as dc:
                n_dst = _count(dc, table)
        good = n_dst >= n_src
        ok = ok and good
        print(f"{table.name:<22}{n_src:>10}{n_dst:>16}   {'ok' if good else 'MISSING ROWS'}")

    print(f"\ntotal copied {sum(r[1] for r in results)}, "
          f"already present {sum(r[2] for r in results)}")
    if not ok:
        print("\nVerification failed: the destination is missing rows. Nothing was "
              "removed from the source, so it is safe to investigate and run again.")
        return 1
    print("Verification passed. Point DATABASE_URL at the destination and restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
