#!/usr/bin/env python
"""
Add columns that the models declare and the live database does not have yet.

    python migrate_schema.py --dry-run     # report the difference, change nothing
    python migrate_schema.py               # add the missing columns
    python migrate_schema.py --backfill    # also fill them with the model's default

Why this script has to exist
---------------------------
`Base.metadata.create_all()` creates missing *tables*. It does not touch a table
that already exists, so a column added to a model afterwards is simply absent from
every database that was created before the change - including the one on your laptop
with all the real listings in it.

Nothing warns you. The tables are all there, the app imports cleanly, and the
failure arrives later as a 500 from whichever screen reads the new column first.
Adding `role` to Artisan broke login exactly that way, which is what prompted this.

It is generic on purpose. Phases 2 onward keep adding columns, and the team needs one
command that is always the right one to run rather than a hand-written ALTER per
change.

How it stays safe on both dialects
----------------------------------
Columns are added as nullable with no SQL default, then optionally backfilled with
the model's own Python default in a separate UPDATE. SQLite refuses a non-constant
default in ALTER TABLE, and a server default would also mean the schema disagrees
with the models about where defaults come from. SQLAlchemy applies the declared
default on every insert from here on, so new rows are correct either way; the
backfill is only for rows that already existed.

This script never drops or alters an existing column. A column that is in the
database and not in the models is reported and left alone - removing data is not
something a migration should do without somebody deciding to.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

# Every other entry point reads .env before touching db, and this one did not - so
# `DATABASE_URL` set in the file was invisible here and the script worked against the
# SQLite default while the application connected to Supabase perfectly well. Columns
# would have been added to the wrong database and the right one left behind.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

import db

try:
    import jobs                                  # noqa: F401  (registers Job on Base)
except Exception as exc:                          # pragma: no cover
    print(f"note: jobs.py was not imported, so its table is not checked ({exc})")


def _ddl_type(column) -> str:
    """The column's type as DDL for whichever database is configured."""
    return column.type.compile(dialect=db.engine.dialect)


def _python_default(column):
    """
    The value the model would put in this column on a fresh insert, or None.

    Handles both a plain value (`default="artisan"`) and a callable
    (`default=now`). A callable is evaluated once here, so a backfilled timestamp
    is the moment of the migration rather than a per-row lie about when the row
    was created.
    """
    d = column.default
    if d is None:
        return None
    if getattr(d, "is_callable", False):
        try:
            return d.arg(None)
        except Exception:
            return None
    return getattr(d, "arg", None)


def plan() -> tuple[list[tuple], list[tuple]]:
    """
    Returns (missing, extra).

    missing: (table, column_name, ddl_type, default) the models declare and the
             database lacks.
    extra:   (table, column_name) the database has and the models no longer declare.
    """
    insp = inspect(db.engine)
    live_tables = set(insp.get_table_names())
    missing: list[tuple] = []
    extra: list[tuple] = []

    for table in db.Base.metadata.sorted_tables:
        if table.name not in live_tables:
            # create_all handles whole missing tables; nothing to alter.
            continue
        live_cols = {c["name"] for c in insp.get_columns(table.name)}
        for column in table.columns:
            if column.name not in live_cols:
                missing.append((table.name, column.name, _ddl_type(column),
                                _python_default(column)))
        for name in sorted(live_cols - {c.name for c in table.columns}):
            extra.append((table.name, name))

    return missing, extra


def apply(missing: list[tuple], backfill: bool) -> int:
    added = 0
    for table, column, ddl_type, default in missing:
        # Identifiers come from our own model metadata, never from user input.
        stmt = f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}'
        try:
            with db.engine.begin() as conn:
                conn.execute(text(stmt))
            added += 1
            print(f"  added   {table}.{column}  {ddl_type}")
        except SQLAlchemyError as exc:
            print(f"  FAILED  {table}.{column}: {exc}")
            continue

        if backfill and default is not None:
            try:
                with db.engine.begin() as conn:
                    conn.execute(
                        text(f"UPDATE {table} SET {column} = :v "
                             f"WHERE {column} IS NULL"), {"v": default})
                print(f"          backfilled existing rows with {default!r}")
            except SQLAlchemyError as exc:
                print(f"          could not backfill: {exc}")

    return added


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Add columns the models declare and the database is missing.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the difference and change nothing")
    ap.add_argument("--backfill", action="store_true",
                    help="after adding a column, fill existing rows with the "
                         "model's default")
    args = ap.parse_args()

    print(f"database {db.safe_url()}\n")

    # Whole new tables first - this is the part create_all does correctly.
    if not args.dry_run:
        db.init()

    missing, extra = plan()

    if extra:
        print("in the database but no longer in the models (left alone):")
        for table, column in extra:
            print(f"  {table}.{column}")
        print()

    if not missing:
        print("every column the models declare is present. Nothing to do.")
        return 0

    print(f"{len(missing)} column(s) missing from the database:")
    for table, column, ddl_type, default in missing:
        shown = "" if default is None else f"  default {default!r}"
        print(f"  {table}.{column:<22}{ddl_type}{shown}")

    if args.dry_run:
        print("\ndry run: nothing was changed. Re-run with --backfill to apply.")
        return 0

    print()
    added = apply(missing, backfill=args.backfill)
    still_missing, _ = plan()
    print(f"\nadded {added} column(s); {len(still_missing)} still missing")
    if not args.backfill and added:
        print("Existing rows hold NULL in the new columns. Re-run with --backfill "
              "to set the model's default on them.")
    return 0 if not still_missing else 1


if __name__ == "__main__":
    sys.exit(main())
