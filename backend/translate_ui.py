"""
Generates the app's UI translations with Nemotron.

Run once (or after adding strings) to produce app/src/i18n/locales/<lang>.json.
The output is committed, so the app never needs the network to render its own
interface - an artisan on aeroplane mode still gets Gujarati.

Both English and Hindi are given as source: the Hindi carries the register we want
(respectful, plain, addressed to a woman artisan), and the English disambiguates
words Hindi leaves loose.

Placeholders like {n} and {amount} must survive untouched, so they are verified
after every batch and the batch is retried if any went missing.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
import llm  # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app" / "src" / "i18n" / "locales"

TARGETS = {
    "bn": "Bengali (বাংলা)",
    "mr": "Marathi (मराठी)",
    "gu": "Gujarati (ગુજરાતી)",
    "ta": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)",
    "kn": "Kannada (ಕನ್ನಡ)",
    "or": "Odia (ଓଡ଼ିଆ)",
}

SYSTEM = """You translate the interface of a mobile app used by Indian artisans -
weavers, potters, metalworkers - many of whom read slowly and have never sold online.

Rules:
- Use plain, everyday {lang}. No technical jargon, no English loanwords where a common
  native word exists. Write the way a helpful shopkeeper would speak.
- Address the user respectfully and directly.
- Keep every placeholder EXACTLY as written: {{n}}, {{amount}}, {{sum}}, {{price}},
  {{diff}}, {{units}}, {{days}}, {{id}}, {{ms}}, {{url}}, {{info}}, {{status}},
  {{what}}, {{err}}, {{phone}}. Never translate or reorder the braces themselves.
- Keep these as-is, they are proper nouns or codes: Kalakriti, Nemotron, GST, GSTIN,
  PAN, IFSC, HSN, OCR, AI, SMS, OTP_DEV_ECHO, Wi-Fi, ONDC, GeM, Amazon, Shopify.
- Keep the text short. A button label must stay a button label.
- Return ONLY a JSON object mapping each given key to its translation. No commentary."""


def batches(items, n):
    it = list(items)
    for i in range(0, len(it), n):
        yield it[i:i + n]


PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Where each script's 128-codepoint block starts. Used to catch the model mixing
# scripts mid-word - "અकेલા" for "અકેલા" - which reads as a stumble and is easy to
# miss in review because the rest of the string is correct.
BLOCK = {"bn": 0x0980, "mr": 0x0900, "gu": 0x0A80, "ta": 0x0B80,
         "te": 0x0C00, "kn": 0x0C80, "or": 0x0B00}
SHARED = {0x0964, 0x0965, 0x0970, 0x0971}      # danda and friends, common to all


def check(src: str, out: str) -> bool:
    return sorted(PLACEHOLDER.findall(src)) == sorted(PLACEHOLDER.findall(out))


def right_script(text: str, code: str) -> bool:
    """No characters from an Indic block other than the target one."""
    start = BLOCK.get(code)
    if not start:
        return True
    for ch in text:
        cp = ord(ch)
        if cp in SHARED:
            continue
        if 0x0900 <= cp <= 0x0D7F and not (start <= cp < start + 0x80):
            return False
    return True


def translate(lang_code: str, lang_name: str, en: dict, hi: dict) -> dict:
    out: dict[str, str] = {}
    keys = list(en.keys())
    total = len(keys)
    for bi, chunk in enumerate(batches(keys, 40), 1):
        payload = {k: {"en": en[k], "hi": hi.get(k, "")} for k in chunk}
        prompt = (f"Translate these {len(chunk)} interface strings into {lang_name}.\n\n"
                  + json.dumps(payload, ensure_ascii=False, indent=1))
        got = {}
        for attempt in range(6):
            try:
                got = llm.chat_json(SYSTEM.format(lang=lang_name), prompt, max_tokens=6000)
                break
            except Exception as e:
                msg = str(e)
                # 503 "Service temporarily overloaded" and 429 are transient. Retrying
                # immediately just adds to the pile, and giving up writes English into
                # the file - which then looks like a finished translation. Back off
                # instead: 20s, 40s, 80s, ...
                transient = "503" in msg or "429" in msg or "overload" in msg.lower()
                wait = min(20 * (2 ** attempt), 300) if transient else 5
                print(f"    batch {bi} attempt {attempt + 1} failed "
                      f"({'transient, waiting %ds' % wait if transient else 'giving up soon'}): "
                      f"{msg[:120]}", flush=True)
                if attempt < 5:
                    time.sleep(wait)
        bad = 0
        for k in chunk:
            v = got.get(k)
            if isinstance(v, dict):           # model sometimes echoes {en, hi}
                v = v.get(lang_code) or v.get("translation") or ""
            if (not isinstance(v, str) or not v.strip() or not check(en[k], v)
                    or not right_script(v, lang_code)):
                bad += 1
                out[k] = en[k]                # fall back to English, never to Hindi
            else:
                out[k] = v.strip()
        print(f"    [{lang_code}] batch {bi}: {len(chunk) - bad}/{len(chunk)} ok "
              f"({len(out)}/{total})", flush=True)
    return out


def main():
    """
    By default this fills gaps only: keys absent from a language file, and keys whose
    value is still byte-identical to the English source, which means an earlier batch
    failed and fell back. Pass --all to retranslate everything.

    Incremental is the sane default. A full pass is roughly half an hour per language,
    and after adding a handful of strings there is no reason to spend that on the four
    hundred that are already right. It is also how Kannada and Odia get repaired: an
    earlier run left them almost entirely English, and that is exactly the condition
    this detects.
    """
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--all" in sys.argv

    en = json.loads((APP / "en.json").read_text(encoding="utf-8"))
    hi = json.loads((APP / "hi.json").read_text(encoding="utf-8"))
    only = args or list(TARGETS)

    for code in only:
        if code not in TARGETS:
            continue
        path = APP / f"{code}.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

        if full:
            todo = dict(en)
        else:
            todo = {k: v for k, v in en.items()
                    if k not in existing or existing[k] == v}
            # Drop keys the catalogue no longer has, so a deleted screen's strings do
            # not linger in eight language files forever.
            existing = {k: v for k, v in existing.items() if k in en}

        if not todo:
            print(f"  {code}: already complete ({len(existing)} strings)", flush=True)
            continue

        print(f"  translating -> {TARGETS[code]}  ({len(todo)} of {len(en)} needed)",
              flush=True)
        got = translate(code, TARGETS[code], todo, hi)

        # Merge: a new translation wins, an earlier good one is kept, English is the
        # last resort - so a failed batch can never undo work that already succeeded.
        merged = {}
        for k in en:
            new = got.get(k)
            old = existing.get(k)
            if new and new != en[k]:
                merged[k] = new
            elif old and old != en[k]:
                merged[k] = old
            else:
                merged[k] = new or old or en[k]

        path.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        native = sum(1 for k in merged if merged[k] != en[k])
        print(f"  wrote {code}.json  ({native}/{len(merged)} translated)", flush=True)


if __name__ == "__main__":
    main()
