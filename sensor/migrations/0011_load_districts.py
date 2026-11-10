from django.db import migrations

from sensor.districts import contains, load_boundaries


def load_districts(apps, schema_editor):

    District = apps.get_model("sensor", "District")
    Container = apps.get_model("sensor", "Container")
    ResidentRequest = apps.get_model("sensor", "ResidentRequest")

    districts = [
        District.objects.update_or_create(slug=slug, defaults={"name": name, "boundary": geometry})[0]
        for slug, name, geometry in load_boundaries()
    ]

    # Place everything that already has coordinates
    for model in (Container, ResidentRequest):
        for row in model.objects.filter(latitude__isnull=False, longitude__isnull=False):
            row.district = next(
                (d for d in districts if contains(d.boundary, row.latitude, row.longitude)),
                None
            )
            row.save(update_fields=["district"])


def unload_districts(apps, schema_editor):

    apps.get_model("sensor", "District").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("sensor", "0010_districts"),
    ]

    operations = [
        migrations.RunPython(load_districts, unload_districts),
    ]
