"""Recoverable deletion; quota is released only after permanent purge."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import Asset, Audit, Collection, Event, ShareLink, UploadSession, User


def revoke(collection_ids):
    links = list(
        ShareLink.objects.filter(
            collection_id__in=collection_ids, revoked_at__isnull=True
        )
    )
    ShareLink.objects.filter(pk__in=[link.pk for link in links]).update(
        revoked_at=timezone.now()
    )
    Event.objects.bulk_create([Event(link=link, kind="revoked") for link in links])


def trash_collection(owner, collection_id):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=owner.pk)
        item = Collection.objects.select_for_update().get(pk=collection_id, owner=user)
        from .resumable import cancel_session

        for session in UploadSession.objects.filter(collection=item, state="pending"):
            cancel_session(user, session.pk)
        now = timezone.now()
        revoke([item.pk])
        Asset.objects.filter(collection=item).update(deleted_at=now)
        item.deleted_at = now
        item.save(update_fields=["deleted_at"])
        Audit.objects.create(
            actor=user, action="collection_trashed", target=str(item.pk)
        )


def trash_assets(owner, identifiers):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=owner.pk)
        assets = list(
            Asset.objects.filter(
                pk__in=identifiers,
                collection__owner=user,
                collection__deleted_at__isnull=True,
            )
        )
        if len(assets) != len(set(identifiers)):
            raise ValidationError(
                "Un fichier n’est plus disponible ou ne vous appartient pas."
            )
        collection_ids = {a.collection_id for a in assets}
        revoke(collection_ids)
        now = timezone.now()
        Asset.objects.filter(pk__in=identifiers).update(deleted_at=now)
        Collection.objects.filter(pk__in=collection_ids).update(updated_at=now)
        for identifier in collection_ids:
            Audit.objects.create(
                actor=user, action="files_trashed", target=str(identifier)
            )
        return len(assets), sum(a.size for a in assets)


def restore(owner, kind, identifier):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=owner.pk)
        if kind == "collection":
            item = get_object_or_404(
                Collection.all_objects,
                owner=user,
                pk=identifier,
                deleted_at__isnull=False,
            )
            Asset.all_objects.filter(
                collection=item, deleted_at=item.deleted_at
            ).update(deleted_at=None)
            item.deleted_at = None
            item.save(update_fields=["deleted_at", "updated_at"])
        elif kind == "asset":
            item = get_object_or_404(
                Asset.all_objects,
                pk=identifier,
                collection__owner=user,
                collection__deleted_at__isnull=True,
                deleted_at__isnull=False,
            )
            # Restoring changes a bundle: existing links stay revoked.
            revoke([item.collection_id])
            item.deleted_at = None
            item.save(update_fields=["deleted_at"])
            Collection.objects.filter(pk=item.collection_id).update(
                updated_at=timezone.now()
            )
        else:
            raise Http404
        Audit.objects.create(
            actor=user, action="trash_restored", target=str(identifier)
        )


def purge(owner, kind, identifier):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=owner.pk)
        if kind == "collection":
            item = get_object_or_404(
                Collection.all_objects,
                owner=user,
                pk=identifier,
                deleted_at__isnull=False,
            )
            assets = list(Asset.all_objects.filter(collection=item))
            preview_ids = [asset.pk for asset in assets]
            item.delete()
        elif kind == "asset":
            item = get_object_or_404(
                Asset.all_objects,
                pk=identifier,
                collection__owner=user,
                deleted_at__isnull=False,
            )
            assets = [item]
            preview_ids = [item.pk]
            item.delete()
        else:
            raise Http404
        size = sum(a.size for a in assets)
        user.used_bytes = max(0, user.used_bytes - size)
        user.save(update_fields=["used_bytes"])
        Audit.objects.create(actor=user, action="trash_purged", target=str(identifier))
        transaction.on_commit(
            lambda: [default_storage.delete(a.blob.name) for a in assets]
        )
        from .previews import cleanup_cache

        transaction.on_commit(lambda: cleanup_cache(preview_ids))
        return size


def purge_expired():
    cutoff = timezone.now() - timedelta(days=settings.TRASH_RETENTION_DAYS)
    for collection in Collection.all_objects.filter(deleted_at__lte=cutoff).iterator():
        try:
            purge(collection.owner, "collection", collection.pk)
        except Http404:
            pass  # A concurrent restore or manual purge wins.
    for asset in (
        Asset.all_objects.filter(deleted_at__lte=cutoff)
        .select_related("collection__owner")
        .iterator()
    ):
        try:
            purge(asset.collection.owner, "asset", asset.pk)
        except Http404:
            pass


@login_required
@require_GET
def index(request):
    folders = Collection.all_objects.filter(
        owner=request.user, deleted_at__isnull=False
    ).order_by("-deleted_at")
    files = (
        Asset.all_objects.filter(
            collection__owner=request.user,
            collection__deleted_at__isnull=True,
            deleted_at__isnull=False,
        )
        .select_related("collection")
        .order_by("-deleted_at")
    )
    total = (
        Asset.all_objects.filter(
            collection__owner=request.user, deleted_at__isnull=False
        ).aggregate(n=Sum("size"))["n"]
        or 0
    )
    return render(
        request,
        "trash.html",
        {
            "nav": "trash",
            "folders": Paginator(folders, 30).get_page(request.GET.get("folders")),
            "files": Paginator(files, 30).get_page(request.GET.get("files")),
            "trash_bytes": total,
            "retention": settings.TRASH_RETENTION_DAYS,
        },
    )


@login_required
@require_POST
def action(request, kind, pk):
    if request.POST.get("action") == "restore":
        restore(request.user, kind, pk)
        messages.success(
            request, "Élément restauré. Créez de nouveaux liens pour le partager."
        )
    elif request.POST.get("action") == "purge":
        if request.POST.get("confirm") != "purge":
            model = (
                Collection.all_objects
                if kind == "collection"
                else Asset.all_objects
                if kind == "asset"
                else None
            )
            if model is None:
                raise Http404
            filters = (
                {"owner": request.user}
                if kind == "collection"
                else {"collection__owner": request.user}
            )
            item = get_object_or_404(model, pk=pk, deleted_at__isnull=False, **filters)
            return render(
                request,
                "trash_confirm.html",
                {"nav": "trash", "item": item, "kind": kind},
            )
        purge(request.user, kind, pk)
        messages.success(
            request, "Élément supprimé définitivement. Le quota a été libéré."
        )
    else:
        raise Http404
    return redirect("trash")
