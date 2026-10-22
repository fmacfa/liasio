"""One disposable decoder process. No Django, credentials or original output."""

import math
import resource
import runpy
import sys
import warnings
from pathlib import Path

# Bound work before importing native parsers. Docker supplies the stronger
# network/filesystem/memory boundary; local development is not a sandbox.
resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024**2, 2 * 1024**2))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
if sys.platform.startswith("linux"):
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024**2, 384 * 1024**2))

source, output, kind = sys.argv[1:4]
if sys.argv[4:] == ["--sandbox"]:
    sandbox = runpy.run_path(str(Path(__file__).with_name("preview_sandbox.py")))
    sandbox["confine"](Path(output).parent)
elif sys.argv[4:]:
    raise ValueError("Invalid renderer options")

from PIL import Image, ImageOps  # noqa: E402

Image.MAX_IMAGE_PIXELS = 24_000_000
warnings.simplefilter("error", Image.DecompressionBombWarning)


def save(image):
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    # A fresh canvas drops EXIF, XMP, comments and color-profile metadata.
    clean = Image.new("RGB", image.size, "white")
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        clean.paste(rgba, mask=rgba.getchannel("A"))
    else:
        clean.paste(image.convert("RGB"))
    clean.save(output, "JPEG", quality=78, optimize=False)


if kind == "image":
    with Image.open(source, formats=["JPEG", "PNG", "WEBP", "GIF"]) as image:
        image.seek(0)  # A still preview, including animated uploads.
        save(ImageOps.exif_transpose(image))
elif kind == "pdf":
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(source) as document:
        if len(document) < 1:
            raise ValueError("Empty PDF")
        page = document[0]
        width, height = page.get_size()
        if not all(math.isfinite(n) and 0 < n <= 100000 for n in (width, height)):
            raise ValueError("Invalid page dimensions")
        # No form initialization, JavaScript invocation, links or attachments.
        bitmap = page.render(scale=min(2, 1600 / max(width, height)), draw_annots=False)
        try:
            save(bitmap.to_pil())
        finally:
            bitmap.close()
            page.close()
else:
    raise ValueError("Unsupported format")
