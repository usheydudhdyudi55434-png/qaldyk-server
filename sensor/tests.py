import tempfile
from datetime import UTC, datetime, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import fire
from .models import Container, ResidentRequest, SensorReading
from .residents import sensor_clearance, sensor_verdict
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
