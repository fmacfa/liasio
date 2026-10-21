import hashlib
import hmac
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.http import Http404
from django.utils import timezone

from .models import RateBucket


def capability(purpose, object_id):
    identifier = uuid.UUID(str(object_id)).hex
    signature = hmac.new(settings.SECRET_KEY.encode(), f"linkvault:{purpose}:{identifier}".encode(), hashlib.sha256).hexdigest()
    return f"{identifier}.{signature}"


def verify_capability(purpose, token):
    try:
        identifier, _ = token.split(".", 1)
        parsed = uuid.UUID(identifier)
        if not hmac.compare_digest(capability(purpose, parsed), token):
            raise ValueError
        return parsed
    except (ValueError, AttributeError):
        raise Http404 from None


def client_ip(request):
    # Only enable TRUST_PROXY behind the supplied non-public app container.
    return (request.META.get("HTTP_X_REAL_IP") if settings.TRUST_PROXY else None) or request.META.get("REMOTE_ADDR", "unknown")


def rate_allowed(scope, identity, limit, seconds=900):
    window = int(time.time()) // seconds
    key = hmac.new(settings.SECRET_KEY.encode(), f"rate:{scope}:{identity}:{window}".encode(), hashlib.sha256).hexdigest()
    RateBucket.objects.get_or_create(key=key, defaults={"expires_at": timezone.now() + timedelta(seconds=seconds * 2)})
    # Atomic increment across workers. Racing readers may conservatively reject.
    RateBucket.objects.filter(pk=key).update(hits=F("hits") + 1)
    return RateBucket.objects.get(pk=key).hits <= limit


def limited(request, scope, limit=20, seconds=900):
    return not rate_allowed(scope, client_ip(request), limit, seconds)
