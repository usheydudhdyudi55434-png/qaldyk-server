import os
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class District(models.Model):
    """An Almaty district with its OpenStreetMap boundary (GeoJSON geometry)."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=100)
    boundary = models.JSONField()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StaffProfile(models.Model):
    """A dispatcher with a district manages only that district; without one, the whole city."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="staff_profile"
    )
    district = models.ForeignKey(
        District,
        on_delete=models.SET_NULL,
        related_name="staff",
        null=True,
        blank=True,
        verbose_name="Аудан"
    )

    # A worker's last position, sent from the tasks page while it is open
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    located_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} — {self.district or 'бүкіл қала'}"


class Container(models.Model):
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=200)
    district = models.ForeignKey(
        District,
        on_delete=models.SET_NULL,
        related_name="containers",
        null=True,
        blank=True
    )

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class SensorReading(models.Model):
    container = models.ForeignKey(
        Container,
        on_delete=models.CASCADE,
        related_name="readings",
        null=True,
        blank=True
    )

    distance = models.FloatField()
    fill_level = models.FloatField()
    status = models.CharField(max_length=20)

    # Fire risk signs; empty on devices without temperature or gas sensors
    temperature = models.FloatField(null=True, blank=True)
    gas_level = models.IntegerField(null=True, blank=True)
    fire_risk = models.CharField(max_length=10, blank=True, default="")
    fire_note = models.CharField(max_length=200, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.container} - {self.fill_level}% - {self.status}"


# Codes skip look-alike characters (0/O, 1/I/L) so residents can read them out
REQUEST_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_request_code():
    return "Q-" + "".join(secrets.choice(REQUEST_CODE_ALPHABET) for _ in range(6))


def request_photo_path(instance, filename):
    # The original name can hold personal details, so only the extension is kept
    extension = os.path.splitext(filename)[1].lower()
    return f"requests/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{extension}"


class ResidentRequest(models.Model):

    class Category(models.TextChoices):
        NOT_COLLECTED = "not_collected", "Қоқыс шығарылмаған"
        OVERFLOW = "overflow", "Контейнер толып кеткен"
        LITTERED = "littered", "Аумақ ластанған"
        OTHER = "other", "Басқа мәселе"

    class Status(models.TextChoices):
        NEW = "new", "Жаңа"
        ACCEPTED = "accepted", "Қабылданды"
        IN_PROGRESS = "in_progress", "Орындалуда"
        DONE = "done", "Орындалды"
        REJECTED = "rejected", "Қабылданбады"

    code = models.CharField(
        max_length=12,
        unique=True,
        default=new_request_code,
        editable=False
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    address = models.CharField(max_length=255)
    description = models.TextField(max_length=2000, blank=True)
    photo = models.FileField(upload_to=request_photo_path, blank=True)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    district = models.ForeignKey(
        District,
        on_delete=models.SET_NULL,
        related_name="requests",
        null=True,
        blank=True
    )

    # The nearest container and what its sensor showed when the request came in
    container = models.ForeignKey(
        Container,
        on_delete=models.SET_NULL,
        related_name="requests",
        null=True,
        blank=True
    )
    container_distance = models.FloatField(null=True, blank=True)
    sensor_fill_level = models.FloatField(null=True, blank=True)
    sensor_reading_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_requests",
        null=True,
        blank=True
    )
    resolution_note = models.TextField(max_length=1000, blank=True)

    # The worker's photo of the finished job
    result_photo = models.FileField(upload_to=request_photo_path, blank=True)

    # A later report of the same problem points at the first one and follows it
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="duplicates",
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} — {self.get_category_display()}"


class RequestEvent(models.Model):
    """One step in a request's history: who changed what, and when."""

    request = models.ForeignKey(
        ResidentRequest,
        on_delete=models.CASCADE,
        related_name="events"
    )
    status = models.CharField(max_length=20, choices=ResidentRequest.Status.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="request_events",
        null=True,
        blank=True
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.request.code}: {self.get_status_display()}"


class RequestAnalysis(models.Model):
    """What the AI saw in the resident's photo. A suggestion; a dispatcher decides."""

    class State(models.TextChoices):
        PENDING = "pending", "Талдануда"
        DONE = "done", "Талданды"
        FAILED = "failed", "Қате"
        SKIPPED = "skipped", "Өткізілді"

    class WasteType(models.TextChoices):
        HOUSEHOLD = "household", "Тұрмыстық қоқыс"
        RECYCLABLES = "recyclables", "Қайта өңделетін (пластик, қағаз, шыны)"
        CONSTRUCTION = "construction", "Құрылыс қалдығы"
        BULKY = "bulky", "Ірі габаритті (жиһаз, техника)"
        GREEN = "green", "Бақ қалдығы (жапырақ, бұтақ)"
        HAZARDOUS = "hazardous", "Қауіпті қалдық"
        MIXED = "mixed", "Аралас"
        UNCLEAR = "unclear", "Анықталмады"

    class Volume(models.TextChoices):
        SMALL = "small", "Аз (бірнеше қап)"
        MEDIUM = "medium", "Орташа (бір контейнерге дейін)"
        LARGE = "large", "Көп (техника керек)"

    class Hazard(models.TextChoices):
        NONE = "none", "Қауіп белгісі жоқ"
        POSSIBLE = "possible", "Қауіп болуы мүмкін"
        HIGH = "high", "Қауіпті"

    request = models.OneToOneField(
        ResidentRequest,
        on_delete=models.CASCADE,
        related_name="analysis"
    )
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)

    is_waste_photo = models.BooleanField(null=True, blank=True)
    waste_type = models.CharField(max_length=20, choices=WasteType.choices, blank=True)
    volume = models.CharField(max_length=10, choices=Volume.choices, blank=True)
    hazard = models.CharField(max_length=10, choices=Hazard.choices, blank=True)
    hazard_reason = models.CharField(max_length=300, blank=True)
    suggested_category = models.CharField(
        max_length=20,
        choices=ResidentRequest.Category.choices,
        blank=True
    )
    confidence = models.FloatField(null=True, blank=True)
    summary = models.TextField(max_length=1000, blank=True)

    # Set when a person should look before acting on the suggestion
    needs_review = models.BooleanField(default=False)
    review_reason = models.CharField(max_length=300, blank=True)

    model = models.CharField(max_length=50, blank=True)
    error = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.request.code}: {self.get_state_display()}"



class WorkerPosition(models.Model):
    """One point of a worker's path, so dispatchers can see where they have been."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="positions"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()

    # GPS accuracy in meters, as reported by the phone
    accuracy = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["user", "created_at"])]

    def __str__(self):
        return f"{self.user} @ {self.created_at:%H:%M}"
