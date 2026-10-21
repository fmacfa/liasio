import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from vault.models import Asset, Audit, Event, Invitation, RateBucket, ServiceState


class Command(BaseCommand):
    help = "Purge expired sessions, rate limits, old events and orphan files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Repeat every five minutes (maintenance container).",
        )

    def handle(self, *args, **options):
        while True:
            now = timezone.now()
            ServiceState.objects.update_or_create(
                key="maintenance", defaults={"data": {"version": settings.APP_VERSION}}
            )
            cutoff = now - timedelta(days=settings.EVENT_RETENTION_DAYS)
            Event.objects.filter(created_at__lt=cutoff).delete()
            Audit.objects.filter(created_at__lt=cutoff).delete()
            RateBucket.objects.filter(expires_at__lt=now).delete()
            Session.objects.filter(expire_date__lte=now).delete()
            Invitation.objects.filter(expires_at__lt=now - timedelta(days=7)).delete()
            from vault.trash import purge_expired
            from vault.resumable import cleanup_expired

            purge_expired()
            cleanup_expired()
            known = set(Asset.all_objects.values_list("blob", flat=True))
            removed = 0
            for path in Path(settings.MEDIA_ROOT).rglob("*"):
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and path.stat().st_mtime < (now - timedelta(days=1)).timestamp()
                ):
                    if path.relative_to(settings.MEDIA_ROOT).as_posix() not in known:
                        path.unlink()
                        removed += 1
            for path in Path(settings.FILE_UPLOAD_TEMP_DIR).glob("*.upload*"):
                if (
                    path.is_file()
                    and path.stat().st_mtime < (now - timedelta(days=1)).timestamp()
                ):
                    path.unlink()
            from vault.operations import monitor

            monitor()
            self.stdout.write(
                f"Maintenance complete; {removed} orphan blob(s) removed."
            )
            if not options["loop"]:
                break
            close_old_connections()
            time.sleep(300)
