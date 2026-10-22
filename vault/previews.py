"""Authorized JPEG derivatives. Originals never leave through this endpoint."""

from django.utils.translation import gettext_lazy as _
import fcntl
import json
import os
import socket
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST

from scripts.preview_runtime import MAX_OUTPUT, MAX_SOURCE, render

from .capacity import CapacityUnavailable, reserve_temporary
from .models import Asset, Audit, ShareLink, User
from .security import limited, rate_allowed, verify_capability

CACHE_BYTES = 256 * 1024**2
CACHE_ENTRIES = 256
CACHE_AGE = 24 * 3600


class PreviewBusy(Exception):
    pass


class PreviewUnavailable(Exception):
    pass


def preview_kind(asset):
    if not settings.PREVIEWS_ENABLED or not 0 < asset.size <= MAX_SOURCE:
        return ""
    if settings.ANTIVIRUS_REQUIRED and not asset.scanned:
        return ""
    extension = PurePosixPath(asset.path).suffix.lower()
    if extension in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    return "pdf" if extension == ".pdf" else ""


def cache_directory():
    directory = Path(settings.FILE_UPLOAD_TEMP_DIR) / "previews"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextmanager
def renderer_lock(*, blocking=False):
    descriptor = os.open(
        cache_directory() / ".render.lock", os.O_RDWR | os.O_CREAT, 0o600
    )
    with os.fdopen(descriptor, "r+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise PreviewBusy from None
        yield


def cache_path(asset):
    # A renderer version prefix prevents reuse after rendering policy changes.
    return cache_directory() / f"v1-{asset.pk.hex}-{asset.sha256}"


def read_cached(asset):
    base = cache_path(asset)
    for extension, age in ((".jpg", CACHE_AGE), (".none", 3600)):
        path = base.with_suffix(extension)
        try:
            with path.open("rb") as source:
                stat = os.fstat(source.fileno())
                if stat.st_mtime < time.time() - age or stat.st_size > MAX_OUTPUT:
                    continue
                if extension == ".none":
                    raise PreviewUnavailable
                data = source.read(MAX_OUTPUT + 1)
                if data.startswith(b"\xff\xd8\xff") and len(data) <= MAX_OUTPUT:
                    return data
        except FileNotFoundError:
            continue
    return None


def prune_cache(*, room=0, removed_ids=()):
    """Caller owns renderer_lock. Cache is bounded and entirely regenerable."""
    removed_ids = {str(pk).replace("-", "") for pk in removed_ids}
    entries = []
    for path in cache_directory().iterdir():
        if path.suffix not in {".jpg", ".none", ".part"} or path.is_symlink():
            continue
        stat = path.stat()
        parts = path.name.split("-", 2)
        maximum_age = 3600 if path.suffix == ".none" else CACHE_AGE
        if (
            stat.st_mtime < time.time() - maximum_age
            or path.suffix == ".part"
            or (len(parts) == 3 and parts[1] in removed_ids)
        ):
            path.unlink(missing_ok=True)
        else:
            entries.append((stat.st_mtime, path, stat.st_size))
    entries.sort()
    size = sum(item[2] for item in entries)
    while entries and (size + room > CACHE_BYTES or len(entries) >= CACHE_ENTRIES):
        _, path, length = entries.pop(0)
        path.unlink(missing_ok=True)
        size -= length


def cleanup_cache(removed_ids=()):
    try:
        with renderer_lock(blocking=bool(removed_ids)):
            prune_cache(removed_ids=removed_ids)
    except PreviewBusy:
        pass


def socket_render(asset, kind):
    with asset.blob.open("rb") as source, socket.socket(socket.AF_UNIX) as client:
        client.settimeout(12)
        client.connect(settings.PREVIEW_SOCKET)
        with client.makefile("rwb") as stream:
            stream.write(
                json.dumps({"size": asset.size, "kind": kind}).encode() + b"\n"
            )
            remaining = asset.size
            while remaining:
                block = source.read(min(65536, remaining))
                if not block:
                    raise PreviewUnavailable
                stream.write(block)
                remaining -= len(block)
            stream.flush()
            header = stream.readline(129)
            if len(header) > 128 or not header.endswith(b"\n"):
                raise PreviewUnavailable
            response = json.loads(header)
            if not isinstance(response, dict):
                raise PreviewUnavailable
            size = response.get("size")
            if type(size) is not int or not 0 <= size <= MAX_OUTPUT:
                raise PreviewUnavailable
            if size == 0:
                return None
            data = stream.read(size)
            if len(data) != size or not data.startswith(b"\xff\xd8\xff"):
                raise PreviewUnavailable
            return data


def derivative(asset):
    kind = preview_kind(asset)
    if not kind:
        raise PreviewUnavailable
    data = read_cached(asset)
    if data is not None:
        return data
    with renderer_lock():
        data = read_cached(asset)
        if data is not None:
            return data
        if not rate_allowed(
            "preview-render-owner", str(asset.collection.owner_id), 120, 300
        ):
            raise PreviewBusy
        prune_cache(room=MAX_OUTPUT)
        with reserve_temporary(
            MAX_OUTPUT * 2, owner_id=asset.collection.owner_id, kind="preview"
        ):
            if settings.PREVIEW_SOCKET:
                data = socket_render(asset, kind)
            elif settings.DEBUG:
                with tempfile.TemporaryDirectory(
                    dir=settings.FILE_UPLOAD_TEMP_DIR
                ) as directory:
                    data = render(asset.blob.path, kind, directory)
            else:
                # No in-process/native decoding fallback on a production server.
                raise PreviewUnavailable
            target = cache_path(asset).with_suffix(".jpg" if data else ".none")
            partial = target.with_suffix(".part")
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data or b"")
            os.replace(partial, target)
            if data is None:
                raise PreviewUnavailable
            return data


def authorized_asset(request, token, asset_id):
    from .views import can_open

    link = get_object_or_404(
        ShareLink.objects.select_related("collection__owner"),
        pk=verify_capability("share", token),
    )
    if (
        not link.allow_previews
        or link.status_code != "active"
        or not link.collection.owner.is_active
    ):
        raise Http404
    if not can_open(request, link):
        raise Http404
    asset = get_object_or_404(Asset, pk=asset_id, collection=link.collection)
    if not preview_kind(asset):
        raise Http404
    return asset


@require_GET
def image(request, token, asset_id):
    if limited(request, "preview", 360, 60):
        return HttpResponse(status=429, headers={"Retry-After": "60"})
    asset = authorized_asset(request, token, asset_id)
    try:
        data = derivative(asset)
    except (PreviewBusy, CapacityUnavailable):
        return HttpResponse(status=503, headers={"Retry-After": "2"})
    except (PreviewUnavailable, OSError, ValueError):
        return HttpResponse(status=422)
    # Rendering may take seconds. Re-check all permissions before releasing bytes.
    authorized_asset(request, token, asset_id)
    return HttpResponse(
        data,
        content_type="image/jpeg",
        headers={
            "Content-Disposition": 'inline; filename="apercu.jpg"',
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )


@login_required
@require_POST
def toggle(request, pk):
    value = request.POST.get("enabled")
    if value not in {"0", "1"}:
        return HttpResponse(status=400)
    with transaction.atomic():
        owner = get_object_or_404(
            User.objects.select_for_update(), pk=request.user.pk, is_active=True
        )
        link = get_object_or_404(ShareLink, pk=pk, collection__owner=owner)
        ShareLink.objects.filter(pk=link.pk).update(allow_previews=value == "1")
        Audit.objects.create(
            actor=owner, action="link_previews_changed", target=str(pk)
        )
    messages.success(request, _("Les aperçus de ce lien ont été mis à jour."))
    return redirect("links")
