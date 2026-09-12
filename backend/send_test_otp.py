#!/usr/bin/env python
"""
Send one real OTP to one real phone, and say exactly what happened.

    python send_test_otp.py 9876543210
    python send_test_otp.py 9876543210 --verify 481920

Why this is separate from preflight.py
--------------------------------------
`preflight.py` deliberately never sends an SMS. It costs money and puts a live
code on somebody's handset, so it reports credentials as *configured* rather than
as *verified* - which is the honest distinction, and it is also why somebody has
to confirm delivery by hand exactly once. This is that one command.

It goes through `auth.request_otp`, not straight to the provider, so what it
exercises is the real login path: the same code generation, the same salted hash
written to `otp_challenges`, the same rate limiting, the same provider selection.
If this puts a code on a phone, login works.

The code is never printed unless OTP_DEV_ECHO is on, for the same reason the API
does not return it. Read it off the handset - that is the whole point of the test.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

import auth          # noqa: E402
import db            # noqa: E402
import sms           # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Send one real OTP and report the result.")
    ap.add_argument("phone", help="10-digit Indian mobile number")
    ap.add_argument("--verify", metavar="CODE",
                    help="verify a code against the most recent challenge for this "
                         "number, completing the round trip")
    args = ap.parse_args()

    provider = sms.provider()
    echo = sms.dev_echo()

    print(f"provider : {provider}")
    print(f"dev echo : {'ON - the code will also be printed below' if echo else 'off'}")

    if provider == "not_configured" and not echo:
        print("\nNothing will be delivered: no provider is configured.")
        print("Set FAST2SMS_API_KEY in backend/.env, then run this again.")
        return 1

    s = db.session()
    try:
        if args.verify:
            ch = (s.query(db.OtpChallenge)
                  .filter(db.OtpChallenge.phone == auth.normalise_phone(args.phone))
                  .order_by(db.OtpChallenge.created_at.desc()).first())
            if not ch:
                print(f"\nNo OTP challenge found for {args.phone}. Send one first.")
                return 1
            out = auth.verify_otp(s, ch.id, args.verify, user_agent="send_test_otp")
            print(f"\nverifying {args.verify} against challenge {ch.id}")
            if out.get("ok"):
                print("  ACCEPTED - login works end to end.")
                print(f"  artisan     : {out['artisan']['id']}")
                print(f"  new account : {out.get('isNewAccount')}")
                print(f"  token       : {out['token'][:32]}...")
                return 0
            print(f"  REJECTED - {out.get('message') or out.get('error')}")
            return 1

        out = auth.request_otp(s, args.phone)
        print()
        print(f"challenge  : {out.get('challengeId')}")
        print(f"delivery   : {out.get('delivery')}")
        print(f"expires in : {out.get('expiresInSeconds')}s")

        if out.get("ok"):
            print(f"\nSent. A code should arrive on {args.phone} within a few seconds.")
            if out.get("devCode"):
                print(f"  (dev echo is on, so for reference the code is "
                      f"{out['devCode']} - turn OTP_DEV_ECHO off to test properly)")
            else:
                print("  The code is NOT printed here and was not returned to any "
                      "client. Read it off the handset.")
            print(f"\nThen finish the round trip:")
            print(f"  python send_test_otp.py {args.phone} --verify <the code>")
            return 0

        print(f"\nNot sent. {out.get('message') or out.get('error')}")
        return 1
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
