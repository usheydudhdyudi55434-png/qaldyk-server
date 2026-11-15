"""The resident rates a finished request: solved, or not solved and reopened.

The rating belongs to the worker who did the job, so the supervision page can show
each worker's real quality instead of only how many requests they closed.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import RequestRating, ResidentRequest
from .residents import log_event, sync_duplicates

Status = ResidentRequest.Status

# A resident can answer for a week after the job was done
RATE_DAYS = 7

# Stops one resident from bouncing the same request back forever
MAX_REOPENS = 2

# The quality table looks at this many recent days
QUALITY_DAYS = 30


def main_of(item):
    """A merged report is rated through the request that carries the work."""
    return item.parent or item


def done_at(item):
    return item.events.filter(status=Status.DONE).order_by("-created_at").values_list("created_at", flat=True).first()


def current_rating(item):
    """The rating given for the latest completion, if any."""

    main = main_of(item)
    finished = done_at(main)

    if finished is None:
        return None

    return main.ratings.filter(created_at__gte=finished).first()


def can_rate(item):
    """Done, within the week, and not rated yet for this completion."""

    main = main_of(item)

    if main.status != Status.DONE:
        return False

    finished = done_at(main)

    if finished is None or timezone.now() - finished > timedelta(days=RATE_DAYS):
        return False

    return current_rating(item) is None


def owner_may_rate(item, wallet=None, chat=None):
    """Only the resident who sent the request answers for it."""

    if item.wallet_id:
        return wallet is not None and wallet.pk == item.wallet_id

    if item.telegram_chat_id:
        return chat is not None and chat.pk == item.telegram_chat_id

    # Older reports from the website have no owner; the code is the only key
    return True


def reopens(item):
    return main_of(item).ratings.filter(is_good=False).count()


def rate(item, is_good, comment=""):
    """Saves the answer; a "not solved" sends the request back to the dispatcher.

    Returns "thanks", "reopened" or "noted" (not solved, but reopened too often already).
    """

    main = main_of(item)
    comment = (comment or "").strip()[:300]

    with transaction.atomic():

        RequestRating.objects.create(request=main, worker=main.assignee, is_good=is_good, comment=comment)

        if is_good:
            return "thanks"

        if reopens(main) > MAX_REOPENS:
            log_event(main, main.status, note=f"Тұрғын: мәселе шешілмеді. {comment}".strip(), notify=False)
            return "noted"

        # Back to the dispatcher's queue, still with the same worker
        main.status = Status.NEW
        main.save(update_fields=["status", "updated_at"])
        log_event(main, Status.NEW, note=f"Тұрғын: мәселе шешілмеді, өтініш қайта ашылды. {comment}".strip())
        sync_duplicates(main)

    return "reopened"


def worker_quality(workers):
    """Each worker's ratings over the last weeks: good, bad and the share of good."""

    since = timezone.now() - timedelta(days=QUALITY_DAYS)

    rows = []

    for worker in workers.annotate(
        good=Count("ratings", filter=Q(ratings__is_good=True, ratings__created_at__gte=since)),
        bad=Count("ratings", filter=Q(ratings__is_good=False, ratings__created_at__gte=since)),
    ):
        rated = worker.good + worker.bad
        rows.append({
            "worker": worker,
            "good": worker.good,
            "bad": worker.bad,
            "rated": rated,
            "share": round(worker.good * 100 / rated) if rated else None,
        })

    rows.sort(key=lambda row: (row["share"] is None, -(row["share"] or 0), -row["rated"]))
    return rows
