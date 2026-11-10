"""Where a dispatcher's workers are now, the path they walked and the job they are heading to."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import ResidentRequest, WorkerPosition
from .residents import ACTIVE_STATUSES, distance_meters
from .roles import WORKERS, IsDispatcher, staff_district

# The tasks page sends a point about every 20 seconds
LIVE_SECONDS = 2 * 60

# Same window the "nearest worker" suggestion trusts
RECENT_SECONDS = 15 * 60

# How much of the path is drawn on the map
TRAIL_MINUTES = 60

# Points less precise than this would draw a jagged path
TRAIL_MAX_ACCURACY = 100


def initials(user):

    parts = [part for part in (user.first_name, user.last_name) if part]

    if not parts:
        parts = [user.username]

    letters = "".join(part[0] for part in parts[:2])

    return (letters if len(parts) > 1 else parts[0][:2]).upper()


def worker_state(age):

    if age is None:
        return "unknown"

    if age <= LIVE_SECONDS:
        return "live"

    return "recent" if age <= RECENT_SECONDS else "offline"


def path_meters(points):
    return sum(
        distance_meters(a[0], a[1], b[0], b[1])
        for a, b in zip(points, points[1:])
    )


def current_task(tasks):
    """The job in progress first, otherwise the oldest accepted one."""

    started = [task for task in tasks if task.status == ResidentRequest.Status.IN_PROGRESS]

    return (started or tasks or [None])[0]


@api_view(['GET'])
@permission_classes([IsDispatcher])
def workers_live(request):

    now = timezone.now()

    district = staff_district(request.user)

    workers = get_user_model().objects.filter(
        is_active=True, groups__name=WORKERS
    ).select_related('staff_profile__district').order_by('first_name', 'username')

    if district:
        workers = workers.filter(staff_profile__district=district)

    workers = list(workers)

    trails = {worker.pk: [] for worker in workers}

    positions = WorkerPosition.objects.filter(
        user__in=workers,
        created_at__gte=now - timedelta(minutes=TRAIL_MINUTES)
    ).order_by('created_at').values_list('user_id', 'latitude', 'longitude', 'accuracy')

    for user_id, latitude, longitude, accuracy in positions:
        if accuracy is None or accuracy <= TRAIL_MAX_ACCURACY:
            trails[user_id].append([round(latitude, 6), round(longitude, 6)])

    tasks = {worker.pk: [] for worker in workers}

    for task in ResidentRequest.objects.filter(
        assignee__in=workers,
        status__in=ACTIVE_STATUSES,
        parent__isnull=True
    ).order_by('created_at'):
        tasks[task.assignee_id].append(task)

    rows = []

    for worker in workers:

        profile = getattr(worker, 'staff_profile', None)
        located = profile is not None and profile.located_at is not None and profile.latitude is not None
        age = int((now - profile.located_at).total_seconds()) if located else None

        task = current_task(tasks[worker.pk])
        task_row = None

        if task:
            task_row = {
                'code': task.code,
                'status': task.status,
                'statusLabel': task.get_status_display(),
                'address': task.address,
                'latitude': task.latitude,
                'longitude': task.longitude,
                'url': reverse('request_detail', args=[task.pk]),
                'distance': (
                    round(distance_meters(profile.latitude, profile.longitude, task.latitude, task.longitude))
                    if located and task.latitude is not None else None
                ),
            }

        trail = trails[worker.pk]

        rows.append({
            'id': worker.pk,
            'name': worker.get_full_name() or worker.username,
            'initials': initials(worker),
            'district': profile.district.name if profile and profile.district else None,
            'latitude': profile.latitude if located else None,
            'longitude': profile.longitude if located else None,
            'locatedAt': profile.located_at.isoformat() if located else None,
            'age': age,
            'state': worker_state(age),
            'trail': trail,
            'walked': round(path_meters(trail)),
            'task': task_row,
            'taskCount': len(tasks[worker.pk]),
        })

    return Response({
        'serverTime': now.isoformat(),
        'trailMinutes': TRAIL_MINUTES,
        'workers': rows,
    })
