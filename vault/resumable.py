"""Authenticated, checksummed upload blocks with durable offsets and quotas."""

from django.utils.translation import gettext_lazy as _
import hashlib
import hmac
import json
import os
import re
import shutil
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files import File
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .api_auth import authorize_current, replay, remember
from .models import Asset, Audit, Collection, UploadPart, UploadSession, User
from .capacity import capacity_lock, check_capacity
from .security import limited
from .storage import add_files, available_links, safe_path


def json_errors(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return JsonResponse(
                {
                    "error": " ".join(exc.messages)
                    if isinstance(exc, ValidationError)
                    else _("Envoi invalide.")
                },
                status=400,
            )
        except OSError:
            return JsonResponse(
                {
                    "error": _(
                        "Stockage indisponible. Réessayez ou contactez l’administrateur."
                    )
                },
                status=503,
            )

    return wrapped


def part_path(part):
    return settings.UPLOAD_STAGING_ROOT / str(part.session_id) / str(part.pk)


def describe(session):
    return {
        "id": str(session.pk),
        "state": session.state,
        "size": session.size,
        "expires_at": session.expires_at.isoformat(),
        "chunk_bytes": settings.UPLOAD_CHUNK_BYTES,
        "parts": [
            {
                "id": str(p.pk),
                "path": p.path,
                "size": p.size,
                "offset": p.offset,
                "fingerprint": p.fingerprint,
            }
            for p in session.parts.all()
        ],
    }


def ensure_pending(session):
    if (
        session.state != "pending"
        or session.expires_at <= timezone.now()
        or session.collection.deleted_at
    ):
        raise ValidationError(_("Cet envoi est terminé, annulé ou expiré."))


def capacity_needed(size):
    check_capacity(2 * size)


@login_required
@require_POST
@json_errors
def start(request, pk):
    if limited(request, "upload-start", 30, 3600):
        return JsonResponse(
            {"error": _("Trop d’envois. Réessayez plus tard.")}, status=429
        )
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ValidationError(_("Envoi invalide."))
    manifest = data.get("files")
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 100:
        raise ValidationError(_("Sélectionnez entre 1 et 100 fichiers."))
    normalized = []
    for item in manifest:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValidationError(_("Liste des fichiers invalide."))
        size = item.get("size")
        fingerprint = item.get("fingerprint")
        if type(size) is not int or size < 0 or size > settings.MAX_FILE_BYTES:
            raise ValidationError(_("Taille de fichier invalide ou trop élevée."))
        if not isinstance(fingerprint, str) or not re.fullmatch(
            r"[a-f0-9]{64}", fingerprint
        ):
            raise ValidationError(_("Empreinte invalide."))
        normalized.append((safe_path(item["path"]), size, fingerprint))
    total = sum(item[1] for item in normalized)
    if total > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(_("La taille maximale du dossier est dépassée."))
    with capacity_lock(), transaction.atomic():
        # Serializes global disk reservations; never lock Instance after User.
        from .models import Instance

        Instance.objects.select_for_update().get(pk=1)
        user = User.objects.select_for_update().get(pk=request.user.pk)
        authorize_current(request, user)
        previous = replay(request)
        if previous is not None:
            return previous
        collection = get_object_or_404(Collection, pk=pk, owner=user)
        if not user.is_active or available_links(collection.links.all()).exists():
            raise ValidationError(
                _("Révoquez les liens actifs avant d’importer des fichiers.")
            )
        if UploadSession.objects.filter(owner=user, state="pending").count() >= 5:
            raise ValidationError(
                _(
                    "Terminez ou annulez un envoi avant d’en commencer un autre (5 maximum)."
                )
            )
        if user.used_bytes + user.reserved_bytes + total > user.quota_bytes:
            raise ValidationError(
                _("Quota insuffisant, y compris la corbeille et les envois en attente.")
            )
        existing = list(
            Asset.all_objects.filter(collection=collection).values_list("path", "size")
        )
        pending = list(
            UploadPart.objects.filter(
                session__collection=collection, session__state="pending"
            ).values_list("path", "size")
        )
        all_paths = [p.casefold() for p, _ in existing + pending] + [
            p.casefold() for p, _, _ in normalized
        ]
        if len(set(all_paths)) != len(all_paths) or len(all_paths) > 100:
            raise ValidationError(
                _(
                    "Chemin déjà présent (corbeille comprise) ou limite de 100 fichiers atteinte."
                )
            )
        if sum(s for _, s in existing + pending) + total > settings.MAX_UPLOAD_BYTES:
            raise ValidationError(_("La taille maximale du dossier est atteinte."))
        path_set = set(all_paths)
        if any(
            "/".join(p.split("/")[:i]) in path_set
            for p in all_paths
            for i in range(1, len(p.split("/")))
        ):
            raise ValidationError(
                _("Un chemin ne peut pas être à la fois un fichier et un dossier.")
            )
        capacity_needed(total)
        session = UploadSession.objects.create(
            owner=user, collection=collection, size=total
        )
        UploadPart.objects.bulk_create(
            [
                UploadPart(session=session, path=p, size=s, fingerprint=f, position=i)
                for i, (p, s, f) in enumerate(normalized)
            ]
        )
        user.reserved_bytes += total
        user.save(update_fields=["reserved_bytes"])
        Audit.objects.create(
            actor=user, action="upload_started", target=str(session.pk)
        )
        return remember(request, JsonResponse(describe(session), status=201))


@login_required
@require_GET
@json_errors
def status(request, pk):
    session = get_object_or_404(
        UploadSession.objects.select_related("collection"), pk=pk, owner=request.user
    )
    return JsonResponse(describe(session))


@login_required
@require_GET
def pending(request, pk):
    collection = get_object_or_404(Collection, pk=pk, owner=request.user)
    return JsonResponse(
        {
            "sessions": [
                describe(s)
                for s in UploadSession.objects.filter(
                    collection=collection,
                    owner=request.user,
                    state="pending",
                    expires_at__gt=timezone.now(),
                ).order_by("created_at")
            ]
        }
    )


@login_required
@require_POST
@json_errors
def chunk(request, pk, part_id):
    # Check ownership before reading any uploaded bytes.
    get_object_or_404(
        UploadPart, pk=part_id, session_id=pk, session__owner=request.user
    )
    length = int(request.META.get("CONTENT_LENGTH") or -1)
    offset = int(request.headers.get("Upload-Offset", "-1"))
    checksum = request.headers.get("Upload-Checksum", "")
    if (
        length < 1
        or length > settings.UPLOAD_CHUNK_BYTES
        or not re.fullmatch(r"[a-f0-9]{64}", checksum)
    ):
        raise ValidationError(_("Bloc invalide."))
    data = request.body
    if len(data) != length or not hmac.compare_digest(
        hashlib.sha256(data).hexdigest(), checksum
    ):
        raise ValidationError(
            _("L’intégrité du bloc n’a pas pu être vérifiée. Réessayez.")
        )
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        authorize_current(request, user)
        if not user.is_active:
            raise Http404
        session = get_object_or_404(
            UploadSession.objects.select_related("collection"), pk=pk, owner=user
        )
        ensure_pending(session)
        part = get_object_or_404(UploadPart, pk=part_id, session=session)
        if offset != part.offset:
            return JsonResponse(
                {
                    "error": _("Position modifiée. Reprenez à la position indiquée."),
                    "offset": part.offset,
                },
                status=409,
            )
        if part.offset + length > part.size:
            raise ValidationError(_("Le bloc dépasse la taille déclarée."))
        if (
            shutil.disk_usage(settings.DATA_DIR).free
            < settings.MIN_FREE_DISK_BYTES + length
        ):
            raise ValidationError(_("Le serveur manque d’espace disque."))
        path = part_path(part)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(fd, "r+b") as output:
            if os.fstat(output.fileno()).st_size < part.offset:
                # An interrupted restore or external disk loss: restart this part.
                part.offset = 0
                part.save(update_fields=["offset"])
                return JsonResponse(
                    {"error": _("Bloc manquant sur le serveur."), "offset": 0},
                    status=409,
                )
            output.truncate(part.offset)
            output.seek(part.offset)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        part.offset += length
        part.save(update_fields=["offset"])
    return JsonResponse({"offset": part.offset})


def cancel_session(owner, pk):
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=owner.pk)
        session = get_object_or_404(UploadSession, pk=pk, owner=user)
        if session.state == "pending":
            user.reserved_bytes = max(0, user.reserved_bytes - session.size)
            user.save(update_fields=["reserved_bytes"])
            session.state = "canceled"
            session.save(update_fields=["state"])
            Audit.objects.create(actor=user, action="upload_canceled", target=str(pk))
        transaction.on_commit(
            lambda: shutil.rmtree(
                settings.UPLOAD_STAGING_ROOT / str(pk), ignore_errors=True
            )
        )


@login_required
@require_POST
@json_errors
def cancel(request, pk):
    cancel_session(request.user, pk)
    return JsonResponse({"state": "canceled"})


@login_required
@require_POST
@json_errors
def finish(request, pk):
    if limited(request, "upload-finish", 30, 3600):
        return JsonResponse(
            {"error": _("Trop de vérifications. Réessayez plus tard.")}, status=429
        )
    # The user lock serializes finalization, chunk writes, deletes and quota changes.
    # A crashed worker rolls back DB state; its staged blocks remain resumable.
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        authorize_current(request, user)
        session = get_object_or_404(
            UploadSession.objects.select_related("collection"), pk=pk, owner=user
        )
        if session.state == "complete":
            return JsonResponse(
                {
                    "state": "complete",
                    "redirect": f"/collections/{session.collection_id}/",
                }
            )
        ensure_pending(session)
        files = []
        try:
            parts = list(session.parts.all())
            for part in parts:
                if part.offset != part.size:
                    raise ValidationError(_("L’envoi n’est pas encore complet."))
                path = part_path(part)
                if part.size == 0:
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    path.touch(mode=0o600, exist_ok=True)
                if not path.exists() or path.stat().st_size != part.size:
                    raise ValidationError(
                        _(
                            "Fichier temporaire incomplet. Annulez puis recommencez l’envoi."
                        )
                    )
                files.append(File(path.open("rb"), name=part.path))
            add_files(
                user,
                session.collection_id,
                files,
                [p.path for p in parts],
                upload_session=session.pk,
            )
            transaction.on_commit(
                lambda: shutil.rmtree(
                    settings.UPLOAD_STAGING_ROOT / str(pk), ignore_errors=True
                )
            )
        finally:
            for file in files:
                file.close()
    return JsonResponse(
        {"state": "complete", "redirect": f"/collections/{session.collection_id}/"}
    )


def cleanup_expired():
    for session in (
        UploadSession.objects.filter(expires_at__lte=timezone.now())
        .select_related("owner")
        .iterator()
    ):
        cancel_session(session.owner, session.pk)
        session.delete()
    # Failed starts and erased accounts may leave directories behind.
    known = {str(pk) for pk in UploadSession.objects.values_list("pk", flat=True)}
    for path in settings.UPLOAD_STAGING_ROOT.iterdir():
        if path.is_dir() and not path.is_symlink() and path.name not in known:
            if path.stat().st_mtime < timezone.now().timestamp() - 86400:
                shutil.rmtree(path)
