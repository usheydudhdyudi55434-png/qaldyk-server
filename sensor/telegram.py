"""Qaldyq Telegram bot: residents report a problem in four steps and hear back as it is solved.

Talks to the Bot API over plain HTTPS. Updates come from the run_telegram_bot
command (long polling), so the bot keeps working when the public address changes.
"""

import json
import logging
import re
import threading
import urllib.error
import urllib.request
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import close_old_connections, transaction
from django.utils import timezone

from .forms import MAX_PHOTO_BYTES, PHOTO_SIGNATURES
from .models import ResidentRequest, TelegramChat

log = logging.getLogger(__name__)

API = "https://api.telegram.org"

# More reports than this from one chat within an hour are turned away
HOURLY_LIMIT = 5

CODE_PATTERN = re.compile(r"Q-[A-HJKMNP-Z2-9]{6}")

Category = ResidentRequest.Category
Status = ResidentRequest.Status

BTN_REPORT = "📝 Өтініш қалдыру"
BTN_MINE = "📋 Менің өтініштерім"
BTN_CHECK = "🔎 Кодпен тексеру"
BTN_CANCEL = "❌ Бас тарту"
BTN_SEND_LOCATION = "📍 Орнымды жіберу"
BTN_SKIP_LOCATION = "Орынсыз жалғастыру"
BTN_NO_ADDRESS = "Мекенжайсыз, нүкте бойынша"
BTN_NO_PHOTO = "Фотосыз жіберу"
BTN_ECO = "🌱 Эко-ұпайлар"

CATEGORY_ICONS = {
    Category.OVERFLOW: "🗑",
    Category.NOT_COLLECTED: "🚛",
    Category.LITTERED: "🧹",
    Category.OTHER: "❓",
}

STATUS_ICONS = {
    Status.NEW: "🆕",
    Status.ACCEPTED: "📥",
    Status.IN_PROGRESS: "🛠",
    Status.DONE: "✅",
    Status.REJECTED: "⛔",
}

PROGRESS = [Status.NEW, Status.ACCEPTED, Status.IN_PROGRESS, Status.DONE]

MAIN_MENU = {
    "keyboard": [
        [{"text": BTN_REPORT}],
        [{"text": BTN_MINE}, {"text": BTN_CHECK}],
        [{"text": BTN_ECO}],
    ],
    "resize_keyboard": True,
}

COMMANDS = [
    {"command": "new", "description": "Жаңа өтініш қалдыру"},
    {"command": "my", "description": "Менің өтініштерім"},
    {"command": "check", "description": "Өтінішті кодымен тексеру"},
    {"command": "eco", "description": "Эко-ұпайлар мен сыйлықтар"},
    {"command": "cancel", "description": "Толтырып жатқан өтініштен бас тарту"},
]


class TelegramError(Exception):
    pass


def enabled():
    return bool(settings.TELEGRAM_BOT_TOKEN)


def bot_link(code=""):
    """t.me link to the bot; with a code, opening it subscribes the chat to that request."""

    username = settings.TELEGRAM_BOT_USERNAME

    if not (enabled() and username):
        return ""

    return f"https://t.me/{username}" + (f"?start={code}" if code else "")


# ============================================================
# BOT API
# ============================================================

def multipart(fields, files):

    boundary = uuid.uuid4().hex
    parts = []

    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )

    for name, (filename, content) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n"
        )

    parts.append(f"--{boundary}--\r\n".encode())

    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def call(method, payload=None, files=None, timeout=20):
    """Calls a Bot API method and returns its result; never puts the token in an error."""

    payload = {key: value for key, value in (payload or {}).items() if value is not None}

    if files:
        fields = {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            for key, value in payload.items()
        }
        body, content_type = multipart(fields, files)
    else:
        body, content_type = json.dumps(payload, ensure_ascii=False).encode(), "application/json"

    request = urllib.request.Request(
        f"{API}/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
        data=body,
        headers={"Content-Type": content_type},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            data = json.load(error)
        except ValueError:
            raise TelegramError(f"{method}: HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TelegramError(f"{method}: {getattr(error, 'reason', error)}") from None

    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'error')}")

    return data["result"]


def download(file_id):
    """The bytes of a file the resident sent, or None when it is too big."""

    info = call("getFile", {"file_id": file_id})

    if info.get("file_size", 0) > MAX_PHOTO_BYTES:
        return None

    url = f"{API}/file/bot{settings.TELEGRAM_BOT_TOKEN}/{info['file_path']}"

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            content = response.read(MAX_PHOTO_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise TelegramError(f"download: {getattr(error, 'reason', error)}") from None

    return None if len(content) > MAX_PHOTO_BYTES else content


def send(chat, text, keyboard=None):
    return call("sendMessage", {
        "chat_id": chat.chat_id,
        "text": text,
        "reply_markup": keyboard,
        "disable_web_page_preview": True,
    })


def keyboard(*rows):
    return {
        "keyboard": [[{"text": text} if isinstance(text, str) else text for text in row] for row in rows],
        "resize_keyboard": True,
    }


# ============================================================
# TEXTS
# ============================================================

def status_url(code):

    host = settings.TUNNEL_HOST

    return f"https://{host}/otinish/?code={code}" if host else ""


def status_text(item):

    lines = [
        f"{STATUS_ICONS[item.status]} {item.code} — {item.get_status_display()}",
        f"{CATEGORY_ICONS[item.category]} {item.get_category_display()}",
        f"📍 {item.address}",
        "",
    ]

    if item.status == Status.REJECTED:
        lines.append("Өтініш қабылданбады.")
    else:
        reached = PROGRESS.index(item.status)
        for index, step in enumerate(PROGRESS):
            mark = "✔" if index < reached else "▶" if index == reached else "▫"
            lines.append(f"{mark} {Status(step).label}")

    if item.resolution_note and item.status in (Status.DONE, Status.REJECTED):
        lines += ["", f"💬 Қызметкердің жауабы: {item.resolution_note}"]

    url = status_url(item.code)

    if url:
        lines += ["", f"Толығырақ: {url}"]

    return "\n".join(lines)


def greet(chat):

    name = f", {chat.first_name}" if chat.first_name else ""

    send(chat, (
        f"Сәлеметсіз бе{name}! 👋\n\n"
        "Мен — Qaldyq боты. Контейнер толып кетсе, қоқыс шығарылмаса немесе аула ластанса, "
        "маған хабарлаңыз. Фото мен орынды жібересіз, мен оны аудан диспетчеріне жеткіземін "
        "және мәселенің шешілу барысын осы чатқа хабарлап отырамын.\n\n"
        "🌱 Расталған әр өтініш үшін эко-ұпай аласыз. Оны серіктестердің сыйлықтарына "
        "айырбастап, ОСИ-ыңыздың рейтингін көтересіз.\n\n"
        "🔒 Аты-жөніңіз бен телефоныңыз сұралмайды."
    ), MAIN_MENU)


# ============================================================
# CONVERSATION
# ============================================================

def chat_for(message):

    sender = message.get("from") or {}

    chat, _ = TelegramChat.objects.get_or_create(chat_id=message["chat"]["id"])

    chat.username = (sender.get("username") or "")[:64]
    chat.first_name = (sender.get("first_name") or "")[:128]

    return chat


def reset(chat):

    chat.state = ""
    chat.draft = {}
    chat.save()


def handle_update(update):

    if "callback_query" in update:
        return handle_callback(update["callback_query"])

    message = update.get("message")

    # Groups and channels are ignored; reports come from private chats
    if message and message.get("chat", {}).get("type") == "private":
        return handle_message(message)


def handle_message(message):

    chat = chat_for(message)
    text = (message.get("text") or "").strip()

    if text.startswith("/start"):
        reset(chat)
        code = text[len("/start"):].strip().upper()
        return subscribe(chat, code) if code else greet(chat)

    if text in ("/cancel", BTN_CANCEL):
        reset(chat)
        return send(chat, "Өтініштен бас тартылды. Қажет болса, қайта бастаңыз.", MAIN_MENU)

    if text in ("/new", BTN_REPORT):
        return begin_report(chat)

    if text in ("/my", BTN_MINE):
        chat.save()
        return list_requests(chat)

    if text in ("/eco", BTN_ECO):
        reset(chat)
        return show_eco(chat)

    if text in ("/check", BTN_CHECK):
        chat.state = "lookup"
        chat.save()
        return send(chat, "Өтініш кодын жазыңыз, мысалы: Q-7KX2MP", MAIN_MENU)

    steps = {
        "category": ask_category_again,
        "location": step_location,
        "address": step_address,
        "photo": step_photo,
    }

    if chat.state in steps:
        return steps[chat.state](chat, message, text)

    found = CODE_PATTERN.search(text.upper())

    if found:
        reset(chat)
        return show_status(chat, found.group())

    chat.save()

    if chat.state == "lookup":
        return send(chat, "Код табылмады. Ол Q- деп басталып, 6 таңбадан тұрады, мысалы: Q-7KX2MP")

    return send(chat, "Мәселе туралы хабарлау үшін «📝 Өтініш қалдыру» батырмасын басыңыз.", MAIN_MENU)


def begin_report(chat):

    recent = chat.requests.filter(created_at__gte=timezone.now() - timedelta(hours=1)).count()

    if recent >= HOURLY_LIMIT:
        chat.save()
        return send(chat, "Бір сағатта тым көп өтініш жіберілді. Сәлден кейін қайталаңыз.", MAIN_MENU)

    chat.state = "category"
    chat.draft = {}
    chat.save()

    send(chat, "Жаңа өтініш. Төрт қысқа қадам бар.", keyboard([BTN_CANCEL]))
    send_categories(chat)


def send_categories(chat):

    send(chat, "1/4. Мәселенің түрін таңдаңыз:", {
        "inline_keyboard": [
            [{"text": f"{CATEGORY_ICONS[value]} {label}", "callback_data": f"cat:{value}"}]
            for value, label in Category.choices
        ]
    })


def ask_category_again(chat, message, text):
    send_categories(chat)


def handle_callback(query):

    try:
        call("answerCallbackQuery", {"callback_query_id": query["id"]})
    except TelegramError:
        pass

    message = query.get("message")

    if not message:
        return

    chat = chat_for({"chat": message["chat"], "from": query.get("from")})
    data = query.get("data", "")

    if data.startswith("sub:"):
        chat.save()
        return subscribe(chat, data[len("sub:"):])

    if data == "eco" or data.startswith(("rw:", "rwok:", "osi", "top")):
        chat.save()
        return eco_callback(chat, data)

    if not (data.startswith("cat:") and chat.state == "category"):
        chat.save()
        return

    value = data[len("cat:"):]

    if value not in Category.values:
        return

    chat.draft = {"category": value}
    chat.state = "location"
    chat.save()

    try:
        call("editMessageText", {
            "chat_id": chat.chat_id,
            "message_id": message["message_id"],
            "text": f"1/4. Түрі: {CATEGORY_ICONS[value]} {Category(value).label}",
        })
    except TelegramError:
        pass

    send(chat, (
        "2/4. 📍 Мәселе орналасқан жерді жіберіңіз.\n\n"
        "Сол жерде тұрсаңыз, төмендегі батырманы басыңыз. Басқа жерде болса, "
        "📎 → «Геопозиция» арқылы картадан нүктені таңдаңыз."
    ), keyboard([{"text": BTN_SEND_LOCATION, "request_location": True}], [BTN_SKIP_LOCATION], [BTN_CANCEL]))


def step_location(chat, message, text):

    venue = message.get("venue") or {}
    location = message.get("location") or venue.get("location")

    if location:

        latitude, longitude = location.get("latitude"), location.get("longitude")

        if not (isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))
                and -90 <= latitude <= 90 and -180 <= longitude <= 180):
            return send(chat, "Орын дұрыс келмеді, қайта жіберіңіз.")

        chat.draft.update(latitude=round(latitude, 6), longitude=round(longitude, 6))

        # A chosen place already has an address, so that step is skipped
        place = ", ".join(part for part in (venue.get("title"), venue.get("address")) if part)

        if place:
            chat.draft["address"] = place[:255]
            return ask_photo(chat, f"3/4. Мекенжай: {place}")

        chat.state = "address"
        chat.save()

        return send(chat, (
            "3/4. ✔ Орын алынды. Енді мекенжайды немесе бағдарды жазыңыз "
            "(мысалы: Абай даңғылы 10, аула ішінде)."
        ), keyboard([BTN_NO_ADDRESS], [BTN_CANCEL]))

    if text == BTN_SKIP_LOCATION:
        chat.state = "address"
        chat.save()
        return send(chat, "3/4. Мекенжайды жазыңыз: көше, үй нөмірі немесе бағдар.", keyboard([BTN_CANCEL]))

    chat.save()

    return send(chat, "Орынды «📍 Орнымды жіберу» батырмасы арқылы жіберіңіз немесе «Орынсыз жалғастыру» таңдаңыз.")


def step_address(chat, message, text):

    if text == BTN_NO_ADDRESS and "latitude" in chat.draft:
        chat.draft["address"] = "Картадағы нүкте (Telegram)"
    elif len(text) >= 5 and not text.startswith("/"):
        chat.draft["address"] = text[:255]
    else:
        chat.save()
        return send(chat, "Мекенжайды толығырақ жазыңыз: көше, үй нөмірі немесе бағдар.")

    return ask_photo(chat, "✔ Мекенжай сақталды.")


def ask_photo(chat, intro):

    chat.state = "photo"
    chat.save()

    return send(chat, (
        f"{intro}\n\n4/4. 📷 Мәселенің фотосын жіберіңіз. Фотоға қысқа сипаттама қоса жазуға болады."
    ), keyboard([BTN_NO_PHOTO], [BTN_CANCEL]))


def step_photo(chat, message, text):

    file_id = None

    if message.get("photo"):
        # Sizes come smallest first; the largest one under the limit is taken
        sizes = [size for size in message["photo"] if size.get("file_size", 0) <= MAX_PHOTO_BYTES]
        file_id = (sizes or message["photo"])[-1]["file_id"]

    document = message.get("document") or {}

    if document.get("mime_type", "").startswith("image/"):
        file_id = document["file_id"]

    if file_id:

        try:
            content = download(file_id)
        except TelegramError as error:
            log.warning("Telegram photo download failed: %s", error)
            chat.save()
            return send(chat, "Фото жүктелмеді. Қайта жіберіңіз немесе «Фотосыз жіберу» таңдаңыз.")

        extension = photo_extension(content)

        if extension is None:
            chat.save()
            return send(chat, "Фото 5 МБ-тан аспауы керек және JPG, PNG немесе WEBP болуы керек.")

        caption = (message.get("caption") or "").strip()

        if caption:
            chat.draft["description"] = caption[:2000]

        return finish(chat, content, extension)

    if text == BTN_NO_PHOTO:
        return finish(chat, None, None)

    if text and not text.startswith("/"):
        chat.draft["description"] = text[:2000]
        chat.save()
        return send(chat, "✔ Сипаттама сақталды. Енді фото жіберіңіз немесе «Фотосыз жіберу» таңдаңыз.")

    chat.save()

    return send(chat, "Фото жіберіңіз немесе «Фотосыз жіберу» таңдаңыз.")


def photo_extension(content):

    if not content:
        return None

    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"

    for signature, extension in zip(PHOTO_SIGNATURES, (".jpg", ".png")):
        if content.startswith(signature):
            return extension

    return None


def finish(chat, photo, extension):

    draft = chat.draft

    item = ResidentRequest(
        category=draft["category"],
        address=draft.get("address", "Telegram"),
        description=draft.get("description", ""),
        latitude=draft.get("latitude"),
        longitude=draft.get("longitude"),
        telegram_chat=chat,
    )

    if photo:
        item.photo.save(f"telegram{extension}", ContentFile(photo), save=False)

    from .residents import submit_request

    with transaction.atomic():
        submit_request(item)

    reset(chat)

    lines = [
        "✅ Өтінішіңіз қабылданды!",
        "",
        f"Коды: {item.code}",
        f"{CATEGORY_ICONS[item.category]} {item.get_category_display()}",
        f"📍 {item.address}",
    ]

    if item.district:
        lines.append(f"🏙 {item.district.name}")

    if item.parent:
        lines += ["", (
            f"ℹ️ Бұл жердегі мәселе туралы бұрын хабарланған ({item.parent.code}). "
            "Өтінішіңіз соған қосылды, бұл оны тезірек шешуге көмектеседі."
        )]

    if item.container and item.sensor_fill_level is not None:
        lines += ["", f"📡 Жақын контейнердің датчигі: {round(item.sensor_fill_level)}% толы."]

    lines += ["", "Мәртебесі өзгерген сайын осы чатқа хабарлаймын."]

    url = status_url(item.code)

    if url:
        lines.append(f"Күйін сайттан да көруге болады: {url}")

    return send(chat, "\n".join(lines), MAIN_MENU)


def show_status(chat, code):

    item = ResidentRequest.objects.filter(code=code).first()

    if item is None:
        return send(chat, f"{code} кодымен өтініш табылмады.", MAIN_MENU)

    if item.telegram_chat_id != chat.pk:
        return send(chat, status_text(item), {
            "inline_keyboard": [[{"text": "🔔 Жаңалықтарын осы чатқа алу", "callback_data": f"sub:{code}"}]]
        })

    return send(chat, status_text(item), MAIN_MENU)


def subscribe(chat, code):

    item = ResidentRequest.objects.filter(code=code).first() if CODE_PATTERN.fullmatch(code) else None

    if item is None:
        return greet(chat)

    item.telegram_chat = chat
    item.save(update_fields=["telegram_chat"])

    return send(chat, f"🔔 {item.code} өтінішінің жаңалықтарын осы чатқа жіберемін.\n\n{status_text(item)}", MAIN_MENU)


def list_requests(chat):

    items = list(chat.requests.order_by("-created_at")[:5])

    if not items:
        return send(chat, "Сізде әлі өтініш жоқ. «📝 Өтініш қалдыру» батырмасын басыңыз.", MAIN_MENU)

    lines = ["Соңғы өтініштеріңіз:", ""]

    for item in items:
        lines.append(f"{STATUS_ICONS[item.status]} {item.code} · {item.get_status_display()} · {item.address[:40]}")

    lines += ["", "Толығырақ білу үшін кодты жазыңыз."]

    return send(chat, "\n".join(lines), MAIN_MENU)


# ============================================================
# NOTIFICATIONS
# ============================================================

def notify_status(request_pk):
    """Tells the resident's chat that the request moved on, without delaying the dispatcher."""

    if not enabled():
        return

    if settings.TELEGRAM_SYNC:
        return deliver(request_pk)

    def run():
        close_old_connections()
        try:
            deliver(request_pk)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()


def deliver(request_pk):

    item = ResidentRequest.objects.select_related("telegram_chat").filter(pk=request_pk).first()

    if item is None or item.telegram_chat is None:
        return

    text = f"🔔 Өтінішіңіз бойынша жаңалық\n\n{status_text(item)}"

    if item.status == Status.DONE:

        points = item.eco_points.filter(amount__gt=0).first()

        if points:
            from .eco import balance
            text += (
                f"\n\n🌱 +{points.amount} эко-ұпай ({points.reason}). "
                f"Балансыңыз: {balance(item.telegram_chat)}"
            )

        text += "\n\nРахмет! Қаланы бірге таза ұстаймыз 💚"

    try:

        if item.status == Status.DONE and item.result_photo:

            with item.result_photo.open("rb") as photo:
                content = photo.read()

            call("sendPhoto", {
                "chat_id": item.telegram_chat.chat_id,
                "caption": text[:1024],
            }, files={"photo": ("result.jpg", content)}, timeout=60)

        else:

            send(item.telegram_chat, text)

    except (TelegramError, OSError) as error:
        # A resident who blocked the bot simply stops hearing from it
        log.warning("Telegram notification for %s failed: %s", item.code, error)


# ============================================================
# ECO-POINTS
# ============================================================

def show_eco(chat):

    from . import eco

    lines = [
        f"🌱 Эко-ұпайларыңыз: {eco.balance(chat)}",
        f"Барлығы жиналды: {eco.earned(chat)}",
        f"🏠 ОСИ: {chat.osi.name if chat.osi else 'таңдалмаған'}",
        "",
        "Ұпай қалай беріледі:",
    ]

    lines += [f"{points} — {text}" for points, text in eco.RULES]

    vouchers = list(chat.redemptions.filter(used_at__isnull=True).select_related("reward")[:3])

    if vouchers:
        lines += ["", "🎟 Пайдаланылмаған ваучерлер:"]
        lines += [f"{voucher.code} — {voucher.reward.title} ({voucher.reward.partner})" for voucher in vouchers]

    rows = [
        [{"text": f"🎁 {reward.title} — {reward.cost} ұпай", "callback_data": f"rw:{reward.pk}"}]
        for reward in eco.available_rewards()[:8]
    ]

    if rows:
        lines += ["", "Сыйлықты таңдаңыз:"]

    rows.append([
        {"text": "🏠 ОСИ таңдау", "callback_data": "osi"},
        {"text": "🏆 Рейтинг", "callback_data": "top"},
    ])

    return send(chat, "\n".join(lines), {"inline_keyboard": rows})


def eco_callback(chat, data):

    from . import eco
    from .models import Osi

    if data == "eco":
        return show_eco(chat)

    if data.startswith("rw:"):

        reward_id = data[len("rw:"):]
        reward = eco.available_rewards().filter(pk=reward_id).first() if reward_id.isdigit() else None

        if reward is None:
            return send(chat, "Бұл сыйлық енді қолжетімсіз.")

        details = "\n".join(part for part in (reward.partner, reward.description) if part)

        return send(chat, (
            f"🎁 {reward.title}\n{details}\n\n"
            f"Бағасы: {reward.cost} ұпай. Сізде: {eco.balance(chat)}.\nАласыз ба?"
        ), {"inline_keyboard": [[
            {"text": "✅ Иә, алу", "callback_data": f"rwok:{reward.pk}"},
            {"text": "Артқа", "callback_data": "eco"},
        ]]})

    if data.startswith("rwok:"):

        reward_id = data[len("rwok:"):]

        if not reward_id.isdigit():
            return None

        try:
            voucher = eco.redeem(chat, int(reward_id))
        except eco.EcoError as error:
            return send(chat, f"⚠️ {error}")

        return send(chat, (
            f"🎉 Құттықтаймыз! Ваучер коды: {voucher.code}\n\n"
            f"{voucher.reward.title} — {voucher.reward.partner}.\n"
            f"Кодты серіктеске көрсетіңіз. Қалған ұпайыңыз: {eco.balance(chat)}"
        ), MAIN_MENU)

    if data == "osi":

        osis = list(Osi.objects.filter(is_active=True).select_related("district")[:30])

        if not osis:
            return send(chat, "ОСИ тізімі әлі толтырылмаған. Жақында қосылады.")

        rows = [
            [{
                "text": osi.name + (f" · {osi.district.name}" if osi.district else ""),
                "callback_data": f"osi:{osi.pk}",
            }]
            for osi in osis
        ]

        return send(chat, "🏠 ОСИ-ыңызды таңдаңыз. Ұпайларыңыз оның рейтингіне қосылады:", {"inline_keyboard": rows})

    if data.startswith("osi:"):

        osi_id = data[len("osi:"):]
        osi = Osi.objects.filter(is_active=True, pk=osi_id).first() if osi_id.isdigit() else None

        if osi is None:
            return send(chat, "Бұл ОСИ табылмады.")

        chat.osi = osi
        chat.save(update_fields=["osi"])

        return send(chat, f"✔ ОСИ таңдалды: {osi.name}. Енді ұпайларыңыз оның рейтингіне қосылады.", MAIN_MENU)

    if data == "top":

        lines = ["🏆 ОСИ рейтингі:"]

        for place, osi in enumerate(eco.osi_ranking(5), start=1):
            lines.append(f"{place}. {osi.name} — {osi.score} ұпай ({osi.residents} тұрғын)")

        if len(lines) == 1:
            lines.append("Әзірге бос.")

        lines += ["", "🏙 Аудандар:"]

        for place, district in enumerate(eco.district_ranking()[:5], start=1):
            lines.append(f"{place}. {district.name} — {district.score} ұпай, {district.solved} мәселе шешілді")

        if settings.TUNNEL_HOST:
            lines += ["", f"Толық рейтинг: https://{settings.TUNNEL_HOST}/eco/"]

        return send(chat, "\n".join(lines))

    return None
