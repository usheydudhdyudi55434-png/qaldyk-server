from datetime import UTC, datetime, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from . import fire
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


class AddCityContainersTests(TestCase):

    def test_creates_ten_located_containers_and_is_safe_to_rerun(self):
        existing = Container.objects.create(name="Container #2", location="Almaty")
        add_reading(existing, 50)

        call_command('add_city_containers', stdout=StringIO())
        call_command('add_city_containers', stdout=StringIO())

        self.assertEqual(Container.objects.count(), 10)
        self.assertFalse(
            Container.objects.filter(latitude__isnull=True).exists()
        )

        existing.refresh_from_db()
        self.assertEqual(existing.location, "Медеу ауданы, Көк базар")
        self.assertEqual(existing.readings.count(), 1)


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


class FireAssessmentTests(TestCase):

    def test_no_fire_sensors_means_no_risk_value(self):
        self.assertEqual(fire.assess(None, None), ("", ""))

    def test_normal_readings(self):
        self.assertEqual(fire.assess(22, 900)[0], fire.NORMAL)

    def test_single_signs(self):
        self.assertEqual(fire.assess(55, 900)[0], fire.WARNING)
        self.assertEqual(fire.assess(70, 900)[0], fire.DANGER)
        self.assertEqual(fire.assess(22, 3200)[0], fire.WARNING)
        self.assertEqual(fire.assess(22, 3900)[0], fire.DANGER)

    def test_two_warning_signs_confirm_danger(self):
        risk, note = fire.assess(55, 3200)

        self.assertEqual(risk, fire.DANGER)
        self.assertIn("Температура 55°C", note)
        self.assertIn("Түтін/газ", note)

    def test_fast_temperature_rise_is_a_warning(self):
        now = timezone.now()
        previous = (20, now - timedelta(minutes=2))

        risk, note = fire.assess(40, None, previous=previous, now=now)

        self.assertEqual(risk, fire.WARNING)
        self.assertIn("минутына 10°C", note)

    def test_old_previous_reading_is_not_compared(self):
        now = timezone.now()
        previous = (20, now - timedelta(hours=1))

        self.assertEqual(
            fire.assess(40, None, previous=previous, now=now)[0],
            fire.NORMAL
        )

    def test_device_can_raise_but_not_lower_the_risk(self):
        self.assertEqual(fire.assess(22, 900, device_risk=fire.WARNING)[0], fire.WARNING)
        self.assertEqual(fire.assess(70, 900, device_risk=fire.NORMAL)[0], fire.DANGER)


class FireApiTests(TestCase):

    def setUp(self):
        self.container = Container.objects.create(name="A", location="Almaty")
        self.url = f'/api/sensor/{self.container.id}/'

    def post(self, **fields):
        return self.client.post(
            self.url,
            {"distance": 149.29, "fill_level": 0, **fields},
            content_type='application/json'
        )

    def test_reading_from_the_new_firmware_is_saved_and_assessed(self):
        response = self.post(
            temperature=5.19,
            gas_level=3628,
            fire_risk="WARNING"
        )

        self.assertEqual(response.status_code, 201)
        reading = SensorReading.objects.get()
        self.assertEqual(reading.gas_level, 3628)
        self.assertEqual(reading.fire_risk, fire.WARNING)
        self.assertIn("Түтін/газ 89%", reading.fire_note)

    def test_old_firmware_without_fire_sensors_still_works(self):
        response = self.post()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(SensorReading.objects.get().fire_risk, "")

    def test_invalid_fire_values_are_rejected_and_nothing_is_saved(self):
        for fields in (
            {"temperature": "hot"},
            {"temperature": 400},
            {"gas_level": 5000},
            {"gas_level": -1},
            {"fire_risk": "PANIC"},
        ):
            self.assertEqual(self.post(**fields).status_code, 400, fields)

        self.assertEqual(SensorReading.objects.count(), 0)

    def test_overview_and_history_expose_fire_signs(self):
        self.client.force_login(create_user())
        self.post(temperature=72, gas_level=1000)

        latest = self.client.get('/api/containers/').json()["containers"][0]["latest"]
        self.assertEqual(latest["fire_risk"], fire.DANGER)
        self.assertEqual(latest["temperature"], 72)

        history = self.client.get(
            '/api/containers/history/',
            {"hours": 1, "metric": "temperature"}
        ).json()
        self.assertEqual(history["series"][str(self.container.id)][0][1], 72)

        self.assertEqual(
            self.client.get('/api/containers/history/', {"metric": "x"}).status_code,
            400
        )

    def test_history_keeps_the_temperature_peak(self):
        self.client.force_login(create_user())

        for temperature in (20, 90, 20):
            self.post(temperature=temperature)

        points = self.client.get(
            '/api/containers/history/',
            {"hours": 1, "metric": "temperature"}
        ).json()["series"][str(self.container.id)]

        self.assertEqual(max(point[1] for point in points), 90)
