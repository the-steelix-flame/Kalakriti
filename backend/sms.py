"""
SMS delivery for OTP.

Two real providers. Both make genuine HTTP calls and return the provider's own
message id; neither is simulated.

If no provider is configured the sender returns `not_configured` and the OTP is
*not* delivered. It is never silently accepted, and the verification path does not
weaken - the code still has to match. For local development, setting
OTP_DEV_ECHO=true additionally returns the code in the API response and logs it,
which is the only way to complete a login on a machine with no SMS account. That
flag is loudly surfaced in the UI so nobody mistakes it for production behaviour.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger("sms")
TIMEOUT = 20


def provider() -> str:
    if os.getenv("MSG91_AUTH_KEY") and os.getenv("MSG91_TEMPLATE_ID"):
        return "msg91"
    if os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"):
        return "twilio"
    return "not_configured"


def dev_echo() -> bool:
    return os.getenv("OTP_DEV_ECHO", "").lower() in ("1", "true", "yes")


def send_otp(phone: str, code: str) -> tuple[str, str, str]:
    """
    Returns (provider, reference, error).

    `phone` is a bare 10-digit Indian number; the country code is added per provider.
    """
    p = provider()

    if p == "msg91":
        # MSG91 is the usual choice for Indian OTP: DLT-registered templates, and it
        # is what most Indian government-facing apps already have an account with.
        try:
            r = requests.post(
                "https://control.msg91.com/api/v5/otp",
                params={
                    "template_id": os.getenv("MSG91_TEMPLATE_ID"),
                    "mobile": f"91{phone}",
                    "authkey": os.getenv("MSG91_AUTH_KEY"),
                    "otp": code,
                },
                timeout=TIMEOUT,
            )
            body = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else {"raw": r.text[:300]}
            if r.status_code < 300 and str(body.get("type", "")).lower() != "error":
                return "msg91", str(body.get("request_id", "")), ""
            return "msg91", "", f"HTTP {r.status_code}: {str(body)[:200]}"
        except Exception as e:
            return "msg91", "", f"{type(e).__name__}: {e}"

    if p == "twilio":
        sid = os.getenv("TWILIO_ACCOUNT_SID")
        frm = os.getenv("TWILIO_FROM_NUMBER", "")
        try:
            r = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                auth=(sid, os.getenv("TWILIO_AUTH_TOKEN", "")),
                data={
                    "To": f"+91{phone}", "From": frm,
                    "Body": f"{code} is your Kalakriti verification code. "
                            f"It expires in 5 minutes.",
                },
                timeout=TIMEOUT,
            )
            body = r.json()
            if r.status_code < 300:
                return "twilio", str(body.get("sid", "")), ""
            return "twilio", "", f"HTTP {r.status_code}: {str(body)[:200]}"
        except Exception as e:
            return "twilio", "", f"{type(e).__name__}: {e}"

    if dev_echo():
        log.warning("OTP for %s is %s (OTP_DEV_ECHO on, no SMS provider)", phone, code)
        return "dev_echo", "", ""

    return "not_configured", "", (
        "No SMS provider configured. Set MSG91_AUTH_KEY + MSG91_TEMPLATE_ID, or "
        "TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_FROM_NUMBER. "
        "For local testing set OTP_DEV_ECHO=true."
    )
