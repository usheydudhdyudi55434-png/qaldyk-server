"""QR codes the system prints: stickers on containers and posters for cleanup days.

Each QR is a plain link, so any phone camera opens it, and the in-app scanner
recognises it without a network call.
"""

import segno
from django.urls import reverse
from django.utils.safestring import mark_safe


def container_path(container):
    return reverse("container_qr", args=[container.pk])


def event_path(event):
    return reverse("event_check_in", args=[event.token])


def svg(url, scale=6):
    """An inline SVG of the QR; dark modules in the brand's navy for print."""
    code = segno.make(url, error="m")
    return mark_safe(code.svg_inline(scale=scale, border=2, dark="#0f2a63", light="#ffffff"))
