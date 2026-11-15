"""The installable Qaldyq app (PWA): manifest, service worker, offline page,
the resident's own requests and web push subscriptions.
"""

import json
import re

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import webpush
from .models import ResidentRequest
from .resident_views import assets

# Codes a phone can ask about at once
MY_CODES_LIMIT = 30

CODE_RE = re.compile(r"^Q-[A-Z0-9]{6}$")


def clean_codes(raw):
    codes = []
    for code in str(raw or "").upper().split(","):
        code = code.strip()
        if CODE_RE.match(code) and code not in codes:
            codes.append(code)
    return codes[:MY_CODES_LIMIT]


@require_GET
def manifest(request):

    icon = lambda name: static(f"sensor/img/{name}")

    data = {
        "id": "/",
        "name": "Qaldyq",
        "short_name": "Qaldyq",
        "description": _("Қоқыс мәселесін хабарлаңыз, өтінішті бақылаңыз, эко-ұпай жинаңыз."),
        "lang": get_language(),
        "start_url": "/?source=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#0d0d0d",
        "theme_color": "#0f2a63",
        "categories": ["utilities", "lifestyle"],
        "icons": [
            {"src": icon("app-icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon("app-icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon("app-icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": _("Өтініш қалдыру"), "url": "/report/?source=app",
             "icons": [{"src": icon("app-icon-192.png"), "sizes": "192x192"}]},
            {"name": _("QR сканер"), "url": "/scan/?source=app",
             "icons": [{"src": icon("app-icon-192.png"), "sizes": "192x192"}]},
            {"name": _("Менің өтініштерім"), "url": "/my/?source=app",
             "icons": [{"src": icon("app-icon-192.png"), "sizes": "192x192"}]},
            {"name": _("Эко-ұпай"), "url": "/wallet/?source=app",
             "icons": [{"src": icon("app-icon-192.png"), "sizes": "192x192"}]},
        ],
    }

    response = HttpResponse(json.dumps(data, ensure_ascii=False), content_type="application/manifest+json")
    response["Cache-Control"] = "no-cache"
    return response


@never_cache
@require_GET
def service_worker(request):
    """Served from the site root so it can control every page."""

    response = render(request, "sw.js", {
        "version": assets()["css_version"],
        "offline_url": "/offline/",
        "icon": static("sensor/img/app-icon-192.png"),
        "badge": static("sensor/img/app-badge-96.png"),
    }, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response


def offline(request):
    return render(request, "offline.html", assets())


def my_requests(request):
    """The resident's requests; the codes live on the phone, not in an account."""

    return render(request, "my_requests.html", {
        "push_key": settings.VAPID_PUBLIC_KEY if webpush.enabled() else "",
        **assets()
    })


@require_GET
def my_status(request):
    """Short status of the requests whose codes the phone remembers."""

    codes = clean_codes(request.GET.get("codes"))
    found = {item.code: item for item in ResidentRequest.objects.filter(code__in=codes).select_related("parent")}

    rows = []
    for code in codes:
        item = found.get(code)
        if item is None:
            rows.append({"code": code, "found": False})
            continue
        # A merged request follows its main one
        main = item.parent or item
        rows.append({
            "code": code,
            "found": True,
            "status": main.status,
            "statusLabel": main.get_status_display(),
            "category": item.get_category_display(),
            "address": item.address,
            "createdAt": timezone.localtime(item.created_at).strftime("%d.%m.%Y %H:%M"),
            "updatedAt": timezone.localtime(main.updated_at).strftime("%d.%m.%Y %H:%M"),
            "url": f"/otinish/?code={code}",
        })

    return JsonResponse({"requests": rows})


@require_POST
def push_subscribe(request):

    if not webpush.enabled():
        return JsonResponse({"error": "push is not configured"}, status=503)

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "invalid json"}, status=400)

    from .wallet_views import current_wallet

    wallet = current_wallet(request)
    codes = clean_codes(body.get("code"))
    item = ResidentRequest.objects.filter(code=codes[0]).first() if codes else None

    # A request's status page names its code; the wallet page subscribes the wallet alone
    if codes and item is None:
        return JsonResponse({"error": "request not found"}, status=404)

    if item is None and wallet is None:
        return JsonResponse({"error": "nothing to subscribe to"}, status=400)

    try:
        webpush.subscribe(
            body.get("subscription") or {}, item, get_language() or settings.LANGUAGE_CODE, wallet=wallet
        )
    except ValueError:
        return JsonResponse({"error": "invalid subscription"}, status=400)

    return JsonResponse({"ok": True})
