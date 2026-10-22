import time
import signal
import subprocess
import sys
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone
from vault.models import ServiceState
from vault.webhooks import deliver_one


class Command(BaseCommand):
    help = "Deliver queued webhooks; --loop polls every five seconds."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--workers", type=int, default=1)

    def handle(self, *args, **options):
        workers = options["workers"]
        if not 1 <= workers <= 4 or (workers > 1 and not options["loop"]):
            raise CommandError("Use 1–4 workers; multiple workers require --loop.")
        if workers > 1:
            return self.supervise(workers)
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

    def supervise(self, workers):
        # Separate processes keep send()'s main-thread alarm deadline intact.
        children = []

        def stop(*_args):
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, stop)
        try:
            for _ in range(workers):
                children.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            str(Path(settings.BASE_DIR) / "manage.py"),
                            "deliver_webhooks",
                            "--loop",
                        ]
                    )
                )
            while all(child.poll() is None for child in children):
                time.sleep(1)
            raise CommandError(
                "A webhook worker stopped; restarting the worker group is required."
            )
        except KeyboardInterrupt:
            pass
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            signal.signal(signal.SIGTERM, previous)
