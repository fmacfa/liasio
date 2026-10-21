from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from vault.models import Audit, ServiceState


class Command(BaseCommand):
    help = "Record completion of an isolated backup restore check (called by restore-test.sh)."

    def add_arguments(self, parser):
        parser.add_argument("--snapshot-created-at", required=True)

    def handle(self, *args, **options):
        created = parse_datetime(options["snapshot_created_at"])
        if not created or timezone.is_naive(created) or created > timezone.now():
            raise CommandError("A valid backup snapshot timestamp is required.")
        ServiceState.objects.update_or_create(
            key="backup_verified",
            defaults={
                "data": {
                    "method": "isolated-docker-restore",
                    "snapshot_created_at": created.isoformat(),
                }
            },
        )
        Audit.objects.create(action="backup_restore_verified")
        self.stdout.write("Backup restore verification recorded.")
