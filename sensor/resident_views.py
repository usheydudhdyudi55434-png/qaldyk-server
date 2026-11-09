from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import RequestUpdateForm, ResidentRequestForm, TaskCompleteForm
from .models import ResidentRequest
from .roles import dispatcher_required, worker_required
from .residents import (
    attach_sensor_snapshot,
    log_event,
    sensor_clearance,
    sensor_verdict,
)
from .views import asset_version


Status = ResidentRequest.Status


def assets():
    return {
        'css_version': asset_version('sensor/css/dashboard.css'),
        'resident_js_version': asset_version('sensor/js/resident.js'),
    }


# ============================================================
# RESIDENTS (public, no login)
# ============================================================

def resident_home(request):

    form = ResidentRequestForm(request.POST or None, request.FILES or None)

    if request.method == 'POST' and form.is_valid():

        resident_request = form.save(commit=False)

        attach_sensor_snapshot(resident_request)

        resident_request.save()

        log_event(resident_request, Status.NEW, note="Тұрғын өтініш жіберді")

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

    requests = ResidentRequest.objects.select_related('container', 'assignee')

    if status in Status.values:
        requests = requests.filter(status=status)
    else:
        status = ''

    counts = dict(
        ResidentRequest.objects.values_list('status').annotate(total=Count('id'))
    )

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
        **assets()
    })


@dispatcher_required
def request_detail(request, pk):

    resident_request = get_object_or_404(
        ResidentRequest.objects.select_related('container', 'assignee'),
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
        'latest': latest,
        'saved': request.GET.get('saved') == '1',
        **assets()
    })


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

    return redirect(f"{reverse('my_tasks')}?done={item.code}")
