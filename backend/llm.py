"""
Language layer — NVIDIA Nemotron 3 Ultra.

Served over an OpenAI-compatible endpoint, so the official `openai` client works
unchanged. Nothing here is billed to us: the key is the user's own NVIDIA key.

Model card: https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
MODEL = os.getenv("NEMOTRON_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
API_KEY = os.getenv("NVIDIA_API_KEY", "")

_client: OpenAI | None = None


def available() -> bool:
    return bool(API_KEY)


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=600)
    return _client


def _strip_reasoning(text: str) -> str:
    """Nemotron is a reasoning model and may emit <think>...</think> before the answer."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def chat(system: str, user: str, max_tokens: int = 4096,
         temperature: float = 0.7) -> str:
    r = client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=max_tokens, temperature=temperature, top_p=0.95)
    return _strip_reasoning(r.choices[0].message.content or "")


# ───────────────────────────────────────────────────────────── JSON handling

def _unfence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    return raw


def _largest_object(raw: str) -> str | None:
    """Find the outermost {...} by brace balance, ignoring braces inside strings."""
    start = raw.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(raw)):
        c = raw[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def _repair(raw: str) -> str:
    """
    Fix the malformations these models actually produce, in order of frequency:
    trailing commas, literal newlines inside strings, and - the one that broke a
    real listing here - an unescaped double quote inside a string value.
    """
    s = re.sub(r",\s*([}\]])", r"\1", raw)          # trailing commas

    out, in_str, esc = [], False, False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                out.append(c); esc = False; continue
            if c == "\\":
                out.append(c); esc = True; continue
            if c == "\n":
                out.append("\\n"); continue
            if c == "\t":
                out.append("\\t"); continue
            if c == '"':
                # A closing quote is followed by structural punctuation; anything
                # else means the model left a quote inside the text.
                rest = s[i + 1:i + 40].lstrip()
                if rest[:1] in (",", "}", "]", ":"):
                    in_str = False
                    out.append(c)
                else:
                    out.append('\\"')
                continue
            out.append(c)
            continue
        if c == '"':
            in_str = True
        out.append(c)
    return "".join(out)


def loads_lenient(raw: str) -> dict[str, Any]:
    """Parse model JSON, repairing it if needed. Raises ValueError if unsalvageable."""
    raw = _unfence(raw)
    for candidate in (raw, _largest_object(raw) or raw):
        for attempt in (candidate, _repair(candidate)):
            try:
                v = json.loads(attempt)
                if isinstance(v, dict):
                    return v
            except json.JSONDecodeError:
                continue
    raise ValueError(f"model did not return valid JSON: {raw[:400]}")


def chat_json(system: str, user: str, max_tokens: int = 4096,
              retries: int = 1) -> dict[str, Any]:
    """
    Ask for JSON and parse defensively.

    If parsing still fails after repair, the model is asked again with its own
    broken output and the parser error attached. That is far more reliable than a
    stricter prompt, and much better than the previous behaviour, which silently
    dropped the whole analysis and left every field empty.
    """
    sys_prompt = (system + "\n\nRespond with a single valid JSON object and nothing "
                  "else. No markdown fence, no commentary. Inside string values use "
                  "single quotes, never double quotes.")
    raw = chat(sys_prompt, user, max_tokens=max_tokens, temperature=0.4)
    try:
        return loads_lenient(raw)
    except ValueError as first:
        if retries <= 0:
            raise
        fix = chat(
            sys_prompt,
            "Your previous reply could not be parsed as JSON.\n"
            f"Parser error: {first}\n\n"
            "Here is what you sent:\n---\n" + raw[:4000] + "\n---\n\n"
            "Send the same content again as strictly valid JSON. Escape or remove any "
            "double quotes inside string values.",
            max_tokens=max_tokens, temperature=0.1)
        return loads_lenient(fix)
