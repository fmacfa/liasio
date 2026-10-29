import sys
from django.core.management.base import BaseCommand
from vault.backup import export_archive


class Command(BaseCommand):
    help = "Stream a backend-neutral files.tar.gz to stdout. Stop all writers before using."

    def handle(self, *args, **options):
        export_archive(sys.stdout.buffer)
