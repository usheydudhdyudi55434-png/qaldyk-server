from django.core.management.base import BaseCommand

from sensor.models import Container


# Ten spots across all eight Almaty districts, coordinates from OpenStreetMap.
# Container #1 and #3 keep their original spots.
CITY_CONTAINERS = [
    ("Container #1", "Алмалы ауданы, Розыбакиев көшесі", 43.2389, 76.8897),
    ("Container #2", "Медеу ауданы, Көк базар", 43.26368, 76.9552),
    ("Container #3", "Бостандық ауданы, Мыңбаев көшесі", 43.2365, 76.886),
    ("Container #4", "Әуезов ауданы, Сайран автовокзалы", 43.24665, 76.85958),
    ("Container #5", "Алатау ауданы, Шаңырақ-1 шағын ауданы", 43.30345, 76.86317),
    ("Container #6", "Жетісу ауданы, Құлагер шағын ауданы", 43.31027, 76.92049),
    ("Container #7", "Түрксіб ауданы, Алматы-1 вокзалы", 43.34145, 76.94917),
    ("Container #8", "Наурызбай ауданы, Шұғыла шағын ауданы", 43.21391, 76.78787),
    ("Container #9", "Бостандық ауданы, Mega Alma-Ata", 43.20165, 76.89232),
    ("Container #10", "Медеу ауданы, Dostyk Plaza", 43.23341, 76.95678),
]


class Command(BaseCommand):

    help = "Creates or moves the ten Almaty test containers. Safe to run again."

    def handle(self, *args, **options):

        for name, location, latitude, longitude in CITY_CONTAINERS:

            container, created = Container.objects.update_or_create(
                name=name,
                defaults={
                    "location": location,
                    "latitude": latitude,
                    "longitude": longitude
                }
            )

            self.stdout.write(
                f"{'created' if created else 'updated'}: "
                f"#{container.id} {name} — {location}"
            )
