"""Links a resident's request to the nearest container and its sensor reading."""

import math

from .models import Container, RequestEvent

# A request further than this from every container is not linked to one
NEAREST_CONTAINER_METERS = 500

# The fill level at which a sensor confirms an overflow complaint
CONFIRM_FILL_LEVEL = 80

# After a request, a fall from full to this level means the bin was emptied
CLEARED_FILL_LEVEL = 30

EARTH_RADIUS_METERS = 6371000


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
