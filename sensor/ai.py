"""AI triage of a resident's photo: what kind of waste, how much, how dangerous.

The model only suggests; a dispatcher decides. The provider follows the key that
is set: GEMINI_API_KEY uses Google Gemini, ANTHROPIC_API_KEY uses Claude. With
neither, the analysis is skipped and the rest of the system works as before.
"""

import base64
import io
import json
import logging
import socket
import threading
import urllib.error
import urllib.request

from django.conf import settings
from django.db import close_old_connections
from PIL import Image, ImageOps

from .models import RequestAnalysis, ResidentRequest

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Free-tier Gemini models are often busy; these answers mean "try the next model"
GEMINI_RETRY_CODES = (429, 500, 503, 504)

# Claude reads images up to this many pixels on the long side without loss
MAX_IMAGE_SIDE = 1568

# Below this the suggestion is shown but flagged for a person to check
LOW_CONFIDENCE = 0.6

SYSTEM_PROMPT = """You triage photos that city residents send to a waste-management service in Almaty, Kazakhstan.

Look at the photo and describe what a cleaning crew would need to know before going there:
- whether the photo shows waste, litter or a waste container at all;
- the main kind of waste;
- roughly how much there is;
- any sign of danger for a worker: syringes, chemicals, batteries, paint or fuel cans, asbestos-like sheets, smoke or fire, broken glass in quantity, dead animals.

Be honest about uncertainty. If the photo is dark, blurred or ambiguous, say so with a low confidence instead of guessing. Never call something safe just because you cannot see a danger clearly: use "possible" when in doubt.

Write the summary in Kazakh, one or two short sentences a dispatcher can read at a glance."""

SCHEMA = {
    "type": "object",
    "properties": {
        "is_waste_photo": {
            "type": "boolean",
            "description": "True when the photo shows waste, litter or a waste container."
        },
        "waste_type": {"type": "string", "enum": RequestAnalysis.WasteType.values},
        "volume": {"type": "string", "enum": RequestAnalysis.Volume.values},
        "hazard": {"type": "string", "enum": RequestAnalysis.Hazard.values},
        "hazard_reason": {
            "type": "string",
            "description": "In Kazakh: what looks dangerous, or an empty string."
        },
        "suggested_category": {"type": "string", "enum": ResidentRequest.Category.values},
        "confidence": {
            "type": "number",
            "description": "From 0 to 1: how sure you are about waste_type and hazard."
        },
        "summary": {"type": "string", "description": "One or two sentences in Kazakh."},
    },
    "required": [
        "is_waste_photo", "waste_type", "volume", "hazard",
        "hazard_reason", "suggested_category", "confidence", "summary",
    ],
    "additionalProperties": False,
}


class AnalysisError(Exception):
    pass


def provider():

    if settings.GEMINI_API_KEY:
        return "gemini"

    if settings.ANTHROPIC_API_KEY:
        return "anthropic"

    return None


def is_enabled():
    return provider() is not None


def prepare_image(photo):
    """Turns an upload into a JPEG no larger than Claude reads at full detail."""

    photo.open("rb")

    try:
        image = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
    finally:
        photo.close()

    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)

    return base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def ask_model(image_data, request):
    """Sends one photo to the configured provider; returns (parsed answer, model)."""

    if provider() == "gemini":
        return ask_gemini(image_data, request)

    return ask_claude(image_data, request)


def request_text(request):
    return (
        f"Resident's category: {request.get_category_display()}\n"
        f"Resident's description: {request.description or '(none)'}"
    )


def ask_gemini(image_data, request):

    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_data}},
                {"text": request_text(request)},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": SCHEMA,
        },
    }).encode()

    last_error = None

    for model in settings.GEMINI_MODELS:

        http_request = urllib.request.Request(
            GEMINI_URL.format(model=model),
            body,
            {"Content-Type": "application/json", "x-goog-api-key": settings.GEMINI_API_KEY},
        )

        try:
            with urllib.request.urlopen(http_request, timeout=45) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in GEMINI_RETRY_CODES:
                last_error = f"{model}: HTTP {error.code}"
                continue
            raise AnalysisError(f"Gemini қатесі: HTTP {error.code}") from error
        except (urllib.error.URLError, socket.timeout, TimeoutError) as error:
            last_error = f"{model}: {error.__class__.__name__}"
            continue

        candidates = reply.get("candidates") or []

        if not candidates:
            raise AnalysisError("Gemini фотоны талдаудан бас тартты")

        if candidates[0].get("finishReason") != "STOP":
            raise AnalysisError(f"Gemini жауабы толық емес: {candidates[0].get('finishReason')}")

        text = "".join(part.get("text", "") for part in candidates[0]["content"]["parts"])

        return json.loads(text), reply.get("modelVersion", model)

    raise AnalysisError(f"Gemini модельдері бос емес ({last_error}), кейінірек қайта талдаңыз")


def ask_claude(image_data, request):
    """Sends one photo to Claude and returns (parsed answer, model that answered)."""

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=90.0)

    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM_PROMPT,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": image_data},
                },
                {"type": "text", "text": request_text(request)},
            ],
        }],
    )

    if response.stop_reason == "refusal":
        raise AnalysisError("Модель фотоны талдаудан бас тартты")

    if response.stop_reason == "max_tokens":
        raise AnalysisError("Модель жауабы толық келмеді")

    text = next((block.text for block in response.content if block.type == "text"), None)

    if text is None:
        raise AnalysisError("Модель жауабында мәтін жоқ")

    return json.loads(text), response.model


def review_reason(analysis, request):
    """Why a person should check this suggestion, or "" when it can be trusted."""

    reasons = []

    if not analysis.is_waste_photo:
        reasons.append("фотода қоқыс көрінбейді")

    if analysis.hazard == RequestAnalysis.Hazard.HIGH:
        reasons.append("қауіпті қалдық белгісі")
    elif analysis.hazard == RequestAnalysis.Hazard.POSSIBLE:
        reasons.append("қауіп болуы мүмкін")

    if analysis.confidence is not None and analysis.confidence < LOW_CONFIDENCE:
        reasons.append("AI сенімсіз")

    if analysis.suggested_category and analysis.suggested_category != request.category:
        reasons.append("санат тұрғынның таңдауынан өзгеше")

    return ", ".join(reasons)


def analyze_request(request_id):
    """Runs the analysis for one request and stores the result. Never raises."""

    request = ResidentRequest.objects.get(pk=request_id)

    analysis, _ = RequestAnalysis.objects.get_or_create(request=request)

    if not request.photo:
        analysis.state = RequestAnalysis.State.SKIPPED
        analysis.error = "Фото тіркелмеген"
        analysis.save()
        return analysis

    if not is_enabled():
        analysis.state = RequestAnalysis.State.SKIPPED
        analysis.error = "AI кілті бапталмаған"
        analysis.save()
        return analysis

    analysis.state = RequestAnalysis.State.PENDING
    analysis.error = ""
    analysis.save()

    try:
        answer, model = ask_model(prepare_image(request.photo), request)
    except Exception as error:
        logger.exception("AI analysis failed for %s", request.code)
        analysis.state = RequestAnalysis.State.FAILED
        analysis.error = str(error)[:300] or error.__class__.__name__
        analysis.save()
        return analysis

    analysis.is_waste_photo = bool(answer["is_waste_photo"])
    analysis.waste_type = answer["waste_type"]
    analysis.volume = answer["volume"]
    analysis.hazard = answer["hazard"]
    analysis.hazard_reason = answer["hazard_reason"][:300]
    analysis.suggested_category = answer["suggested_category"]
    analysis.confidence = min(max(float(answer["confidence"]), 0.0), 1.0)
    analysis.summary = answer["summary"][:1000]
    analysis.model = model
    analysis.review_reason = review_reason(analysis, request)
    analysis.needs_review = bool(analysis.review_reason)
    analysis.state = RequestAnalysis.State.DONE
    analysis.save()

    return analysis


def start_analysis(request):
    """Starts the analysis without making the resident wait for it."""

    RequestAnalysis.objects.get_or_create(request=request)

    if settings.AI_ANALYSIS_SYNC:
        return analyze_request(request.pk)

    def run():
        close_old_connections()
        try:
            analyze_request(request.pk)
        finally:
            close_old_connections()

    threading.Thread(target=run, daemon=True).start()
