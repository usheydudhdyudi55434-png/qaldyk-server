"""The main administrator's panel: staff, containers and system status, in Kazakh.

The Django admin stays available for rare, technical changes.
"""

from django.conf import settings
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from . import ai
from .districts import district_for
from .manage_forms import ContainerForm, PasswordForm, StaffCreateForm, StaffForm
from .models import Container, District, ResidentRequest, SensorReading
from .residents import LOCATION_FRESH_MINUTES, minutes_label
from .roles import ROLE_LABELS, apply_role, is_admin, role_of, role_required, staff_district
from .views import asset_version

admin_required = role_required(is_admin)


def assets():
    return {
        'css_version': asset_version('sensor/css/dashboard.css'),
        'resident_js_version': asset_version('sensor/js/resident.js'),
    }


def done(url_name, message, **kwargs):
    return redirect(f"{reverse(url_name, kwargs=kwargs)}?{urlencode({'done': message})}")


# ============================================================
# STAFF
# ============================================================

@admin_required
def staff_list(request):

    now = timezone.now()

    rows = []

    users = get_user_model().objects.select_related(
        'staff_profile__district'
    ).prefetch_related('groups').order_by('-is_active', 'username')

    for user in users:

        role = role_of(user)
        profile = getattr(user, 'staff_profile', None)
        seen = None

        if role == 'worker' and profile and profile.located_at:
            minutes = int((now - profile.located_at).total_seconds() // 60)
            seen = {
                'label': minutes_label(minutes) + " бұрын",
                'fresh': minutes <= LOCATION_FRESH_MINUTES,
            }

        rows.append({
            'user': user,
            'role': role,
            'role_label': ROLE_LABELS[role],
            'district': staff_district(user),
            'seen': seen,
        })

    counts = {role: sum(1 for row in rows if row['role'] == role and row['user'].is_active)
              for role in ROLE_LABELS}

    return render(request, 'manage/staff_list.html', {
        'rows': rows,
        'counts': counts,
        'done': request.GET.get('done', ''),
        'tab': 'staff',
        **assets()
    })


@admin_required
def staff_create(request):

    form = StaffCreateForm(request.POST or None, initial={'is_active': True})

    if request.method == 'POST' and form.is_valid():

        data = form.cleaned_data

        user = get_user_model().objects.create_user(
            username=data['username'],
            password=data['password1'],
            first_name=data['first_name'],
            last_name=data['last_name'],
            is_active=data['is_active'],
        )

        apply_role(user, data['role'], data['district'])

        return done('manage_staff', f"{user.username} қосылды")

    return render(request, 'manage/staff_form.html', {
        'form': form,
        'creating': True,
        'tab': 'staff',
        **assets()
    })


@admin_required
def staff_edit(request, pk):

    user = get_object_or_404(get_user_model(), pk=pk)

    if user.is_superuser:
        return render(request, 'manage/staff_form.html', {
            'target': user,
            'locked': True,
            'tab': 'staff',
            **assets()
        })

    role = role_of(user)

    form = StaffForm(request.POST or None, initial={
        'first_name': user.first_name,
        'last_name': user.last_name,
        'role': role if role in ('head', 'district', 'worker') else None,
        'district': staff_district(user),
        'is_active': user.is_active,
    })

    if request.method == 'POST' and form.is_valid():

        data = form.cleaned_data

        user.first_name = data['first_name']
        user.last_name = data['last_name']
        user.is_active = data['is_active']
        user.save(update_fields=['first_name', 'last_name', 'is_active'])

        apply_role(user, data['role'], data['district'])

        return done('manage_staff', f"{user.username} сақталды")

    return render(request, 'manage/staff_form.html', {
        'form': form,
        'target': user,
        'tab': 'staff',
        **assets()
    })


@admin_required
def staff_password(request, pk):

    user = get_object_or_404(get_user_model(), pk=pk)

    form = PasswordForm(request.POST or None, user=user)

    if request.method == 'POST' and form.is_valid():

        user.set_password(form.cleaned_data['password1'])
        user.save(update_fields=['password'])

        # Changing your own password must not sign you out
        if user == request.user:
            update_session_auth_hash(request, user)

        return done('manage_staff', f"{user.username} құпия сөзі ауыстырылды")

    return render(request, 'manage/password_form.html', {
        'form': form,
        'target': user,
        'tab': 'staff',
        **assets()
    })


# ============================================================
# CONTAINERS
# ============================================================

@admin_required
def container_list(request):

    containers = Container.objects.select_related('district').annotate(
        reading_count=Count('readings', distinct=True),
        last_reading=Max('readings__created_at'),
        request_count=Count('requests', distinct=True),
    ).order_by('district__name', 'name')

    return render(request, 'manage/container_list.html', {
        'containers': containers,
        'device_base': request.build_absolute_uri('/api/sensor/'),
        'done': request.GET.get('done', ''),
        'tab': 'containers',
        **assets()
    })


def save_container(form):

    container = form.save(commit=False)
    container.district = district_for(container.latitude, container.longitude)
    container.save()

    return container


@admin_required
def container_create(request):

    form = ContainerForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        container = save_container(form)
        return done('manage_containers', f"{container.name} қосылды (№{container.pk})")

    return render(request, 'manage/container_form.html', {
        'form': form,
        'creating': True,
        'tab': 'containers',
        **assets()
    })


@admin_required
def container_edit(request, pk):

    container = get_object_or_404(Container, pk=pk)

    form = ContainerForm(request.POST or None, instance=container)

    if request.method == 'POST' and form.is_valid():
        container = save_container(form)
        return done('manage_containers', f"{container.name} сақталды")

    return render(request, 'manage/container_form.html', {
        'form': form,
        'container': container,
        'device_url': request.build_absolute_uri(f'/api/sensor/{container.pk}/'),
        'can_delete': not container.readings.exists() and not container.requests.exists(),
        'tab': 'containers',
        **assets()
    })


@admin_required
@require_POST
def container_delete(request, pk):

    container = get_object_or_404(Container, pk=pk)

    # Deleting would also delete the container's readings, so only empty ones go
    if container.readings.exists() or container.requests.exists():
        return redirect('manage_container_edit', pk=pk)

    name = container.name
    container.delete()

    return done('manage_containers', f"{name} өшірілді")


# ============================================================
# SYSTEM
# ============================================================

@admin_required
def system_status(request):

    provider = ai.provider()

    return render(request, 'manage/system.html', {
        'ai_provider': {'gemini': 'Google Gemini', 'anthropic': 'Claude (Anthropic)'}.get(provider),
        'ai_models': settings.GEMINI_MODELS if provider == 'gemini' else [ai.MODEL] if provider else [],
        'tunnel_host': settings.TUNNEL_HOST,
        'debug': settings.DEBUG,
        'counts': {
            'Аудандар': District.objects.count(),
            'Контейнерлер': Container.objects.count(),
            'Датчик көрсеткіштері': SensorReading.objects.count(),
            'Тұрғындардың өтініштері': ResidentRequest.objects.filter(parent__isnull=True).count(),
            'Белсенді аккаунттар': get_user_model().objects.filter(is_active=True).count(),
        },
        'tab': 'system',
        **assets()
    })
