"""Verify a restored vault, including trash, staged blocks and encrypted MFA."""

import hashlib
import json

from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from vault.mfa import cipher
from vault.models import Asset, Collection, ShareLink, UploadSession, User
from vault.resumable import part_path


class Command(BaseCommand):
    help = "Read every stored byte and verify hashes, quota counters, staged uploads and MFA keys."

    def handle(self, *args, **options):
        count = total = 0
        errors = []
        for asset in Asset.all_objects.all().iterator():
            try:
                with asset.blob.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                if digest != asset.sha256 or asset.blob.size != asset.size:
                    errors.append(f"Integrity mismatch for asset {asset.pk}")
            except OSError:
                errors.append(f"Missing asset {asset.pk}")
            count += 1
            total += asset.size
        for user in User.objects.all():
            stored = (
                Asset.all_objects.filter(collection__owner=user).aggregate(
                    n=Sum("size")
                )["n"]
                or 0
            )
            reserved = (
                UploadSession.objects.filter(owner=user, state="pending").aggregate(
                    n=Sum("size")
                )["n"]
                or 0
            )
            if (stored, reserved) != (user.used_bytes, user.reserved_bytes):
                errors.append(f"Quota counter mismatch for account {user.pk}")
            if user.mfa_secret:
                try:
                    cipher().decrypt(user.mfa_secret.encode())
                except (InvalidToken, ValueError):
                    errors.append(
                        f"Cannot decrypt MFA for account {user.pk}; restore its original encryption key"
                    )
        for session in UploadSession.objects.filter(state="pending").prefetch_related(
            "parts"
        ):
            for part in session.parts.all():
                path = part_path(part)
                if part.offset and (
                    not path.exists() or path.stat().st_size < part.offset
                ):
                    errors.append(f"Missing staged bytes for part {part.pk}")
        if errors:
            raise CommandError("\n".join(errors))
        self.stdout.write(
            json.dumps(
                {
                    "verified": True,
                    "files": count,
                    "bytes": total,
                    "users": User.objects.count(),
                    "collections": Collection.all_objects.count(),
                    "links": ShareLink.objects.count(),
                }
            )
        )
