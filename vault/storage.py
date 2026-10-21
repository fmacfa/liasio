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

from .models import Asset, Audit, Collection, Event, ShareLink, UploadPart, User
from .capacity import LeasedFile, reserve_temporary


def safe_path(value):
    value = unicodedata.normalize("NFC", value)
    parts = value.split("/")
    if not value or len(value) > 500 or len(parts) > 20 or "\\" in value:
        raise ValidationError("Chemin de fichier invalide.")
    for part in parts:
        if (
            not part
            or part in {".", ".."}
            or part.startswith(".")
            or part.endswith((" ", "."))
            or len(part) > 180
        ):
            raise ValidationError(
                "Les chemins cachés, vides ou relatifs ne sont pas autorisés."
            )
        if any(unicodedata.category(c).startswith("C") for c in part) or re.search(
            r'[<>:"|?*]', part
        ):
            raise ValidationError(
                "Un nom de fichier contient des caractères non autorisés."
            )
        if part.split(".")[0].upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(10)),
            *(f"LPT{i}" for i in range(10)),
        }:
            raise ValidationError("Nom de fichier réservé.")
    extension = PurePosixPath(value).suffix.lower().lstrip(".")
    if extension not in settings.ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Type de fichier non autorisé : {extension or 'sans extension'}."
        )
    return value


def scan(upload):
    if not settings.CLAMAV_HOST:
        if settings.ANTIVIRUS_REQUIRED:
            raise ValidationError(
                "L’antivirus est indisponible. Aucun fichier n’a été enregistré."
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
                "Fichier refusé par l’antivirus ou analyse incomplète."
            )
        return True
    except OSError:
        raise ValidationError(
            "L’antivirus ne répond pas. Réessayez plus tard."
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
    owner,
    collection_id,
    uploads,
    paths,
    *,
    upload_session=None,
    capacity_reserved=False,
):
    if not uploads or len(uploads) > 100 or len(paths) != len(uploads):
        raise ValidationError("Sélectionnez entre 1 et 100 fichiers.")
    normalized = [safe_path(p) for p in paths]
    if len({p.casefold() for p in normalized}) != len(normalized):
        raise ValidationError("Deux fichiers portent le même chemin.")
    size = sum(f.size for f in uploads)
    if size > settings.MAX_UPLOAD_BYTES or any(
        f.size > settings.MAX_FILE_BYTES for f in uploads
    ):
        raise ValidationError("La taille maximale de l’envoi est dépassée.")
    if not upload_session and not capacity_reserved:
        with reserve_temporary(2 * size, owner_id=owner.pk, kind="import"):
            return add_files(
                owner, collection_id, uploads, paths, capacity_reserved=True
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
            user = User.objects.select_for_update().get(pk=owner.pk)
            collection = Collection.objects.select_for_update().get(
                pk=collection_id, owner=user
            )
            if not user.is_active:
                raise ValidationError("Compte désactivé.")
            if available_links(collection.links.all()).exists():
                raise ValidationError(
                    "Révoquez les liens actifs avant de modifier les fichiers du dossier."
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
                    raise ValidationError("Session expirée ou invalide.")
                reservation = session.size
            if (
                user.used_bytes + user.reserved_bytes - reservation + size
                > user.quota_bytes
            ):
                raise ValidationError("Votre quota de stockage est atteint.")
            existing = {
                p.casefold()
                for p in Asset.all_objects.filter(collection=collection).values_list(
                    "path", flat=True
                )
            }
            if (
                sum(
                    Asset.all_objects.filter(collection=collection).values_list(
                        "size", flat=True
                    )
                )
                + size
                > settings.MAX_UPLOAD_BYTES
            ):
                raise ValidationError("La taille maximale du dossier est atteinte.")
            if (
                existing.intersection(p.casefold() for p in normalized)
                or len(existing) + len(uploads) > 100
            ):
                raise ValidationError(
                    "Chemin déjà présent ou limite de 100 fichiers par dossier atteinte."
                )
            if (
                not upload_session
                and UploadPart.objects.filter(
                    session__collection=collection, session__state="pending"
                ).exists()
            ):
                raise ValidationError(
                    "Terminez ou annulez les envois reprenables avant un import classique."
                )
            all_paths = existing | {p.casefold() for p in normalized}
            if any(
                "/".join(path.split("/")[:i]) in all_paths
                for path in all_paths
                for i in range(1, len(path.split("/")))
            ):
                raise ValidationError(
                    "Un chemin ne peut pas être à la fois un fichier et un dossier."
                )
            for upload, path, scanned, digest in checked:
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
                actor=user, action="files_uploaded", target=str(collection.pk)
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


def download_bundle(link):
    """Build before claiming: a missing file must not consume a one-use link."""
    files = list(link.collection.files.all())
    if not files:
        raise ValidationError("Ce dossier est vide.")
    if settings.ANTIVIRUS_REQUIRED and any(not f.scanned for f in files):
        raise ValidationError("Ce dossier contient des fichiers non analysés.")
    # Always a disk temporary file: memory usage is independent of bundle size.
    # ZIP_STORED requires payload bytes plus two UTF-8 names and headers per
    # entry. The extra 1 MiB safely covers ZIP64 and all bounded metadata.
    size = (
        sum(asset.size + 2 * len(asset.path.encode("utf-8")) + 256 for asset in files)
        + 1024 * 1024
    )
    lease = reserve_temporary(size, owner_id=link.collection.owner_id)
    try:
        bundle = LeasedFile(
            tempfile.TemporaryFile(dir=settings.FILE_UPLOAD_TEMP_DIR), lease
        )
    except BaseException:
        lease.close()
        raise
    try:
        with zipfile.ZipFile(
            bundle, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for asset in files:
                archive.write(asset.blob.path, arcname=asset.path)
        bundle.seek(0)
        with transaction.atomic():
            # Same lock order as uploads/deletes/suspension; re-check revocation,
            # expiration, owner state and count immediately before authorization.
            owner = User.objects.select_for_update().get(pk=link.collection.owner_id)
            if not owner.is_active:
                raise ValidationError("Ce lien n’est plus disponible.")
            changed = available_links(ShareLink.objects.filter(pk=link.pk)).update(
                downloads=F("downloads") + 1
            )
            if not changed:
                raise ValidationError("Ce lien n’est plus disponible.")
            Event.objects.create(link=link, kind="download_started")
        return bundle
    except Exception:
        bundle.close()
        raise
