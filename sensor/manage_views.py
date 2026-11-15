"""The main administrator's panel: staff, containers and system status, in Kazakh.

The Django admin stays available for rare, technical changes.
"""

from django.conf import settings
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.db.models import Count, Max
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _

from . import ai, eco, telegram
from .districts import district_for
from .manage_forms import ContainerForm, OsiForm, PasswordForm, RewardForm, StaffCreateForm, StaffForm
from .models import (
    Container, District, Osi, Redemption, ResidentRequest, Reward, SensorReading, TelegramChat,
)
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
                'label': _("%(time)s бұрын") % {'time': minutes_label(minutes)},
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

        return done('manage_staff', _("%(name)s қосылды") % {"name": user.username})

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

        return done('manage_staff', _("%(name)s сақталды") % {"name": user.username})

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

        return done('manage_staff', _("%(name)s құпия сөзі ауыстырылды") % {"name": user.username})

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
        return done('manage_containers', _("%(name)s қосылды (№%(number)s)") % {"name": container.name, "number": container.pk})

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
        return done('manage_containers', _("%(name)s сақталды") % {"name": container.name})

    from .qr import container_path, svg

    return render(request, 'manage/container_form.html', {
        'form': form,
        'container': container,
        'qr': svg(request.build_absolute_uri(container_path(container)), scale=5),
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

    return done('manage_containers', _("%(name)s өшірілді") % {"name": name})


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
        'telegram_on': telegram.enabled(),
        'telegram_link': telegram.bot_link(),
        'telegram_chats': TelegramChat.objects.count(),
        'telegram_requests': ResidentRequest.objects.filter(telegram_chat__isnull=False).count(),
        'debug': settings.DEBUG,
        'counts': {
            _('Аудандар'): District.objects.count(),
            _('Контейнерлер'): Container.objects.count(),
            _('Датчик көрсеткіштері'): SensorReading.objects.count(),
            _('Тұрғындардың өтініштері'): ResidentRequest.objects.filter(parent__isnull=True).count(),
            _('Белсенді аккаунттар'): get_user_model().objects.filter(is_active=True).count(),
        },
        'tab': 'system',
        **assets()
    })


# ============================================================
# ECO-POINTS
# ============================================================

@admin_required
def eco_overview(request):

    return render(request, 'manage/eco.html', {
        'totals': eco.totals(),
        'rewards': Reward.objects.annotate(taken=Count('redemptions')).order_by('-is_active', 'cost'),
        'osis': Osi.objects.select_related('district').annotate(
            residents=Count('chats', distinct=True) + Count('wallets', distinct=True)
        ).order_by('-is_active', 'name'),
        'redemptions': Redemption.objects.select_related('reward', 'chat', 'wallet')[:30],
        'done': request.GET.get('done', ''),
        'tab': 'eco',
        **assets()
    })


def edit_object(request, form_class, instance, title, back_message):

    form = form_class(request.POST or None, instance=instance)

    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        return done('manage_eco', back_message % {'name': saved})

    return render(request, 'manage/simple_form.html', {
        'form': form,
        'title': title,
        'back_url': reverse('manage_eco'),
        'tab': 'eco',
        **assets()
    })


@admin_required
def reward_create(request):
    return edit_object(request, RewardForm, None, _("Жаңа сыйлық"), _("%(name)s қосылды"))


@admin_required
def reward_edit(request, pk):
    reward = get_object_or_404(Reward, pk=pk)
    return edit_object(request, RewardForm, reward, reward.title, _("%(name)s сақталды"))


@admin_required
def osi_create(request):
    return edit_object(request, OsiForm, None, _("Жаңа ОСИ"), _("%(name)s қосылды"))


@admin_required
def osi_edit(request, pk):
    osi = get_object_or_404(Osi, pk=pk)
    return edit_object(request, OsiForm, osi, osi.name, _("%(name)s сақталды"))


@admin_required
@require_POST
def redemption_use(request, pk):

    redemption = get_object_or_404(Redemption, pk=pk)

    # A voucher is spent once; pressing again changes nothing
    if redemption.used_at is None:
        redemption.used_at = timezone.now()
        redemption.save(update_fields=['used_at'])

    return done('manage_eco', _("%(code)s пайдаланылды деп белгіленді") % {"code": redemption.code})


# ============================================================
# QR CODES AND CLEANUP DAYS
# ============================================================

@admin_required
def container_stickers(request, pk=None):
    """A printable sheet of QR stickers: every container, or just one."""

    from .qr import container_path, svg

    containers = Container.objects.select_related('district').order_by('pk')

    if pk is not None:
        containers = containers.filter(pk=pk)
        if not containers:
            raise Http404

    stickers = [
        {"container": container, "qr": svg(request.build_absolute_uri(container_path(container)), scale=5)}
        for container in containers
    ]

    return render(request, 'manage/stickers.html', {
        'stickers': stickers,
        'single': stickers[0]['container'] if pk is not None else None,
        'host': request.get_host(),
        'tab': 'containers',
        **assets()
    })


@admin_required
def container_qr_file(request, pk, kind):
    """The container's QR as a file to send to a print shop: PNG or SVG."""

    import io

    import segno

    from .qr import container_path

    if kind not in ("png", "svg"):
        raise Http404

    container = get_object_or_404(Container, pk=pk)
    code = segno.make(request.build_absolute_uri(container_path(container)), error="m")

    buffer = io.BytesIO()
    code.save(buffer, kind=kind, scale=20 if kind == "png" else 10, border=4, dark="#0f2a63", light="#ffffff")

    response = HttpResponse(buffer.getvalue(), content_type="image/png" if kind == "png" else "image/svg+xml")
    response["Content-Disposition"] = f'attachment; filename="qaldyq-container-{container.pk}-qr.{kind}"'
    return response


@admin_required
def event_list(request):

    from .models import CleanupEvent

    from . import events

    rows = list(CleanupEvent.objects.select_related('osi').annotate(joined=Count('check_ins')))
    for event in rows:
        event.state = events.state(event)

    return render(request, 'manage/events.html', {
        'events': rows,
        'done': request.GET.get('done', ''),
        'tab': 'events',
        **assets()
    })


def edit_event(request, event, title):

    from .manage_forms import EventForm

    form = EventForm(request.POST or None, instance=event)

    if request.method == 'POST' and form.is_valid():
        from . import events

        saved = form.save()
        message = _("%(name)s сақталды") % {"name": saved.title}

        # A published event is announced to its association's residents, once
        told = events.announce(saved)
        if told:
            message += ". " + _("Хабарлама жіберілді: %(count)s тұрғын") % {"count": told}

        return done('manage_event_qr', message, pk=saved.pk)

    from .events import RADIUS_METERS

    return render(request, 'manage/event_form.html', {
        'form': form,
        'title': title,
        'radius': RADIUS_METERS,
        'tab': 'events',
        **assets()
    })


@admin_required
def event_create(request):
    return edit_event(request, None, _("Жаңа сенбілік"))


@admin_required
def event_edit(request, pk):
    from .models import CleanupEvent
    event = get_object_or_404(CleanupEvent, pk=pk)
    return edit_event(request, event, event.title)


@admin_required
@require_POST
def event_toggle(request, pk):
    """Switches the event's QR on or off in one click."""

    from .models import CleanupEvent

    from . import events

    event = get_object_or_404(CleanupEvent, pk=pk)
    event.is_active = not event.is_active
    event.save(update_fields=['is_active'])

    message = _("QR қосылды") if event.is_active else _("QR өшірілді")

    told = events.announce(event) if event.is_active else 0
    if told:
        message += ". " + _("Хабарлама жіберілді: %(count)s тұрғын") % {"count": told}
    return done('manage_event_qr', message, pk=event.pk)


@admin_required
def event_qr(request, pk):
    """The event's poster: a big QR to print or show on a screen, and who has come."""

    from . import events
    from .models import CleanupEvent
    from .qr import event_path, svg

    event = get_object_or_404(CleanupEvent.objects.select_related('osi'), pk=pk)
    url = request.build_absolute_uri(event_path(event))

    return render(request, 'manage/event_qr.html', {
        'event': event,
        'qr': svg(url, scale=10),
        'url': url,
        'state': events.state(event),
        'audience': sum(query.count() for query in events.audience(event)),
        'check_ins': event.check_ins.select_related('wallet').order_by('-created_at'),
        'done': request.GET.get('done', ''),
        'tab': 'events',
        **assets()
    })
