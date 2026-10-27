"""Opaque share codes; the database stores only a lookup digest."""

import base64
import hashlib
import hmac
import uuid

from django.conf import settings


def short_code(object_id):
    identifier = uuid.UUID(str(object_id))
    signature = hmac.new(
        settings.SECRET_KEY.encode(),
        f"linkvault:alias:v1:share:{identifier.hex}".encode(),
        hashlib.sha256,
    ).digest()[:16]
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def code_digest(code):
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def resolve_short_code(code):
    from .models import ShareLink

    identifier = (
        ShareLink.objects.filter(short_code_digest=code_digest(code))
        .values_list("pk", flat=True)
        .first()
    )
    # A stale or corrupted lookup must never authorize a different link. This
    # also invalidates old codes immediately when SECRET_KEY changes.
    if identifier is None or not hmac.compare_digest(short_code(identifier), code):
        raise ValueError("Invalid share code")
    return identifier


def preferred_share_token(link):
    from .security import capability

    code = short_code(link.pk)
    if link.short_code_digest == code_digest(code):
        return code
    # Remain usable during key rotation or an upgrade from an older writer.
    # Reading/copying a URL must not write to the database.
    return capability("share", link.pk)
