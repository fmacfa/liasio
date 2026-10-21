import logging
import re

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect


class RedactURLs(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(r"/(s|invite|reset)/[^\s?\"']+", r"/\1/[redacted]/", message)
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
            length = 0
        ceiling = settings.MAX_UPLOAD_BYTES + 1024 * 1024 if request.path.endswith("/upload/") else 1024 * 1024
        if length > ceiling:
            response = HttpResponse("Envoi trop volumineux.", status=413)
        elif request.path.endswith("/upload/") and request.method == "POST" and not request.META.get("CONTENT_LENGTH"):
            response = HttpResponse("Content-Length requis.", status=411)
        else:
            response = self.get_response(request)
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["Cache-Control"] = "no-store, private"
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.endswith("/upload/") and request.method == "POST":
            # Runs before CsrfViewMiddleware parses multipart data. Authentication
            # middleware has already populated request.user in its request phase.
            if not request.user.is_authenticated:
                return HttpResponse("Authentification requise.", status=403)
            from .models import Collection
            if not Collection.objects.filter(pk=view_kwargs.get("pk"), owner=request.user).exists():
                return HttpResponse("Dossier introuvable.", status=404)
