"""Links a resident's request to the nearest container and its sensor reading."""

import math
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from .models import Container, RequestEvent, ResidentRequest
from .roles import WORKERS

# A request further than this from every container is not linked to one
NEAREST_CONTAINER_METERS = 500

# The fill level at which a sensor confirms an overflow complaint
CONFIRM_FILL_LEVEL = 80

# After a request, a fall from full to this level means the bin was emptied
CLEARED_FILL_LEVEL = 30

EARTH_RADIUS_METERS = 6371000

# A new report this close to an open one, about the same kind of problem, is a duplicate
DUPLICATE_METERS = 50
DUPLICATE_DAYS = 7

# Categories that describe the same job on the ground; "other" is never merged
PROBLEM_GROUPS = {
    ResidentRequest.Category.OVERFLOW: "bin",
    ResidentRequest.Category.NOT_COLLECTED: "bin",
    ResidentRequest.Category.LITTERED: "litter",
}

# A worker's position older than this is shown but not trusted for "nearest"
LOCATION_FRESH_MINUTES = 15

ACTIVE_STATUSES = (
    ResidentRequest.Status.ACCEPTED,
    ResidentRequest.Status.IN_PROGRESS,
)

OPEN_STATUSES = (
    ResidentRequest.Status.NEW,
    ResidentRequest.Status.ACCEPTED,
    ResidentRequest.Status.IN_PROGRESS,
)


def distance_meters(lat1, lon1, lat2, lon2):

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )

    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


def nearest_container(latitude, longitude):
    """Returns (container, meters) within NEAREST_CONTAINER_METERS, or (None, None)."""

    best = (None, None)

    located = Container.objects.filter(
        latitude__isnull=False,
        longitude__isnull=False
    )

    for container in located:
        meters = distance_meters(
            latitude, longitude,
            container.latitude, container.longitude
        )

        if meters <= NEAREST_CONTAINER_METERS and (best[1] is None or meters < best[1]):
            best = (container, meters)

    return best


def attach_sensor_snapshot(request):
    """Fills in the nearest container and its latest reading before the first save."""

    if request.latitude is None or request.longitude is None:
        return

    container, meters = nearest_container(request.latitude, request.longitude)

    if container is None:
        return

    request.container = container
    request.container_distance = round(meters)

    reading = container.readings.order_by('-created_at').first()

    if reading:
        request.sensor_fill_level = reading.fill_level
        request.sensor_reading_at = reading.created_at


def eligible_workers(request):
    """Workers who may take this request: those attached to its district.

    A request without a district (no map point) can go to any worker.
    """

    workers = get_user_model().objects.filter(is_active=True, groups__name=WORKERS)

    if request.district_id:
        workers = workers.filter(staff_profile__district_id=request.district_id)

    return workers.order_by("username")


def distance_label(meters):
    return f"{round(meters)} м" if meters < 1000 else f"{meters / 1000:.1f} км".replace(".", ",")


def minutes_label(minutes):
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} сағ {minutes % 60} мин"


def suggest_workers(request):
    """Eligible workers, nearest first; workers with an old or no position go last."""

    now = timezone.now()

    workers = eligible_workers(request).select_related("staff_profile").annotate(
        active=Count(
            "assigned_requests",
            filter=Q(
                assigned_requests__status__in=ACTIVE_STATUSES,
                assigned_requests__parent__isnull=True
            )
        )
    )

    rows = []

    for worker in workers:

        profile = getattr(worker, "staff_profile", None)

        row = {"user": worker, "active": worker.active, "distance": None, "fresh": False, "seen": None}

        if profile and profile.located_at and profile.latitude is not None:

            minutes = int((now - profile.located_at).total_seconds() // 60)
            row["fresh"] = minutes <= LOCATION_FRESH_MINUTES
            row["seen"] = minutes_label(minutes) + " бұрын"

            if request.latitude is not None:
                row["distance"] = distance_meters(
                    request.latitude, request.longitude,
                    profile.latitude, profile.longitude
                )
                row["distance_label"] = distance_label(row["distance"])

        rows.append(row)

    rows.sort(key=lambda row: (
        not row["fresh"],
        row["distance"] if row["distance"] is not None else float("inf"),
        row["active"],
    ))

    return rows


def find_primary(request):
    """The open request this new one repeats, or None."""

    group = PROBLEM_GROUPS.get(request.category)

    if group is None or request.latitude is None or request.longitude is None:
        return None

    candidates = ResidentRequest.objects.filter(
        parent__isnull=True,
        status__in=OPEN_STATUSES,
        created_at__gte=timezone.now() - timedelta(days=DUPLICATE_DAYS),
        latitude__isnull=False,
        longitude__isnull=False
    ).exclude(pk=request.pk)

    best = (None, None)

    for candidate in candidates:

        if PROBLEM_GROUPS.get(candidate.category) != group:
            continue

        meters = distance_meters(
            request.latitude, request.longitude,
            candidate.latitude, candidate.longitude
        )

        if meters <= DUPLICATE_METERS and (best[1] is None or meters < best[1]):
            best = (candidate, meters)

    return best[0]


def sync_duplicates(parent, actor=None):
    """Gives every merged report the primary request's progress and result."""

    for child in parent.duplicates.all():

        changed = child.status != parent.status

        child.status = parent.status
        child.assignee = parent.assignee
        child.resolution_note = parent.resolution_note

        if parent.result_photo:
            child.result_photo = parent.result_photo.name

        child.save()

        if changed:
            log_event(
                child,
                child.status,
                actor=actor,
                note=f"Негізгі өтініш {parent.code} бойынша жаңартылды"
            )


def log_event(request, status, actor=None, note=""):

    return RequestEvent.objects.create(
        request=request,
        status=status,
        actor=actor,
        note=note[:300]
    )


def sensor_clearance(request):
    """When the sensor saw the bin emptied after the request came in.

    Returns (fill before, fill after, time) or None. Only a proof for the
    dispatcher; a person still closes the request.
    """

    if request.container is None:
        return None

    peak = request.sensor_fill_level or 0

    readings = request.container.readings.filter(
        created_at__gt=request.created_at
    ).order_by('created_at').values_list('fill_level', 'created_at')

    for fill_level, created_at in readings.iterator():

        if peak >= CONFIRM_FILL_LEVEL and fill_level <= CLEARED_FILL_LEVEL:
            return round(peak), round(fill_level), created_at

        peak = max(peak, fill_level)

    return None


def sensor_verdict(request):
    """What the sensor says about the complaint, for the dispatcher."""

    if request.container is None:
        return "none", "Жақын жерде датчигі бар контейнер жоқ"

    if request.sensor_fill_level is None:
        return "none", "Контейнердің датчигінен дерек болмаған"

    fill = round(request.sensor_fill_level)

    if fill >= CONFIRM_FILL_LEVEL:
        return "confirmed", f"Датчик контейнердің толғанын растайды: {fill}%"

    return "unconfirmed", f"Датчик бойынша контейнер толмаған: {fill}%"
