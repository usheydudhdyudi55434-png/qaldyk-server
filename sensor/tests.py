from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
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


def create_user():
    return get_user_model().objects.create_user(
        username="dispatcher",
        password="correct-horse-battery"
    )


class AccessTests(TestCase):

    def setUp(self):
        self.container = Container.objects.create(
            name="A",
            location="Almaty"
        )

    def test_anonymous_dashboard_redirects_to_login(self):
        response = self.client.get('/dashboard/')

        self.assertRedirects(response, '/login/?next=/dashboard/')

    def test_anonymous_cannot_read_sensor_data(self):
        urls = (
            '/api/containers/',
            '/api/containers/history/',
            f'/api/sensor/{self.container.id}/',
        )

        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_device_can_post_without_login(self):
        response = self.client.post(
            f'/api/sensor/{self.container.id}/',
            {"distance": 10, "fill_level": 90},
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 201)

    def test_logged_in_user_can_read_sensor_data(self):
        self.client.force_login(create_user())

        response = self.client.get(f'/api/sensor/{self.container.id}/')

        self.assertEqual(response.status_code, 200)


class LoginTests(TestCase):

    def setUp(self):
        create_user()

    def login(self, password, next_url=''):
        return self.client.post('/login/', {
            "username": "dispatcher",
            "password": password,
            "next": next_url
        })

    def test_correct_password_opens_the_dashboard(self):
        response = self.login("correct-horse-battery")

        self.assertRedirects(response, '/dashboard/')

    def test_wrong_password_shows_error_and_stays_logged_out(self):
        response = self.login("wrong")

        self.assertContains(response, "Логин немесе құпия сөз қате.")
        self.assertEqual(self.client.get('/dashboard/').status_code, 302)

    def test_external_next_url_is_ignored(self):
        response = self.login(
            "correct-horse-battery",
            next_url='https://evil.example.com/'
        )

        self.assertRedirects(response, '/dashboard/')

    def test_logout_ends_the_session(self):
        self.login("correct-horse-battery")

        response = self.client.post('/logout/')

        self.assertRedirects(response, '/login/')
        self.assertEqual(self.client.get('/dashboard/').status_code, 302)


class DashboardTests(TestCase):

    def test_assets_are_versioned_so_browsers_reload_them(self):
        self.client.force_login(create_user())

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

    def setUp(self):
        self.client.force_login(create_user())

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
        self.client.force_login(create_user())
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
