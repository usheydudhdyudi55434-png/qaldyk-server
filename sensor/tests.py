from datetime import UTC, datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Container, SensorReading


def add_reading(container, fill_level, age=timedelta(0)):
    reading = SensorReading.objects.create(
        container=container,
        distance=100 - fill_level,
        fill_level=fill_level,
        status="NORMAL"
    )
    SensorReading.objects.filter(id=reading.id).update(
        created_at=timezone.now() - age
    )
    return reading


class DashboardTests(TestCase):

    def test_assets_are_versioned_so_browsers_reload_them(self):
        response = self.client.get('/dashboard/')

        self.assertRegex(response.content.decode(), r'dashboard\.js\?v=\d+"')
        self.assertRegex(response.content.decode(), r'dashboard\.css\?v=\d+"')


class SensorLocationTests(TestCase):

    def setUp(self):
        self.container = Container.objects.create(
            name="A",
            location="Almaty",
            latitude=43.2,
            longitude=76.9
        )
        self.url = f'/api/sensor/{self.container.id}/'

    def post(self, **fields):
        return self.client.post(
            self.url,
            {"distance": 10, "fill_level": 90, **fields},
            content_type='application/json'
        )

    def test_coordinates_update_the_container(self):
        response = self.post(latitude=43.25, longitude=76.95)

        self.assertEqual(response.status_code, 201)
        self.container.refresh_from_db()
        self.assertEqual(self.container.latitude, 43.25)
        self.assertEqual(self.container.longitude, 76.95)

    def test_reading_without_coordinates_keeps_the_location(self):
        response = self.post()

        self.assertEqual(response.status_code, 201)
        self.container.refresh_from_db()
        self.assertEqual(self.container.latitude, 43.2)
        self.assertEqual(self.container.longitude, 76.9)

    def test_invalid_coordinates_are_rejected_and_nothing_is_saved(self):
        invalid = (
            {"latitude": 91, "longitude": 76.9},
            {"latitude": 43.2, "longitude": 181},
            {"latitude": 43.2},
            {"longitude": 76.9},
            {"latitude": "abc", "longitude": 76.9},
            {"latitude": "nan", "longitude": 76.9},
        )

        for fields in invalid:
            response = self.post(**fields)
            self.assertEqual(response.status_code, 400, fields)

        self.assertEqual(SensorReading.objects.count(), 0)
        self.container.refresh_from_db()
        self.assertEqual(self.container.latitude, 43.2)


class ContainersOverviewTests(TestCase):

    def test_returns_newest_reading_for_each_container(self):
        container = Container.objects.create(
            name="A",
            location="Almaty",
            latitude=43.2,
            longitude=76.9
        )
        add_reading(container, 90, age=timedelta(minutes=1))
        add_reading(container, 10, age=timedelta(hours=1))

        response = self.client.get('/api/containers/')

        self.assertEqual(response.status_code, 200)
        item = response.json()["containers"][0]
        self.assertEqual(item["name"], "A")
        self.assertEqual(item["latitude"], 43.2)
        self.assertEqual(item["latest"]["fill_level"], 90)

    def test_container_without_readings_has_null_latest(self):
        Container.objects.create(name="Empty", location="Almaty")

        response = self.client.get('/api/containers/')

        self.assertIsNone(response.json()["containers"][0]["latest"])


class ContainersHistoryTests(TestCase):

    def setUp(self):
        self.container = Container.objects.create(
            name="A",
            location="Almaty"
        )

    def test_rejects_unsupported_hours(self):
        for hours in ("2", "abc", "-1"):
            response = self.client.get(
                '/api/containers/history/',
                {"hours": hours}
            )
            self.assertEqual(response.status_code, 400)

    def test_excludes_readings_outside_range(self):
        add_reading(self.container, 40, age=timedelta(minutes=10))
        add_reading(self.container, 70, age=timedelta(hours=2))

        response = self.client.get(
            '/api/containers/history/',
            {"hours": 1}
        )

        points = response.json()["series"][str(self.container.id)]
        self.assertEqual([point[1] for point in points], [40])

    def test_averages_readings_in_the_same_bucket(self):
        now = timezone.now()
        bucket_seconds = 24 * 3600 // 120
        bucket_start = (
            int((now - timedelta(hours=1)).timestamp())
            // bucket_seconds * bucket_seconds
        )

        for fill_level, offset in ((20, 1), (40, 2)):
            reading = add_reading(self.container, fill_level)
            SensorReading.objects.filter(id=reading.id).update(
                created_at=datetime.fromtimestamp(
                    bucket_start + offset,
                    tz=UTC
                )
            )

        response = self.client.get('/api/containers/history/')

        points = response.json()["series"][str(self.container.id)]
        self.assertEqual(points, [[bucket_start * 1000, 30]])

    def test_empty_history_returns_empty_series(self):
        response = self.client.get('/api/containers/history/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["series"], {})
