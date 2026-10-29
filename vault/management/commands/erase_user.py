from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from vault.models import Asset, Audit, Invitation, User


class Command(BaseCommand):
    help = "Permanently erase a member and their files, links, invitations and events."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("This is permanent. Back up first and pass --confirm.")
        with transaction.atomic():
            try:
                user = User.objects.select_for_update().get(
                    email__iexact=options["email"]
                )
            except User.DoesNotExist:
                raise CommandError("Unknown account.") from None
            if user.is_staff:
                raise CommandError(
                    "Administrator erasure is disabled. Transfer administration first."
                )
            if user.managed_workspaces.exists():
                raise CommandError(
                    "Transfer responsibility for this account's team spaces before erasing it."
                )
            blobs = list(
                Asset.all_objects.filter(collection__owner=user).values_list(
                    "blob", flat=True
                )
            )
            asset_ids = list(
                Asset.all_objects.filter(collection__owner=user).values_list(
                    "pk", flat=True
                )
            )
            Invitation.objects.filter(email__iexact=user.email).delete()
            Audit.objects.filter(actor=user).delete()
            from django.contrib.sessions.models import Session

            for session in Session.objects.all().iterator():
                if str(session.get_decoded().get("_auth_user_id", "")) == str(user.pk):
                    session.delete()
            user.delete()
            transaction.on_commit(
                lambda: [default_storage.delete(blob) for blob in blobs]
            )
            from vault.previews import cleanup_cache

            transaction.on_commit(lambda: cleanup_cache(asset_ids))
        self.stdout.write(
            "Account and files erased. Remember the retention policy for your backups."
        )
