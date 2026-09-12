"""
SMS delivery for OTP.

Three real providers. All three make genuine HTTP calls and return the provider's
own message id; none is simulated.

  msg91     The production answer for India: our own sender id and our own
            DLT-registered template. Needs that template approved first, which
            takes days.
  fast2sms  The one that can be working this afternoon. Its OTP route sends under
            Fast2SMS's own approved template, so nothing needs DLT registration at
            our end - an API key is the only requirement. The message wording and
            sender id are theirs.
  twilio    Works, but Indian carriers filter unregistered international senders,
            so delivery is the least predictable.

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
    """
    Which provider this process will actually use, in preference order.

    MSG91 first because it is the right production answer for India: a DLT-registered
    template under our own sender id. Fast2SMS second because it is the only one of
    the three that can be live the same afternoon - its OTP route sends under
    Fast2SMS's own pre-approved template, so no DLT registration is needed at our
    end. Twilio last: it works, but Indian carriers filter unregistered
    international senders, so delivery is the least predictable of the three.

    Only providers whose credentials are actually present are considered, so the
    order only decides between two that are both configured.
    """
    if os.getenv("MSG91_AUTH_KEY") and os.getenv("MSG91_TEMPLATE_ID"):
        return "msg91"
    if os.getenv("FAST2SMS_API_KEY"):
        return "fast2sms"
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

    if p == "fast2sms":
        # Fast2SMS's OTP route delivers under *their* DLT-approved template, which
        # reads "Your OTP: <code>". That is the whole reason this provider is here:
        # it needs an API key and nothing else, where MSG91 needs a template of our
        # own approved on the DLT portal first, and that takes days.
        #
        # The cost of that convenience is that the message wording and the sender id
        # are theirs, not ours. Fine for getting a real code onto a real phone now;
        # MSG91 is still the right answer before this is in anybody's hands for real.
        #
        # We pass our own generated code rather than letting them generate one,
        # because the hash we verify against is ours and the provider must never be
        # the source of truth for what the code is.
        try:
            r = requests.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": os.getenv("FAST2SMS_API_KEY", ""),
                         "Content-Type": "application/json"},
                json={"route": "otp", "variables_values": code, "numbers": phone},
                timeout=TIMEOUT,
            )
            body = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else {"raw": r.text[:300]}
            # `message` is a list on the bulk routes and a plain string on some
            # errors, so it is normalised rather than assumed to be either.
            msg = body.get("message", "")
            if isinstance(msg, list):
                msg = "; ".join(str(m) for m in msg)
            if r.status_code < 300 and body.get("return") is True:
                return "fast2sms", str(body.get("request_id", "")), ""
            return "fast2sms", "", f"HTTP {r.status_code}: {msg or str(body)[:200]}"
        except Exception as e:
            return "fast2sms", "", f"{type(e).__name__}: {e}"

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
        "No SMS provider configured. Set FAST2SMS_API_KEY (fastest - no DLT "
        "registration needed), or MSG91_AUTH_KEY + MSG91_TEMPLATE_ID, or "
        "TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN + TWILIO_FROM_NUMBER. "
        "For local testing set OTP_DEV_ECHO=true."
    )
