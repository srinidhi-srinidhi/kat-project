import asyncio
import json
from types import SimpleNamespace

import pytest

import app.etown.services.services_utils as services_utils
from app.schemas.api import GeneralCategoryGenerationResponse


class _FakeModel:
    async def ainvoke(self, prompt):
        return SimpleNamespace(
            content=json.dumps({"business_type": "product", "subcategory": "Women's clothing"})
        )


@pytest.fixture
def fake_model(monkeypatch):
    monkeypatch.setattr(services_utils, "gpt41_precise", _FakeModel())


@pytest.mark.asyncio
async def test_general_category_is_generated_by_default(fake_model, monkeypatch):
    async def general(description, **kwargs):
        return GeneralCategoryGenerationResponse(generalcategory="clothing_store")

    monkeypatch.setattr(services_utils, "generate_general_category_logic", general)

    result = await services_utils.generate_category_logic("A boutique for women's formal wear")

    assert result.business_type == "product"
    assert result.subcategory == "Women's clothing"
    assert result.generalcategory == "clothing_store"


@pytest.mark.asyncio
async def test_failed_general_category_is_none_not_unknown(fake_model, monkeypatch):
    async def general(description, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(services_utils, "generate_general_category_logic", general)

    result = await services_utils.generate_category_logic("A boutique for women's formal wear")

    assert result.generalcategory is None


@pytest.mark.asyncio
async def test_opting_out_skips_the_general_category_call(fake_model, monkeypatch):
    calls = []

    async def general(description, **kwargs):
        calls.append(description)
        return GeneralCategoryGenerationResponse(generalcategory="clothing_store")

    monkeypatch.setattr(services_utils, "generate_general_category_logic", general)

    result = await services_utils.generate_category_logic(
        "A boutique for women's formal wear", include_general=False
    )

    assert result.generalcategory is None
    assert calls == []


@pytest.mark.asyncio
async def test_unparseable_classification_cancels_the_general_category_call(monkeypatch):
    class _BadModel:
        async def ainvoke(self, prompt):
            return SimpleNamespace(content="no json here")

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def general(description, **kwargs):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(services_utils, "gpt41_precise", _BadModel())
    monkeypatch.setattr(services_utils, "generate_general_category_logic", general)

    with pytest.raises(ValueError):
        await services_utils.generate_category_logic("A boutique")

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert cancelled.is_set() or not started.is_set()
