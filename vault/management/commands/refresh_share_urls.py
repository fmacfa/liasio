"""Refresh the lookup after an intentional SECRET_KEY rotation or upgrade."""

from django.core.management.base import BaseCommand
from django.db import DEFAULT_DB_ALIAS

from vault.models import ShareLink
from vault.share_urls import code_digest, short_code


class Command(BaseCommand):
    help = "Refresh short sharing URL lookups without changing access rules or counters."

    def add_arguments(self, parser):
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)

    def handle(self, *args, **options):
        links = ShareLink.objects.using(options["database"])
        after = None
        updated = 0
        while True:
            query = links.order_by("pk")
            if after is not None:
                query = query.filter(pk__gt=after)
            batch = list(query.only("pk", "short_code_digest")[:500])
            if not batch:
                break
            changed = []
            for link in batch:
                digest = code_digest(short_code(link.pk))
                if link.short_code_digest != digest:
                    link.short_code_digest = digest
                    changed.append(link)
            links.bulk_update(changed, ["short_code_digest"], batch_size=500)
            updated += len(changed)
            after = batch[-1].pk
        self.stdout.write(f"Refreshed {updated} short sharing URL lookups.")
