import json

from django.core.management.base import BaseCommand, CommandError

from vault import updates


class Command(BaseCommand):
    help = "Exchange update requests with the host service."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("poll", "finish"))
        parser.add_argument("--job")
        parser.add_argument("--state", choices=("installed", "failed", "queued"))

    def handle(self, *args, **options):
        if options["action"] == "poll":
            self.stdout.write(json.dumps(updates.poll()))
        elif options["job"] and options["state"]:
            updates.finish(options["job"], options["state"])
        else:
            raise CommandError("finish requires --job and --state")
