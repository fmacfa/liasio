from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("vault", "0008_link_batches")]

    operations = [
        migrations.AlterField(
            model_name="apitoken",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
