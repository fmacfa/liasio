import base64
import hashlib
import hmac
import re
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.http import Http404
from django.utils import timezone

from .models import RateBucket


def _capability_signature(purpose, identifier, *, compact):
    namespace = "linkvault:compact:v1" if compact else "linkvault"
    return hmac.new(
        settings.SECRET_KEY.encode(),
        f"{namespace}:{purpose}:{identifier.hex}".encode(),
        hashlib.sha256,
    ).digest()


def capability(purpose, object_id):
    """A 43-character URL-safe UUID + HMAC-SHA256-128 capability.

    The 128-bit tag follows RFC 2104 section 5. Domain separation keeps this
    format distinct from legacy signatures and invitations distinct from shares.
    Tokens remain derived from SECRET_KEY, never stored as plaintext in the DB.
    """
    identifier = uuid.UUID(str(object_id))
    signature = _capability_signature(purpose, identifier, compact=True)[:16]
    return (
        base64.urlsafe_b64encode(identifier.bytes + signature)
        .rstrip(b"=")
        .decode("ascii")
    )


def verify_capability(purpose, token):
    try:
        if not isinstance(token, str):
            raise ValueError
        if (
            purpose == "share"
            and len(token) == 22
            and re.fullmatch(r"[A-Za-z0-9_-]{21}[AQgw]", token)
        ):
            from .share_urls import resolve_short_code

            return resolve_short_code(token)
        elif len(token) == 43 and re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            payload = base64.b64decode(token + "=", altchars=b"-_", validate=True)
            parsed = uuid.UUID(bytes=payload[:16])
            # Compare the canonical encoding too: reject alternate padding bits.
            expected = capability(purpose, parsed)
        elif len(token) == 97 and re.fullmatch(r"[0-9a-f]{32}\.[0-9a-f]{64}", token):
            parsed = uuid.UUID(hex=token[:32])
            signature = _capability_signature(purpose, parsed, compact=False)
            expected = f"{parsed.hex}.{signature.hex()}"
        else:
            raise ValueError
        if not hmac.compare_digest(expected, token):
            raise ValueError
        return parsed
    except (ValueError, AttributeError, TypeError):
        raise Http404 from None


def client_ip(request):
    # Only enable TRUST_PROXY behind the supplied non-public app container.
    return (
        request.META.get("HTTP_X_REAL_IP") if settings.TRUST_PROXY else None
    ) or request.META.get("REMOTE_ADDR", "unknown")


def rate_allowed(scope, identity, limit, seconds=900):
    window = int(time.time()) // seconds
    key = hmac.new(
        settings.SECRET_KEY.encode(),
        f"rate:{scope}:{identity}:{window}".encode(),
        hashlib.sha256,
    ).hexdigest()
    RateBucket.objects.get_or_create(
        key=key,
        defaults={"expires_at": timezone.now() + timedelta(seconds=seconds * 2)},
    )
    # Atomic increment across workers. Racing readers may conservatively reject.
    RateBucket.objects.filter(pk=key).update(hits=F("hits") + 1)
    return RateBucket.objects.get(pk=key).hits <= limit


def limited(request, scope, limit=20, seconds=900):
    return not rate_allowed(scope, client_ip(request), limit, seconds)
