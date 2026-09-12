"""
Authentication: phone + OTP, backed by real server-side sessions.

Design notes
------------
* The OTP is the front door. A phone number is the one credential this user already
  has and cannot forget, and it is what every marketplace and courier will actually
  call. Passwords are the fallback, not the design.
* Password sign-in exists anyway, because OTP delivery depends on an SMS provider
  with credit on it. When that fails there is otherwise no way into the product at
  all - not for an artisan whose network is down, and not for anybody being shown
  it. It is a real implementation: scrypt, per-account lockout, constant-time
  compare. See `set_password` for why it is not a shortcut.
* The OTP is six digits from `secrets`, stored only as a salted SHA-256 hash with a
  five-minute expiry, five verify attempts, and a per-phone send cooldown. A screen
  that accepted any six digits would be worse than useless here, since the phone
  number is what every marketplace and courier will actually call.
* A JWT carries the session id; the session row is authoritative, so logout and
  expiry genuinely invalidate rather than relying on the client discarding a token.
* An Artisan row is created only after a successful verification, so its existence
  always means a verified phone.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

import db
import sms

JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALG = "HS256"
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "60"))
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "300"))
OTP_RESEND_COOLDOWN = int(os.getenv("OTP_RESEND_COOLDOWN", "45"))
_KEY_FILE = os.getenv("JWT_SECRET_FILE", "jwt_secret.txt")


def _secret() -> str:
    """Persisted so a restart does not log everybody out."""
    global JWT_SECRET
    if JWT_SECRET:
        return JWT_SECRET
    if os.path.exists(_KEY_FILE):
        JWT_SECRET = open(_KEY_FILE, encoding="utf-8").read().strip()
    else:
        JWT_SECRET = secrets.token_urlsafe(48)
        with open(_KEY_FILE, "w", encoding="utf-8") as f:
            f.write(JWT_SECRET)
    return JWT_SECRET


def normalise_phone(raw: str) -> str | None:
    """Accepts +91XXXXXXXXXX, 91XXXXXXXXXX, 0XXXXXXXXXX, XXXXXXXXXX."""
    d = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) != 10 or d[0] not in "6789":
        return None
    return d


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256((salt + code).encode()).hexdigest()


# ────────────────────────────────────────────────────────────────── OTP issue

def request_otp(s, phone_raw: str) -> dict:
    phone = normalise_phone(phone_raw)
    if not phone:
        return {"ok": False, "error": "invalid_phone",
                "message": "Enter a valid 10-digit Indian mobile number."}

    recent = (s.query(db.OtpChallenge)
              .filter(db.OtpChallenge.phone == phone)
              .order_by(db.OtpChallenge.created_at.desc()).first())
    if recent and recent.created_at:
        created = db.naive_utc(recent.created_at)
        age = (db.now() - created).total_seconds()
        if age < OTP_RESEND_COOLDOWN and not recent.consumed:
            return {"ok": False, "error": "cooldown",
                    "retryAfter": int(OTP_RESEND_COOLDOWN - age),
                    "message": f"Please wait {int(OTP_RESEND_COOLDOWN - age)}s "
                               f"before asking for another code."}

    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(8)
    ch = db.OtpChallenge(
        id=db.nid("otp"), phone=phone, code_hash=_hash(code, salt), salt=salt,
        expires_at=db.now() + timedelta(seconds=OTP_TTL_SECONDS),
    )

    provider, ref, err = sms.send_otp(phone, code)
    ch.delivery = provider
    ch.delivery_ref = ref
    s.add(ch)
    db.log_event(s, "otp", ch.id, "note",
                 detail=f"otp issued to {phone[:2]}****{phone[-2:]} via {provider}")
    s.commit()

    out = {
        "ok": provider != "not_configured",
        "challengeId": ch.id,
        "phone": phone,
        "delivery": provider,
        "expiresInSeconds": OTP_TTL_SECONDS,
    }
    if provider == "not_configured":
        out["error"] = "sms_not_configured"
        out["message"] = err
    elif err:
        out["ok"] = False
        out["error"] = "sms_failed"
        out["message"] = err
    # Development escape hatch only, and always labelled as such.
    if sms.dev_echo():
        out["devCode"] = code
        out["devNotice"] = ("SMS is not configured, so the code is shown here. "
                            "This only happens because OTP_DEV_ECHO is enabled.")
    return out


# ───────────────────────────────────────────────────────────────── OTP verify

def verify_otp(s, challenge_id: str, code: str, user_agent: str = "",
               guest_token: str = "") -> dict:
    ch = s.get(db.OtpChallenge, challenge_id)
    if not ch:
        return {"ok": False, "error": "unknown_challenge",
                "message": "That code request has expired. Please ask for a new code."}
    if ch.consumed:
        return {"ok": False, "error": "already_used",
                "message": "This code was already used. Please request a new one."}

    exp = db.naive_utc(ch.expires_at)
    if not exp or db.now() > exp:
        return {"ok": False, "error": "expired",
                "message": "The code has expired. Please request a new one."}
    if ch.attempts >= ch.max_attempts:
        return {"ok": False, "error": "too_many_attempts",
                "message": "Too many wrong attempts. Please request a new code."}

    ch.attempts += 1
    s.commit()

    if not secrets.compare_digest(_hash((code or "").strip(), ch.salt), ch.code_hash):
        left = max(ch.max_attempts - ch.attempts, 0)
        return {"ok": False, "error": "wrong_code", "attemptsLeft": left,
                "message": f"That code is not correct. {left} attempts left."}

    ch.consumed = 1

    artisan = s.query(db.Artisan).filter(db.Artisan.phone == ch.phone).first()
    created = False
    if not artisan:
        artisan = db.Artisan(id=db.nid("art"), phone=ch.phone)
        s.add(artisan)
        created = True
    artisan.phone_verified = 1
    artisan.phone_verified_at = db.now()

    sess = db.Session(id=db.nid("ses"), artisan_id=artisan.id,
                      expires_at=db.now() + timedelta(days=SESSION_DAYS),
                      user_agent=(user_agent or "")[:200])
    s.add(sess)
    db.log_event(s, "artisan", artisan.id, "status", "", "phone_verified",
                 "created" if created else "login")

    # Hand the guest's existing drafts to the account they just proved they own.
    claimed = claim_guest_drafts(s, artisan.id, guest_token)
    s.commit()

    return {"ok": True, "token": issue_token(sess), "artisan": artisan.public(),
            "isNewAccount": created, "claimedDrafts": claimed}


# ───────────────────────────────────────────────────────────────────── tokens

def claim_guest_drafts(s, artisan_id: str, guest_token: str) -> int:
    """
    Hand drafts made before signing in to the account that just proved itself.

    Shared by both ways in. A draft belongs to a device until somebody proves who
    they are, and which credential they proved it with does not change what happens
    to the work - losing a morning's photographs because you signed in with a
    password rather than a code would be an absurd distinction to the person it
    happened to.

    Only unclaimed rows move. A draft already belonging to somebody is never
    reassigned by presenting a device token.
    """
    if not guest_token:
        return 0
    rows = (s.query(db.Listing)
            .filter(db.Listing.guest_token == guest_token,
                    db.Listing.artisan_id.is_(None)).all())
    for r in rows:
        r.artisan_id = artisan_id
        db.log_event(s, "listing", r.id, "note", detail="claimed by artisan on login")
    return len(rows)


# ───────────────────────────────────────────────────── password sign-in

# scrypt rather than a plain hash, and from the standard library rather than a new
# dependency. SHA-256 over a password is a rejected design here: it is fast, which is
# exactly the wrong property - a commodity GPU tries billions of SHA-256 guesses a
# second, and the passwords real people choose do not survive that. scrypt is
# memory-hard, so the same attack needs memory per guess instead of just arithmetic.
#
# These parameters cost roughly 100ms and 16MB per attempt on this hardware. That is
# deliberately slow: it is unnoticeable once at sign-in, and it is what makes an
# offline attack on a stolen database expensive rather than an afternoon's work.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LEN = 32

MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "8"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))


def hash_password(plaintext: str) -> str:
    """`scrypt$n$r$p$salt$hash`, all base64. Self-describing so parameters can rise."""
    import base64

    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(plaintext.encode("utf-8"), salt=salt, n=SCRYPT_N,
                        r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN)
    b64 = lambda b: base64.b64encode(b).decode()          # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(dk)}"


def check_password(plaintext: str, stored: str) -> bool:
    """
    Constant-time compare against a stored hash.

    An empty or unparseable hash returns False rather than raising. An account with
    no password set must fail to sign in, not error in a way that distinguishes it
    from a wrong password - that difference is itself something worth not leaking.
    """
    import base64

    if not stored or not plaintext:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(plaintext.encode("utf-8"),
                            salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p),
                            dklen=len(base64.b64decode(hash_b64)))
        return secrets.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


def set_password(s, artisan: db.Artisan, plaintext: str) -> db.Artisan:
    """Set or replace a password. Eight characters minimum, and that is the floor."""
    if len(plaintext or "") < 8:
        raise ValueError("a password needs at least 8 characters")
    artisan.password_hash = hash_password(plaintext)
    artisan.password_set_at = db.now()
    artisan.failed_logins = 0
    artisan.locked_until = None
    s.flush()
    return artisan


def login_with_password(s, phone_raw: str, password: str,
                        user_agent: str = "") -> dict:
    """
    Sign in with a phone number and a password.

    Every failure returns the same message. "No such account" and "wrong password"
    are different facts, and telling them apart hands an attacker a way to find out
    which phone numbers are registered - which for this user base is a list of
    people, not of usernames.
    """
    phone = normalise_phone(phone_raw)
    if not phone:
        return {"ok": False, "error": "bad_phone",
                "message": "Enter a 10-digit mobile number."}

    a = s.query(db.Artisan).filter(db.Artisan.phone == phone).first()
    now = db.now()

    # Cost the attempt before checking anything, so a missing account and a wrong
    # password take about the same time.
    if a is None:
        hash_password(password or "x")
        return {"ok": False, "error": "bad_credentials",
                "message": "That number and password do not match."}

    if a.locked_until and db.naive_utc(a.locked_until) > now:
        mins = int((db.naive_utc(a.locked_until) - now).total_seconds() // 60) + 1
        return {"ok": False, "error": "locked",
                "message": f"Too many wrong attempts. Try again in {mins} minute(s)."}

    if not check_password(password, a.password_hash or ""):
        a.failed_logins = (a.failed_logins or 0) + 1
        if a.failed_logins >= MAX_FAILED_LOGINS:
            a.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            a.failed_logins = 0
            s.commit()
            return {"ok": False, "error": "locked",
                    "message": f"Too many wrong attempts. Try again in "
                               f"{LOCKOUT_MINUTES} minutes."}
        s.commit()
        return {"ok": False, "error": "bad_credentials",
                "message": "That number and password do not match."}

    a.failed_logins = 0
    a.locked_until = None
    sess = db.Session(id=db.nid("ses"), artisan_id=a.id, user_agent=user_agent[:200],
                      expires_at=now + timedelta(days=SESSION_DAYS))
    s.add(sess)
    db.log_event(s, "artisan", a.id, "login", "", "password", user_agent[:80])
    s.commit()
    return {"ok": True, "token": issue_token(sess), "artisan": a.public()}


def issue_token(sess: db.Session) -> str:
    """
    Sign a token whose `exp` claim agrees with the session row it came from.

    The tzinfo below is not decoration. Every timestamp in this schema is naive
    UTC, and `datetime.timestamp()` on a naive value interprets it as *local*
    time - so on a laptop in IST the exp claim came out 5.5 hours before the
    expiry actually stored on the session, and in a zone behind UTC it came out
    later than it. The session row is checked separately and is authoritative,
    which is the only reason this was survivable: a 30-day session losing five
    and a half hours looks like nothing.

    It stops looking like nothing the moment SESSION_DAYS is shortened past the
    offset, because then every token is born already expired and nobody can log
    in anywhere - with the OTP, the session row and the signature all correct.
    Stating the zone makes the claim mean what it says.
    """
    exp = db.naive_utc(sess.expires_at).replace(tzinfo=timezone.utc)
    return jwt.encode(
        {"sid": sess.id, "aid": sess.artisan_id, "exp": int(exp.timestamp())},
        _secret(), algorithm=JWT_ALG)


def artisan_from_token(s, token: str | None) -> db.Artisan | None:
    """The session row is authoritative - a valid signature is not enough."""
    if not token:
        return None
    token = token.replace("Bearer ", "").strip()
    try:
        claims = jwt.decode(token, _secret(), algorithms=[JWT_ALG])
    except Exception:
        return None
    sess = s.get(db.Session, claims.get("sid", ""))
    if not sess or sess.revoked:
        return None
    exp = db.naive_utc(sess.expires_at)
    if not exp or db.now() > exp:
        return None
    return s.get(db.Artisan, sess.artisan_id)


def logout(s, token: str | None) -> bool:
    if not token:
        return False
    token = token.replace("Bearer ", "").strip()
    try:
        claims = jwt.decode(token, _secret(), algorithms=[JWT_ALG],
                            options={"verify_exp": False})
    except Exception:
        return False
    sess = s.get(db.Session, claims.get("sid", ""))
    if not sess:
        return False
    sess.revoked = 1
    s.commit()
    return True
