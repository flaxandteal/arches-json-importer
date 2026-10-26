from django.db import migrations

ARCHES_JSON_IMPORT_MODULE_PK = "0a1c9d67-8b4e-4f2a-9d31-6c5b2e7a4f18"


def set_reversible(value):
    def run(apps, schema_editor):
        ETLModule = apps.get_model("models", "ETLModule")
        ETLModule.objects.filter(pk=ARCHES_JSON_IMPORT_MODULE_PK).update(
            reversible=value
        )

    return run


class Migration(migrations.Migration):

    dependencies = [
        ("arches_json_importer", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(set_reversible(True), set_reversible(False)),
    ]
