"""Bounded branding PNGs, without a native image decoder in the web process.

Browsers convert JPEG/WebP locally. Only RGB/RGBA, non-interlaced 8-bit PNG
scanlines cross this boundary. Metadata, animations and trailing data are removed.
"""

import re
import struct
import zlib

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_safe

from .models import Branding
from .security import limited

LIMITS = {"logo": (512, 512 * 1024), "background": (1600, 2 * 1024**2)}
SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def canonical_png(data, kind):
    edge, maximum = LIMITS[kind]
    if len(data) > maximum or not data.startswith(SIGNATURE):
        raise ValueError("PNG size/signature")
    position, header, compressed, ended, chunks = 8, None, bytearray(), False, 0
    idat_ended = False
    while position < len(data):
        chunks += 1
        if chunks > 1024 or position + 12 > len(data):
            raise ValueError("PNG chunk boundary")
        length = struct.unpack_from(">I", data, position)[0]
        tag = data[position + 4 : position + 8]
        end = position + 12 + length
        if end > len(data) or not re.fullmatch(b"[A-Za-z]{4}", tag):
            raise ValueError("PNG chunk length/type")
        payload = data[position + 8 : end - 4]
        if zlib.crc32(tag + payload) != struct.unpack_from(">I", data, end - 4)[0]:
            raise ValueError("PNG checksum")
        if header is None and tag != b"IHDR":
            raise ValueError("PNG header order")
        if tag == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("PNG header")
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            if not (
                1 <= width <= edge
                and 1 <= height <= edge
                and depth == 8
                and color in (2, 6)
                and (compression, filtering, interlace) == (0, 0, 0)
            ):
                raise ValueError("PNG dimensions/encoding")
            header = payload
        elif tag == b"IDAT":
            if idat_ended:
                raise ValueError("PNG IDAT order")
            compressed.extend(payload)
        elif tag == b"IEND":
            if length or not compressed or end != len(data):
                raise ValueError("PNG end/trailing data")
            ended = True
            break
        else:
            if compressed:
                idat_ended = True
            # Reject unknown critical chunks (including palette PNGs), strip all
            # ancillary chunks without interpreting or decompressing metadata.
            if not tag[0] & 32:
                raise ValueError("PNG critical chunk")
        position = end
    if not ended:
        raise ValueError("PNG incomplete")
    stride = 1 + width * (3 if color == 2 else 4)
    expected = stride * height  # At most ~10 MiB, regardless of compressed input.
    inflater = zlib.decompressobj()
    raw = inflater.decompress(bytes(compressed), expected + 1)
    if (
        len(raw) != expected
        or not inflater.eof
        or inflater.unused_data
        or inflater.unconsumed_tail
    ):
        raise ValueError("PNG pixel size")
    if any(raw[i] > 4 for i in range(0, expected, stride)):
        raise ValueError("PNG scanline filter")
    # Recompress the bounded scanlines, excluding metadata and arbitrary padding.
    output = (
        SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    if len(output) > maximum:
        raise ValueError("PNG output size")
    return output


def clean_image(upload, kind):
    maximum = LIMITS[kind][1]
    try:
        if upload.size > maximum:
            raise ValueError("image size")
        data = canonical_png(upload.read(maximum + 1), kind)
    except (ValueError, zlib.error, struct.error):
        raise ValidationError(
            _(
                "Image invalide ou trop volumineuse. Utilisez un PNG RVB ou laissez le navigateur optimiser un JPEG/WebP."
            )
        ) from None
    from .storage import scan

    scan(SimpleUploadedFile(f"{kind}.png", data, content_type="image/png"))
    return data


def contrast_color(color):
    rgb = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    luminance = sum(
        c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )
    return "#000000" if luminance > 0.179 else "#ffffff"


def response_for(request, item, kind, token=None):
    if not item:
        raise Http404
    if kind in LIMITS:
        data = getattr(item, f"{kind}_data")
        if not data:
            raise Http404
        response = HttpResponse(bytes(data), content_type="image/png")
        response["Content-Disposition"] = f'inline; filename="{kind}.png"'
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        return response
    if kind != "style":
        raise Http404
    declarations = []
    for value, prop in (
        (item.accent_color, "--accent"),
        (item.background_color, "--brand-background"),
    ):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            declarations.append(f"{prop}:{value}")
    if re.fullmatch(r"#[0-9a-fA-F]{6}", item.accent_color):
        declarations.append(f"--accent-contrast:{contrast_color(item.accent_color)}")
    if item.background_data:
        url = (
            reverse("share_brand_media", args=[token, "background"])
            if token
            else reverse("brand_media", args=["background"])
        )
        declarations.append(f'--brand-image:url("{url}")')
    return HttpResponse(
        ".branded-surface{" + ";".join(declarations) + "}",
        content_type="text/css; charset=utf-8",
    )


@login_required
@require_safe
def own(request, kind):
    return response_for(
        request, Branding.objects.filter(user=request.user).first(), kind
    )


@require_safe
def shared(request, token, kind):
    from .views import public_link, can_open, too_many

    if limited(request, "brand-media", 240, 60):
        return too_many(request)
    link = public_link(token)
    if (
        link.status_code != "active"
        or not link.collection.owner.is_active
        or not can_open(request, link)
    ):
        raise Http404
    return response_for(
        request, getattr(link.collection.owner, "branding", None), kind, token
    )
