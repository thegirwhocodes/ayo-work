"""Short-lived signed caller identity passed from Twilio's webhook to its stream."""

import base64
import hashlib
import hmac
import binascii
import re
import time


# A caller whose number is withheld, blocked or not in E.164 form still gets a signed token, so the
# stream is provably one our Twilio webhook admitted. It carries no phone number.
ANONYMOUS = "anonymous"


def sign_caller(caller_id: str, secret: str) -> str:
    if (caller_id != ANONYMOUS and not re.fullmatch(r"\+[1-9][0-9]{6,14}", caller_id)) or not secret:
        raise ValueError("caller identity and signing secret are required")
    payload = base64.urlsafe_b64encode(f"{int(time.time())}:{caller_id}".encode()).decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_caller(token: str, secret: str, max_age: int = 300) -> str:
    if not secret:
        raise ValueError("caller signing secret is missing")
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("invalid signature")
        timestamp, caller = base64.urlsafe_b64decode(payload.encode()).decode().split(":", 1)
        age = time.time() - int(timestamp)
        if not caller or not 0 <= age <= max_age:
            raise ValueError("expired or empty caller identity")
        return caller
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid caller identity") from exc
