"""Web push to the installed Qaldyq app: a phone hears when its requests move on.

A resident subscribes from the status page; the browser gives an endpoint and keys,
which are kept with the requests they asked about. Messages are signed with the
VAPID keys from .env and written in the language the resident used.
"""

import json
import logging
import threading

from django.conf import settings
from django.db import close_old_connections
from django.utils import translation
from django.utils.translation import gettext as _

from .models import PushSubscription, ResidentRequest

log = logging.getLogger(__name__)

Status = ResidentRequest.Status

# A day is enough: a status older than that is better read on the page
TTL_SECONDS = 24 * 60 * 60


def enabled():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def subscribe(data, resident_request, language, wallet=None):
    """Stores the browser's subscription; links it to a request and to the phone's wallet."""

    keys = data.get("keys") or {}
    endpoint = str(data.get("endpoint") or "")

    if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("invalid subscription")

    subscription, _created = PushSubscription.objects.update_or_create(
        endpoint=endpoint[:600],
        defaults={
            "p256dh": str(keys["p256dh"])[:200],
            "auth": str(keys["auth"])[:100],
            "language": language,
            **({"wallet": wallet} if wallet else {}),
        },
    )

    if resident_request is not None:
        subscription.requests.add(resident_request)

    return subscription


def message(item):
    """Title, text and link of the notification, in the active language."""

    title = _("Өтініш %(code)s") % {"code": item.code}
    body = _("Жаңа мәртебе: %(status)s") % {"status": item.get_status_display()}

    if item.status == Status.DONE:
        points = item.eco_points.filter(amount__gt=0).first()
        body += ". " + _("Жұмыс аяқталды, нәтиженің фотосын қараңыз.")
        if points:
            body += " " + _("+%(amount)s эко-ұпай") % {"amount": points.amount}
    elif item.status == Status.REJECTED and item.resolution_note:
        body += ". " + item.resolution_note[:120]

    return {
        "title": title,
        "body": body,
        "url": f"/otinish/?code={item.code}",
        "tag": item.code,
    }


def send(subscription, payload):
    """Sends one notification; a subscription the browser dropped is forgotten."""

    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=TTL_SECONDS,
            timeout=10,
        )
    except WebPushException as error:
        status = getattr(error.response, "status_code", None)
        if status in (404, 410):
            subscription.delete()
        else:
            log.warning("Web push failed for %s: %s", subscription.pk, error)


def deliver(request_pk):

    item = ResidentRequest.objects.filter(pk=request_pk).first()

    if item is None:
        return

    for subscription in item.push_subscriptions.all():
        with translation.override(subscription.language):
            payload = message(item)
        send(subscription, payload)


def notify_status(request_pk):
    """Tells subscribed phones that the request moved on, without delaying the dispatcher."""

    if not enabled():
        return

    if settings.PUSH_SYNC:
        return deliver(request_pk)

    def run():
        close_old_connections()
        try:
            deliver(request_pk)
        except Exception:
            log.exception("Web push for request %s failed", request_pk)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
