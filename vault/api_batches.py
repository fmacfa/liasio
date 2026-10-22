"""Atomic, idempotent batches shared with the browser's My batches workspace."""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from . import batches
from .api import link_data, link_form_values, page
from .api_auth import (
    api,
    authorize_current,
    error,
    json_body,
    prepare_receipt,
    remember,
    replay,
)
from .link_lists import MAX_LINKS
from .models import Collection, User

CONDITION_FIELDS = {
    "expires_at",
    "expires_in_days",
    "max_downloads",
    "password",
    "allow_previews",
}


def address(route, *args):
    return settings.APP_URL.rstrip("/") + reverse(route, args=args)


def batch_data(batch, *, details=False):
    state = (
        "complete"
        if batch.completed_at
        else "expired"
        if batch.created_at <= timezone.now() - timedelta(hours=24)
        else "draft"
    )
    data = {
        "id": str(batch.pk),
        "collection_id": str(batch.collection_id),
        "name": batch.name,
        "state": state,
        "count": len(batch.source_rows),
        "created_at": batch.created_at.isoformat(),
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
        "view_url": address("link_batch", batch.pk),
        "api_url": address("api_link_batch", batch.pk),
        "exports": {
            format: address("api_link_batch_export", batch.pk, format)
            for format in ("xlsx", "csv")
        }
        if batch.completed_at
        else None,
    }
    if details:
        data.update(
            headers=batch.headers,
            source_rows=batch.source_rows,
            items=[
                {"position": item.position + 1, **link_data(item.link)}
                for item in batch.items.select_related("link__collection")
            ],
        )
    return data


def inputs(data):
    if set(data) - {"name", "names", "count", "conditions"}:
        raise ValidationError(_("Champs de lot inconnus."))
    if not isinstance(data.get("name"), str):
        raise ValidationError(_("Indiquez un nom de lot."))
    name = forms.CharField(max_length=80, validators=[batches.SINGLE_LINE]).clean(
        data["name"]
    )
    if ("names" in data) == ("count" in data):
        raise ValidationError(_("Fournissez names ou count, mais pas les deux."))
    if "names" in data:
        labels = data["names"]
        if (
            not isinstance(labels, list)
            or not 1 <= len(labels) <= MAX_LINKS
            or any(not isinstance(label, str) for label in labels)
        ):
            raise ValidationError(_("names doit contenir de 1 à 200 noms de liens."))
    else:
        count = data["count"]
        if type(count) is not int or not 1 <= count <= MAX_LINKS:
            raise ValidationError(_("count doit être un entier de 1 à 200."))
        labels = [f"Link {number:03d}" for number in range(1, count + 1)]
    form = batches.NamesForm(
        labels, {f"label_{i}": label for i, label in enumerate(labels)}
    )
    if not form.is_valid():
        raise ValidationError(
            _("Chaque nom doit contenir de 1 à 80 caractères sur une seule ligne.")
        )
    labels = [form.cleaned_data[f"label_{i}"] for i in range(len(labels))]
    conditions = data.get("conditions", {})
    if not isinstance(conditions, dict) or set(conditions) - CONDITION_FIELDS:
        raise ValidationError(_("Conditions de lot invalides ou champs inconnus."))
    return name, labels, link_form_values(conditions)


def conditions_form(values):
    form = batches.ConditionsForm(values)
    if not form.is_valid():
        raise ValidationError(
            [message for errors in form.errors.values() for message in errors]
        )
    return form


def locked_owner(request):
    owner = get_object_or_404(
        User.objects.select_for_update(), pk=request.user.pk, is_active=True
    )
    authorize_current(request, owner)
    return owner


@api("links:write", ("POST",))
def create(request, pk):
    try:
        data = json_body(request)
    except RecursionError:
        return error("invalid_request", "JSON nesting is too deep.", 400)
    prepare_receipt(request)
    # Replays precede time-dependent validation, quotas and expensive hashing.
    # Do not keep an owner lock during password hashing: it also guards uploads.
    with transaction.atomic():
        owner = locked_owner(request)
        previous = replay(request)
        if previous is not None:
            return previous
        get_object_or_404(Collection, pk=pk, owner=owner)
        batches.check_batch_capacity(owner)
        if batches.rate_limited(request, "batch-prepare", 10):
            return error(
                "rate_limited", "Too many batch requests. Try again later.", 429
            )
    name, labels, values = inputs(data)
    conditions = conditions_form(values)
    password = conditions.cleaned_data["password"]
    password_hash = make_password(password) if password else ""
    with transaction.atomic():
        owner = locked_owner(request)
        previous = replay(request)
        if previous is not None:
            return previous
        collection = get_object_or_404(
            Collection.objects.select_for_update(), pk=pk, owner=owner
        )
        # Resolve durations at creation and reject a date that expired while waiting.
        conditions = conditions_form(values)
        batch = batches.new_batch(
            owner, collection, name, ["Name"], [[label] for label in labels]
        )
        batches.complete_batch(
            batch, owner, labels, conditions.cleaned_data, password_hash
        )
        return remember(
            request, JsonResponse(batch_data(batch, details=True), status=201)
        )


@api("read")
def index(request):
    queryset = batches.owner_batches(request.user).order_by("-created_at", "-pk")
    if "collection_id" in request.GET:
        collection = get_object_or_404(
            Collection, pk=request.GET["collection_id"], owner=request.user
        )
        queryset = queryset.filter(collection=collection)
    return page(request, queryset, batch_data)


@api("read")
def detail(request, pk):
    return JsonResponse(batch_data(batches.owned_batch(request, pk), details=True))


@api("read")
def export(request, pk, format):
    if format not in ("xlsx", "csv"):
        raise Http404
    batch = batches.owned_batch(request, pk)
    if not batch.completed_at:
        raise Http404
    if batches.rate_limited(request, "batch-export", 60):
        return error("rate_limited", "Too many exports. Try again later.", 429)
    return batches.export_response(batch, format)
