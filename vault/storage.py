from django.utils.translation import gettext_lazy as _
import hashlib
import re
import socket
import struct
import tempfile
import unicodedata
import uuid
import zipfile
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import Asset, Audit, Event, ShareLink, UploadPart, User
from .capacity import LeasedFile, reserve_temporary
from .access import collection_for, locked_collection


def safe_path(value):
    value = unicodedata.normalize("NFC", value)
    parts = value.split("/")
    if not value or len(value) > 500 or len(parts) > 20 or "\\" in value:
        raise ValidationError(_("Chemin de fichier invalide."))
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or part.startswith(".")
            or part.endswith((" ", "."))
            or len(part) > 180
        ):
            raise ValidationError(
                _("Les chemins cachés, vides ou relatifs ne sont pas autorisés.")
            )
        if any(unicodedata.category(c).startswith("C") for c in part) or re.search(
            r'[<>:"|?*]', part
        ):
            raise ValidationError(
                _("Un nom de fichier contient des caractères non autorisés.")
            )
        if part.split(".")[0].upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(10)),
            *(f"LPT{i}" for i in range(10)),
        }:
            raise ValidationError(_("Nom de fichier réservé."))
    extension = PurePosixPath(value).suffix.lower().lstrip(".")
    if extension not in settings.ALLOWED_EXTENSIONS:
        raise ValidationError(
            _("Type de fichier non autorisé : %(extension)s.")
            % {"extension": extension or _("sans extension")}
        )
    return value


def scan(upload):
    if not settings.CLAMAV_HOST:
        if settings.ANTIVIRUS_REQUIRED:
            raise ValidationError(
                _("L’antivirus est indisponible. Aucun fichier n’a été enregistré.")
            )
        return False
    try:
        upload.seek(0)
        with socket.create_connection(
            (settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=20
        ) as client:
            client.settimeout(120)
            client.sendall(b"zINSTREAM\x00")
            for chunk in upload.chunks(65536):
                client.sendall(struct.pack("!I", len(chunk)) + chunk)
            client.sendall(struct.pack("!I", 0))
            result = b""
            while b"\x00" not in result and len(result) < 4096:
                data = client.recv(1024)
                if not data:
                    break
                result += data
        # Fail closed on infected, encrypted/over-limit (ClamAV heuristics),
        # truncated, unexpected or error responses.
        if result != b"stream: OK\x00":
            raise ValidationError(
                _("Fichier refusé par l’antivirus ou analyse incomplète.")
            )
        return True
    except OSError:
        raise ValidationError(
            _("L’antivirus ne répond pas. Réessayez plus tard.")
        ) from None
    finally:
        upload.seek(0)


def available_links(queryset):
    return (
        queryset.filter(revoked_at__isnull=True, collection__deleted_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .filter(Q(max_downloads__isnull=True) | Q(downloads__lt=F("max_downloads")))
    )


def add_files(
    actor,
    collection_id,
    uploads,
    paths,
    *,
    upload_session=None,
    capacity_reserved=False,
    replace_files=False,
):
    initial = collection_for(actor, collection_id, write=True)
    if not uploads or len(uploads) > 100 or len(paths) != len(uploads):
        raise ValidationError(_("Sélectionnez entre 1 et 100 fichiers."))
    normalized = [safe_path(p) for p in paths]
    if len({p.casefold() for p in normalized}) != len(normalized):
        raise ValidationError(_("Deux fichiers portent le même chemin."))
    size = sum(f.size for f in uploads)
    if size > settings.MAX_UPLOAD_BYTES or any(
        f.size > settings.MAX_FILE_BYTES for f in uploads
    ):
        raise ValidationError(_("La taille maximale de l’envoi est dépassée."))
    if not upload_session and not capacity_reserved:
        with reserve_temporary(2 * size, owner_id=initial.owner_id, kind="import"):
            return add_files(
                actor,
                collection_id,
                uploads,
                paths,
                capacity_reserved=True,
                replace_files=replace_files,
            )
    checked = []
    for upload, path in zip(uploads, normalized, strict=True):
        scanned = scan(upload)
        digest = hashlib.sha256()
        for chunk in upload.chunks():
            digest.update(chunk)
        upload.seek(0)
        checked.append((upload, path, scanned, digest.hexdigest()))
    written = []
    try:
        with transaction.atomic():
            collection, user = locked_collection(actor, collection_id)
            if not user.is_active:
                raise ValidationError(_("Compte désactivé."))
            from .versions import legacy_links, snapshot

            if legacy_links(collection).exists():
                raise ValidationError(
                    _(
                        "Révoquez les liens actifs avant de modifier les fichiers du dossier."
                    )
                )
            reservation = 0
            if upload_session:
                from .models import UploadSession

                session = UploadSession.objects.select_for_update().get(
                    pk=upload_session, owner=user, state="pending"
                )
                if (
                    session.expires_at <= timezone.now()
                    or session.collection_id != collection.pk
                    or session.size != size
                ):
                    raise ValidationError(_("Session expirée ou invalide."))
                reservation = session.size
            if (
                user.used_bytes + user.reserved_bytes - reservation + size
                > user.quota_bytes
            ):
                raise ValidationError(_("Votre quota de stockage est atteint."))
            current = list(
                Asset.all_objects.filter(
                    collection=collection, superseded_at__isnull=True
                )
            )
            by_path = {a.path.casefold(): a for a in current}
            replacement_paths = (
                {p.casefold() for p in normalized} & by_path.keys()
                if replace_files
                else set()
            )
            if any(by_path[p].deleted_at for p in replacement_paths):
                raise ValidationError(
                    _(
                        "Restaurez ou supprimez définitivement le fichier de la corbeille avant de le remplacer."
                    )
                )
            existing = set(by_path) - replacement_paths
            if (
                sum(
                    a.size
                    for a in current
                    if a.path.casefold() not in replacement_paths
                )
                + size
                > settings.MAX_UPLOAD_BYTES
            ):
                raise ValidationError(_("La taille maximale du dossier est atteinte."))
            if (
                existing.intersection(p.casefold() for p in normalized)
                or len(existing) + len(uploads) > 100
            ):
                raise ValidationError(
                    _(
                        "Chemin déjà présent ou limite de 100 fichiers par dossier atteinte."
                    )
                )
            if replacement_paths:
                snapshot(collection, actor)
            if (
                not upload_session
                and UploadPart.objects.filter(
                    session__collection=collection, session__state="pending"
                ).exists()
            ):
                raise ValidationError(
                    _(
                        "Terminez ou annulez les envois reprenables avant un import classique."
                    )
                )
            all_paths = existing | {p.casefold() for p in normalized}
            if any(
                "/".join(path.split("/")[:i]) in all_paths
                for path in all_paths
                for i in range(1, len(path.split("/")))
            ):
                raise ValidationError(
                    _("Un chemin ne peut pas être à la fois un fichier et un dossier.")
                )
            for upload, path, scanned, digest in checked:
                if path.casefold() in replacement_paths:
                    Asset.all_objects.filter(pk=by_path[path.casefold()].pk).update(
                        superseded_at=timezone.now()
                    )
                name = default_storage.save(f"{user.pk}/{uuid.uuid4().hex}", upload)
                written.append(name)
                Asset.objects.create(
                    collection=collection,
                    path=path,
                    blob=name,
                    size=upload.size,
                    sha256=digest,
                    scanned=scanned,
                )
            User.objects.filter(pk=user.pk).update(
                used_bytes=F("used_bytes") + size,
                reserved_bytes=F("reserved_bytes") - reservation,
            )
            if upload_session:
                session.state = "complete"
                session.save(update_fields=["state"])
            collection.save(update_fields=["updated_at"])
            Audit.objects.create(
                actor=actor, action="files_uploaded", target=str(collection.pk)
            )
    except Exception:
        for name in written:
            default_storage.delete(name)
        raise


def delete_collection(owner, collection_id):
    from .trash import trash_collection

    return trash_collection(owner, collection_id)


def delete_assets(owner, identifiers):
    from .trash import trash_assets

    return trash_assets(owner, identifiers)


def selected_files(queryset, identifiers=None):
    if identifiers is not None:
        try:
            identifiers = [uuid.UUID(str(value)) for value in identifiers]
        except (ValueError, TypeError, AttributeError):
            raise ValidationError(_("Sélection de fichiers invalide.")) from None
        if not 1 <= len(identifiers) <= 100 or len(set(identifiers)) != len(
            identifiers
        ):
            raise ValidationError(_("Sélectionnez au moins un fichier, sans doublon."))
        queryset = queryset.filter(pk__in=identifiers)
    files = list(queryset)
    if not files or identifiers is not None and len(files) != len(identifiers):
        raise ValidationError(_("Un fichier sélectionné n’est plus disponible."))
    return files


def build_bundle(files, owner_id, *, raw=False):
    if settings.ANTIVIRUS_REQUIRED and any(not f.scanned for f in files):
        raise ValidationError(_("Ce dossier contient des fichiers non analysés."))
    size = (
        sum(f.size + 2 * len(f.path.encode("utf-8")) + 256 for f in files) + 1024 * 1024
    )
    lease = reserve_temporary(size, owner_id=owner_id)
    try:
        bundle = LeasedFile(
            tempfile.TemporaryFile(dir=settings.FILE_UPLOAD_TEMP_DIR), lease
        )
    except BaseException:
        lease.close()
        raise
    try:
        if raw:
            if len(files) != 1:
                raise ValidationError(_("Sélection de fichiers invalide."))
            with files[0].blob.open("rb") as source:
                copy_original(source, bundle, files[0].size)
        else:
            with zipfile.ZipFile(
                bundle, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as archive:
                for asset in files:
                    try:
                        path = asset.blob.path
                    except NotImplementedError:
                        with (
                            asset.blob.open("rb") as source,
                            archive.open(
                                asset.path, "w", force_zip64=True
                            ) as destination,
                        ):
                            copy_original(source, destination, asset.size)
                    else:
                        archive.write(path, arcname=asset.path)
        bundle.seek(0)
        return bundle
    except BaseException:
        bundle.close()
        raise


def copy_original(source, destination, size):
    remaining = size
    while remaining:
        block = source.read(min(1024 * 1024, remaining))
        if not block:
            raise ValidationError(_("Le fichier stocké est incomplet."))
        destination.write(block)
        remaining -= len(block)
    if source.read(1):
        raise ValidationError(_("La taille du fichier stocké a changé."))


def member_bundle(actor, collection_id, identifiers=None, *, raw=False):
    """Authenticated download, independent of recipient links and their counters."""
    collection = collection_for(actor, collection_id)
    files = selected_files(collection.files.all(), identifiers)
    bundle = build_bundle(files, collection.owner_id, raw=raw)
    try:
        with transaction.atomic():
            locked, _owner = locked_collection(actor, collection_id, write=False)
            current = selected_files(locked.files.all(), identifiers)
            if {f.pk for f in current} != {f.pk for f in files}:
                raise ValidationError(
                    _("Le dossier a changé. Relancez le téléchargement.")
                )
            Audit.objects.create(
                actor=actor, action="collection_downloaded", target=str(collection_id)
            )
        return bundle
    except BaseException:
        bundle.close()
        raise


def download_bundle(link, identifiers=None, *, raw=False, authorize=None):
    """Build before claiming; recheck content and access before consuming a use."""
    from .access import lock_workspace
    from .versions import files_for

    files = selected_files(files_for(link), identifiers)
    bundle = build_bundle(files, link.collection.owner_id, raw=raw)
    try:
        with transaction.atomic():
            lock_workspace(link.collection)
            owner = User.objects.select_for_update().get(pk=link.collection.owner_id)
            current = ShareLink.objects.select_related("collection__owner").get(
                pk=link.pk
            )
            if (
                not owner.is_active
                or current.collection.owner_id != owner.pk
                or current.status_code != "active"
            ):
                raise ValidationError(_("Ce lien n’est plus disponible."))
            if authorize is not None and not authorize(current):
                raise ValidationError(_("Ce lien n’est plus disponible."))
            current_files = selected_files(files_for(current), identifiers)
            if {f.pk for f in current_files} != {f.pk for f in files}:
                raise ValidationError(
                    _("Le dossier a changé. Relancez le téléchargement.")
                )
            changed = available_links(ShareLink.objects.filter(pk=link.pk)).update(
                downloads=F("downloads") + 1
            )
            if not changed:
                raise ValidationError(_("Ce lien n’est plus disponible."))
            Event.objects.create(link=current, kind="download_started")
        return bundle
    except BaseException:
        bundle.close()
        raise
