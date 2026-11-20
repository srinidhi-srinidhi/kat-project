"""Images Endpoint Tests - unit tests for POST /api/images/generate and its watermark option.

Unit tests for the images endpoint: the watermark and store flags reach handle_generate_image, store=False skips Blob and returns inline, failures become 502, the route needs verify_token, and AI metadata survives an unstamped image.

Functions:
    _png(): Build a flat PNG of the given size
    _patch_handler(): Replace handle_generate_image with a recorder returning a fixed output
    test_generate_passes_watermark_flag_and_fields(): The watermark flag and request fields reach the handler unchanged
    test_generate_defaults_to_watermarked(): Omitting watermark and store requests a watermarked, stored image
    test_generate_passes_store_flag(): store=false reaches the handler and the response reports an unstored inline image
    test_handler_skips_blob_when_store_is_off(): store=False never uploads and returns a decodable data URL in the requested format
    test_generate_failure_is_502(): A handler failure message becomes a 502 with that message
    test_generate_route_requires_bearer_token(): The route is mounted at /api/images/generate behind verify_token
    test_request_rejects_oversized_sides(): Sides above 1280 are rejected before any generation
    test_watermark_stamp_is_optional_but_metadata_stays(): stamp=False keeps every pixel yet still tags the PNG as AI-generated

Updates:
    30-Sep-2026: Initial creation; store flag tests.
"""
from io import BytesIO

import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError

from app.api.v1.endpoints import images
from app.agent_infrastructure.tools.tools_utils import generate_image as gi


def _png(size=(512, 512), colour=(40, 160, 90)):
    """Build a flat PNG of the given size."""
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def _patch_handler(monkeypatch, output):
    """Replace handle_generate_image with a recorder returning a fixed output."""
    calls = []

    async def _handler(**kwargs):
        calls.append(kwargs)
        return ("Here's the image.", output) if output else ("Sorry, the image service is temporarily unavailable.", None)

    monkeypatch.setattr(images, "handle_generate_image", _handler)
    return calls


_OUTPUT = {
    "image_url": "https://blob.example/a.jpg", "blob_path": "ai_generated/a.jpg", "image_type": "offer",
    "watermarked": False, "text_on_image": ["20% OFF", "Style Hub"], "design": {"headline": "20% OFF", "layout": "top"},
    "enhanced_prompt": "cakes", "seed": 7, "width": 1024, "height": 1024, "brand_logo": False, "text_check": None, "stored": True,
}


@pytest.mark.asyncio
async def test_generate_passes_watermark_flag_and_fields(monkeypatch):
    """The watermark flag and request fields reach the handler unchanged."""
    calls = _patch_handler(monkeypatch, _OUTPUT)
    payload = images.GenerateImageRequest(prompt="20% off at Style Hub", image_type="offer", watermark=False, seed=7, user_profile_id="up-1")
    response = await images.generate_image(payload, current_user="token")
    assert calls[0]["watermark"] is False
    assert (calls[0]["image_type"], calls[0]["seed"], calls[0]["user_profile_id"], calls[0]["store_format"]) == ("offer", 7, "up-1", "jpeg")
    assert response.watermarked is False and response.image_url == _OUTPUT["image_url"]
    assert response.text_on_image == ["20% OFF", "Style Hub"]


@pytest.mark.asyncio
async def test_generate_defaults_to_watermarked(monkeypatch):
    """Omitting watermark and store requests a watermarked, stored image."""
    calls = _patch_handler(monkeypatch, {**_OUTPUT, "image_type": "logo", "watermarked": True})
    response = await images.generate_image(images.GenerateImageRequest(prompt="logo for Sweet Crumbs", image_type="logo"), current_user="token")
    assert calls[0]["watermark"] is True and calls[0]["store_format"] == "png"
    assert calls[0]["store"] is True
    assert response.watermarked is True and response.stored is True


@pytest.mark.asyncio
async def test_generate_passes_store_flag(monkeypatch):
    """store=false reaches the handler and the response reports an unstored inline image."""
    inline = {**_OUTPUT, "image_url": "data:image/jpeg;base64,AAAA", "blob_path": None, "stored": False}
    calls = _patch_handler(monkeypatch, inline)
    response = await images.generate_image(images.GenerateImageRequest(prompt="20% off at Style Hub", image_type="offer", store=False), current_user="token")
    assert calls[0]["store"] is False
    assert response.stored is False and response.blob_path is None and response.image_url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
@pytest.mark.parametrize("image_type,store_format,mime", [("offer", "jpeg", "image/jpeg"), ("logo", "png", "image/png")])
async def test_handler_skips_blob_when_store_is_off(monkeypatch, image_type, store_format, mime):
    """store=False never calls the blob upload and returns a decodable data URL in the requested format."""
    import base64
    import json

    import httpx

    class _Response:
        status_code = 200
        content = _png()
        text = ""

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, params=None, headers=None):
            return _Response()

    class _Planner:
        def bind(self, **_kwargs):
            return self

        async def ainvoke(self, _messages):
            return type("Reply", (), {"content": json.dumps({"scene": "cakes", "headline": "20% OFF"})})()

    async def _no_upload(*_args, **_kwargs):
        raise AssertionError("blob upload must not run when store is off")

    async def _clean(_png_bytes):
        return False

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(gi, "_store_png", _no_upload)
    monkeypatch.setattr(gi, "_has_stray_text", _clean)
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _Planner())
    _message, output = await gi.handle_generate_image("20% off cakes", image_type, 512, 512, store_format=store_format, store=False)
    assert output["stored"] is False and output["blob_path"] is None
    header, data = output["image_url"].split(",", 1)
    assert header == f"data:{mime};base64"
    assert Image.open(BytesIO(base64.b64decode(data))).size == (512, 512)


@pytest.mark.asyncio
async def test_generate_failure_is_502(monkeypatch):
    """A handler failure message becomes a 502 with that message."""
    _patch_handler(monkeypatch, None)
    with pytest.raises(HTTPException) as caught:
        await images.generate_image(images.GenerateImageRequest(prompt="a cat"), current_user="token")
    assert caught.value.status_code == 502 and "unavailable" in caught.value.detail


def test_generate_route_requires_bearer_token():
    """The route is mounted at /api/images/generate behind verify_token."""
    from app.core.security import verify_token
    from app.main import app

    route = next(route for route in images.router.routes if getattr(route, "path", "") == "/generate")
    assert "POST" in route.methods
    assert any(dependency.call is verify_token for dependency in route.dependant.dependencies)
    assert "post" in app.openapi()["paths"]["/api/images/generate"]


def test_request_rejects_oversized_sides():
    """Sides above 1280 are rejected before any generation."""
    with pytest.raises(ValidationError):
        images.GenerateImageRequest(prompt="a cat", width=1536)


def test_watermark_stamp_is_optional_but_metadata_stays():
    """stamp=False keeps every pixel yet still tags the PNG as AI-generated."""
    source = _png()
    clean = Image.open(BytesIO(gi._apply_watermark(source, stamp=False)))
    assert clean.info.get("ai_generated") == "true"
    assert clean.convert("RGB").getcolors(512 * 512) == [(512 * 512, (40, 160, 90))]
    if gi.settings.IMAGE_WATERMARK_ENABLED:
        stamped = Image.open(BytesIO(gi._apply_watermark(source))).convert("RGB")
        assert len(stamped.getcolors(512 * 512)) > 1
