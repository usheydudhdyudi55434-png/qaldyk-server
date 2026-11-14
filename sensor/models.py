import os
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


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
        verbose_name=_("Аудан")
    )

    # A worker's last position, sent from the tasks page while it is open
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    located_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} — {self.district or _('бүкіл қала')}"


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
        NOT_COLLECTED = "not_collected", _("Қоқыс шығарылмаған")
        OVERFLOW = "overflow", _("Контейнер толып кеткен")
        LITTERED = "littered", _("Аумақ ластанған")
        OTHER = "other", _("Басқа мәселе")

    class Status(models.TextChoices):
        NEW = "new", _("Жаңа")
        ACCEPTED = "accepted", _("Қабылданды")
        IN_PROGRESS = "in_progress", _("Орындалуда")
        DONE = "done", _("Орындалды")
        REJECTED = "rejected", _("Қабылданбады")

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

    # The Telegram chat that hears about this request's progress
    telegram_chat = models.ForeignKey(
        "TelegramChat",
        on_delete=models.SET_NULL,
        related_name="requests",
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
        PENDING = "pending", _("Талдануда")
        DONE = "done", _("Талданды")
        FAILED = "failed", _("Қате")
        SKIPPED = "skipped", _("Өткізілді")

    class WasteType(models.TextChoices):
        HOUSEHOLD = "household", _("Тұрмыстық қоқыс")
        RECYCLABLES = "recyclables", _("Қайта өңделетін (пластик, қағаз, шыны)")
        CONSTRUCTION = "construction", _("Құрылыс қалдығы")
        BULKY = "bulky", _("Ірі габаритті (жиһаз, техника)")
        GREEN = "green", _("Бақ қалдығы (жапырақ, бұтақ)")
        HAZARDOUS = "hazardous", _("Қауіпті қалдық")
        MIXED = "mixed", _("Аралас")
        UNCLEAR = "unclear", _("Анықталмады")

    class Volume(models.TextChoices):
        SMALL = "small", _("Аз (бірнеше қап)")
        MEDIUM = "medium", _("Орташа (бір контейнерге дейін)")
        LARGE = "large", _("Көп (техника керек)")

    class Hazard(models.TextChoices):
        NONE = "none", _("Қауіп белгісі жоқ")
        POSSIBLE = "possible", _("Қауіп болуы мүмкін")
        HIGH = "high", _("Қауіпті")

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


class TelegramChat(models.Model):
    """A resident talking to the Telegram bot, with the request they are filling in."""

    chat_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=64, blank=True)
    first_name = models.CharField(max_length=128, blank=True)

    # Which question the bot is waiting an answer for, and the answers so far
    state = models.CharField(max_length=20, blank=True)
    draft = models.JSONField(default=dict, blank=True)

    # The homeowners' association the resident's eco-points count towards
    osi = models.ForeignKey(
        "Osi",
        on_delete=models.SET_NULL,
        related_name="chats",
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"@{self.username}" if self.username else str(self.chat_id)


def new_voucher_code():
    return "ECO-" + "".join(secrets.choice(REQUEST_CODE_ALPHABET) for _ in range(6))


class Osi(models.Model):
    """A homeowners' association (ОСИ); its residents' eco-points add up in a public rating."""

    name = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True)
    district = models.ForeignKey(
        District,
        on_delete=models.SET_NULL,
        related_name="osis",
        null=True,
        blank=True
    )
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class EcoPoints(models.Model):
    """One line of a resident's eco-points: earned for a confirmed request, or spent on a reward."""

    chat = models.ForeignKey(
        TelegramChat,
        on_delete=models.CASCADE,
        related_name="points"
    )
    request = models.ForeignKey(
        ResidentRequest,
        on_delete=models.SET_NULL,
        related_name="eco_points",
        null=True,
        blank=True
    )
    amount = models.IntegerField()
    reason = models.CharField(max_length=300)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A request pays out once, however often its status is saved
            models.UniqueConstraint(
                fields=["request"],
                condition=models.Q(amount__gt=0),
                name="one_award_per_request"
            )
        ]

    def __str__(self):
        return f"{self.chat}: {self.amount:+d}"


class Reward(models.Model):
    """Something a partner gives for eco-points: a discount, a coffee, a ticket."""

    title = models.CharField(max_length=120)
    partner = models.CharField(max_length=120)
    description = models.CharField(max_length=300, blank=True)
    cost = models.PositiveIntegerField()

    # Empty means unlimited
    stock = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["cost", "title"]

    def __str__(self):
        return f"{self.title} ({self.partner})"


class Redemption(models.Model):
    """A reward a resident took; the partner checks the voucher code."""

    chat = models.ForeignKey(
        TelegramChat,
        on_delete=models.CASCADE,
        related_name="redemptions"
    )
    reward = models.ForeignKey(
        Reward,
        on_delete=models.PROTECT,
        related_name="redemptions"
    )
    cost = models.PositiveIntegerField()
    code = models.CharField(max_length=12, unique=True, default=new_voucher_code, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.code
