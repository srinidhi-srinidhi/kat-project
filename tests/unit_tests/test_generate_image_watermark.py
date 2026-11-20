"""Generate Image Watermark Tests - unit tests for the Etown watermark and AI-generated metadata.

Unit tests for generate_image._apply_watermark and _encode_for_web: bottom-right placement, dark-corner text whitening, unchanged dimensions, and AI provenance tags in PNG, JPEG and WebP output.

Functions:
    _solid_png(): Build a solid-colour PNG of the given size
    test_watermark_only_touches_bottom_right(): Pixels change only in the bottom-right corner
    test_light_corner_keeps_dark_text(): Light backgrounds keep the logo's dark lettering
    test_dark_corner_whitens_text(): Dark backgrounds get near-white lettering
    test_png_carries_ai_metadata(): Watermarked PNG carries AI text and XMP tags
    test_switch_off_skips_stamp_but_keeps_metadata(): IMAGE_WATERMARK_ENABLED=False skips the stamp, keeps AI tags
    test_web_encodes_carry_ai_metadata(): JPEG and WebP re-encodes carry EXIF and XMP AI tags

Updates:
    28-Sep-2026: Initial creation; IMAGE_WATERMARK_ENABLED switch-off test.
"""
from io import BytesIO

import pytest
from PIL import Image

from app.agent_infrastructure.tools.tools_utils.generate_image import _apply_watermark, _encode_for_web
from app.core.config import settings


def _solid_png(size, colour):
    """Build a solid-colour PNG of the given size."""
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("size", [(1024, 1024), (1280, 720), (720, 1280)])
def test_watermark_only_touches_bottom_right(size):
    """Pixels change only in the bottom-right corner and the size is unchanged."""
    out = Image.open(BytesIO(_apply_watermark(_solid_png(size, (230, 230, 230))))).convert("RGB")
    assert out.size == size
    diff = Image.new("RGB", size, (230, 230, 230))
    changed = [
        (x, y) for x in range(0, size[0], 4) for y in range(0, size[1], 4)
        if out.getpixel((x, y)) != diff.getpixel((x, y))
    ]
    assert changed
    assert min(x for x, _ in changed) > size[0] * 0.6
    assert min(y for _, y in changed) > size[1] * 0.75


def test_light_corner_keeps_dark_text():
    """Light backgrounds keep the logo's dark lettering."""
    out = Image.open(BytesIO(_apply_watermark(_solid_png((1024, 1024), (245, 245, 245))))).convert("L")
    corner = out.crop((700, 850, 1024, 1024))
    assert corner.getextrema()[0] < 80


def test_dark_corner_whitens_text():
    """Dark backgrounds get near-white lettering."""
    out = Image.open(BytesIO(_apply_watermark(_solid_png((1024, 1024), (20, 20, 30))))).convert("RGB")
    corner = out.crop((700, 850, 1024, 1024))
    assert sum(1 for p in corner.get_flattened_data() if min(p) > 200) > 500


def test_png_carries_ai_metadata():
    """Watermarked PNG carries AI text and XMP tags."""
    out = Image.open(BytesIO(_apply_watermark(_solid_png((512, 512), (100, 150, 200)))))
    assert out.text.get("ai_generated") == "true"
    assert out.text.get("Software") == "Etown AI"
    assert "trainedAlgorithmicMedia" in out.text.get("XML:com.adobe.xmp", "")


def test_switch_off_skips_stamp_but_keeps_metadata(monkeypatch):
    """IMAGE_WATERMARK_ENABLED=False leaves pixels untouched while still tagging the PNG as AI-generated."""
    monkeypatch.setattr(settings, "IMAGE_WATERMARK_ENABLED", False)
    out = Image.open(BytesIO(_apply_watermark(_solid_png((512, 512), (230, 230, 230)))))
    assert out.convert("RGB").getcolors() == [(512 * 512, (230, 230, 230))]
    assert out.text.get("ai_generated") == "true"


@pytest.mark.parametrize("fmt", ["jpeg", "webp"])
def test_web_encodes_carry_ai_metadata(fmt):
    """JPEG and WebP re-encodes carry EXIF and XMP AI tags."""
    body, _ext, _ctype = _encode_for_web(_apply_watermark(_solid_png((512, 512), (90, 90, 90))), fmt, 85, None)
    image = Image.open(BytesIO(body))
    assert image.getexif().get(0x010E) == "AI-generated image created with Etown"
    assert "trainedAlgorithmicMedia" in str(image.info.get("xmp", b""))
