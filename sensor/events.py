"""Cleanup days (сенбілік): a resident scans the event's QR on the spot and earns eco-points.

A check-in counts only while the event runs, only once per wallet, and, when the
event has a map point, only near it, so a QR photo shared online is worth nothing.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import EcoPoints, EventCheckIn
from .residents import distance_meters

# People arrive a little early and leave a little late
GRACE = timedelta(minutes=30)

# How far from the event's point a check-in still counts
RADIUS_METERS = 300


class EventError(Exception):
    pass


def state(event, now=None):
    """'off', 'soon', 'open' or 'over', for the page and for the check-in."""

    now = now or timezone.now()

    # Switched off by the organiser: not the same as finished, and the page says so
    if not event.is_active:
        return "off"

    if now > event.ends_at + GRACE:
        return "over"

    if now < event.starts_at - GRACE:
        return "soon"

    return "open"


def check_in(event, wallet, latitude=None, longitude=None):
    """Records the wallet at the event and pays its points; raises EventError with a reason."""

    current = state(event)

    if current == "off":
        raise EventError(_("Бұл сенбіліктің QR-коды өшірулі. Ұйымдастырушыдан оны қосуын сұраңыз."))

    if current == "soon":
        raise EventError(_("Сенбілік әлі басталған жоқ. QR-ды шара кезінде сканерлеңіз."))

    if current == "over":
        raise EventError(_("Бұл сенбілік аяқталды."))

    distance = None

    if event.latitude is not None and event.longitude is not None:

        if latitude is None or longitude is None:
            raise EventError(_("Қатысуды растау үшін орныңызды анықтауға рұқсат беріңіз."))

        distance = distance_meters(latitude, longitude, event.latitude, event.longitude)

        if distance > RADIUS_METERS:
            raise EventError(
                _("Сіз шара өтетін жерден %(meters)s м қашықтасыз. QR-ды сенбілік орнында сканерлеңіз.")
                % {"meters": round(distance)}
            )

    try:
        with transaction.atomic():
            EventCheckIn.objects.create(event=event, wallet=wallet, distance=distance)
            return EcoPoints.objects.create(
                wallet=wallet, amount=event.points, reason=f"Сенбілік: {event.title}"
            )
    except IntegrityError:
        raise EventError(_("Сіз бұл сенбілікке тіркеліп қойғансыз."))


def upcoming(limit=3):
    from .models import CleanupEvent

    return CleanupEvent.objects.filter(
        is_active=True, ends_at__gte=timezone.now() - GRACE
    ).select_related("osi").order_by("starts_at")[:limit]


# ============================================================
# TELLING THE CITY
# ============================================================

def audience(event):
    """Everyone in the city who can be told: every phone with notifications on
    and every resident who has talked to the Telegram bot.

    A cleanup day is open to all, so the news is not limited to its association.
    """

    from .models import PushSubscription, TelegramChat

    return PushSubscription.objects.all(), TelegramChat.objects.all()


def map_links(event):
    """Where the event is, in the map apps residents use: 2GIS, Google and Yandex."""

    from urllib.parse import quote

    if event.latitude is not None:
        lat, lng = f"{event.latitude:.6f}", f"{event.longitude:.6f}"
        return [
            ("2GIS", f"https://2gis.kz/almaty/geo/{lng}%2C{lat}"),
            ("Google Maps", f"https://www.google.com/maps/search/?api=1&query={lat}%2C{lng}"),
            ("Яндекс Карты", f"https://yandex.kz/maps/?pt={lng},{lat}&z=17&l=map"),
        ]

    # Without a point, the address is searched for
    place = quote(f"Алматы, {event.address}")
    return [
        ("2GIS", f"https://2gis.kz/almaty/search/{place}"),
        ("Google Maps", f"https://www.google.com/maps/search/?api=1&query={place}"),
        ("Яндекс Карты", f"https://yandex.kz/maps/?text={place}"),
    ]


def announcement(event):
    """Title, text and link of the push, in the active language."""

    from django.utils.dateformat import format as date_format

    when = date_format(timezone.localtime(event.starts_at), "d.m.Y H:i")

    return {
        "title": _("Сенбілік: %(title)s") % {"title": event.title},
        "body": _("%(when)s, %(place)s. Қатысқанға +%(points)s эко-ұпай.") % {
            "when": when, "place": event.address, "points": event.points,
        },
        "url": f"/events/{event.pk}/",
        "tag": f"event-{event.pk}",
    }


def telegram_text(event):

    from django.utils.dateformat import format as date_format

    when = date_format(timezone.localtime(event.starts_at), "d.m.Y H:i")

    return (
        f"🌱 Сенбілік: {event.title}\n"
        f"📅 {when}\n"
        f"📍 {event.address}\n"
        f"🎁 Қатысқанға +{event.points} эко-ұпай\n"
        f"🗺 2GIS: {map_links(event)[0][1]}\n\n"
        "Шара орнындағы QR-ды телефон камерасымен немесе Qaldyq қосымшасындағы сканермен оқыңыз."
    )


def announce(event):
    """Tells the city's residents about a newly published event, once.

    Returns how many phones and chats it is sent to; 0 when it is not the time.
    """

    import threading
    import time

    from django.conf import settings
    from django.db import close_old_connections
    from django.utils import translation

    from . import telegram, webpush
    from .models import CleanupEvent

    if event.notified_at or state(event) in ("off", "over"):
        return 0

    subscriptions, chats = audience(event)
    subscriptions = list(subscriptions) if webpush.enabled() else []
    chats = list(chats) if telegram.enabled() else []
    count = len(subscriptions) + len(chats)

    # Marked first, so a second save cannot send it twice
    CleanupEvent.objects.filter(pk=event.pk).update(notified_at=timezone.now(), notified_count=count)

    def send_all():
        for subscription in subscriptions:
            with translation.override(subscription.language):
                payload = announcement(event)
            webpush.send(subscription, payload)
        for chat in chats:
            try:
                telegram.send(chat, telegram_text(event))
            except telegram.TelegramError:
                pass
            # Telegram allows about 30 messages a second; stay well under it
            if not settings.PUSH_SYNC:
                time.sleep(0.05)

    def run():
        close_old_connections()
        try:
            send_all()
        finally:
            close_old_connections()

    # Tests send inline; the admin's page does not wait for dozens of phones
    if settings.PUSH_SYNC:
        send_all()
    else:
        threading.Thread(target=run, daemon=True).start()

    return count
