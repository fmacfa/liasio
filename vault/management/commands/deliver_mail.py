import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from vault.mail import deliver_one
from vault.models import ServiceState


def worker():
    close_old_connections()
    try:
        return deliver_one()
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Deliver approved emails fairly across accounts (no SMTP credentials in output)."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--workers", type=int, choices=(1, 2), default=1)

    def handle(self, *args, **options):
        if not settings.EMAIL_HOST and not options["loop"]:
            raise CommandError("EMAIL_HOST is not configured.")
        with ThreadPoolExecutor(max_workers=options["workers"]) as executor:
            while True:
                ServiceState.objects.update_or_create(
                    key="mail", defaults={"data": {"version": settings.APP_VERSION}}
                )
                results = (
                    list(executor.map(lambda _: worker(), range(options["workers"])))
                    if settings.EMAIL_HOST
                    else [False]
                )
                if not options["loop"]:
                    break
                if not any(results):
                    time.sleep(5)
