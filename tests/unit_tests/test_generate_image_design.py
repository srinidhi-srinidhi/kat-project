"""Generate Image Design Tests - unit tests for design planning, typesetting and the refinement seed.

Unit tests for generate_image.plan_image_design, _layout_text, _typeset_design and handle_generate_image: JSON parsing and fallback, business and previous-design context, text fitting, logo white cleanup and recomposition, poster text regions, and seed reuse.

Classes:
    _FakeLLM(): Stand-in for gpt4o_mini_creative returning a fixed reply and recording messages

Functions:
    _png(): Build a PNG of the given size and colour, optionally with a dark square emblem
    test_plan_parses_json_and_limits_fields(): Valid JSON becomes a design with clipped text and a checked accent
    test_plan_falls_back_on_bad_json(): Non-JSON replies fall back to the raw prompt with no text
    test_logo_name_capitalised_only_when_all_lowercase(): All-lowercase logo names get capitals; styled names stay
    test_plan_sends_business_and_previous_design(): Business details and the previous design reach the LLM
    test_plan_drops_invented_phone_and_placeholders(): Unsupplied names, words, numbers, phones, dates and placeholders are dropped
    test_provider_phone_sent_only_on_request(): The profile phone reaches the planner only when the request asks for a number
    test_layout_text_fits_width(): Long text wraps and shrinks to fit the width, and no word is ever dropped
    test_grounding_uses_values_and_normalises_prices(): Section labels and the brand colour hex ground nothing; separators do not matter
    test_general_text_only_on_request(): General images keep a headline only when the request asks for text
    test_grounding_allows_generic_inflections(): Announcement words and generic-word inflections keep a grounded subline
    test_footer_business_name_capitalised(): An all-lowercase business name in an offer or feed footer is capitalised
    test_long_footer_cut_at_segment_boundary(): The footer limit drops whole trailing segments instead of slicing a phone number
    test_long_headline_fully_drawn_on_narrow_images(): Text sizes follow the shorter side, so a long headline keeps all its lines
    test_layout_picks_script_font(): Each Indic script gets its own Noto font and Latin stays on Poppins
    test_logo_without_name_flattens_background(): A faint grey logo background becomes pure white
    test_logo_with_name_recomposes_emblem_and_name(): The emblem sits above typeset name pixels on white
    test_poster_text_stays_in_top_and_footer_regions(): Headline and footer change only their regions
    test_handle_reuses_previous_seed_and_reports_text(): Refinement reuses the seed and the message lists exact text
    _fake_model(): Patch httpx, blob storage and the text check so generation returns statuses in order and logs every call
    test_handle_regenerates_once_on_stray_text(): A picture with stray lettering is regenerated once with a new seed and plain surfaces
    test_handle_keeps_first_image_when_retry_fails(): A failed regeneration keeps the first image and its seed
    test_handle_checks_every_designed_image(): Designed images always get the text check; town cards skip it
    test_handle_passes_layout_and_keeps_previous(): A refinement keeps its layout and fresh posters get a seeded layout
    test_handle_brands_provider_posters(): A provider's profile image becomes the avatar and its colour the accent, except on refinements
    test_layouts_change_only_their_text_area(): Each layout blurs, darkens and writes only its own text area
    test_side_layout_falls_back_to_top_on_portrait(): A side layout on a portrait image is drawn as a top layout
    test_brand_logo_drawn_as_badge_avatar(): The provider logo is pasted as a round avatar at the left of the footer badge

Updates:
    29-Sep-2026: Initial creation.
    30-Sep-2026: Added invented text, phone, date and placeholder tests; provider phone only on request; stray-text retry, layout, branding and footer-name tests.
"""
import json
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from app.agent_infrastructure.tools.tools_utils import generate_image as gi


def _png(size=(512, 512), colour=(246, 246, 246), emblem=False):
    """Build a PNG of the given size and colour, optionally with a dark square emblem."""
    image = Image.new("RGB", size, colour)
    if emblem:
        ImageDraw.Draw(image).rectangle((200, 60, 312, 172), fill=(20, 60, 160))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class _FakeLLM:
    """Stand-in for gpt4o_mini_creative returning a fixed reply and recording messages."""

    def __init__(self, reply):
        """Store the reply text and an empty message log."""
        self.reply = reply
        self.messages = None

    def bind(self, **_kwargs):
        """Return self so bind(...).ainvoke works."""
        return self

    async def ainvoke(self, messages):
        """Record the messages and return an object carrying the reply as content."""
        self.messages = messages
        return type("Reply", (), {"content": self.reply})()


@pytest.mark.asyncio
async def test_plan_parses_json_and_limits_fields(monkeypatch):
    """Valid JSON becomes a design with clipped text and a checked accent."""
    reply = json.dumps({"scene": "a red film reel", "headline": "Dorothy", "subline": "dorothy " * 30, "footer": "", "accent": "red", "font": "serif"})
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(reply))
    design = await gi.plan_image_design("logo for dorothy", "logo")
    assert design["scene"] == "a red film reel"
    assert design["headline"] == "Dorothy"
    assert len(design["subline"]) <= gi._TEXT_LIMITS["subline"] and design["subline"].endswith("dorothy")
    assert design["accent"] == ""
    assert design["font"] == "serif"


@pytest.mark.asyncio
async def test_plan_falls_back_on_bad_json(monkeypatch):
    """Non-JSON replies fall back to the raw prompt with no text."""
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM("a lovely logo"))
    design = await gi.plan_image_design("logo for my shop", "logo")
    assert design["scene"] == "logo for my shop"
    assert design["headline"] == design["subline"] == design["footer"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("name,expected", [("goopi shop", "Goopi Shop"), ("ChatGPT", "ChatGPT"), ("IBM", "IBM"), ("சாய் ஸ்டோர்", "சாய் ஸ்டோர்")])
async def test_logo_name_capitalised_only_when_all_lowercase(monkeypatch, name, expected):
    """All-lowercase logo names get capitals; styled names stay."""
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "s", "headline": name})))
    assert (await gi.plan_image_design(f"logo for {name}", "logo"))["headline"] == expected


@pytest.mark.asyncio
async def test_plan_sends_business_and_previous_design(monkeypatch):
    """Business details and the previous design reach the LLM."""
    fake = _FakeLLM(json.dumps({"scene": "s", "headline": "Kumar Tea Stall"}))
    monkeypatch.setattr(gi, "gpt4o_mini_creative", fake)
    await gi.plan_image_design(
        "add a leaf",
        "logo",
        {"businessname": "Kumar Tea Stall", "businesscategory": "Tea shop", "mobilenumber": None},
        {"enhanced_prompt": "a steaming cup", "design": {"headline": "Kumar Tea Stall"}},
    )
    user_message = fake.messages[1]["content"]
    assert "Business name: Kumar Tea Stall" in user_message
    assert "Phone" not in user_message
    assert "a steaming cup" in user_message


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,business,footer,expected", [
    ("poster for 20% off cakes this weekend at Sweet Crumbs", None, "Sweet Crumbs · Valid this weekend · Call 90123 45678", "Sweet Crumbs · Valid this weekend"),
    ("poster for Sweet Crumbs, call me on 9876543210", None, "Sweet Crumbs · Call +91 98765 43210", "Sweet Crumbs · Call +91 98765 43210"),
    ("poster with my number", {"businessname": "Sweet Crumbs", "mobilenumber": "9876543210"}, "Sweet Crumbs · Call 98765 43210", "Sweet Crumbs · Call 98765 43210"),
    ("Sweet Crumbs offer valid till 01-10-2026", None, "Sweet Crumbs · Till 01-10-2026", "Sweet Crumbs · Till 01-10-2026"),
    ("post announcing a new branch", None, "Your Business Name", ""),
    ("post announcing a new Sweet Crumbs branch", None, "Sweet Crumbs · Call [Phone]", "Sweet Crumbs"),
    ("30% off haircuts this Diwali at Style Hub", None, "Style Hub · Till 5 Nov", "Style Hub"),
    ("50% off sarees, Lakshmi Silks", None, "Lakshmi Silks · Till 5 Oct", "Lakshmi Silks"),
    ("20% off till October 5th at Style Hub", None, "Style Hub · Till 5 Oct", "Style Hub · Till 5 Oct"),
    ("Style Hub sale valid until 12/10", None, "Style Hub · Valid till 12/10", "Style Hub · Valid till 12/10"),
    ("Style Hub sale valid until 12/10", None, "Style Hub · Valid till 15/10", "Style Hub"),
    ("Diwali sale at Style Hub", None, "Style Hub · Valid this Diwali", "Style Hub · Valid this Diwali"),
    ("poster for 20% off", None, "Sweet Crumbs", ""),
    ("20% off at Style Hub", None, "Style Hub · All clothing and accessories", "Style Hub"),
    ("20% off at Style Hub", None, "Style Hub · Only ₹499", "Style Hub"),
    ("20% off on haircuts at Style Hub", None, "Style Hub · Haircut offer", "Style Hub · Haircut offer"),
    ("दिवाली सेल - सभी साड़ियों पर 50% छूट, Lakshmi Silks", None, "Lakshmi Silks · सभी साड़ियों पर", "Lakshmi Silks · सभी साड़ियों पर"),
])
async def test_plan_drops_invented_phone_and_placeholders(monkeypatch, prompt, business, footer, expected):
    """Unsupplied names, words, numbers, phones, dates and placeholder segments are dropped; supplied ones stay."""
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "s", "headline": "20% OFF", "footer": footer})))
    assert (await gi.plan_image_design(prompt, "offer", business))["footer"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,sent", [
    ("announce evening yoga batches from 6 pm", False),
    ("offer poster: first month free, add my number", True),
    ("poster with my contact details", True),
])
async def test_provider_phone_sent_only_on_request(monkeypatch, prompt, sent):
    """The profile phone reaches the planner, and survives on the image, only when the request asks for a number."""
    fake = _FakeLLM(json.dumps({"scene": "s", "headline": "Yoga", "footer": "Olive Pink · 9952073434"}))
    monkeypatch.setattr(gi, "gpt4o_mini_creative", fake)
    design = await gi.plan_image_design(prompt, "feed", {"businessname": "Olive Pink", "mobilenumber": "9952073434"})
    assert ("Phone: 9952073434" in fake.messages[1]["content"]) is sent
    assert design["footer"] == ("Olive Pink · 9952073434" if sent else "Olive Pink")


def test_layout_text_fits_width():
    """Long text wraps and shrinks to fit the width, and no word is ever dropped."""
    text = "Grand opening of our brand new branch in Chennai"
    layout = gi._layout_text(text, "heavy", 400, 90, 2)
    assert all(width <= 400 for _, width in layout["lines"])
    assert layout["size"] < 90
    assert " ".join("".join(run for run, _ in runs) for runs, _ in layout["lines"]) == text


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,business,field,text,expected", [
    ("20% off cakes", {"businessname": "Sweet Crumbs", "businesscategory": "Bakery"}, "footer", "Shop Name · Provider Profile · Sweet Crumbs", "Sweet Crumbs"),
    ("offer 10% off", {"businessname": "Sweet Crumbs", "brand_colour": "#B91C1C"}, "headline", "Buy 1 Get 1", ""),
    ("cakes now Rs 1499 at Sweet Crumbs", None, "subline", "Now ₹1,499", "Now ₹1,499"),
    ("cakes now Rs 1,499 at Sweet Crumbs", None, "subline", "Now ₹1499", "Now ₹1499"),
])
async def test_grounding_uses_values_and_normalises_prices(monkeypatch, prompt, business, field, text, expected):
    """Section labels and the brand colour hex ground nothing, and thousands separators do not matter."""
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "s", field: text})))
    assert (await gi.plan_image_design(prompt, "offer", business))[field] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt,expected", [
    ("a tall portrait of a Kathakali dancer", ""),
    ("a Kathakali dancer with the title Kathakali Dancer", "Kathakali Dancer"),
    ("a Kathakali dancer, write 'Kathakali Dancer' on it", "Kathakali Dancer"),
])
async def test_general_text_only_on_request(monkeypatch, prompt, expected):
    """General images keep a headline only when the request asks for text, and never a subline or footer."""
    reply = json.dumps({"scene": "s", "headline": "Kathakali Dancer", "subline": "Kathakali", "footer": "Kathakali"})
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(reply))
    design = await gi.plan_image_design(prompt, "general")
    assert (design["headline"], design["subline"], design["footer"]) == (expected, "", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("subline", ["Starting at 6 PM", "Now offering evening batches", "Celebrating evening yoga"])
async def test_grounding_allows_generic_inflections(monkeypatch, subline):
    """Announcement words and inflections of generic words do not get a grounded subline dropped."""
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "s", "subline": subline})))
    design = await gi.plan_image_design("announce that we now have evening yoga batches from 6 pm", "feed")
    assert design["subline"] == subline


@pytest.mark.asyncio
@pytest.mark.parametrize("image_type,prompt,business,footer,expected", [
    ("offer", "20% off everything this week", {"businessname": "asif boss juice corner"}, "asif boss juice corner", "Asif Boss Juice Corner"),
    ("feed", "we now open at 7 am", {"businessname": "asif boss juice corner"}, "Visit asif boss juice corner", "Visit Asif Boss Juice Corner"),
    ("offer", "20% off at style hub", None, "style hub", "Style Hub"),
    ("offer", "20% off at ELEVE STUDIO", {"businessname": "ELEVE STUDIO"}, "ELEVE STUDIO", "ELEVE STUDIO"),
    ("offer", "20% off at McDonald's, valid till 10 October", None, "McDonald's · valid till 10 October", "McDonald's · valid till 10 October"),
])
async def test_footer_business_name_capitalised(monkeypatch, image_type, prompt, business, footer, expected):
    """An all-lowercase business name in an offer or feed footer is capitalised; styled names and later segments stay."""
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "s", "footer": footer})))
    assert (await gi.plan_image_design(prompt, image_type, business))["footer"] == expected


@pytest.mark.asyncio
async def test_long_footer_cut_at_segment_boundary(monkeypatch):
    """The footer limit drops whole trailing segments instead of slicing a phone number."""
    name = "Sri Lakshmi Narasimha Silks and Sarees Emporium of Mylapore Luz Corner Chennai"
    footer = f"{name} · Valid this Diwali · Call 98765 43210"
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "s", "footer": footer})))
    design = await gi.plan_image_design(f"{name} Diwali offer, call 9876543210", "offer")
    assert design["footer"] == f"{name} · Valid this Diwali"


@pytest.mark.parametrize("size,layout", [((256, 1280), "center"), ((1024, 1024), "side"), ((256, 256), "bottom")])
def test_long_headline_fully_drawn_on_narrow_images(size, layout):
    """Text sizes follow the shorter side, so a long headline fits every layout without losing lines."""
    headline = "Mega Diwali Dhamaka Sale on All Silk Sarees"
    layout_result = gi._layout_text(headline, "heavy", size[0] * (0.44 if layout == "side" else 0.86), int(min(size) * 0.12), 2)
    assert " ".join("".join(run for run, _ in runs) for runs, _ in layout_result["lines"]) == headline
    buffer = BytesIO()
    Image.new("RGB", size, (40, 160, 90)).save(buffer, "PNG")
    out = Image.open(BytesIO(gi._typeset_design(buffer.getvalue(), "offer", {"headline": headline, "layout": layout}))).convert("RGB")
    assert out.size == size


@pytest.mark.parametrize("text,font_file", [
    ("கடை", "NotoSansTamil-Bold.ttf"), ("दुकान", "NotoSansDevanagari-Bold.ttf"), ("കട", "NotoSansMalayalam-Bold.ttf"),
    ("దుకాణం", "NotoSansTelugu-Bold.ttf"), ("ಅಂಗಡಿ", "NotoSansKannada-Bold.ttf"), ("দোকান", "NotoSansBengali-Bold.ttf"),
    ("દુકાન", "NotoSansGujarati-Bold.ttf"), ("ਦੁਕਾਨ", "NotoSansGurmukhi-Bold.ttf"), ("ଦୋକାନ", "NotoSansOriya-Bold.ttf"),
    ("Shop", "Poppins-Bold.ttf"),
])
def test_layout_picks_script_font(text, font_file):
    """Each Indic script gets its own Noto font and Latin stays on Poppins."""
    runs, _width = gi._layout_text(text, "bold", 1000, 40, 1)["lines"][0]
    assert all(font.path.endswith(font_file) for _, font in runs)


def test_logo_without_name_flattens_background():
    """A faint grey logo background becomes pure white."""
    out = Image.open(BytesIO(gi._typeset_design(_png(emblem=True), "logo", {"headline": ""}))).convert("RGB")
    assert out.getpixel((5, 5)) == (255, 255, 255)
    assert out.getpixel((256, 116)) == (20, 60, 160)


def test_logo_with_name_recomposes_emblem_and_name():
    """The emblem sits above typeset name pixels on white."""
    design = {"headline": "Dorothy", "subline": "", "accent": "#C8102E", "font": "serif"}
    out = Image.open(BytesIO(gi._typeset_design(_png(emblem=True), "logo", design))).convert("RGB")
    assert out.size == (512, 512)
    pixels = [(x, y) for x in range(0, 512, 2) for y in range(0, 512, 2) if out.getpixel((x, y)) != (255, 255, 255)]
    blue_rows = [y for x, y in pixels if out.getpixel((x, y))[2] > 120 and out.getpixel((x, y))[0] < 80]
    red_rows = [y for x, y in pixels if out.getpixel((x, y))[0] > 150 and out.getpixel((x, y))[2] < 110]
    assert blue_rows and red_rows
    assert max(blue_rows) < min(red_rows)


def test_poster_text_stays_in_top_and_footer_regions():
    """Headline and footer change only their regions."""
    design = {"headline": "25% OFF", "subline": "", "footer": "Sports Arena", "accent": "#1D4ED8"}
    source = Image.new("RGB", (512, 512), (40, 160, 90))
    buffer = BytesIO()
    source.save(buffer, "PNG")
    out = Image.open(BytesIO(gi._typeset_design(buffer.getvalue(), "offer", design))).convert("RGB")
    middle = out.crop((0, 230, 512, 400))
    assert middle.getcolors() == [(512 * 170, (40, 160, 90))]
    assert out.crop((0, 0, 512, 120)).getcolors(512 * 120) != [(512 * 120, (40, 160, 90))]
    footer = out.crop((0, 440, 400, 512)).tobytes()
    assert any(footer[i + 2] > 180 and footer[i] < 80 for i in range(0, len(footer), 3))


@pytest.mark.asyncio
async def test_handle_reuses_previous_seed_and_reports_text(monkeypatch):
    """Refinement reuses the seed and the message lists exact text."""
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "a film reel", "headline": "Dorothy"})))
    sent = {}

    class _Response:
        status_code = 200
        content = _png(emblem=True)
        text = ""

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, params=None, headers=None):
            sent.update(params)
            return _Response()

    async def _no_store(*_args, **_kwargs):
        return None, None

    async def _clean(_png_bytes):
        return False

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(gi, "_store_png", _no_store)
    monkeypatch.setattr(gi, "_has_stray_text", _clean)
    message, output = await gi.handle_generate_image(
        "make the reel red", "logo", 512, 512,
        previous={"enhanced_prompt": "a film reel", "design": {"headline": "Dorothy"}, "seed": 4242},
    )
    assert sent["seed"] == 4242
    assert "negative_prompt" not in sent
    assert output["seed"] == 4242
    assert output["text_on_image"] == ["Dorothy"]
    assert '"Dorothy"' in message


def _fake_model(monkeypatch, statuses, checks, logo=None):
    """Patch httpx, blob storage and the text check so generation returns statuses in order and logs every call."""
    calls = {"seeds": [], "prompts": [], "urls": [], "checks": list(checks)}

    class _Response:
        def __init__(self, status, content):
            self.status_code = status
            self.content = content
            self.text = ""

        def raise_for_status(self):
            if self.status_code != 200:
                raise RuntimeError(self.status_code)

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, params=None, headers=None):
            calls["urls"].append(url)
            if params is None:
                return _Response(200, logo)
            calls["seeds"].append(params["seed"])
            calls["prompts"].append(params["prompt"])
            return _Response(statuses[len(calls["seeds"]) - 1], _png((512, 512), (40, 160, 90)))

    async def _no_store(*_args, **_kwargs):
        return None, None

    async def _check(_png_bytes):
        return calls["checks"].pop(0)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(gi, "_store_png", _no_store)
    monkeypatch.setattr(gi, "_has_stray_text", _check)
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "cakes", "headline": "20% OFF", "footer": "Sweet Crumbs"})))
    return calls


@pytest.mark.asyncio
async def test_handle_regenerates_once_on_stray_text(monkeypatch):
    """A picture with stray lettering is regenerated once with a new seed and the second image is kept."""
    calls = _fake_model(monkeypatch, [200, 200], [True, False])
    _message, output = await gi.handle_generate_image("20% off at Sweet Crumbs", "offer", 512, 512, seed=11)
    assert len(calls["seeds"]) == 2 and calls["seeds"][0] == 11 and calls["seeds"][1] != 11
    assert output["seed"] == calls["seeds"][1]
    assert output["text_check"] == {"stray_text": False, "attempts": 2}
    assert calls["prompts"][0] == "cakes" and calls["prompts"][1] == "cakes" + gi._PLAIN_SURFACES


@pytest.mark.asyncio
async def test_handle_passes_layout_and_keeps_previous(monkeypatch):
    """A refinement keeps the previous layout and the planner is told it; fresh posters get a seeded layout."""
    _fake_model(monkeypatch, [200, 200], [False, False])
    planner = _FakeLLM(json.dumps({"scene": "cakes", "headline": "40% OFF", "layout": "top"}))
    monkeypatch.setattr(gi, "gpt4o_mini_creative", planner)
    previous = {"enhanced_prompt": "cakes", "design": {"headline": "30% OFF", "layout": "side"}, "seed": 7}
    _message, output = await gi.handle_generate_image("change it to 40%", "offer", 512, 512, previous=previous)
    assert "Layout: side" in planner.messages[1]["content"]
    assert output["design"]["layout"] == "side"
    _message, output = await gi.handle_generate_image("40% off cakes", "offer", 512, 512, seed=7)
    assert output["design"]["layout"] == gi.random.Random(7).choice(("top", "bottom", "center", "side"))


@pytest.mark.asyncio
async def test_handle_keeps_first_image_when_retry_fails(monkeypatch):
    """A failed regeneration keeps the first image and its seed instead of failing the request."""
    calls = _fake_model(monkeypatch, [200, 500], [True])
    _message, output = await gi.handle_generate_image("20% off at Sweet Crumbs", "offer", 512, 512, seed=11)
    assert len(calls["seeds"]) == 2
    assert output is not None and output["seed"] == 11
    assert output["text_on_image"] == ["20% OFF", "Sweet Crumbs"]


@pytest.mark.asyncio
async def test_handle_checks_every_designed_image(monkeypatch):
    """Designed images always get the text check; town cards skip it."""
    calls = _fake_model(monkeypatch, [200, 200], [False])
    _message, output = await gi.handle_generate_image("20% off at Sweet Crumbs", "offer", 512, 512)
    assert output["text_check"] == {"stray_text": False, "attempts": 1}
    _message, output = await gi.handle_generate_image("Mylapore, Chennai", "town", 512, 512)
    assert output["text_check"] is None and len(calls["seeds"]) == 2


@pytest.mark.asyncio
async def test_handle_brands_provider_posters(monkeypatch):
    """A provider's profile image becomes the badge avatar and its dominant colour the accent and a planner hint."""
    logo = BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 40)).save(logo, "PNG")
    calls = _fake_model(monkeypatch, [200], [False], logo=logo.getvalue())
    planner = _FakeLLM(json.dumps({"scene": "cakes", "headline": "20% OFF", "footer": "Sweet Crumbs", "accent": "#123456"}))
    monkeypatch.setattr(gi, "gpt4o_mini_creative", planner)

    async def _row(_query, _user_profile_id):
        return {"businessname": "Sweet Crumbs", "businesscategory": "Bakery", "mobilenumber": "9876543210", "serviceimageurl": "/etowndev/logo.png"}

    monkeypatch.setattr(gi.Database, "fetchrow", _row)
    monkeypatch.setattr(gi, "get_blob_base_url", lambda: "https://blob.example")
    _message, output = await gi.handle_generate_image("20% off cakes", "offer", 512, 512, user_profile_id="up-1")
    assert calls["urls"][0] == "https://blob.example/etowndev/logo.png"
    assert output["brand_logo"] is True
    assert "Brand colour: #" in planner.messages[1]["content"]
    assert output["design"]["accent"] != "#123456"
    red, green, blue = (int(output["design"]["accent"][i:i + 2], 16) for i in (1, 3, 5))
    assert red > 150 and green < 80 and blue < 80
    previous = {"enhanced_prompt": "cakes", "design": {"headline": "20% OFF", "footer": "Sweet Crumbs", "layout": "top"}, "seed": 3}
    _fake_model(monkeypatch, [200], [False], logo=logo.getvalue())
    monkeypatch.setattr(gi, "gpt4o_mini_creative", _FakeLLM(json.dumps({"scene": "cakes", "headline": "20% OFF", "accent": "#1D4ED8"})))
    _message, output = await gi.handle_generate_image("make the badge blue", "offer", 512, 512, user_profile_id="up-1", previous=previous)
    assert output["design"]["accent"] == "#1D4ED8"


@pytest.mark.parametrize("layout,changed,unchanged", [
    ("top", (0, 0, 512, 90), (0, 250, 512, 420)),
    ("bottom", (0, 400, 512, 480), (0, 0, 512, 250)),
    ("center", (0, 220, 512, 290), (0, 0, 512, 110)),
    ("side", (0, 150, 250, 350), (300, 0, 512, 420)),
])
def test_layouts_change_only_their_text_area(layout, changed, unchanged):
    """Each layout blurs, darkens and writes only its own text area."""
    image = Image.new("RGB", (512, 512), (40, 160, 90))
    ImageDraw.Draw(image).rectangle((0, 0, 511, 511), outline=(40, 160, 90))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    design = {"headline": "25% OFF", "subline": "", "footer": "", "layout": layout}
    out = Image.open(BytesIO(gi._typeset_design(buffer.getvalue(), "offer", design))).convert("RGB")
    assert len(out.crop(changed).getcolors(512 * 512)) > 1
    assert out.crop(unchanged).getcolors(512 * 512) == [((unchanged[2] - unchanged[0]) * (unchanged[3] - unchanged[1]), (40, 160, 90))]


def test_side_layout_falls_back_to_top_on_portrait():
    """A side layout on a portrait image is drawn as a top layout across the full width."""
    buffer = BytesIO()
    Image.new("RGB", (384, 640), (40, 160, 90)).save(buffer, "PNG")
    design = {"headline": "25% OFF", "subline": "", "footer": "", "layout": "side"}
    out = Image.open(BytesIO(gi._typeset_design(buffer.getvalue(), "offer", design))).convert("RGB")
    assert out.getpixel((370, 10)) != (40, 160, 90)
    assert out.getpixel((370, 600)) == (40, 160, 90)


def test_brand_logo_drawn_as_badge_avatar():
    """The provider logo is pasted as a round avatar at the left of the footer badge."""
    buffer, logo = BytesIO(), BytesIO()
    Image.new("RGB", (512, 512), (40, 160, 90)).save(buffer, "PNG")
    Image.new("RGB", (64, 64), (250, 220, 0)).save(logo, "PNG")
    design = {"headline": "", "subline": "", "footer": "Sweet Crumbs", "accent": "#1D4ED8", "layout": "top"}
    out = Image.open(BytesIO(gi._typeset_design(buffer.getvalue(), "offer", design, logo.getvalue()))).convert("RGB")
    yellow = [(x, y) for x in range(0, 200) for y in range(440, 512) if out.getpixel((x, y))[0] > 200 and out.getpixel((x, y))[2] < 80]
    assert yellow and max(x for x, _ in yellow) < 80
