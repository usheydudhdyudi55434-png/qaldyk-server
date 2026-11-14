"""Who may do what: dispatchers route requests, workers carry them out.

Roles are Django groups, managed in /admin/ under Groups and Users.
Superusers count as dispatchers.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission

DISPATCHERS = "Диспетчерлер"
WORKERS = "Жұмысшылар"


def in_group(user, name):
    return user.is_authenticated and user.groups.filter(name=name).exists()


def is_dispatcher(user):
    return user.is_authenticated and (user.is_superuser or in_group(user, DISPATCHERS))


def is_worker(user):
    return in_group(user, WORKERS)


def staff_district(user):
    """The district a dispatcher or worker is limited to, or None for the whole city."""

    if not user.is_authenticated:
        return None

    profile = getattr(user, "staff_profile", None)

    return profile.district if profile else None


def is_head_dispatcher(user):
    """A dispatcher without a district oversees every district."""

    return is_dispatcher(user) and staff_district(user) is None


def scope_by_district(queryset, user):
    """Limits requests or containers to the user's district, when they have one."""

    district = staff_district(user)

    return queryset.filter(district=district) if district else queryset.all()


def home_for(user):
    """The page a user lands on, or None when the account has no role yet."""

    if is_dispatcher(user):
        return "/dashboard/"

    if is_worker(user):
        return "/tasks/"

    return None


def role_required(check):

    def decorator(view):

        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):

            if check(request.user):
                return view(request, *args, **kwargs)

            # The other role is sent to its own page instead of an error
            home = home_for(request.user)

            if home:
                return redirect(home)

            from .views import asset_version

            return render(request, 'no_role.html', {
                'css_version': asset_version('sensor/css/dashboard.css')
            }, status=403)

        return wrapped

    return decorator


ROLE_ADMIN = "admin"
ROLE_HEAD = "head"
ROLE_DISTRICT = "district"
ROLE_WORKER = "worker"
ROLE_NONE = "none"

ROLE_LABELS = {
    ROLE_ADMIN: _("Әкімші"),
    ROLE_HEAD: _("Бас диспетчер"),
    ROLE_DISTRICT: _("Аудан диспетчері"),
    ROLE_WORKER: _("Жұмысшы"),
    ROLE_NONE: _("Рөлі жоқ"),
}


def role_of(user):
    """One role per account, as the admin panel shows and sets it."""

    if user.is_superuser:
        return ROLE_ADMIN

    # Reads prefetched groups when the caller loaded them
    names = {group.name for group in user.groups.all()}

    if DISPATCHERS in names:
        return ROLE_DISTRICT if staff_district(user) else ROLE_HEAD

    if WORKERS in names:
        return ROLE_WORKER

    return ROLE_NONE


def apply_role(user, role, district=None):
    """Puts the account in exactly one role group and stores its district."""

    from django.contrib.auth.models import Group

    from .models import StaffProfile

    dispatchers, _created = Group.objects.get_or_create(name=DISPATCHERS)
    workers, _created = Group.objects.get_or_create(name=WORKERS)

    user.groups.remove(dispatchers, workers)

    if role in (ROLE_HEAD, ROLE_DISTRICT):
        user.groups.add(dispatchers)
    elif role == ROLE_WORKER:
        user.groups.add(workers)

    if role == ROLE_HEAD:
        district = None

    profile, _created = StaffProfile.objects.get_or_create(user=user)

    if profile.district != district:
        profile.district = district
        profile.save(update_fields=["district"])

    # The cached profile would otherwise keep the old district
    user.staff_profile = profile


def is_admin(user):
    return user.is_authenticated and user.is_superuser


dispatcher_required = role_required(is_dispatcher)

worker_required = role_required(is_worker)


class IsDispatcher(BasePermission):

    def has_permission(self, request, view):
        return is_dispatcher(request.user)
