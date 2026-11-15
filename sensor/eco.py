"""Eco-points: residents earn them for confirmed reports and spend them on partners' rewards.

Points are paid only when a worker closes the request with a photo, so a false
report earns nothing. The sensor and the photo analysis add bonuses.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count, F, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from .models import District, EcoPoints, EcoWallet, Osi, Redemption, ResidentRequest, Reward, TelegramChat
from .residents import CONFIRM_FILL_LEVEL

POINTS_CONFIRMED = 10
POINTS_DUPLICATE = 3
BONUS_SENSOR = 5
BONUS_PHOTO = 5

# Stops anyone from farming points with many small reports
DAILY_CAP = 50

RULES = [
    (f"+{POINTS_CONFIRMED}", _("Өтінішіңіз орындалып, қызметкер оны фотомен растағанда")),
    (f"+{BONUS_SENSOR}", _("Контейнердің датчигі де толғанын растаса")),
    (f"+{BONUS_PHOTO}", _("Фотоңызда мәселе анық көрінсе (AI тексереді)")),
    (f"+{POINTS_DUPLICATE}", _("Бұрын хабарланған мәселені растасаңыз")),
    ("0", _("Өтініш қабылданбаса немесе жалған болса")),
]


class EcoError(Exception):
    pass


# A resident's points live with their Telegram chat or with their app wallet;
# both have .points and .redemptions, so the rest of the code treats them alike.

def owner_of(request):
    return request.telegram_chat or request.wallet


def owned_by(owner):
    """The field that points an eco-points line or a voucher at this owner."""
    return {"chat": owner} if isinstance(owner, TelegramChat) else {"wallet": owner}


def balance(owner):
    return owner.points.aggregate(total=Coalesce(Sum("amount"), 0))["total"]


def earned(owner):
    return owner.points.filter(amount__gt=0).aggregate(total=Coalesce(Sum("amount"), 0))["total"]


def award_for(request):
    """The points this request earns now, with the reasons; (0, []) when it earns none."""

    if request.status != ResidentRequest.Status.DONE:
        return 0, []

    if request.parent_id:
        return POINTS_DUPLICATE, ["Расталған қайталама хабар"]

    amount, reasons = POINTS_CONFIRMED, ["Өтініш орындалды"]

    if request.sensor_fill_level is not None and request.sensor_fill_level >= CONFIRM_FILL_LEVEL:
        amount += BONUS_SENSOR
        reasons.append("датчик растады")

    analysis = getattr(request, "analysis", None)

    if analysis and analysis.is_waste_photo and not analysis.needs_review:
        amount += BONUS_PHOTO
        reasons.append("фото анық")

    return amount, reasons


def award(request):
    """Credits the resident's chat or wallet once per request; returns the entry or None."""

    owner = owner_of(request)

    if owner is None:
        return None

    amount, reasons = award_for(request)

    if amount <= 0:
        return None

    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)

    earned_today = owner.points.filter(amount__gt=0, created_at__gte=today).aggregate(
        total=Coalesce(Sum("amount"), 0)
    )["total"]

    amount = min(amount, DAILY_CAP - earned_today)

    if amount <= 0:
        return None

    if amount < award_for(request)[0]:
        reasons.append("күндік шекке жетті")

    try:
        with transaction.atomic():
            return EcoPoints.objects.create(
                request=request, amount=amount, reason=", ".join(reasons), **owned_by(owner)
            )
    except IntegrityError:
        # Already paid for this request
        return None


def available_rewards():
    return Reward.objects.filter(is_active=True).filter(Q(stock__isnull=True) | Q(stock__gt=0))


def redeem(owner, reward_id):
    """Spends points on a reward and returns the voucher; raises EcoError with a reason."""

    with transaction.atomic():

        # Locking the owner stops two taps from spending the same points twice
        owner = type(owner).objects.select_for_update().get(pk=owner.pk)

        reward = Reward.objects.select_for_update().filter(pk=reward_id).first()

        if reward is None or not reward.is_active:
            raise EcoError(gettext("Бұл сыйлық енді қолжетімсіз."))

        if reward.stock is not None and reward.stock <= 0:
            raise EcoError(gettext("Бұл сыйлық таусылды."))

        have = balance(owner)

        if have < reward.cost:
            raise EcoError(gettext("Ұпай жетпейді: керегі %(cost)s, сізде %(have)s.") % {"cost": reward.cost, "have": have})

        redemption = Redemption.objects.create(reward=reward, cost=reward.cost, **owned_by(owner))

        EcoPoints.objects.create(amount=-reward.cost, reason=f"Сыйлық: {reward.title}", **owned_by(owner))

        if reward.stock is not None:
            Reward.objects.filter(pk=reward.pk).update(stock=F("stock") - 1)

    return redemption


class SubquerySum(Subquery):
    """SUM over the rows of a subquery, so two kinds of owner do not multiply each other's rows."""

    template = "(SELECT SUM(amount) FROM (%(subquery)s) AS lines)"
    output_field = IntegerField()


def osi_points():
    """Points earned by an association's residents, from Telegram and from the app."""

    lines = EcoPoints.objects.filter(amount__gt=0).filter(
        Q(chat__osi=OuterRef("pk")) | Q(wallet__osi=OuterRef("pk"))
    ).order_by().values("amount")

    return Coalesce(SubquerySum(lines), 0)


def osi_ranking(limit=10):
    """Associations by the eco-points their residents earned (spending does not lower it)."""

    return Osi.objects.filter(is_active=True).annotate(
        score=osi_points(),
        residents=Count("chats", distinct=True) + Count("wallets", distinct=True),
    ).order_by("-score", "name")[:limit]


def district_ranking():
    """Districts by eco-points earned for requests solved there."""

    return District.objects.annotate(
        score=Coalesce(Sum("requests__eco_points__amount", filter=Q(requests__eco_points__amount__gt=0)), 0),
        solved=Count("requests", filter=Q(requests__status=ResidentRequest.Status.DONE), distinct=True),
    ).order_by("-score", "name")


def totals():
    positive = EcoPoints.objects.filter(amount__gt=0)

    return {
        "awarded": positive.aggregate(total=Coalesce(Sum("amount"), 0))["total"],
        "spent": -EcoPoints.objects.filter(amount__lt=0).aggregate(total=Coalesce(Sum("amount"), 0))["total"],
        "residents": (
            positive.filter(chat__isnull=False).values("chat").distinct().count()
            + positive.filter(wallet__isnull=False).values("wallet").distinct().count()
        ),
        "vouchers": Redemption.objects.count(),
    }
