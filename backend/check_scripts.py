# -*- coding: utf-8 -*-
"""
Finds and repairs script leakage in the generated UI translations.

The translation model occasionally emits a word with one or two Devanagari letters
embedded in an otherwise correct Gujarati or Bengali string - "અकेલા" instead of
"અકેલા". It renders as a visible stumble mid-word, and it is exactly the kind of
defect nobody notices in review because the string is 95% right.

Repair is safe for the Indic blocks that are ISCII-aligned: Devanagari, Bengali,
Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada and Malayalam occupy 128-codepoint
blocks whose letters sit at the same offsets. Mapping a letter across is therefore a
lookup, not a guess. Where the target block has no character at that offset - Tamil
lacks most of the aspirated consonants, for instance - there is no honest repair, and
the string falls back to English so the gap is visible rather than mangled.

    python check_scripts.py           # report only
    python check_scripts.py --fix     # repair in place
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app" / "src" / "i18n" / "locales"

# Start of each script's 128-codepoint block.
BLOCK = {
    "hi": 0x0900, "bn": 0x0980, "gu": 0x0A80, "or": 0x0B00,
    "ta": 0x0B80, "te": 0x0C00, "kn": 0x0C80, "mr": 0x0900,
}
DEVA = 0x0900

# Shared across scripts: danda, double danda, abbreviation sign, Devanagari digits are
# not used here but the punctuation is common to every Indian script.
SHARED = {0x0964, 0x0965, 0x0970, 0x0971}


def in_block(ch: str, start: int) -> bool:
    return start <= ord(ch) < start + 0x80


def stray(text: str, target: int) -> list[str]:
    """Characters from another Indic block than the target."""
    out = []
    for ch in text:
        cp = ord(ch)
        if cp in SHARED:
            continue
        if 0x0900 <= cp <= 0x0D7F and not in_block(ch, target):
            out.append(ch)
    return out


def transliterate(text: str, target: int) -> str | None:
    """
    Map stray Devanagari letters into the target script by block offset.

    Returns None if any character has no assigned counterpart, because a repair that
    invents a letter is worse than an untranslated string.
    """
    out = []
    for ch in text:
        cp = ord(ch)
        if cp in SHARED or not (0x0900 <= cp <= 0x0D7F) or in_block(ch, target):
            out.append(ch)
            continue
        if not (DEVA <= cp < DEVA + 0x80):
            return None                      # not Devanagari; do not guess
        mapped = chr(target + (cp - DEVA))
        try:
            unicodedata.name(mapped)         # raises if unassigned
        except ValueError:
            return None
        out.append(mapped)
    return "".join(out)


def main() -> int:
    fix = "--fix" in sys.argv
    en = json.loads((APP / "en.json").read_text(encoding="utf-8"))
    total_bad = total_fixed = total_english = 0

    for code, start in BLOCK.items():
        f = APP / f"{code}.json"
        if not f.exists() or code in ("hi", "mr"):
            continue                          # hi is authored; mr shares Devanagari
        data = json.loads(f.read_text(encoding="utf-8"))
        bad = {k: v for k, v in data.items() if isinstance(v, str) and stray(v, start)}
        if not bad:
            print(f"  {code}: clean")
            continue
        total_bad += len(bad)
        print(f"  {code}: {len(bad)} strings mix scripts")
        for k, v in list(bad.items())[:4]:
            print(f"      {k}: {v[:52]}")
        if fix:
            for k, v in bad.items():
                repaired = transliterate(v, start)
                if repaired and not stray(repaired, start):
                    data[k] = repaired
                    total_fixed += 1
                else:
                    data[k] = en.get(k, v)    # visible gap beats mangled text
                    total_english += 1
            f.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")
            print(f"      -> repaired in place")

    print(f"\n  {total_bad} affected"
          + (f", {total_fixed} transliterated, {total_english} fell back to English"
             if fix else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
