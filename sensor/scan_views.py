"""The app's QR scanner and the pages its codes open: a container's quick report
and a cleanup day's check-in.
"""

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from . import events
from .models import CleanupEvent, Container
from .resident_views import assets
from .wallet_views import current_wallet, remember, wallet_for


def scanner(request):
    return render(request, "scan.html", assets())


def container_qr(request, pk):
    """A container sticker's link: the report form, already filled for this container."""

    container = get_object_or_404(Container, pk=pk)

    return redirect(f"{reverse('resident_home')}?{urlencode({'container': container.pk})}")


def parse_point(data):
    try:
        latitude = float(data.get("latitude", ""))
        longitude = float(data.get("longitude", ""))
    except ValueError:
        return None, None

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None

    return latitude, longitude


def place_context(event):
    return {"map_links": events.map_links(event), "radius": events.RADIUS_METERS}


def event_info(request, pk):
    """What a notification opens: the event, its place on the map, and how to join.

    The check-in itself stays behind the QR at the event, so this page cannot be used for it.
    """

    event = get_object_or_404(CleanupEvent.objects.select_related("osi"), pk=pk, is_active=True)

    return render(request, "event.html", {
        "event": event,
        "state": events.state(event),
        "info_only": True,
        **place_context(event),
        **assets()
    })


@require_http_methods(["GET", "POST"])
def event_check_in(request, token):
    """A cleanup day's QR: shows the event and records the resident when they confirm."""

    event = get_object_or_404(CleanupEvent.objects.select_related("osi"), token=token)
    wallet = current_wallet(request)
    error = ""

    if request.method == "POST":

        wallet = wallet or wallet_for(request)
        latitude, longitude = parse_point(request.POST)

        try:
            line = events.check_in(event, wallet, latitude, longitude)
        except events.EventError as failure:
            error = str(failure)
        else:
            message = _("Сенбілікке қатысқаныңыз үшін рахмет! +%(points)s эко-ұпай") % {"points": line.amount}
            return remember(
                redirect(f"{reverse('wallet')}?{urlencode({'done': message})}"), wallet, request
            )

        response = render(request, "event.html", {
            "event": event, "state": events.state(event), "error": error, "joined": False,
            "needs_location": event.latitude is not None, **place_context(event), **assets()
        })
        return remember(response, wallet, request)

    return render(request, "event.html", {
        "event": event,
        "state": events.state(event),
        "joined": bool(wallet and event.check_ins.filter(wallet=wallet).exists()),
        "needs_location": event.latitude is not None,
        "error": error,
        **place_context(event),
        **assets()
    })
