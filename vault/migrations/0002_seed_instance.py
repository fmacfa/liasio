from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model("vault", "Instance").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("vault", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
