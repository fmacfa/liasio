from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vault", "0005_apitoken_webhook_webhookdelivery_apireceipt")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="webhook_lease",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.AddField(
            model_name="user",
            name="webhook_lease_until",
            field=models.DateTimeField(null=True, editable=False),
        ),
        migrations.AddIndex(
            model_name="webhookdelivery",
            index=models.Index(fields=["state", "next_at"], name="webhook_due_idx"),
        ),
    ]
