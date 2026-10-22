"""Receiver-side verification. Call before acknowledging a LinkVault webhook.

Supply the original request bytes, the HTTP headers, and the signing secret.
Persist event['id'] with a UNIQUE constraint in the SAME transaction as your
side effects. Return 2xx for already processed events. Never log the secret.
"""

import hashlib
import hmac
import json
import re
import time
import uuid


def verify(raw_body, headers, secret, *, now=None):
    if len(raw_body) > 65536:
        raise ValueError("Payload too large")
    # Framework headers should be case-insensitive; plain dicts work too.
    values = {key.lower(): value for key, value in headers.items()}
    timestamp = values.get("x-linkvault-timestamp", "")
    signature = values.get("x-linkvault-signature", "")
    if (
        not re.fullmatch(r"[0-9]{1,12}", timestamp)
        or abs((time.time() if now is None else now) - int(timestamp)) > 300
    ):
        raise ValueError("Stale or invalid timestamp")
    if not re.fullmatch(r"v1=[0-9a-f]{64}", signature):
        raise ValueError("Invalid signature format")
    expected = hmac.new(
        secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature[3:], expected):
        raise ValueError("Invalid signature")
    event = json.loads(raw_body)
    uuid.UUID(event["id"])
    if not isinstance(event.get("type"), str) or not isinstance(
        event.get("data"), dict
    ):
        raise ValueError("Invalid event")
    return event
