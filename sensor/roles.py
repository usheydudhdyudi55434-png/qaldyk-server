"""Who may do what: dispatchers route requests, workers carry them out.

Roles are Django groups, managed in /admin/ under Groups and Users.
Superusers count as dispatchers.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from rest_framework.permissions import BasePermission

DISPATCHERS = "Диспетчерлер"
WORKERS = "Жұмысшылар"


def in_group(user, name):
    return user.is_authenticated and user.groups.filter(name=name).exists()


def is_dispatcher(user):
    return user.is_authenticated and (user.is_superuser or in_group(user, DISPATCHERS))


def is_worker(user):
    return in_group(user, WORKERS)


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


dispatcher_required = role_required(is_dispatcher)

worker_required = role_required(is_worker)


class IsDispatcher(BasePermission):

    def has_permission(self, request, view):
        return is_dispatcher(request.user)
