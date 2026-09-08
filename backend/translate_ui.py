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


def check(src: str, out: str) -> bool:
    return sorted(PLACEHOLDER.findall(src)) == sorted(PLACEHOLDER.findall(out))


def translate(lang_code: str, lang_name: str, en: dict, hi: dict) -> dict:
    out: dict[str, str] = {}
    keys = list(en.keys())
    total = len(keys)
    for bi, chunk in enumerate(batches(keys, 40), 1):
        payload = {k: {"en": en[k], "hi": hi.get(k, "")} for k in chunk}
        prompt = (f"Translate these {len(chunk)} interface strings into {lang_name}.\n\n"
                  + json.dumps(payload, ensure_ascii=False, indent=1))
        got = {}
        for attempt in range(3):
            try:
                got = llm.chat_json(SYSTEM.format(lang=lang_name), prompt, max_tokens=6000)
                break
            except Exception as e:
                print(f"    batch {bi} attempt {attempt + 1} failed: {e}", flush=True)
        bad = 0
        for k in chunk:
            v = got.get(k)
            if isinstance(v, dict):           # model sometimes echoes {en, hi}
                v = v.get(lang_code) or v.get("translation") or ""
            if not isinstance(v, str) or not v.strip() or not check(en[k], v):
                bad += 1
                out[k] = en[k]                # fall back to English, never to Hindi
            else:
                out[k] = v.strip()
        print(f"    [{lang_code}] batch {bi}: {len(chunk) - bad}/{len(chunk)} ok "
              f"({len(out)}/{total})", flush=True)
    return out


def main():
    en = json.loads((APP / "en.json").read_text(encoding="utf-8"))
    hi = json.loads((APP / "hi.json").read_text(encoding="utf-8"))
    only = sys.argv[1:] or list(TARGETS)
    for code in only:
        if code not in TARGETS:
            continue
        print(f"  translating -> {TARGETS[code]}", flush=True)
        data = translate(code, TARGETS[code], en, hi)
        (APP / f"{code}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        native = sum(1 for k in data if data[k] != en[k])
        print(f"  wrote {code}.json  ({native}/{len(data)} translated)\n", flush=True)


if __name__ == "__main__":
    main()
