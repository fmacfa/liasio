from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from vault.models import Audit, User


class Command(BaseCommand):
    help = "Set an individual user's storage quota (GiB)."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("gib", type=int)

    def handle(self, *args, **options):
        if options["gib"] < 1:
            raise CommandError("Quota must be at least 1 GiB.")
        with transaction.atomic():
            try:
                user = User.objects.select_for_update().get(email__iexact=options["email"])
            except User.DoesNotExist:
                raise CommandError("Unknown account.") from None
            user.quota_bytes = options["gib"] * 1024**3
            if user.quota_bytes < user.used_bytes:
                raise CommandError("Quota cannot be lower than current usage.")
            user.save(update_fields=["quota_bytes"])
            Audit.objects.create(action="quota_updated", target=str(user.pk))
        self.stdout.write("Quota updated.")
