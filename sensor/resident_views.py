import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .ai import analyze_request, review_reason, start_analysis
from .forms import RequestUpdateForm, ResidentRequestForm, TaskCompleteForm
from .models import RequestAnalysis, ResidentRequest
from .districts import district_for
from .models import District, StaffProfile, WorkerPosition
from .roles import WORKERS
from .roles import (
    DISPATCHERS,
    dispatcher_required,
    scope_by_district,
    staff_district,
    worker_required,
)
from .residents import (
    ACTIVE_STATUSES,
    LOCATION_FRESH_MINUTES,
    attach_sensor_snapshot,
    eligible_workers,
    find_primary,
    minutes_label,
    suggest_workers,
    log_event,
    sensor_clearance,
    sensor_verdict,
    sync_duplicates,
)
from .views import asset_version


Status = ResidentRequest.Status

# A new request waiting longer than this is flagged on the supervision page
SUPERVISION_LATE_MINUTES = 60

# How long a worker's path is kept for the live map
POSITION_KEEP_HOURS = 24


def assets():
    return {
        'css_version': asset_version('sensor/css/dashboard.css'),
        'resident_js_version': asset_version('sensor/js/resident.js'),
        'worker_js_version': asset_version('sensor/js/worker.js'),
    }


# ============================================================
# RESIDENTS (public, no login)
# ============================================================

def resident_home(request):

    form = ResidentRequestForm(request.POST or None, request.FILES or None)

    if request.method == 'POST' and form.is_valid():

        resident_request = form.save(commit=False)

        attach_sensor_snapshot(resident_request)

        resident_request.district = district_for(
            resident_request.latitude, resident_request.longitude
        )

        primary = find_primary(resident_request)

        if primary:
            resident_request.parent = primary
            resident_request.status = primary.status
            resident_request.assignee = primary.assignee

        resident_request.save()

        log_event(resident_request, Status.NEW, note="Тұрғын өтініш жіберді")

        if primary:
            log_event(
                resident_request,
                resident_request.status,
                note=f"Осы жердегі {primary.code} өтінішімен біріктірілді"
            )
            log_event(
                primary,
                primary.status,
                note=f"Тағы бір тұрғын хабарлады: {resident_request.code}"
            )

        if resident_request.photo:
            transaction.on_commit(lambda: start_analysis(resident_request))

        return redirect(
            f"{reverse('resident_status')}?code={resident_request.code}&new=1"
        )

    return render(request, 'resident.html', {'form': form, **assets()})


def resident_status(request):

    code = request.GET.get('code', '').strip().upper()

    resident_request = None

    if code:
        resident_request = ResidentRequest.objects.filter(code=code).first()

    return render(request, 'resident_status.html', {
        'code': code,
        'resident_request': resident_request,
        'is_new': request.GET.get('new') == '1',
        'steps': status_steps(resident_request),
        **assets()
    })


def status_steps(resident_request):
    """The progress line shown to the resident, with the time each step was reached."""

    if resident_request is None:
        return []

    reached = {}

    for event in resident_request.events.all():
        reached.setdefault(event.status, event.created_at)

    reached.setdefault(Status.NEW, resident_request.created_at)

    if resident_request.status == Status.REJECTED:
        order = [Status.NEW, Status.REJECTED]
    else:
        order = [Status.NEW, Status.ACCEPTED, Status.IN_PROGRESS, Status.DONE]

    current = order.index(resident_request.status)

    steps = []

    for index, status in enumerate(order):

        if status == Status.REJECTED:
            state = 'rejected'
        elif index < current:
            state = 'done'
        elif index == current:
            state = 'current'
        else:
            state = 'todo'

        steps.append({
            'label': status.label,
            'state': state,
            'at': reached.get(status) if index <= current else None
        })

    return steps


# ============================================================
# DISPATCHER
# ============================================================

@dispatcher_required
def requests_list(request):

    status = request.GET.get('status', '')

    # A district manager sees only their district; city dispatchers may pick one
    scoped = scope_by_district(ResidentRequest.objects.filter(parent__isnull=True), request.user)

    district = None

    if staff_district(request.user) is None:
        district = District.objects.filter(slug=request.GET.get('district', '')).first()
        if district:
            scoped = scoped.filter(district=district)

    # Merged reports are listed under their primary request, not on their own
    requests = scoped.select_related(
        'container', 'assignee', 'analysis', 'district'
    ).annotate(duplicate_count=Count('duplicates'))

    if status in Status.values:
        requests = requests.filter(status=status)
    else:
        status = ''

    counts = dict(scoped.values_list('status').annotate(total=Count('id')))

    filters = [{'value': '', 'label': 'Барлығы', 'count': sum(counts.values())}] + [
        {'value': value, 'label': label, 'count': counts.get(value, 0)}
        for value, label in Status.choices
    ]

    rows = [
        {'item': item, 'verdict': sensor_verdict(item)}
        for item in requests[:200]
    ]

    return render(request, 'requests_list.html', {
        'rows': rows,
        'filters': filters,
        'status': status,
        'districts': District.objects.all() if staff_district(request.user) is None else [],
        'district': district,
        **assets()
    })


@dispatcher_required
def request_detail(request, pk):

    resident_request = get_object_or_404(
        scope_by_district(
            ResidentRequest.objects.select_related('container', 'assignee', 'analysis', 'district'),
            request.user
        ),
        pk=pk
    )

    before = (resident_request.status, resident_request.assignee_id)

    form = RequestUpdateForm(request.POST or None, instance=resident_request)

    if request.method == 'POST' and form.is_valid():

        updated = form.save()

        assignee_changed = updated.assignee_id != before[1]

        # One save is one history entry, even when status and assignee both change
        if assignee_changed or updated.status != before[0]:
            note = ""

            if assignee_changed:
                note = (
                    f"Жауапты: {updated.assignee.username}"
                    if updated.assignee else "Жауапты алынып тасталды"
                )

            log_event(updated, updated.status, actor=request.user, note=note)

        sync_duplicates(updated, actor=request.user)

        return redirect(f"{reverse('request_detail', args=[pk])}?saved=1")

    latest = None

    if resident_request.container:
        latest = resident_request.container.readings.order_by('-created_at').first()

    return render(request, 'request_detail.html', {
        'item': resident_request,
        'form': form,
        'verdict': sensor_verdict(resident_request),
        'clearance': sensor_clearance(resident_request),
        'events': resident_request.events.select_related('actor'),
        'analysis': RequestAnalysis.objects.filter(request=resident_request).first(),
        'duplicates': resident_request.duplicates.order_by('created_at'),
        'suggestions': (
            suggest_workers(resident_request)
            if resident_request.status in (Status.NEW, *ACTIVE_STATUSES)
            and resident_request.parent_id is None
            else None
        ),
        'latest': latest,
        'saved': request.GET.get('saved') == '1',
        **assets()
    })


@dispatcher_required
@require_POST
def request_assign(request, pk):
    """Assigns one of the suggested workers in a single click."""

    item = get_object_or_404(
        scope_by_district(ResidentRequest.objects, request.user),
        pk=pk,
        parent__isnull=True
    )

    # Only a worker attached to the request's district can be chosen
    worker = get_object_or_404(eligible_workers(item), pk=request.POST.get('worker'))

    item.assignee = worker

    if item.status == Status.NEW:
        item.status = Status.ACCEPTED

    item.save(update_fields=['assignee', 'status', 'updated_at'])

    log_event(item, item.status, actor=request.user, note=f"Жауапты: {worker.username} (ұсыныс бойынша)")

    sync_duplicates(item, actor=request.user)

    return redirect(f"{reverse('request_detail', args=[pk])}?saved=1")


@dispatcher_required
def supervision(request):
    """The head dispatcher's view of how every district is being handled."""

    if staff_district(request.user):
        return redirect('dashboard')

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    day_ago = now - timedelta(days=1)
    fresh_after = now - timedelta(minutes=LOCATION_FRESH_MINUTES)

    rows = []

    for district in [*District.objects.all(), None]:

        requests = ResidentRequest.objects.filter(parent__isnull=True, district=district)
        new = requests.filter(status=Status.NEW)
        oldest = new.order_by('created_at').first()

        # Minutes from a request arriving to a dispatcher accepting it
        accept_minutes = []

        for item in requests.filter(created_at__gte=week_ago).prefetch_related('events'):
            accepted = next(
                (event for event in item.events.all() if event.status != Status.NEW),
                None
            )
            if accepted:
                accept_minutes.append((accepted.created_at - item.created_at).total_seconds() / 60)

        workers = get_user_model().objects.filter(
            is_active=True,
            groups__name=WORKERS,
            staff_profile__district=district
        ) if district else get_user_model().objects.none()

        oldest_minutes = int((now - oldest.created_at).total_seconds() // 60) if oldest else None

        rows.append({
            'district': district,
            'dispatchers': get_user_model().objects.filter(
                is_active=True,
                groups__name=DISPATCHERS,
                staff_profile__district=district
            ).order_by('username') if district else [],
            'workers': workers.count(),
            'workers_online': workers.filter(staff_profile__located_at__gte=fresh_after).count(),
            'new': new.count(),
            'oldest': minutes_label(oldest_minutes) if oldest else None,
            'late': oldest_minutes is not None and oldest_minutes > SUPERVISION_LATE_MINUTES,
            'active': requests.filter(status__in=ACTIVE_STATUSES).count(),
            'done_today': requests.filter(
                status=Status.DONE,
                events__status=Status.DONE,
                events__created_at__gte=day_ago
            ).distinct().count(),
            'accept_avg': (
                minutes_label(round(sum(accept_minutes) / len(accept_minutes)))
                if accept_minutes else None
            ),
        })

    return render(request, 'supervision.html', {
        'rows': rows,
        'late_minutes': SUPERVISION_LATE_MINUTES,
        **assets()
    })


@worker_required
@require_POST
def worker_location(request):
    """Stores the worker's position, sent from the tasks page about once a minute."""

    try:
        data = json.loads(request.body)
        latitude = float(data['latitude'])
        longitude = float(data['longitude'])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'error': 'latitude and longitude are required'}, status=400)

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return JsonResponse({'error': 'latitude and longitude are out of range'}, status=400)

    try:
        accuracy = float(data['accuracy']) if data.get('accuracy') is not None else None
    except (ValueError, TypeError):
        accuracy = None

    now = timezone.now()

    profile, _ = StaffProfile.objects.get_or_create(user=request.user)
    profile.latitude = latitude
    profile.longitude = longitude
    profile.located_at = now
    profile.save(update_fields=['latitude', 'longitude', 'located_at'])

    # The path for the live map; a day is kept, older points are dropped
    WorkerPosition.objects.create(
        user=request.user, latitude=latitude, longitude=longitude, accuracy=accuracy, created_at=now
    )
    WorkerPosition.objects.filter(
        user=request.user, created_at__lt=now - timedelta(hours=POSITION_KEEP_HOURS)
    ).delete()

    return JsonResponse({'ok': True})


@dispatcher_required
@require_POST
def request_analyze(request, pk):
    """Runs the photo analysis again; the dispatcher waits a few seconds for it."""

    item = get_object_or_404(scope_by_district(ResidentRequest.objects, request.user), pk=pk)

    analyze_request(item.pk)

    return redirect('request_detail', pk=pk)


@dispatcher_required
@require_POST
def request_unmerge(request, pk):
    """Turns a merged report back into a request of its own."""

    item = get_object_or_404(
        scope_by_district(ResidentRequest.objects, request.user),
        pk=pk,
        parent__isnull=False
    )
    parent = item.parent

    item.parent = None
    item.status = Status.NEW
    item.assignee = None
    item.save(update_fields=['parent', 'status', 'assignee', 'updated_at'])

    log_event(item, Status.NEW, actor=request.user, note=f"{parent.code} өтінішінен бөлінді")
    log_event(parent, parent.status, actor=request.user, note=f"{item.code} бөлек өтініш болды")

    return redirect('request_detail', pk=pk)


@dispatcher_required
@require_POST
def request_accept_category(request, pk):

    item = get_object_or_404(scope_by_district(ResidentRequest.objects, request.user), pk=pk)
    analysis = get_object_or_404(RequestAnalysis, request=item, state=RequestAnalysis.State.DONE)

    if analysis.suggested_category and analysis.suggested_category != item.category:
        old = item.get_category_display()
        item.category = analysis.suggested_category
        item.save(update_fields=['category', 'updated_at'])

        log_event(
            item,
            item.status,
            actor=request.user,
            note=f"Санат AI ұсынысымен өзгертілді: {old} → {item.get_category_display()}"
        )

        analysis.review_reason = review_reason(analysis, item)
        analysis.needs_review = bool(analysis.review_reason)
        analysis.save(update_fields=['review_reason', 'needs_review', 'updated_at'])

    return redirect('request_detail', pk=pk)


# ============================================================
# WORKER: MY TASKS
# ============================================================

ACTIVE = (Status.ACCEPTED, Status.IN_PROGRESS)


@worker_required
def my_tasks(request):

    tasks = ResidentRequest.objects.filter(
        assignee=request.user
    ).select_related('container')

    active = [
        {
            'item': item,
            'verdict': sensor_verdict(item),
            'clearance': sensor_clearance(item),
            'form': TaskCompleteForm(instance=item, prefix=f"task{item.pk}"),
        }
        for item in tasks.filter(status__in=ACTIVE).order_by('created_at')
    ]

    return render(request, 'tasks.html', {
        'active': active,
        'finished': tasks.filter(status=Status.DONE).order_by('-updated_at')[:10],
        'done_code': request.GET.get('done', ''),
        **assets()
    })


@worker_required
@require_POST
def task_start(request, pk):

    # Someone else's task looks the same as one that does not exist
    item = get_object_or_404(ResidentRequest, pk=pk, assignee=request.user, status__in=ACTIVE)

    if item.status != Status.IN_PROGRESS:
        item.status = Status.IN_PROGRESS
        item.save(update_fields=['status', 'updated_at'])
        log_event(item, Status.IN_PROGRESS, actor=request.user, note="Қызметкер жұмысқа кірісті")
        sync_duplicates(item, actor=request.user)

    return redirect('my_tasks')


@worker_required
@require_POST
def task_complete(request, pk):

    item = get_object_or_404(ResidentRequest, pk=pk, assignee=request.user, status__in=ACTIVE)

    form = TaskCompleteForm(
        request.POST,
        request.FILES,
        instance=item,
        prefix=f"task{item.pk}"
    )

    if not form.is_valid():
        return render(request, 'tasks.html', {
            'active': [{
                'item': item,
                'verdict': sensor_verdict(item),
                'clearance': sensor_clearance(item),
                'form': form,
            }],
            'finished': [],
            'done_code': '',
            **assets()
        }, status=400)

    item = form.save(commit=False)
    item.status = Status.DONE
    item.save()

    log_event(item, Status.DONE, actor=request.user, note="Қызметкер фотомен растады")

    sync_duplicates(item, actor=request.user)

    return redirect(f"{reverse('my_tasks')}?done={item.code}")
