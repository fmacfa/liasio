import json
from django.core.management.base import BaseCommand, CommandError
from vault.operations import monitor, snapshot


class Command(BaseCommand):
    help = "Check readiness or update operational alerts (SMTP only when configured)."

    def add_arguments(self, parser):
        parser.add_argument("--notify", action="store_true")

    def handle(self, *args, **options):
        result = monitor() if options["notify"] else snapshot()
        self.stdout.write(json.dumps(result, sort_keys=True))
        if not result["ready"]:
            raise CommandError("Instance is not ready.")
