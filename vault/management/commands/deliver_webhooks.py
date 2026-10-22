import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from vault.models import ServiceState
from vault.webhooks import deliver_one


class Command(BaseCommand):
    help = "Deliver queued webhooks; --loop polls every five seconds."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        while True:
            ServiceState.objects.update_or_create(
                key="webhooks", defaults={"data": {"running": True}}
            )
            for _ in range(50):
                if not deliver_one():
                    break
                ServiceState.objects.filter(key="webhooks").update(
                    updated_at=timezone.now()
                )
            if not options["loop"]:
                break
            close_old_connections()
            time.sleep(5)
