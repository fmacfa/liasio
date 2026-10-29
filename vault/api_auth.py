"""Bearer credentials and encrypted, 24-hour idempotency receipts."""

import hashlib
import hmac
import json
import re
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables
from django.utils.translation import gettext as _

from .mfa import cipher
from .models import APIReceipt, APIToken
from .security import limited, rate_allowed


def digest(value):
    return hmac.new(
        settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def credential_version(user):
    return digest(f"api-credentials:{user.password}:{user.mfa_version}")


def valid(token, user):
    from .sessions import requires_mfa

    return (
        user.is_active
        and (bool(user.mfa_secret) or not requires_mfa(user))
        and not token.revoked_at
        and (token.expires_at is None or token.expires_at > timezone.now())
        and hmac.compare_digest(token.credential_version, credential_version(user))
    )


def authorize_current(request, user):
    """Recheck after acquiring the owner lock, shared with token revocation."""
    if getattr(request, "api_token", None):
        token = APIToken.objects.get(pk=request.api_token.pk)
        if not valid(token, user):
            raise Http404


def error(code, message, status):
    response = JsonResponse(
        {"error": {"code": code, "message": message}}, status=status
    )
    if status == 429:
        response["Retry-After"] = "60"
    return response


def api(scope, methods=("GET",)):
    def decorate(view):
        @csrf_exempt  # Only Authorization: Bearer is accepted; never cookie authority.
        @wraps(view)
        @sensitive_variables("raw", "token")
        def wrapped(request, *args, **kwargs):
            if limited(request, "api-ip", 1200, 60):
                return error("rate_limited", "Too many requests.", 429)
            raw = request.headers.get("Authorization", "")
            match = re.fullmatch(r"Bearer lv_([a-f0-9]{32})\.([A-Za-z0-9_-]{43})", raw)
            token = (
                APIToken.objects.select_related("owner").filter(pk=match[1]).first()
                if match
                else None
            )
            if (
                not token
                or not hmac.compare_digest(token.digest, digest(raw[7:]))
                or not valid(token, token.owner)
            ):
                response = error("unauthorized", "A valid API key is required.", 401)
                response["WWW-Authenticate"] = "Bearer"
                return response
            if scope not in token.scopes:
                return error(
                    "insufficient_scope", "This key lacks the required permission.", 403
                )
            if request.method not in methods:
                response = error("method_not_allowed", "Method not allowed.", 405)
                response["Allow"] = ", ".join(methods)
                return response
            if not rate_allowed("api-owner", str(token.owner_id), 600, 60):
                return error("rate_limited", "Too many requests.", 429)
            request.user, request.api_token = token.owner, token
            APIToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
            try:
                response = view(request, *args, **kwargs)
            except Http404:
                response = error("not_found", "Resource unavailable.", 404)
            except (
                ValidationError,
                ValueError,
                TypeError,
                KeyError,
                UnicodeError,
            ) as exc:
                message = (
                    " ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else "Invalid request."
                )
                response = error("invalid_request", message, 400)
            except OSError:
                response = error(
                    "unavailable", "Storage unavailable. Retry later.", 503
                )
            if response.status_code >= 400 and response.headers.get(
                "Content-Type", ""
            ).startswith("application/json"):
                payload = json.loads(response.content)
                if isinstance(payload.get("error"), str):
                    payload["error"] = {
                        "code": "offset_conflict"
                        if response.status_code == 409
                        else "invalid_request",
                        "message": payload["error"],
                    }
                    response.content = json.dumps(payload).encode()
            if response.status_code == 429:
                response["Retry-After"] = "60"
            return response

        return wrapped

    return decorate


def json_body(request):
    if request.content_type != "application/json":
        raise ValidationError(_("Utilisez le type application/json."))
    value = json.loads(request.body)
    if not isinstance(value, dict):
        raise ValidationError(_("Un objet JSON est requis."))
    return value


def prepare_receipt(request):
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
        raise ValidationError(
            _(
                "Idempotency-Key est requis (8 à 128 lettres, chiffres, tirets ou soulignements)."
            )
        )
    request.api_receipt = (
        digest(key),
        digest(request.path + ":" + request.body.decode()),
    )


def replay(request):
    if not getattr(request, "api_receipt", None):
        return None
    key, fingerprint = request.api_receipt
    receipt = APIReceipt.objects.filter(token=request.api_token, key=key).first()
    if receipt and receipt.created_at < timezone.now() - timedelta(hours=24):
        receipt.delete()
        receipt = None
    if receipt:
        if not hmac.compare_digest(receipt.fingerprint, fingerprint):
            return error(
                "idempotency_conflict",
                "This key was used with a different request.",
                409,
            )
        response = JsonResponse(
            json.loads(cipher().decrypt(receipt.response.encode())),
            status=receipt.status,
        )
        response["Idempotency-Replayed"] = "true"
        return response
    if (
        APIReceipt.objects.filter(
            token__owner=request.user,
            created_at__gte=timezone.now() - timedelta(hours=24),
        ).count()
        >= 1000
    ):
        raise ValidationError(
            _("Limite quotidienne de créations API atteinte (1 000).")
        )
    return None


def remember(request, response):
    if getattr(request, "api_receipt", None) and 200 <= response.status_code < 300:
        key, fingerprint = request.api_receipt
        APIReceipt.objects.create(
            token=request.api_token,
            key=key,
            fingerprint=fingerprint,
            response=cipher().encrypt(response.content).decode(),
            status=response.status_code,
        )
    return response
