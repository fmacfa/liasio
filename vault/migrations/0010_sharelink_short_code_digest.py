import base64
import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models


def populate_short_codes(apps, schema_editor):
    # Frozen wire format: migrations must not depend on future codec changes.
    links = apps.get_model("vault", "ShareLink").objects.using(
        schema_editor.connection.alias
    )
    after = None
    while True:
        query = links.order_by("pk")
        if after is not None:
            query = query.filter(pk__gt=after)
        batch = list(query.only("pk", "short_code_digest")[:500])
        if not batch:
            break
        for link in batch:
            signature = hmac.new(
                settings.SECRET_KEY.encode(),
                f"linkvault:alias:v1:share:{link.pk.hex}".encode(),
                hashlib.sha256,
            ).digest()[:16]
            code = base64.urlsafe_b64encode(signature).rstrip(b"=")
            link.short_code_digest = hashlib.sha256(code).hexdigest()
        links.bulk_update(batch, ["short_code_digest"], batch_size=500)
        after = batch[-1].pk


class Migration(migrations.Migration):
    dependencies = [("vault", "0009_apitoken_optional_expiry")]

    operations = [
        migrations.AddField(
            model_name="sharelink",
            name="short_code_digest",
            field=models.CharField(
                max_length=64, unique=True, null=True, blank=True, editable=False
            ),
        ),
        migrations.RunPython(populate_short_codes, migrations.RunPython.noop),
    ]
