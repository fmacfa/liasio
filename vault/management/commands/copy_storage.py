"""Explicit, hash-verified local/S3 copy; never deletes or renames originals."""

import hashlib
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand, CommandError

from vault.models import Asset
from vault.object_storage import PrivateS3Storage


class Command(BaseCommand):
    help = "Copy all originals to local or S3 with unchanged keys. Stop writers; default is dry-run."

    def add_arguments(self, parser):
        parser.add_argument("destination", choices=("local", "s3"))
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--local-directory", type=Path)

    def handle(self, *args, **options):
        if options["destination"] == "local":
            if (
                not options["local_directory"]
                or not options["local_directory"].is_absolute()
            ):
                raise CommandError("Provide an absolute --local-directory.")
            target = FileSystemStorage(location=options["local_directory"])
        else:
            if not settings.S3_BUCKET:
                raise CommandError("Configure S3_BUCKET and AWS credentials first.")
            target = PrivateS3Storage()
        count = 0
        for asset in Asset.all_objects.order_by("pk").iterator():
            if not options["apply"]:
                count += 1
                continue
            if not target.exists(asset.blob.name):
                with asset.blob.open("rb") as source:
                    name = target.save(asset.blob.name, source)
                if name != asset.blob.name:
                    raise CommandError(
                        "Destination collision; copied key differs. No originals were removed."
                    )
            with target.open(asset.blob.name, "rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if digest != asset.sha256 or target.size(asset.blob.name) != asset.size:
                raise CommandError(
                    "Destination verification failed; do not change STORAGE_BACKEND."
                )
            count += 1
        self.stdout.write(
            f"{'Verified' if options['apply'] else 'Would copy'} {count} objects. No originals removed."
        )
