"""Versioned automation API. File ingestion delegates to the browser's pipeline."""

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from . import resumable
from .access import collections_for, collection_for, locked_collection
from .api_auth import (
    api,
    authorize_current,
    json_body,
    prepare_receipt,
    replay,
    remember,
)
from .forms import CollectionForm, LinkForm
from .models import Audit, Collection, Event, ShareLink, User


def text_fields(data, fields):
    if any(
        data.get(name) is not None and not isinstance(data[name], str)
        for name in fields
    ):
        raise ValueError("Expected text fields")


def collection_data(item):
    return {
        "id": str(item.pk),
        "title": item.title,
        "description": item.description,
        "recipient_note": item.recipient_note,
        "created_at": item.created_at.isoformat(),
        "files": [
            {
                "id": str(f.pk),
                "path": f.path,
                "size": f.size,
                "sha256": f.sha256,
                "scanned": f.scanned,
            }
            for f in item.files.all()
        ],
    }


def link_data(item):
    return {
        "id": str(item.pk),
        "collection_id": str(item.collection_id),
        "label": item.label,
        "url": item.public_url,
        "status": item.status_code,
        "password_protected": bool(item.password_hash),
        "allow_previews": item.allow_previews,
        "max_downloads": item.max_downloads,
        "downloads": item.downloads,
        "expires_at": item.expires_at.isoformat() if item.expires_at else None,
    }


def page(request, queryset, serialize):
    offset = int(request.GET.get("offset", "0"))
    if offset < 0 or offset > 100000:
        raise ValueError
    rows = list(queryset[offset : offset + 51])
    return JsonResponse(
        {
            "results": [serialize(r) for r in rows[:50]],
            "next_offset": offset + 50 if len(rows) > 50 else None,
        }
    )


@api("read")
def collections(request):
    return page(
        request,
        collections_for(request.user)
        .prefetch_related("files")
        .order_by("-created_at", "pk"),
        collection_data,
    )


@api("read")
def collection(request, pk):
    return JsonResponse(collection_data(collection_for(request.user, pk)))


@api("files:write", ("POST",))
def create_collection(request):
    data = json_body(request)
    prepare_receipt(request)
    text_fields(data, ["title", "description", "recipient_note"])
    form = CollectionForm(data)
    if not form.is_valid():
        return JsonResponse(
            {"error": {"code": "validation", "fields": form.errors.get_json_data()}},
            status=400,
        )
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        authorize_current(request, user)
        previous = replay(request)
        if previous is not None:
            return previous
        item = form.save(commit=False)
        item.owner = user
        item.save()
        Audit.objects.create(
            actor=user, action="collection_created", target=str(item.pk)
        )
        return remember(request, JsonResponse(collection_data(item), status=201))


def link_form_values(data):
    """Strict JSON types and timezone parsing shared by single and bulk creation."""
    data = dict(data)
    text_fields(data, ["label", "password", "expires_at", "expiry_mode"])
    if (
        data.get("expires_in_days") is not None
        and type(data["expires_in_days"]) is not int
    ):
        raise ValidationError(_("expires_in_days doit être un entier."))
    if data.get("expires_at") and data.get("expires_in_days") is not None:
        raise ValidationError(_("Choisissez une date ou une durée, pas les deux."))
    if "allow_previews" in data and type(data["allow_previews"]) is not bool:
        raise ValidationError(_("allow_previews doit être un booléen."))
    if data.get("max_downloads") is not None and type(data["max_downloads"]) is not int:
        raise ValidationError(_("max_downloads doit être un entier."))
    if data.get("expires_at"):
        value = parse_datetime(data["expires_at"])
        if value is None or timezone.is_naive(value):
            raise ValidationError(
                _("expires_at doit inclure un fuseau horaire (ISO 8601).")
            )
        data["expires_at"] = value
    return data


@api("links:write", ("POST",))
def create_link(request, pk):
    data = json_body(request)
    prepare_receipt(request)
    form = LinkForm(link_form_values(data))
    with transaction.atomic():
        item, owner = locked_collection(request.user, pk, request=request)
        previous = replay(request)
        if previous is not None:
            return previous
        # A retry can arrive after the original link's expiry. Replay its receipt
        # before applying the time-dependent validation for a new creation.
        if not form.is_valid():
            return JsonResponse(
                {
                    "error": {
                        "code": "validation",
                        "fields": form.errors.get_json_data(),
                    }
                },
                status=400,
            )
        if not item.files.exists():
            raise ValidationError(
                _("Ajoutez au moins un fichier avant de créer un lien.")
            )
        if settings.ANTIVIRUS_REQUIRED and item.files.filter(scanned=False).exists():
            raise ValidationError(
                _("Des fichiers n’ont pas été analysés par l’antivirus.")
            )
        link = form.save(commit=False)
        link.collection = item
        if form.cleaned_data["password"]:
            link.password_hash = make_password(form.cleaned_data["password"])
        link.save()
        Event.objects.create(link=link, kind="created")
        Audit.objects.create(
            actor=request.user, action="link_created", target=str(link.pk)
        )
        return remember(request, JsonResponse(link_data(link), status=201))


@api("read")
def links(request):
    return page(
        request,
        ShareLink.objects.filter(
            collection__in=collections_for(request.user, write=True)
        )
        .select_related("collection")
        .order_by("-created_at", "pk"),
        link_data,
    )


@api("read")
def link(request, pk):
    return JsonResponse(
        link_data(
            get_object_or_404(
                ShareLink.objects.select_related("collection"),
                collection__in=collections_for(request.user, write=True),
                pk=pk,
            )
        )
    )


@api("links:write", ("POST",))
def revoke_link(request, pk):
    link = get_object_or_404(
        ShareLink, collection__in=collections_for(request.user, write=True), pk=pk
    )
    with transaction.atomic():
        locked_collection(request.user, link.collection_id, request=request)
        link.refresh_from_db()
        if not link.revoked_at:
            link.revoked_at = timezone.now()
            link.save(update_fields=["revoked_at"])
            Event.objects.create(link=link, kind="revoked")
            Audit.objects.create(
                actor=request.user, action="link_revoked", target=str(pk)
            )
    return JsonResponse(link_data(link))


@api("files:write", ("POST",))
def upload_start(request, pk):
    json_body(request)
    prepare_receipt(request)
    return resumable.start(request, pk)


@api("read")
def upload_status(request, pk):
    return resumable.status(request, pk)


@api("read")
def uploads_pending(request, pk):
    return resumable.pending(request, pk)


@api("files:write", ("POST",))
def upload_chunk(request, pk, part_id):
    return resumable.chunk(request, pk, part_id)


@api("files:write", ("POST",))
def upload_finish(request, pk):
    return resumable.finish(request, pk)


@api("files:write", ("POST",))
def upload_cancel(request, pk):
    # cancel_session reacquires this same lock, as does key revocation.
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        authorize_current(request, user)
        return resumable.cancel(request, pk)
