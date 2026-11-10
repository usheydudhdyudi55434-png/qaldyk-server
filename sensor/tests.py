import base64
import json
import urllib.error
import tempfile
from datetime import UTC, datetime, timedelta
from io import BytesIO, StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from . import ai, fire
from .models import Container, District, RequestAnalysis, ResidentRequest, SensorReading, StaffProfile
from .residents import sensor_clearance, sensor_verdict, suggest_workers
from .roles import DISPATCHERS, WORKERS


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
    user = get_user_model().objects.create_user(
        username="dispatcher",
        password="correct-horse-battery"
    )
    user.groups.add(Group.objects.get(name=DISPATCHERS))
    return user


def create_worker(username="worker"):
    user = get_user_model().objects.create_user(
        username=username,
        password="pass-12345-x"
    )
    user.groups.add(Group.objects.get(name=WORKERS))
    return user


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

        self.assertEqual(Container.objects.count(), 8)
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


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ResidentRequestTests(TestCase):

    def setUp(self):
        self.container = Container.objects.create(
            name="Container #1",
            location="Almaty",
            latitude=43.2389,
            longitude=76.8897
        )
        add_reading(self.container, 92)

    def submit(self, **fields):
        data = {
            "category": ResidentRequest.Category.OVERFLOW,
            "address": "Розыбакиев көшесі",
            "description": "Контейнер толып кеткен",
            **fields
        }
        return self.client.post('/', data)

    def test_resident_page_is_public(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Өтінішті жіберу")
        self.assertContains(response, 'name="category"', count=4)
        self.assertNotContains(response, "Select an option")

    def test_request_gets_a_code_and_the_resident_sees_its_status(self):
        response = self.submit()

        item = ResidentRequest.objects.get()
        self.assertRegex(item.code, r"^Q-[A-HJ-NP-Z2-9]{6}$")
        self.assertRedirects(response, f"/otinish/?code={item.code}&new=1")

        page = self.client.get('/otinish/', {"code": item.code.lower()})
        self.assertContains(page, item.code)
        self.assertContains(page, "Жаңа")

    def test_request_near_a_container_keeps_its_sensor_reading(self):
        self.submit(latitude="43.2391", longitude="76.8899")

        item = ResidentRequest.objects.get()
        self.assertEqual(item.container, self.container)
        self.assertLess(item.container_distance, 50)
        self.assertEqual(item.sensor_fill_level, 92)
        self.assertEqual(sensor_verdict(item)[0], "confirmed")

    def test_request_far_from_containers_is_not_linked(self):
        self.submit(latitude="43.35", longitude="77.05")

        item = ResidentRequest.objects.get()
        self.assertIsNone(item.container)
        self.assertEqual(sensor_verdict(item)[0], "none")

    def test_low_sensor_reading_does_not_confirm(self):
        add_reading(self.container, 20)

        self.submit(latitude="43.2389", longitude="76.8897")

        self.assertEqual(sensor_verdict(ResidentRequest.objects.get())[0], "unconfirmed")

    def test_invalid_requests_are_not_saved(self):
        for fields in (
            {"category": ""},
            {"address": ""},
            {"website": "http://spam.example"},
            {"latitude": "43.2"},
        ):
            response = self.submit(**fields)
            self.assertEqual(response.status_code, 200, fields)

        self.assertEqual(ResidentRequest.objects.count(), 0)

    def test_photo_is_saved_under_a_random_name(self):
        photo = SimpleUploadedFile("Ivanov_phone_87011234567.png", PNG_BYTES, "image/png")

        self.submit(photo=photo)

        name = ResidentRequest.objects.get().photo.name
        self.assertTrue(name.startswith("requests/"))
        self.assertNotIn("Ivanov", name)
        self.assertTrue(name.endswith(".png"))

    def test_non_image_and_large_photos_are_rejected(self):
        fake = SimpleUploadedFile("photo.jpg", b"not really an image", "image/jpeg")
        large = SimpleUploadedFile(
            "big.png",
            PNG_BYTES + b"\x00" * (5 * 1024 * 1024),
            "image/png"
        )

        for photo in (fake, large):
            response = self.submit(photo=photo)
            self.assertEqual(response.status_code, 200)

        self.assertEqual(ResidentRequest.objects.count(), 0)

    def test_unknown_code_says_so(self):
        response = self.client.get('/otinish/', {"code": "Q-NOPE22"})

        self.assertContains(response, "табылмады")


class DispatcherRequestTests(TestCase):

    def setUp(self):
        self.item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.LITTERED,
            address="Абай даңғылы"
        )

    def test_dispatcher_pages_need_a_login(self):
        for url in ('/dashboard/requests/', f'/dashboard/requests/{self.item.pk}/'):
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_list_filters_by_status(self):
        self.client.force_login(create_user())
        done = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OTHER,
            address="Сайран",
            status=ResidentRequest.Status.DONE
        )

        response = self.client.get('/dashboard/requests/', {"status": "done"})

        self.assertContains(response, done.code)
        self.assertNotContains(response, self.item.code)

    def test_update_reaches_the_resident(self):
        worker = create_worker()
        self.client.force_login(create_user())

        response = self.client.post(f'/dashboard/requests/{self.item.pk}/', {
            "status": ResidentRequest.Status.DONE,
            "assignee": worker.pk,
            "resolution_note": "Аумақ тазартылды"
        })

        self.assertRedirects(response, f'/dashboard/requests/{self.item.pk}/?saved=1')
        self.item.refresh_from_db()
        self.assertEqual(self.item.assignee, worker)

        self.client.logout()
        page = self.client.get('/otinish/', {"code": self.item.code})
        self.assertContains(page, "Орындалды")
        self.assertContains(page, "Аумақ тазартылды")

    def test_dashboard_links_to_new_requests(self):
        self.client.force_login(create_user())

        response = self.client.get('/dashboard/')

        self.assertContains(response, '/dashboard/requests/')
        self.assertContains(response, 'count-badge">1<')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class WorkerTaskTests(TestCase):

    def setUp(self):
        self.worker = create_worker("worker")
        self.other = create_worker("other")
        self.item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OVERFLOW,
            address="Абай даңғылы",
            status=ResidentRequest.Status.ACCEPTED,
            assignee=self.worker
        )

    def complete(self, **fields):
        return self.client.post(f'/tasks/{self.item.pk}/complete/', {
            f"task{self.item.pk}-resolution_note": "Контейнер босатылды",
            **fields
        })

    def test_worker_sees_only_own_active_tasks(self):
        foreign = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OTHER,
            address="Сайран",
            status=ResidentRequest.Status.ACCEPTED,
            assignee=self.other
        )
        self.client.force_login(self.worker)

        response = self.client.get('/tasks/')

        self.assertContains(response, self.item.code)
        self.assertNotContains(response, foreign.code)

    def test_worker_cannot_touch_someone_elses_task(self):
        self.client.force_login(self.other)

        self.assertEqual(self.client.post(f'/tasks/{self.item.pk}/start/').status_code, 404)
        self.assertEqual(self.complete().status_code, 404)

    def test_start_moves_the_task_on_and_logs_it(self):
        self.client.force_login(self.worker)

        self.client.post(f'/tasks/{self.item.pk}/start/')

        self.item.refresh_from_db()
        self.assertEqual(self.item.status, ResidentRequest.Status.IN_PROGRESS)
        event = self.item.events.get()
        self.assertEqual(event.actor, self.worker)

    def test_completion_needs_a_photo(self):
        self.client.force_login(self.worker)

        response = self.complete()

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "фото қажет", status_code=400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, ResidentRequest.Status.ACCEPTED)

    def test_completion_with_photo_reaches_the_resident(self):
        self.client.force_login(self.worker)

        response = self.complete(**{
            f"task{self.item.pk}-result_photo": SimpleUploadedFile("after.png", PNG_BYTES, "image/png")
        })

        self.assertRedirects(response, f'/tasks/?done={self.item.code}')
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, ResidentRequest.Status.DONE)
        self.assertTrue(self.item.result_photo.name.startswith("requests/"))

        self.client.logout()
        page = self.client.get('/otinish/', {"code": self.item.code})
        self.assertContains(page, "Жұмыстан кейін")
        self.assertContains(page, "Контейнер босатылды")

    def test_tasks_need_a_login(self):
        self.assertEqual(self.client.get('/tasks/').status_code, 302)
        self.assertEqual(self.client.get(f'/tasks/{self.item.pk}/start/').status_code, 302)


class RequestHistoryTests(TestCase):

    def test_creation_and_dispatcher_changes_are_logged(self):
        self.client.post('/', {
            "category": ResidentRequest.Category.LITTERED,
            "address": "Сайран"
        })
        item = ResidentRequest.objects.get()
        dispatcher = create_user()
        worker = create_worker()
        self.client.force_login(dispatcher)

        self.client.post(f'/dashboard/requests/{item.pk}/', {
            "status": ResidentRequest.Status.ACCEPTED,
            "assignee": worker.pk,
            "resolution_note": ""
        })

        notes = [(event.status, event.actor, event.note) for event in item.events.all()]
        self.assertEqual(notes[0][:2], (ResidentRequest.Status.NEW, None))
        self.assertEqual(notes[1:], [
            (ResidentRequest.Status.ACCEPTED, dispatcher, f"Жауапты: {worker.username}")
        ])

        detail = self.client.get(f'/dashboard/requests/{item.pk}/')
        self.assertContains(detail, "Тұрғын өтініш жіберді")

    def test_sensor_clearance_after_the_request(self):
        container = Container.objects.create(
            name="A", location="Almaty", latitude=43.2389, longitude=76.8897
        )
        add_reading(container, 92, age=timedelta(minutes=5))
        item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OVERFLOW,
            address="Абай",
            container=container,
            sensor_fill_level=92
        )

        self.assertIsNone(sensor_clearance(item))

        add_reading(container, 94)
        add_reading(container, 4)
        SensorReading.objects.filter(container=container).update(
            created_at=timezone.now() + timedelta(minutes=1)
        )

        before, after, _ = sensor_clearance(item)
        self.assertEqual((before, after), (94, 4))


class RoleTests(TestCase):

    def setUp(self):
        self.dispatcher = create_user()
        self.worker = create_worker()
        self.item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OVERFLOW,
            address="Абай"
        )

    def login(self, username, password):
        return self.client.post('/login/', {"username": username, "password": password})

    def test_each_role_lands_on_its_own_page(self):
        self.assertRedirects(self.login("worker", "pass-12345-x"), '/tasks/')
        self.client.logout()
        self.assertRedirects(self.login("dispatcher", "correct-horse-battery"), '/dashboard/')

    def test_worker_cannot_open_dispatcher_pages_or_data(self):
        self.client.force_login(self.worker)

        for url in ('/dashboard/', '/dashboard/requests/', f'/dashboard/requests/{self.item.pk}/'):
            self.assertRedirects(self.client.get(url), '/tasks/', fetch_redirect_response=False)

        for url in ('/api/containers/', '/api/containers/history/'):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_dispatcher_is_sent_from_tasks_to_the_panel(self):
        self.client.force_login(self.dispatcher)

        self.assertRedirects(self.client.get('/tasks/'), '/dashboard/', fetch_redirect_response=False)

    def test_account_without_a_role_sees_an_explanation(self):
        get_user_model().objects.create_user(username="nobody", password="pass-12345-x")
        self.client.login(username="nobody", password="pass-12345-x")

        response = self.client.get('/dashboard/')

        self.assertContains(response, "рөл берілмеген", status_code=403)

    def test_only_workers_can_be_assigned(self):
        self.client.force_login(self.dispatcher)

        page = self.client.get(f'/dashboard/requests/{self.item.pk}/')
        self.assertContains(page, f'<option value="{self.worker.pk}">worker</option>', html=True)
        self.assertNotContains(page, f'<option value="{self.dispatcher.pk}">')

        response = self.client.post(f'/dashboard/requests/{self.item.pk}/', {
            "status": ResidentRequest.Status.ACCEPTED,
            "assignee": self.dispatcher.pk,
            "resolution_note": ""
        })
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.assignee)

    def test_accepting_needs_a_worker(self):
        self.client.force_login(self.dispatcher)

        response = self.client.post(f'/dashboard/requests/{self.item.pk}/', {
            "status": ResidentRequest.Status.ACCEPTED,
            "assignee": "",
            "resolution_note": ""
        })

        self.assertContains(response, "орындаушы жұмысшыны таңдаңыз")
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, ResidentRequest.Status.NEW)


class StaleFormTests(TestCase):

    def test_form_from_a_stale_tab_explains_what_happened(self):
        client = self.client_class(enforce_csrf_checks=True)
        worker = create_worker()
        client.force_login(worker)
        client.get('/tasks/')

        response = client.post('/tasks/1/start/', {"csrfmiddlewaretoken": "stale"})

        self.assertContains(response, "Бет ескіріп қалды", status_code=403)
        self.assertContains(response, 'href="/tasks/"', status_code=403)


def model_answer(**overrides):
    answer = {
        "is_waste_photo": True,
        "waste_type": "household",
        "volume": "medium",
        "hazard": "none",
        "hazard_reason": "",
        "suggested_category": "overflow",
        "confidence": 0.92,
        "summary": "Контейнер толы, айналасында тұрмыстық қоқыс қаптары жатыр.",
    }
    answer.update(overrides)
    return answer, "claude-opus-5-5"


def photo_upload(name="photo.png", size=(2400, 1200)):
    buffer = BytesIO()
    Image.new("RGB", size, (120, 140, 90)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), "image/png")


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(),
    ANTHROPIC_API_KEY="test-key",
    GEMINI_API_KEY="",
    AI_ANALYSIS_SYNC=True
)
class AiAnalysisTests(TestCase):

    def submit(self, **fields):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post('/', {
                "category": ResidentRequest.Category.OVERFLOW,
                "address": "Абай даңғылы",
                "photo": photo_upload(),
                **fields
            })
        return ResidentRequest.objects.get()

    def test_photo_is_shrunk_to_what_claude_reads_in_full(self):
        item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OVERFLOW,
            address="Абай",
            photo=photo_upload()
        )

        data = base64.standard_b64decode(ai.prepare_image(item.photo))
        image = Image.open(BytesIO(data))

        self.assertEqual(image.format, "JPEG")
        self.assertEqual(max(image.size), ai.MAX_IMAGE_SIDE)

    def test_a_new_request_with_a_photo_is_analysed(self):
        with mock.patch("sensor.ai.ask_model", return_value=model_answer()) as ask:
            item = self.submit()

        ask.assert_called_once()
        analysis = item.analysis
        self.assertEqual(analysis.state, RequestAnalysis.State.DONE)
        self.assertEqual(analysis.waste_type, "household")
        self.assertFalse(analysis.needs_review)

    def test_danger_and_disagreement_are_flagged_for_review(self):
        answer = model_answer(hazard="high", hazard_reason="Шприцтер көрінеді", suggested_category="littered")

        with mock.patch("sensor.ai.ask_model", return_value=answer):
            item = self.submit()

        analysis = item.analysis
        self.assertTrue(analysis.needs_review)
        self.assertIn("қауіпті", analysis.review_reason)
        self.assertIn("санат", analysis.review_reason)

        self.client.force_login(create_user())
        self.assertContains(self.client.get('/dashboard/requests/'), "Қауіпті")
        detail = self.client.get(f'/dashboard/requests/{item.pk}/')
        self.assertContains(detail, "Шприцтер көрінеді")
        self.assertContains(detail, "арнайы бригада")

    def test_dispatcher_can_accept_the_suggested_category(self):
        with mock.patch("sensor.ai.ask_model", return_value=model_answer(suggested_category="littered")):
            item = self.submit()

        self.client.force_login(create_user())
        self.client.post(f'/dashboard/requests/{item.pk}/accept-category/')

        item.refresh_from_db()
        self.assertEqual(item.category, ResidentRequest.Category.LITTERED)
        self.assertFalse(item.analysis.needs_review)
        self.assertIn("AI ұсынысымен", item.events.last().note)

    def test_a_failure_never_blocks_the_resident(self):
        with mock.patch("sensor.ai.ask_model", side_effect=ai.AnalysisError("timeout")):
            item = self.submit()

        self.assertEqual(item.status, ResidentRequest.Status.NEW)
        self.assertEqual(item.analysis.state, RequestAnalysis.State.FAILED)
        self.assertEqual(item.analysis.error, "timeout")

    @override_settings(ANTHROPIC_API_KEY="")
    def test_without_a_key_the_analysis_is_skipped(self):
        with mock.patch("sensor.ai.ask_model") as ask:
            item = self.submit()

        ask.assert_not_called()
        self.assertEqual(item.analysis.state, RequestAnalysis.State.SKIPPED)

    def test_request_without_a_photo_is_not_sent_to_the_model(self):
        with mock.patch("sensor.ai.ask_model") as ask, self.captureOnCommitCallbacks(execute=True):
            self.client.post('/', {
                "category": ResidentRequest.Category.LITTERED,
                "address": "Сайран"
            })

        ask.assert_not_called()

    def test_only_dispatchers_can_rerun_the_analysis(self):
        item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OVERFLOW,
            address="Абай",
            photo=photo_upload()
        )
        self.client.force_login(create_worker())

        with mock.patch("sensor.ai.ask_model") as ask:
            response = self.client.post(f'/dashboard/requests/{item.pk}/analyze/')

        self.assertRedirects(response, '/tasks/', fetch_redirect_response=False)
        ask.assert_not_called()


class FakeHttpResponse:

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self, *args):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def gemini_reply(answer, model="gemini-3.5-flash-lite"):
    return FakeHttpResponse({
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": json.dumps(answer)}]},
        }],
        "modelVersion": model,
    })


@override_settings(
    GEMINI_API_KEY="gemini-test",
    ANTHROPIC_API_KEY="claude-test",
    GEMINI_MODELS=["busy-model", "spare-model"]
)
class GeminiProviderTests(TestCase):

    def setUp(self):
        self.item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.OVERFLOW,
            address="Абай"
        )

    def test_a_gemini_key_selects_gemini(self):
        self.assertEqual(ai.provider(), "gemini")

        with override_settings(GEMINI_API_KEY=""):
            self.assertEqual(ai.provider(), "anthropic")

        with override_settings(GEMINI_API_KEY="", ANTHROPIC_API_KEY=""):
            self.assertIsNone(ai.provider())

    def test_a_busy_model_falls_back_to_the_next_one(self):
        busy = urllib.error.HTTPError("url", 503, "busy", {}, None)
        answer = model_answer()[0]

        with mock.patch("sensor.ai.urllib.request.urlopen", side_effect=[busy, gemini_reply(answer)]) as urlopen:
            parsed, model = ai.ask_model("aW1n", self.item)

        self.assertEqual(parsed["waste_type"], "household")
        self.assertEqual(model, "gemini-3.5-flash-lite")
        second = urlopen.call_args_list[1].args[0]
        self.assertIn("spare-model", second.full_url)
        self.assertEqual(second.get_header("X-goog-api-key"), "gemini-test")

    def test_all_models_busy_is_reported(self):
        busy = urllib.error.HTTPError("url", 429, "quota", {}, None)

        with mock.patch("sensor.ai.urllib.request.urlopen", side_effect=[busy, busy]):
            with self.assertRaisesRegex(ai.AnalysisError, "кейінірек"):
                ai.ask_model("aW1n", self.item)

    def test_a_blocked_answer_is_an_error(self):
        blocked = FakeHttpResponse({"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]})

        with mock.patch("sensor.ai.urllib.request.urlopen", return_value=blocked):
            with self.assertRaisesRegex(ai.AnalysisError, "SAFETY"):
                ai.ask_model("aW1n", self.item)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DuplicateRequestTests(TestCase):

    # About 30 m apart; 0.003 degrees of latitude is about 330 m
    HERE = {"latitude": "43.238900", "longitude": "76.889700"}
    NEXT_DOOR = {"latitude": "43.239150", "longitude": "76.889750"}
    FAR = {"latitude": "43.241900", "longitude": "76.889700"}

    def report(self, category=ResidentRequest.Category.OVERFLOW, **point):
        self.client.post('/', {"category": category, "address": "Абай даңғылы", **point})
        return ResidentRequest.objects.order_by('-id').first()

    def test_a_nearby_report_of_the_same_problem_is_merged(self):
        first = self.report(**self.HERE)
        second = self.report(category=ResidentRequest.Category.NOT_COLLECTED, **self.NEXT_DOOR)

        self.assertEqual(second.parent, first)
        self.assertIn(f"{first.code} өтінішімен біріктірілді", second.events.last().note)
        self.assertIn(second.code, first.events.last().note)

        self.client.force_login(create_user())
        listing = self.client.get('/dashboard/requests/')
        self.assertContains(listing, first.code)
        self.assertNotContains(listing, f'>{second.code}</a>')
        self.assertContains(listing, "+1")
        self.assertContains(self.client.get('/dashboard/'), 'count-badge">1<')

    def test_reports_that_are_not_duplicates_stay_separate(self):
        first = self.report(**self.HERE)

        self.assertIsNone(self.report(**self.FAR).parent)
        self.assertIsNone(self.report(category=ResidentRequest.Category.LITTERED, **self.NEXT_DOOR).parent)
        self.assertIsNone(self.report(category=ResidentRequest.Category.OTHER, **self.NEXT_DOOR).parent)
        self.assertIsNone(self.report().parent)

        first.status = ResidentRequest.Status.DONE
        first.save()
        ResidentRequest.objects.exclude(pk=first.pk).update(status=ResidentRequest.Status.DONE)
        self.assertIsNone(self.report(**self.NEXT_DOOR).parent)

    def test_a_late_report_joins_the_job_already_in_progress(self):
        worker = create_worker()
        first = self.report(**self.HERE)
        first.status = ResidentRequest.Status.IN_PROGRESS
        first.assignee = worker
        first.save()

        second = self.report(**self.NEXT_DOOR)

        self.assertEqual(second.status, ResidentRequest.Status.IN_PROGRESS)
        self.assertEqual(second.assignee, worker)

    def test_finishing_the_primary_finishes_every_merged_report(self):
        worker = create_worker()
        first = self.report(**self.HERE)
        second = self.report(**self.NEXT_DOOR)
        first.status = ResidentRequest.Status.ACCEPTED
        first.assignee = worker
        first.save()

        self.client.force_login(worker)
        self.client.post(f'/tasks/{first.pk}/complete/', {
            f"task{first.pk}-result_photo": SimpleUploadedFile("after.png", PNG_BYTES, "image/png"),
            f"task{first.pk}-resolution_note": "Тазартылды"
        })

        second.refresh_from_db()
        self.assertEqual(second.status, ResidentRequest.Status.DONE)
        self.assertEqual(second.resolution_note, "Тазартылды")
        first.refresh_from_db()
        self.assertEqual(second.result_photo.name, first.result_photo.name)

        self.client.logout()
        page = self.client.get('/otinish/', {"code": second.code})
        self.assertContains(page, "Орындалды")
        self.assertContains(page, "Өтініштер біріктірілді")

    def test_dispatcher_can_split_a_wrong_merge(self):
        first = self.report(**self.HERE)
        second = self.report(**self.NEXT_DOOR)
        self.client.force_login(create_user())

        detail = self.client.get(f'/dashboard/requests/{second.pk}/')
        self.assertContains(detail, "негізгі")
        self.assertNotContains(detail, 'name="status"')

        self.client.post(f'/dashboard/requests/{second.pk}/unmerge/')

        second.refresh_from_db()
        self.assertIsNone(second.parent)
        self.assertEqual(second.status, ResidentRequest.Status.NEW)
        self.assertIn(second.code, first.events.last().note)


def create_manager(district_slug, username="manager"):
    user = get_user_model().objects.create_user(username=username, password="pass-12345-x")
    user.groups.add(Group.objects.get(name=DISPATCHERS))
    StaffProfile.objects.create(user=user, district=District.objects.get(slug=district_slug))
    return user


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DistrictTests(TestCase):

    # Points inside two districts, from the bundled OpenStreetMap boundaries
    ALMALY = {"latitude": "43.238900", "longitude": "76.889700"}
    MEDEU = {"latitude": "43.263680", "longitude": "76.955200"}

    def report(self, **point):
        self.client.post('/', {
            "category": ResidentRequest.Category.LITTERED,
            "address": "Алматы",
            **point
        })
        return ResidentRequest.objects.order_by('-id').first()

    def test_all_eight_districts_are_loaded(self):
        self.assertEqual(District.objects.count(), 8)

    def test_each_district_gets_one_test_container_in_its_own_district(self):
        call_command('add_city_containers', stdout=StringIO())

        self.assertEqual(
            sorted(Container.objects.values_list('district__slug', flat=True)),
            sorted(District.objects.values_list('slug', flat=True))
        )

        for container in Container.objects.all():
            self.assertEqual(
                container.district.name,
                container.location.split(",")[0],
                container.name
            )

    def test_a_request_gets_its_district_from_the_map_point(self):
        self.assertEqual(self.report(**self.MEDEU).district.slug, "medeu")
        self.assertIsNone(self.report().district)
        self.assertIsNone(self.report(latitude="43.20", longitude="76.62").district)

    def test_a_district_manager_sees_only_their_district(self):
        almaly = self.report(**self.ALMALY)
        medeu = self.report(**self.MEDEU)
        Container.objects.create(name="A", location="x", latitude=43.2389, longitude=76.8897,
                                 district=District.objects.get(slug="almaly"))
        Container.objects.create(name="M", location="x", latitude=43.2637, longitude=76.9552,
                                 district=District.objects.get(slug="medeu"))
        self.client.force_login(create_manager("medeu"))

        listing = self.client.get('/dashboard/requests/')
        self.assertContains(listing, medeu.code)
        self.assertNotContains(listing, almaly.code)
        self.assertContains(listing, "Медеу ауданы")

        self.assertEqual(self.client.get(f'/dashboard/requests/{almaly.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/dashboard/requests/{medeu.pk}/').status_code, 200)

        names = [c["name"] for c in self.client.get('/api/containers/').json()["containers"]]
        self.assertEqual(names, ["M"])

    def test_a_city_dispatcher_sees_everything_and_can_filter(self):
        almaly = self.report(**self.ALMALY)
        medeu = self.report(**self.MEDEU)
        self.client.force_login(create_user())

        everything = self.client.get('/dashboard/requests/')
        self.assertContains(everything, almaly.code)
        self.assertContains(everything, medeu.code)

        filtered = self.client.get('/dashboard/requests/', {"district": "almaly"})
        self.assertContains(filtered, almaly.code)
        self.assertNotContains(filtered, medeu.code)

    def test_a_request_can_only_go_to_its_districts_workers(self):
        medeu = self.report(**self.MEDEU)
        local = create_worker("local")
        StaffProfile.objects.create(user=local, district=District.objects.get(slug="medeu"))
        other = create_worker("other")
        StaffProfile.objects.create(user=other, district=District.objects.get(slug="almaly"))
        free = create_worker("free")
        self.client.force_login(create_manager("medeu"))

        page = self.client.get(f'/dashboard/requests/{medeu.pk}/')

        self.assertContains(page, f'<option value="{local.pk}">local</option>', html=True)
        self.assertNotContains(page, f'<option value="{free.pk}">')
        self.assertNotContains(page, f'<option value="{other.pk}">')


class RetiredContainerTests(TestCase):

    def test_an_unused_second_container_is_removed_but_one_with_data_is_kept(self):
        unused = Container.objects.create(name="Container #10", location="x")
        used = Container.objects.create(name="Container #9", location="x")
        add_reading(used, 40)

        call_command('add_city_containers', stdout=StringIO())

        self.assertFalse(Container.objects.filter(pk=unused.pk).exists())
        self.assertTrue(Container.objects.filter(pk=used.pk).exists())


class DistrictMapTests(TestCase):

    def test_boundaries_are_served_with_the_managers_district_marked(self):
        self.client.force_login(create_manager("medeu"))

        features = self.client.get('/api/districts/').json()["features"]

        self.assertEqual(len(features), 8)
        self.assertEqual([f["properties"]["slug"] for f in features if f["properties"]["mine"]], ["medeu"])
        self.assertTrue(all(f["geometry"]["type"] in ("Polygon", "MultiPolygon") for f in features))

    def test_workers_cannot_read_the_boundaries(self):
        self.client.force_login(create_worker())

        self.assertEqual(self.client.get('/api/districts/').status_code, 403)


def place_worker(username, district_slug, latitude=None, longitude=None, minutes_ago=1):
    worker = create_worker(username)
    StaffProfile.objects.create(
        user=worker,
        district=District.objects.get(slug=district_slug),
        latitude=latitude,
        longitude=longitude,
        located_at=timezone.now() - timedelta(minutes=minutes_ago) if latitude is not None else None
    )
    return worker


class NearestWorkerTests(TestCase):

    def setUp(self):
        self.manager = create_manager("medeu")
        self.item = ResidentRequest.objects.create(
            category=ResidentRequest.Category.LITTERED,
            address="Көк базар",
            latitude=43.26368,
            longitude=76.9552,
            district=District.objects.get(slug="medeu")
        )

    def test_location_is_stored_from_the_tasks_page(self):
        worker = place_worker("w", "medeu")
        self.client.force_login(worker)

        response = self.client.post(
            '/tasks/location/',
            {"latitude": 43.25, "longitude": 76.95},
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        worker.staff_profile.refresh_from_db()
        self.assertEqual(worker.staff_profile.latitude, 43.25)
        self.assertIsNotNone(worker.staff_profile.located_at)

        for bad in ({"latitude": 99, "longitude": 1}, {"latitude": "x"}, {}):
            self.assertEqual(
                self.client.post('/tasks/location/', bad, content_type='application/json').status_code,
                400
            )

    def test_only_workers_send_a_location(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            '/tasks/location/',
            {"latitude": 43.25, "longitude": 76.95},
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 302)

    def test_suggestions_put_the_nearest_fresh_worker_first(self):
        place_worker("far", "medeu", 43.2300, 76.9550)
        place_worker("near", "medeu", 43.2640, 76.9560)
        place_worker("stale", "medeu", 43.2637, 76.9552, minutes_ago=120)
        place_worker("unknown", "medeu")
        place_worker("elsewhere", "almaly", 43.2637, 76.9552)

        names = [row["user"].username for row in suggest_workers(self.item)]

        self.assertEqual(names, ["near", "far", "stale", "unknown"])

    def test_assigning_a_suggested_worker_accepts_the_request(self):
        worker = place_worker("near", "medeu", 43.2640, 76.9560)
        self.client.force_login(self.manager)

        page = self.client.get(f'/dashboard/requests/{self.item.pk}/')
        self.assertContains(page, "Ең жақын")

        self.client.post(f'/dashboard/requests/{self.item.pk}/assign/', {"worker": worker.pk})

        self.item.refresh_from_db()
        self.assertEqual(self.item.assignee, worker)
        self.assertEqual(self.item.status, ResidentRequest.Status.ACCEPTED)
        self.assertIn("ұсыныс бойынша", self.item.events.last().note)

    def test_a_worker_from_another_district_cannot_be_assigned(self):
        outsider = place_worker("outsider", "almaly", 43.2640, 76.9560)
        self.client.force_login(self.manager)

        response = self.client.post(f'/dashboard/requests/{self.item.pk}/assign/', {"worker": outsider.pk})

        self.assertEqual(response.status_code, 404)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.assignee)


class SupervisionTests(TestCase):

    def test_head_dispatcher_sees_every_district_and_late_ones(self):
        create_manager("medeu", username="medeu_dispatcher")
        late = ResidentRequest.objects.create(
            category=ResidentRequest.Category.LITTERED,
            address="x",
            district=District.objects.get(slug="almaly")
        )
        ResidentRequest.objects.filter(pk=late.pk).update(created_at=timezone.now() - timedelta(hours=3))
        self.client.force_login(create_user())

        page = self.client.get('/dashboard/supervision/')

        self.assertEqual(page.status_code, 200)
        for district in District.objects.all():
            self.assertContains(page, district.name)
        self.assertContains(page, "medeu_dispatcher")
        self.assertContains(page, "3 сағ 0 мин")
        self.assertContains(page, 'class="is-late"')

    def test_a_district_manager_is_sent_back_to_the_panel(self):
        self.client.force_login(create_manager("medeu"))

        self.assertRedirects(self.client.get('/dashboard/supervision/'), '/dashboard/', fetch_redirect_response=False)


def create_admin(username="boss"):
    return get_user_model().objects.create_superuser(username=username, password="pass-12345-x")


class AdminPanelTests(TestCase):

    def setUp(self):
        self.client.force_login(create_admin())

    def test_only_superusers_open_the_panel(self):
        self.client.force_login(create_user())
        self.assertRedirects(self.client.get('/manage/'), '/dashboard/', fetch_redirect_response=False)

        self.client.logout()
        self.assertEqual(self.client.get('/manage/').status_code, 302)

    def test_pages_render(self):
        create_worker()
        create_manager("medeu")
        container = Container.objects.create(name="Bin", location="Somewhere", latitude=43.26, longitude=76.95)

        for url in ['/manage/', '/manage/staff/new/', '/manage/containers/', '/manage/containers/new/',
                    f'/manage/containers/{container.pk}/', '/manage/system/']:
            self.assertEqual(self.client.get(url).status_code, 200, url)

        page = self.client.get('/manage/').content.decode()
        self.assertIn("Аудан диспетчері", page)
        self.assertIn("Медеу ауданы", page)

    def test_creates_a_district_worker(self):
        response = self.client.post('/manage/staff/new/', {
            'username': 'aidos', 'first_name': 'Айдос', 'last_name': '',
            'role': 'worker', 'district': District.objects.get(slug='medeu').pk,
            'password1': 'Qaldyq-strong-2026', 'password2': 'Qaldyq-strong-2026', 'is_active': 'on',
        })

        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username='aidos')
        self.assertTrue(user.check_password('Qaldyq-strong-2026'))
        self.assertEqual(list(user.groups.values_list('name', flat=True)), [WORKERS])
        self.assertEqual(user.staff_profile.district.slug, 'medeu')

    def test_district_roles_need_a_district(self):
        response = self.client.post('/manage/staff/new/', {
            'username': 'nodistrict', 'role': 'district',
            'password1': 'Qaldyq-strong-2026', 'password2': 'Qaldyq-strong-2026',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "аудан таңдаңыз")
        self.assertFalse(get_user_model().objects.filter(username='nodistrict').exists())

    def test_rejects_taken_login_and_weak_or_mismatched_password(self):
        create_worker("taken")

        response = self.client.post('/manage/staff/new/', {
            'username': 'TAKEN', 'role': 'head', 'password1': '123', 'password2': '1234',
        })

        self.assertContains(response, "логин бос емес")
        self.assertContains(response, "сәйкес келмейді")

    def test_changing_role_moves_groups_and_district(self):
        manager = create_manager("medeu")

        self.client.post(f'/manage/staff/{manager.pk}/', {'role': 'head', 'district': District.objects.get(slug='medeu').pk})

        manager.refresh_from_db()
        self.assertEqual(list(manager.groups.values_list('name', flat=True)), [DISPATCHERS])
        self.assertIsNone(StaffProfile.objects.get(user=manager).district)
        self.assertFalse(manager.is_active)

        self.client.post(f'/manage/staff/{manager.pk}/', {
            'role': 'worker', 'district': District.objects.get(slug='almaly').pk, 'is_active': 'on',
        })

        manager.refresh_from_db()
        self.assertEqual(list(manager.groups.values_list('name', flat=True)), [WORKERS])
        self.assertEqual(StaffProfile.objects.get(user=manager).district.slug, 'almaly')
        self.assertTrue(manager.is_active)

    def test_superusers_are_not_editable_here(self):
        other = create_admin("other")

        self.client.post(f'/manage/staff/{other.pk}/', {'role': 'worker', 'district': District.objects.first().pk})

        other.refresh_from_db()
        self.assertTrue(other.is_active)
        self.assertFalse(other.groups.exists())

    def test_resets_a_password(self):
        worker = create_worker()

        response = self.client.post(f'/manage/staff/{worker.pk}/password/', {
            'password1': 'Brand-new-pass-77', 'password2': 'Brand-new-pass-77',
        })

        self.assertEqual(response.status_code, 302)
        worker.refresh_from_db()
        self.assertTrue(worker.check_password('Brand-new-pass-77'))

    def test_container_gets_its_district_from_the_map_point(self):
        response = self.client.post('/manage/containers/new/', {
            'name': 'Container #11', 'location': 'Көк базар', 'latitude': '43.263680', 'longitude': '76.955200',
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Container.objects.get(name='Container #11').district.slug, 'medeu')

    def test_container_needs_a_map_point(self):
        response = self.client.post('/manage/containers/new/', {'name': 'X', 'location': 'Y'})

        self.assertContains(response, "орнын белгілеңіз")

    def test_only_empty_containers_can_be_deleted(self):
        used = Container.objects.create(name="Used", location="A", latitude=43.26, longitude=76.95)
        add_reading(used, 40)
        empty = Container.objects.create(name="Empty", location="B", latitude=43.26, longitude=76.95)

        self.client.post(f'/manage/containers/{used.pk}/delete/')
        self.client.post(f'/manage/containers/{empty.pk}/delete/')

        self.assertTrue(Container.objects.filter(pk=used.pk).exists())
        self.assertFalse(Container.objects.filter(pk=empty.pk).exists())


class LiveWorkersTests(TestCase):

    def setUp(self):
        self.near = place_worker("near", "medeu", 43.2640, 76.9560)
        self.near.first_name, self.near.last_name = "Берік", "Сапаров"
        self.near.save()
        self.idle = place_worker("idle", "medeu")
        self.stale = place_worker("stale", "medeu", 43.25, 76.94, minutes_ago=40)
        self.other = place_worker("other", "almaly", 43.25, 76.90)

    def test_positions_build_a_path_and_old_points_are_dropped(self):
        from .models import WorkerPosition

        WorkerPosition.objects.create(
            user=self.near, latitude=43.0, longitude=76.0, created_at=timezone.now() - timedelta(hours=30)
        )
        self.client.force_login(self.near)

        for latitude in (43.2600, 43.2610):
            self.client.post('/tasks/location/', {"latitude": latitude, "longitude": 76.956, "accuracy": 12},
                             content_type='application/json')

        points = list(WorkerPosition.objects.filter(user=self.near).values_list('latitude', 'accuracy'))
        self.assertEqual(points, [(43.26, 12.0), (43.261, 12.0)])

    def test_district_manager_sees_only_their_workers(self):
        self.client.force_login(create_manager("medeu"))

        names = {row['id'] for row in self.client.get('/api/workers/').json()['workers']}

        self.assertEqual(names, {self.near.pk, self.idle.pk, self.stale.pk})

    def test_head_dispatcher_sees_every_district(self):
        self.client.force_login(create_user())

        self.assertEqual(len(self.client.get('/api/workers/').json()['workers']), 4)

    def test_workers_and_residents_cannot_read_positions(self):
        self.client.force_login(self.near)
        self.assertEqual(self.client.get('/api/workers/').status_code, 403)

        self.client.logout()
        self.assertIn(self.client.get('/api/workers/').status_code, (401, 403))

    def test_state_trail_and_current_job(self):
        from .models import WorkerPosition

        now = timezone.now()
        for minutes, latitude, accuracy in ((90, 43.20, 10), (20, 43.2620, 10), (10, 43.2630, 500), (5, 43.2635, 8)):
            WorkerPosition.objects.create(user=self.near, latitude=latitude, longitude=76.956, accuracy=accuracy,
                                          created_at=now - timedelta(minutes=minutes))

        medeu = District.objects.get(slug="medeu")
        ResidentRequest.objects.create(category=ResidentRequest.Category.LITTERED, address="Ескі", district=medeu,
                                       latitude=43.27, longitude=76.96, assignee=self.near,
                                       status=ResidentRequest.Status.ACCEPTED)
        started = ResidentRequest.objects.create(category=ResidentRequest.Category.OVERFLOW, address="Көк базар",
                                                 district=medeu, latitude=43.26368, longitude=76.9552,
                                                 assignee=self.near, status=ResidentRequest.Status.IN_PROGRESS)

        self.client.force_login(create_manager("medeu"))
        rows = {row['id']: row for row in self.client.get('/api/workers/').json()['workers']}

        near = rows[self.near.pk]
        self.assertEqual(near['state'], "live")
        self.assertEqual(near['initials'], "БС")
        # The 90-minute-old point is outside the window and the 500 m one is too imprecise
        self.assertEqual(near['trail'], [[43.262, 76.956], [43.2635, 76.956]])
        self.assertGreater(near['walked'], 100)
        self.assertEqual(near['task']['code'], started.code)
        self.assertEqual(near['taskCount'], 2)
        self.assertIsNotNone(near['task']['distance'])

        self.assertEqual(rows[self.idle.pk]['state'], "unknown")
        self.assertIsNone(rows[self.idle.pk]['latitude'])
        self.assertEqual(rows[self.stale.pk]['state'], "offline")
        self.assertIsNone(rows[self.stale.pk]['task'])
