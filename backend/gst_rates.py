#!/usr/bin/env python
"""
Seed the HSN -> GST rate reference table.

Run once after the tables exist, and again whenever a rate changes:

    python gst_rates.py            # insert missing rows, leave existing ones alone
    python gst_rates.py --force    # overwrite every row from this file
    python gst_rates.py --list     # print what is in the table now

Why this is a data file and not a lookup in code
------------------------------------------------
A GST rate is law. It changes by notification, it is not derivable from anything
about the product, and getting it wrong costs the seller money or gets them a
notice. So the rates live in one table, seeded from one file, with a `source` on
every row saying which chapter or notification it came from.

Two things this file deliberately does NOT do
---------------------------------------------
1. It does not guess. Only headings that are genuinely relevant to handloom,
   handicraft and the categories this app catalogues are listed. An HSN heading
   absent from here reads back as *unknown* from `db.gst_for_hsn`, never as zero.
   That asymmetry is on purpose: most of these headings really are nil-rated, which
   is exactly what would make a silent default to 0% feel correct and be wrong for
   the 12% ones.

2. It does not resolve value-conditional rates. Several textile headings are 5%
   below a per-piece sale value and 12% above it. Those rows carry
   `needs_confirmation = 1` and the condition in words, so a settlement built on
   them stops and asks instead of quietly picking one.

Before any of this computes a real payout, somebody with a CA's advice has to read
this table once. It is a researched starting point, not a substitute for that.
"""
from __future__ import annotations

import argparse
import sys

import db

# (hsn, description, rate, condition_note, needs_confirmation, source)
#
# Chapter 50-63 cover textiles; 42, 44, 68-71, 94 and 97 cover the leather, wood,
# stone, metal, jewellery and artware categories this app also catalogues.
RATES: list[tuple[str, str, float, str, int, str]] = [
    # ── Handloom and textile fabric, by fibre ──────────────────────────────────
    ("5007", "Woven fabrics of silk or silk waste", 5.0, "", 0, "GST Ch.50"),
    ("5111", "Woven fabrics of carded wool", 5.0, "", 0, "GST Ch.51"),
    ("5112", "Woven fabrics of combed wool", 5.0, "", 0, "GST Ch.51"),
    ("5208", "Woven fabrics of cotton, 85%+ cotton, <=200 g/m2", 5.0, "", 0, "GST Ch.52"),
    ("5209", "Woven fabrics of cotton, 85%+ cotton, >200 g/m2", 5.0, "", 0, "GST Ch.52"),
    ("5210", "Woven cotton fabric mixed with man-made fibre", 5.0, "", 0, "GST Ch.52"),
    ("5309", "Woven fabrics of flax (linen)", 5.0, "", 0, "GST Ch.53"),
    ("5311", "Woven fabrics of other vegetable textile fibres, incl. jute",
     5.0, "", 0, "GST Ch.53"),

    # ── Made-up textile articles ──────────────────────────────────────────────
    ("5701", "Carpets and floor coverings, knotted", 5.0,
     "Rate is value-conditional for some floor coverings - confirm the per-piece "
     "sale value before applying this to a payout.", 1, "GST Ch.57"),
    ("5702", "Carpets, woven, not tufted or flocked", 5.0,
     "Value-conditional for some sub-headings - confirm before use.", 1, "GST Ch.57"),
    ("5703", "Carpets and floor coverings, tufted", 5.0,
     "Value-conditional for some sub-headings - confirm before use.", 1, "GST Ch.57"),
    ("5705", "Other carpets and textile floor coverings, incl. durries",
     5.0, "", 0, "GST Ch.57"),
    ("6117", "Made-up clothing accessories, knitted or crocheted", 5.0,
     "5% up to the notified per-piece sale value, 12% above it - confirm the value.",
     1, "GST Ch.61"),
    ("6214", "Shawls, scarves, mufflers, mantillas and veils", 5.0,
     "5% up to the notified per-piece sale value, 12% above it - confirm the value.",
     1, "GST Ch.62"),
    ("6217", "Other made-up clothing accessories", 5.0,
     "5% up to the notified per-piece sale value, 12% above it - confirm the value.",
     1, "GST Ch.62"),
    ("6301", "Blankets and travelling rugs", 5.0,
     "5% up to the notified sale value, 12% above it - confirm the value.",
     1, "GST Ch.63"),
    ("6302", "Bed linen, table linen, toilet and kitchen linen", 5.0,
     "5% up to the notified sale value, 12% above it - confirm the value.",
     1, "GST Ch.63"),
    ("6304", "Other furnishing articles, incl. bedspreads and cushion covers", 5.0,
     "5% up to the notified sale value, 12% above it - confirm the value.",
     1, "GST Ch.63"),
    ("6305", "Sacks and bags, of textile materials, incl. jute bags",
     5.0, "", 0, "GST Ch.63"),

    # ── Leather, wood, cane ───────────────────────────────────────────────────
    ("4202", "Trunks, suitcases, handbags and similar containers", 18.0,
     "", 0, "GST Ch.42"),
    ("4414", "Wooden frames for paintings, photographs and mirrors",
     12.0, "", 0, "GST Ch.44"),
    ("4419", "Tableware and kitchenware, of wood", 12.0, "", 0, "GST Ch.44"),
    ("4420", "Wood marquetry and inlaid wood; wooden articles of furniture",
     12.0, "", 0, "GST Ch.44"),
    ("4421", "Other articles of wood", 12.0, "", 0, "GST Ch.44"),
    ("4602", "Basketwork, wickerwork and other articles of plaiting materials",
     5.0, "", 0, "GST Ch.46"),

    # ── Stone, ceramic, glass ─────────────────────────────────────────────────
    ("6802", "Worked monumental or building stone, incl. marble artware",
     18.0, "", 0, "GST Ch.68"),
    ("6815", "Articles of stone or other mineral substances", 12.0, "", 0, "GST Ch.68"),
    ("6912", "Ceramic tableware and kitchenware, other than porcelain",
     12.0, "", 0, "GST Ch.69"),
    ("6913", "Statuettes and other ornamental ceramic articles", 12.0, "", 0, "GST Ch.69"),
    ("7013", "Glassware for table, kitchen, toilet or indoor decoration",
     18.0, "", 0, "GST Ch.70"),
    ("7018", "Glass beads, imitation pearls and similar small glass ware",
     5.0, "", 0, "GST Ch.70"),

    # ── Jewellery and metal ───────────────────────────────────────────────────
    ("7113", "Articles of jewellery of precious metal", 3.0, "", 0, "GST Ch.71"),
    ("7117", "Imitation jewellery", 3.0, "", 0, "GST Ch.71"),
    ("7323", "Table, kitchen and household articles of iron or steel",
     12.0, "", 0, "GST Ch.73"),
    ("7418", "Table, kitchen and household articles of copper, incl. brassware",
     12.0, "", 0, "GST Ch.74"),
    ("8306", "Bells, gongs, statuettes and ornaments of base metal",
     12.0, "", 0, "GST Ch.83"),

    # ── Furniture, toys, artware ──────────────────────────────────────────────
    ("9403", "Other furniture and parts thereof", 18.0, "", 0, "GST Ch.94"),
    ("9503", "Tricycles, dolls and other toys", 12.0,
     "Wooden toys are separately notified at a lower rate in some cases - confirm.",
     1, "GST Ch.95"),
    ("9601", "Worked ivory, bone, horn, shell and other carving material",
     12.0, "", 0, "GST Ch.96"),
    ("9701", "Paintings, drawings and pastels, executed by hand",
     12.0, "", 0, "GST Ch.97"),
    ("9703", "Original sculptures and statuary, in any material", 12.0, "", 0, "GST Ch.97"),
]


def seed(force: bool = False) -> tuple[int, int]:
    """Insert missing rows. Returns (written, left alone)."""
    s = db.session()
    written = skipped = 0
    try:
        for hsn, desc, rate, cond, confirm, source in RATES:
            row = s.get(db.HsnGstRate, hsn)
            if row is not None and not force:
                skipped += 1
                continue
            if row is None:
                row = db.HsnGstRate(hsn_code=hsn)
                s.add(row)
            row.description = desc
            row.gst_rate = rate
            row.condition_note = cond
            row.needs_confirmation = confirm
            row.source = source
            written += 1
        s.commit()
    finally:
        s.close()
    return written, skipped


def show() -> None:
    s = db.session()
    try:
        rows = s.query(db.HsnGstRate).order_by(db.HsnGstRate.hsn_code).all()
        if not rows:
            print("the table is empty - run this script with no arguments to seed it")
            return
        print(f"{'HSN':<8}{'rate':>6}  {'confirm':<9}description")
        for r in rows:
            flag = "yes" if r.needs_confirmation else ""
            print(f"{r.hsn_code:<8}{r.gst_rate:>5.1f}%  {flag:<9}{r.description[:56]}")
        print(f"\n{len(rows)} headings. "
              f"{sum(1 for r in rows if r.needs_confirmation)} need a human to confirm "
              f"the value threshold before they compute a payout.")
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed the HSN/GST reference table.")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing rows from this file")
    ap.add_argument("--list", action="store_true", help="print the table and exit")
    args = ap.parse_args()

    db.init()
    if args.list:
        show()
        return 0

    written, skipped = seed(force=args.force)
    print(f"wrote {written} heading(s), left {skipped} already present")
    if skipped and not args.force:
        print("pass --force to overwrite the rows that were left alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
