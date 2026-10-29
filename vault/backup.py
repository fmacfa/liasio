"""Backend-neutral, bounded export of database-referenced originals and upload blocks."""

import re
import tarfile

from django.core.management.base import CommandError

from .models import Asset, UploadSession
from .resumable import part_path


def export_archive(destination):
    with tarfile.open(fileobj=destination, mode="w|gz") as archive:
        for asset in Asset.all_objects.order_by("pk").iterator():
            name = asset.blob.name
            if not re.fullmatch(r"[0-9]+/[0-9a-f]{32}", name):
                raise CommandError("Unexpected object key; export aborted.")
            info = tarfile.TarInfo("files/" + name)
            info.size, info.mode, info.uid, info.gid = asset.size, 0o600, 10001, 10001
            with asset.blob.open("rb") as source:
                archive.addfile(info, source)
        for session in UploadSession.objects.filter(state="pending").prefetch_related(
            "parts"
        ):
            for part in session.parts.all():
                if not part.offset:
                    continue
                path = part_path(part)
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size < part.offset
                ):
                    raise CommandError("Missing upload block; export aborted.")
                info = tarfile.TarInfo(
                    "uploads/" + str(session.pk) + "/" + str(part.pk)
                )
                info.size, info.mode, info.uid, info.gid = (
                    part.offset,
                    0o600,
                    10001,
                    10001,
                )
                with path.open("rb") as source:
                    archive.addfile(info, source)
