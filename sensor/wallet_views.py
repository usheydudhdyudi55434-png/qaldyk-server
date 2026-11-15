"""The resident's eco-wallet in the app: balance, history, rewards and vouchers.

There is no account. The wallet opens with the first request sent from this phone and
its code is kept in a cookie; the same code typed on another phone opens it there.
"""

from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from django.conf import settings

from . import eco, events, webpush
from .models import EcoWallet, Osi
from .resident_views import assets

COOKIE = "qaldyq_wallet"
COOKIE_AGE = 2 * 365 * 24 * 60 * 60

HISTORY_SIZE = 20


def current_wallet(request):
    code = request.COOKIES.get(COOKIE, "").strip().upper()
    return EcoWallet.objects.filter(code=code).select_related("osi").first() if code else None


def wallet_for(request):
    """The phone's wallet, opened now if it has none yet."""
    return current_wallet(request) or EcoWallet.objects.create()


def remember(response, wallet, request):
    response.set_cookie(
        COOKIE, wallet.code, max_age=COOKIE_AGE, httponly=True, samesite="Lax", secure=request.is_secure()
    )
    return response


def back(**params):
    url = reverse('wallet')
    return redirect(f"{url}?{urlencode(params)}" if params else url)


def wallet_page(request):

    wallet = current_wallet(request)

    context = {
        'wallet': wallet,
        'rewards': eco.available_rewards(),
        'osis': Osi.objects.filter(is_active=True).select_related('district'),
        'events': events.upcoming(3),
        'push_key': settings.VAPID_PUBLIC_KEY if webpush.enabled() else '',
        'rules': eco.RULES,
        'daily_cap': eco.DAILY_CAP,
        'voucher': request.GET.get('voucher', ''),
        'error': request.GET.get('error', ''),
        'done': request.GET.get('done', ''),
        **assets()
    }

    if wallet:
        balance = eco.balance(wallet)
        earned = eco.earned(wallet)

        # How far each reward is, so the page can show progress instead of a refusal
        rewards = list(context['rewards'])
        for reward in rewards:
            reward.need = max(reward.cost - balance, 0)
            reward.progress = 100 if not reward.cost else min(100, round(balance * 100 / reward.cost))

        # The cheapest reward still out of reach is the next goal on the card
        goal = min((reward for reward in rewards if reward.need), key=lambda reward: reward.cost, default=None)

        history = list(wallet.points.select_related('request')[:HISTORY_SIZE])
        for line in history:
            line.kind = (
                'reward' if line.amount < 0
                else 'event' if line.request_id is None and line.reason.startswith('Сенбілік')
                else 'request'
            )

        vouchers = list(wallet.redemptions.select_related('reward'))

        context.update({
            'rewards': rewards,
            'balance': balance,
            'earned': earned,
            'spent': earned - balance,
            'goal': goal,
            'history': history,
            'vouchers': vouchers,
            'active_vouchers': sum(1 for voucher in vouchers if not voucher.used_at),
            'events_joined': wallet.check_ins.count(),
            'masked_code': f"{wallet.code[:2]}••••-{wallet.code[-4:]}",
            'pending': wallet.requests.exclude(status__in=('done', 'rejected')).count(),
        })

    return render(request, 'wallet.html', context)


@require_POST
def wallet_redeem(request, reward_id):

    wallet = current_wallet(request)

    if wallet is None:
        return back()

    try:
        voucher = eco.redeem(wallet, reward_id)
    except eco.EcoError as error:
        return back(error=str(error))

    return back(voucher=voucher.code)


@require_POST
def wallet_osi(request):

    wallet = current_wallet(request)

    if wallet is not None:
        osi = Osi.objects.filter(pk=request.POST.get('osi') or 0, is_active=True).first()
        wallet.osi = osi
        wallet.save(update_fields=['osi'])

    return back(done=_("ОСИ сақталды.") if wallet and wallet.osi else _("ОСИ таңдалмады."))


@require_POST
def wallet_restore(request):
    """Opens a wallet from its code, for a new phone or a cleared browser."""

    code = request.POST.get('code', '').strip().upper()
    wallet = EcoWallet.objects.filter(code=code).first() if code else None

    if wallet is None:
        return back(error=_("Мұндай кодпен әмиян табылмады."))

    return remember(back(done=_("Әмиян осы телефонда ашылды.")), wallet, request)
