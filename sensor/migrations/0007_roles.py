from django.db import migrations

DISPATCHERS = "Диспетчерлер"
WORKERS = "Жұмысшылар"


def create_roles(apps, schema_editor):

    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    dispatchers, _ = Group.objects.get_or_create(name=DISPATCHERS)
    Group.objects.get_or_create(name=WORKERS)

    # Accounts made before roles existed were all dispatchers; keep their access
    for user in User.objects.filter(is_superuser=False):
        user.groups.add(dispatchers)


def remove_roles(apps, schema_editor):

    apps.get_model("auth", "Group").objects.filter(name__in=(DISPATCHERS, WORKERS)).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("sensor", "0006_request_history_and_result_photo"),
    ]

    operations = [
        migrations.RunPython(create_roles, remove_roles),
    ]
