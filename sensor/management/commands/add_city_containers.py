from django.core.management.base import BaseCommand

from sensor.districts import district_for
from sensor.models import Container


# One test container in each of the eight Almaty districts, coordinates from
# OpenStreetMap. Container #1 and #3 keep their original spots.
CITY_CONTAINERS = [
    ("Container #1", "Алмалы ауданы, Розыбакиев көшесі", 43.2389, 76.8897),
    ("Container #2", "Медеу ауданы, Көк базар", 43.26368, 76.9552),
    ("Container #3", "Бостандық ауданы, Мыңбаев көшесі", 43.2365, 76.886),
    ("Container #4", "Әуезов ауданы, Сайран автовокзалы", 43.24665, 76.85958),
    ("Container #5", "Алатау ауданы, Шаңырақ-1 шағын ауданы", 43.30345, 76.86317),
    ("Container #6", "Жетісу ауданы, Құлагер шағын ауданы", 43.31027, 76.92049),
    ("Container #7", "Түрксіб ауданы, Алматы-1 вокзалы", 43.34145, 76.94917),
    ("Container #8", "Наурызбай ауданы, Шұғыла шағын ауданы", 43.21391, 76.78787),
]

# Second containers of a district from an earlier layout; removed only while unused
RETIRED_CONTAINERS = ("Container #9", "Container #10")


class Command(BaseCommand):

    help = "Creates or moves the ten Almaty test containers. Safe to run again."

    def handle(self, *args, **options):

        for name, location, latitude, longitude in CITY_CONTAINERS:

            container, created = Container.objects.update_or_create(
                name=name,
                defaults={
                    "location": location,
                    "latitude": latitude,
                    "longitude": longitude,
                    "district": district_for(latitude, longitude)
                }
            )

            self.stdout.write(
                f"{'created' if created else 'updated'}: "
                f"#{container.id} {name} — {location}"
            )

        for name in RETIRED_CONTAINERS:

            container = Container.objects.filter(name=name).first()

            if container is None:
                continue

            # Deleting a container deletes its readings, so one with data is kept
            if container.readings.exists() or container.requests.exists():
                self.stdout.write(f"kept: #{container.id} {name} has readings or requests")
                continue

            container.delete()
            self.stdout.write(f"removed: {name}")
