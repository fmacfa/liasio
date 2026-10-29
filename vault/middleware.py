from django.utils.translation import gettext_lazy as _
import logging
import re

from django.conf import settings
from django.http import HttpResponse


class RedactURLs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(
            r"/(s|invite|reset|file-requests)/[^\s?\"']+", r"/\1/[redacted]/", message
        )
        message = re.sub(
            r"/oidc/callback/[^\s\"']*", "/oidc/callback/[redacted]", message
        )
        record.msg, record.args = message, ()
        return True


class RequestBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.get_host()  # Force host validation even when views don't need it.
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = -1
        ceiling = (
            settings.MAX_MULTIPART_BYTES + 1024 * 1024
            if request.path.endswith("/upload/")
            else 1024 * 1024
        )
        if request.path == "/branding/":
            ceiling = 3 * 1024 * 1024
        if request.path.endswith("/links/batch/"):
            ceiling = 1152 * 1024
        if request.path.startswith("/api/v1/collections/") and request.path.endswith(
            "/link-batches/"
        ):
            ceiling = 256 * 1024
        if request.path.endswith("/chunk/"):
            ceiling = settings.UPLOAD_CHUNK_BYTES
        if length < 0:
            response = HttpResponse(_("Longueur invalide."), status=400)
        elif length > ceiling:
            response = HttpResponse(_("Envoi trop volumineux."), status=413)
        elif (
            request.path.endswith("/upload/")
            and request.method == "POST"
            and not request.META.get("CONTENT_LENGTH")
        ):
            response = HttpResponse(_("Content-Length requis."), status=411)
        else:
            try:
                response = self.get_response(request)
            finally:
                lease = getattr(request, "upload_capacity_lease", None)
                if lease:
                    # Django normally closes uploaded files at response.close;
                    # do so before returning this request's reserved disk budget.
                    try:
                        request.close()
                    finally:
                        lease.close()
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
        )
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["Cache-Control"] = "no-store, private"
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.endswith("/links/batch/") and request.method == "POST":
            if not request.user.is_authenticated:
                return HttpResponse(_("Authentification requise."), status=403)
            from .access import collections_for
            from .capacity import CapacityUnavailable, reserve_temporary
            from .security import limited, rate_allowed

            if (
                not collections_for(request.user, write=True)
                .filter(pk=view_kwargs.get("pk"))
                .exists()
            ):
                return HttpResponse(_("Dossier introuvable."), status=404)
            if not request.META.get("CONTENT_LENGTH"):
                return HttpResponse(_("Content-Length requis."), status=411)
            if limited(request, "batch-upload", 40, 3600) or not rate_allowed(
                "batch-upload-owner", str(request.user.pk), 20, 3600
            ):
                return HttpResponse(
                    _("Trop de demandes. Réessayez plus tard."), status=429
                )
            request.upload_byte_budget = 1024 * 1024
            try:
                request.upload_capacity_lease = reserve_temporary(
                    3 * 1024 * 1024, owner_id=request.user.pk, kind="link-list"
                )
            except CapacityUnavailable as exc:
                return HttpResponse(" ".join(exc.messages), status=503)
        if request.path == "/branding/" and request.method == "POST":
            if not request.user.is_authenticated:
                return HttpResponse(_("Authentification requise."), status=403)
            from .capacity import CapacityUnavailable, reserve_temporary
            from .security import limited

            if limited(request, "branding", 30, 3600):
                return HttpResponse(
                    _("Trop d’envois. Réessayez plus tard."), status=429
                )
            request.upload_byte_budget = 2560 * 1024
            try:
                request.upload_capacity_lease = reserve_temporary(
                    6 * 1024 * 1024, owner_id=request.user.pk, kind="branding"
                )
            except CapacityUnavailable as exc:
                return HttpResponse(" ".join(exc.messages), status=503)
        if request.path.endswith("/upload/") and request.method == "POST":
            # Runs before CsrfViewMiddleware parses multipart data. Authentication
            # middleware has already populated request.user in its request phase.
            if not request.user.is_authenticated:
                return HttpResponse(_("Authentification requise."), status=403)
            from .access import collections_for
            from .capacity import CapacityUnavailable, reserve_temporary
            from .security import limited

            collection = (
                collections_for(request.user, write=True)
                .select_related("owner")
                .filter(pk=view_kwargs.get("pk"))
                .first()
            )
            if collection is None:
                return HttpResponse(_("Dossier introuvable."), status=404)

            if limited(request, "upload", 60, 3600):
                return HttpResponse(
                    _("Trop d’envois. Réessayez plus tard."), status=429
                )
            user = collection.owner
            remaining = max(0, user.quota_bytes - user.used_bytes - user.reserved_bytes)
            length = int(request.META.get("CONTENT_LENGTH") or 0)
            if not user.is_active or remaining == 0 or length > remaining + 1024 * 1024:
                return HttpResponse(_("Quota de stockage insuffisant."), status=413)
            request.upload_byte_budget = min(
                remaining, settings.MAX_MULTIPART_BYTES, length
            )
            try:
                request.upload_capacity_lease = reserve_temporary(
                    2 * length, owner_id=user.pk, kind="multipart"
                )
            except CapacityUnavailable as exc:
                return HttpResponse(" ".join(exc.messages), status=503)
