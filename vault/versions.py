"""Snapshots retain immutable blob rows. Only current path pointers change."""

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from .access import collection_for, locked_collection
from .models import Asset, Audit, CollectionRevision, Event, User


def files_for(link):
    if link.revision_id:
        return Asset.all_objects.filter(
            revisions__pk=link.revision_id,
            revisions__collection_id=link.collection_id,
            collection_id=link.collection_id,
            deleted_at__isnull=True,
        ).order_by("created_at", "pk")
    return link.collection.files.all()


def legacy_links(collection):
    from .storage import available_links

    return available_links(
        collection.links.filter(revision__isnull=True, follow_updates=False)
    )


def snapshot(collection, actor, name=""):
    """Caller holds the collection/owner locks. Snapshots share immutable bytes."""
    assets = list(collection.files.all())
    if not assets:
        raise ValidationError(_("Ce dossier est vide."))
    last = collection.revisions.first()
    if (
        last
        and not name
        and set(
            last.assets.through.objects.filter(
                collectionrevision_id=last.pk
            ).values_list("asset_id", flat=True)
        )
        == {a.pk for a in assets}
    ):
        return last
    if collection.revisions.count() >= 100:
        raise ValidationError(_("La limite de 100 versions par dossier est atteinte."))
    number = (collection.revisions.aggregate(n=Max("number"))["n"] or 0) + 1
    item = CollectionRevision.objects.create(
        collection=collection, number=number, name=name[:80], created_by=actor
    )
    item.assets.add(*assets)
    Audit.objects.create(actor=actor, action="revision_created", target=str(item.pk))
    return item


def content_settings(collection, actor, values):
    follow = values.get("follow_updates", False)
    revision_id = getattr(values.get("revision_id"), "pk", values.get("revision_id"))
    if follow and revision_id:
        raise ValidationError(
            _("Choisissez une version figée ou le suivi des mises à jour.")
        )
    if follow:
        return {"follow_updates": True, "revision": None}
    if revision_id:
        revision = get_object_or_404(
            CollectionRevision, pk=revision_id, collection=collection
        )
        assets = Asset.all_objects.filter(revisions=revision)
        if not assets.exists() or assets.filter(deleted_at__isnull=False).exists():
            raise ValidationError(
                _("Cette version contient des fichiers placés dans la corbeille.")
            )
    else:
        revision = snapshot(collection, actor)
    return {"follow_updates": False, "revision": revision}


def remove_revision(actor, collection_id, revision_id):
    with transaction.atomic():
        collection, _owner = locked_collection(actor, collection_id)
        if collection.owner_id != actor.pk:
            raise Http404
        item = get_object_or_404(
            CollectionRevision, pk=revision_id, collection=collection
        )
        for link in item.links.filter(revoked_at__isnull=True):
            link.revoked_at = timezone.now()
            link.save(update_fields=["revoked_at"])
            Event.objects.create(link=link, kind="revoked")
        item.delete()
        Audit.objects.create(
            actor=actor, action="revision_deleted", target=str(revision_id)
        )


def restore_revision(actor, collection_id, revision_id):
    from .models import UploadSession

    with transaction.atomic():
        collection, _owner = locked_collection(actor, collection_id)
        if collection.owner_id != actor.pk:
            raise Http404
        revision = get_object_or_404(
            CollectionRevision, pk=revision_id, collection=collection
        )
        assets = list(Asset.all_objects.filter(revisions=revision))
        if not assets or any(a.deleted_at for a in assets):
            raise ValidationError(
                _("Restaurez d’abord les fichiers concernés depuis la corbeille.")
            )
        if (
            legacy_links(collection).exists()
            or UploadSession.objects.filter(
                collection=collection, state="pending"
            ).exists()
        ):
            raise ValidationError(
                _(
                    "Figez les anciens liens et terminez les imports avant de restaurer une version."
                )
            )
        if collection.files.exists():
            snapshot(collection, actor)
        Asset.objects.filter(collection=collection).update(superseded_at=timezone.now())
        Asset.all_objects.filter(pk__in=[a.pk for a in assets]).update(
            superseded_at=None
        )
        collection.save(update_fields=["updated_at"])
        Audit.objects.create(
            actor=actor, action="revision_restored", target=str(revision.pk)
        )


def purge_unreferenced_versions():
    cutoff = timezone.now() - timedelta(days=settings.TRASH_RETENTION_DAYS)
    candidates = Asset.all_objects.filter(
        superseded_at__lte=cutoff, revisions__isnull=True
    )
    for asset in candidates.select_related("collection__owner").iterator():
        with transaction.atomic():
            owner = User.objects.select_for_update().get(pk=asset.collection.owner_id)
            current = Asset.all_objects.filter(
                pk=asset.pk,
                superseded_at__lte=cutoff,
                revisions__isnull=True,
                collection__owner=owner,
            ).first()
            if current is None:
                continue
            name, size = current.blob.name, current.size
            current.delete()
            owner.used_bytes = max(0, owner.used_bytes - size)
            owner.save(update_fields=["used_bytes"])
            transaction.on_commit(lambda name=name: default_storage.delete(name))


@login_required
@require_http_methods(["GET", "POST"])
def index(request, pk):
    collection = collection_for(request.user, pk, write=True)
    if request.method == "POST":
        try:
            with transaction.atomic():
                collection, _owner = locked_collection(
                    request.user, pk, request=request
                )
                action = request.POST.get("action")
                if action == "snapshot":
                    snapshot(
                        collection,
                        request.user,
                        request.POST.get("name", "").strip()[:80],
                    )
                elif action == "freeze":
                    revision = snapshot(collection, request.user)
                    legacy_links(collection).update(revision=revision)
                elif action == "delete" and request.POST.get("confirm") == "delete":
                    remove_revision(request.user, pk, request.POST.get("revision"))
                elif action == "restore" and request.POST.get("confirm") == "restore":
                    restore_revision(request.user, pk, request.POST.get("revision"))
                else:
                    raise Http404
            messages.success(request, _("Versions du dossier mises à jour."))
            return redirect("versions", pk=pk)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    revisions = list(collection.revisions.select_related("created_by"))
    for revision in revisions:
        revision.stored_files = list(Asset.all_objects.filter(revisions=revision))
        revision.byte_count = sum(a.size for a in revision.stored_files)
    return render(
        request,
        "collections/versions.html",
        {
            "nav": "collections",
            "collection": collection,
            "revisions": revisions,
            "legacy_links": legacy_links(collection).exists(),
            "is_owner": collection.owner_id == request.user.pk,
        },
    )
