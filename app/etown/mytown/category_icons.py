"""Category Icons - Lucide SVG icons and Tamil display names for etown_category rows.

Domain utilities and a per-worker LISTEN service: picks a Lucide icon by category name, uploads its SVG to AZURE_BLOB_CONTAINER as etown_category.categoryimage, stores a Tamil displayname_ta from the madlad translate gateway, then mirrors the row to ets_category, for each category announced on CATEGORY_CREATED_CHANNEL.

Features:
    - Icon choice: exact category name, then keyword prefix, then Lucide tag match, then the store icon
    - Blob storage: one SVG per category at category_icons/<categoryname>.svg
    - Missing-only: a category that already has a categoryimage is never overwritten
    - Event-driven: trg_capture_etown_category sends pg_notify after commit; every worker listens and fills
    - Tamil names: each announced category's English display name is translated once into displayname_ta
    - Dataverse mirror: once its icon is stored, each announced category is PATCHed onto ets_category
    - Backfill: fill_missing_category_icons() covers every category without an icon, used by scripts/backfill_category_icons.py
    - Tamil backfill: fill_missing_category_tamil_names() covers every category without displayname_ta

Classes:
    No classes defined (module exposes icon helpers and listener lifecycle functions)

Functions:
    choose_category_icon(): Pick a Lucide icon name for a category by exact name, keyword or Lucide tag
    store_category_icon(): Upload one category's Lucide SVG to blob storage and record its path
    fill_missing_category_icons(): Store an icon for every etown_category row without a categoryimage
    category_english_name(): Return a category's English display name, derived from categoryname when empty
    translate_category_name(): Translate an English category name to Tamil with the gateway AI search v2 uses
    store_category_tamil_name(): Translate one category's display name to Tamil and record it as displayname_ta
    fill_missing_category_tamil_names(): Store a Tamil name for every etown_category row without displayname_ta
    _store_icon_and_mirror(): Store a new category's icon and Tamil name, then mirror its row to Dataverse
    _on_category_created(): asyncpg LISTEN callback that stores and mirrors the announced category
    _listener_loop(): Hold a dedicated LISTEN connection open, reconnecting with backoff
    start_category_icon_listener(): Start the listener task (once, from the lifespan)
    stop_category_icon_listener(): Cancel and await the listener task

Dependencies:
    - app.core.config: LUCIDE_STATIC_URL, AZURE_BLOB_CONTAINER, madlad_translate_url and madlad_api_key
    - app.db.client: Database pool and open_listener_connection
    - app.utils.blob_storage: upload_blob_bytes and normalize_container_name
    - app.utils.http_client: Shared HTTP client for the Lucide CDN and the translate gateway
    - app.core.logging: Centralized logger
    - app.etown.mytown.mytown_syncing: sync_category_to_dataverse for the ets_category mirror

Updates:
    16-Oct-2026: Created; icons are stored on the trigger's pg_notify and by the backfill script.
    20-Oct-2026: Announced categories are mirrored to ets_category after their icon is stored; announced categories also get a Tamil displayname_ta from the madlad translate gateway; added fill_missing_category_tamil_names.
"""

import asyncio
import re
from typing import Dict, Optional, Set, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.mytown.mytown_syncing import sync_category_to_dataverse
from app.utils.blob_storage import normalize_container_name, upload_blob_bytes
from app.utils.http_client import get_http_client

CATEGORY_CREATED_CHANNEL = "etown_category_created"
DEFAULT_CATEGORY_ICON = "store"
CATEGORY_ICON_BLOB_FOLDER = "category_icons"
CATEGORY_ICON_EXACT: Dict[str, str] = {
    "all": "layout-grid",
    "favourites": "heart",
    "other": DEFAULT_CATEGORY_ICON,
    "medical_store": "pill",
    "general_store": "shopping-cart",
    "real_estate": "building-2",
}
CATEGORY_ICON_KEYWORDS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("hospital", "clinic"), "hospital"),
    (("dental", "dentist"), "smile"),
    (("pharma", "chemist", "medicine", "drug"), "pill"),
    (("ambulance",), "ambulance"),
    (("health", "doctor", "medical", "nurs", "physio", "diagnos", "lab"), "stethoscope"),
    (("vet", "pet", "animal"), "paw-print"),
    (("gym", "fitness", "yoga"), "dumbbell"),
    (("salon", "beauty", "spa", "parlour", "parlor", "makeup", "cosmetic", "bridal"), "flower-2"),
    (("barber", "hair"), "scissors"),
    (("tailor", "boutique", "cloth", "garment", "fashion", "apparel", "textile"), "shirt"),
    (("laundry", "dry", "wash"), "washing-machine"),
    (("shoe", "footwear"), "footprints"),
    (("jewel", "gold"), "gem"),
    (("optic", "eye", "spectacle"), "glasses"),
    (("food", "snack", "restaurant", "dhaba", "eat", "dine", "dining", "cater", "tiffin", "mess", "kitchen"), "utensils"),
    (("cafe", "coffee", "tea"), "coffee"),
    (("bake", "baker", "cake", "sweet", "dessert"), "cake"),
    (("grocer", "kirana", "supermarket", "mart", "provision", "general"), "shopping-cart"),
    (("vegetable", "fruit"), "apple"),
    (("dairy", "milk"), "milk"),
    (("meat", "chicken", "mutton"), "drumstick"),
    (("fish", "seafood"), "fish"),
    (("flower", "florist"), "flower"),
    (("gift",), "gift"),
    (("book", "stationer"), "book-open"),
    (("mobile", "phone"), "smartphone"),
    (("computer", "laptop", "software", "tech"), "laptop"),
    (("electronic", "appliance", "tv"), "monitor"),
    (("electrician", "electric"), "zap"),
    (("plumb", "water"), "droplets"),
    (("furniture", "sofa"), "sofa"),
    (("paint",), "paint-roller"),
    (("clean", "pest"), "spray-can"),
    (("home", "house", "interior"), "house"),
    (("carpent", "construct", "hardware", "build", "cement"), "hammer"),
    (("bike", "cycle"), "bike"),
    (("car", "auto", "vehicle", "garage", "tyre", "taxi", "cab"), "car"),
    (("petrol", "fuel", "gas"), "fuel"),
    (("truck", "courier", "logistic", "mover", "packer", "delivery"), "truck"),
    (("bus",), "bus"),
    (("travel", "tour", "flight"), "plane"),
    (("hotel", "lodge", "homestay", "resort", "stay"), "bed"),
    (("school", "tuition", "coach", "education", "class", "institute", "academy", "training"), "graduation-cap"),
    (("music", "dance"), "music"),
    (("photo", "studio", "camera", "video"), "camera"),
    (("event", "wedding", "decor", "party"), "sparkles"),
    (("bank", "financ", "loan"), "landmark"),
    (("insur", "security"), "shield"),
    (("legal", "lawyer", "advocate", "notary"), "scale"),
    (("account", "tax", "audit"), "calculator"),
    (("print", "xerox"), "printer"),
    (("property", "estate", "rent"), "building-2"),
    (("agri", "farm", "seed", "fertil", "nursery", "plant", "garden"), "sprout"),
    (("cyber", "internet", "wifi"), "wifi"),
    (("baby", "kid", "toy"), "baby"),
    (("key", "lock"), "key"),
    (("repair", "service", "mechanic", "maintenance"), "wrench"),
)
_KEEPALIVE_SECONDS = 900
_TRANSLATE_TIMEOUT_SECONDS = 10.0
_TRANSLATE_MAX_NEW_TOKENS = 64

_lucide_tags: Optional[Dict[str, Set[str]]] = None
_listener_task: Optional[asyncio.Task] = None
_store_tasks: Set[asyncio.Task] = set()


async def choose_category_icon(categoryname: str) -> str:
    """Return the Lucide icon name for a category by exact name, keyword prefix, Lucide tag match, or the store default."""
    global _lucide_tags
    name = (categoryname or "").strip().lower()
    if name in CATEGORY_ICON_EXACT:
        return CATEGORY_ICON_EXACT[name]
    tokens = [t for t in re.split(r"[^a-z0-9]+", name) if t]
    for keywords, icon in CATEGORY_ICON_KEYWORDS:
        if any(token.startswith(keyword) for token in tokens for keyword in keywords):
            return icon

    if _lucide_tags is None:
        try:
            async with get_http_client() as client:
                response = await client.get(f"{settings.LUCIDE_STATIC_URL}/tags.json")
            response.raise_for_status()
            _lucide_tags = {icon: {tag.lower() for tag in tags} for icon, tags in response.json().items()}
        except Exception:
            logger.warning("category icons: could not load the Lucide tag list", exc_info=True)
            return DEFAULT_CATEGORY_ICON
    words = {t for t in tokens if len(t) > 2}
    words |= {t[:-1] for t in words if t.endswith("s")}
    best_icon, best_score = DEFAULT_CATEGORY_ICON, 0
    for icon, tags in _lucide_tags.items():
        score = 3 * len(words & set(icon.split("-"))) + len(words & tags)
        if score > best_score or (score == best_score and score and len(icon) < len(best_icon)):
            best_icon, best_score = icon, score
    return best_icon


async def store_category_icon(categoryname: str) -> Optional[str]:
    """Upload a category's Lucide SVG to blob storage and record it as categoryimage when the row still has none.

    Args:
        categoryname: The etown_category.categoryname to give an icon.

    Returns:
        The stored blob path, or None when the icon could not be stored.
    """
    container = normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container or not settings.LUCIDE_STATIC_URL:
        logger.warning("category icons: AZURE_BLOB_CONTAINER or LUCIDE_STATIC_URL is not configured; skipping %s", categoryname)
        return None
    try:
        icon = await choose_category_icon(categoryname)
        async with get_http_client() as client:
            response = await client.get(f"{settings.LUCIDE_STATIC_URL}/icons/{icon}.svg")
            if response.status_code != 200 and icon != DEFAULT_CATEGORY_ICON:
                icon = DEFAULT_CATEGORY_ICON
                response = await client.get(f"{settings.LUCIDE_STATIC_URL}/icons/{icon}.svg")
        response.raise_for_status()
        blob_stem = re.sub(r"[^a-z0-9]+", "_", categoryname.lower()).strip("_") or "other"
        blob_path = await upload_blob_bytes(
            container, f"{CATEGORY_ICON_BLOB_FOLDER}/{blob_stem}.svg", response.content, "image/svg+xml"
        )
        await Database.execute(
            "UPDATE public.etown_category SET categoryimage = $2, modifiedon = (now() AT TIME ZONE 'utc') "
            "WHERE categoryname = $1 AND NULLIF(BTRIM(categoryimage), '') IS NULL",
            categoryname, blob_path,
        )
        logger.info("category icons: stored %s for category %s at %s", icon, categoryname, blob_path)
        return blob_path
    except Exception:
        logger.warning("category icons: could not store an icon for category %s", categoryname, exc_info=True)
        return None


async def fill_missing_category_icons() -> int:
    """Store an icon for every etown_category row whose categoryimage is empty.

    Returns:
        The number of categories that received an icon.
    """
    rows = await Database.fetch(
        "SELECT categoryname FROM public.etown_category WHERE NULLIF(BTRIM(categoryimage), '') IS NULL ORDER BY categoryname"
    )
    filled = 0
    for row in rows:
        if await store_category_icon(row["categoryname"]):
            filled += 1
    return filled


def category_english_name(categoryname: str, displayname: Optional[str]) -> str:
    """Return the category's English display name, deriving it from categoryname as the capture trigger does when empty."""
    if displayname and displayname.strip():
        return displayname.strip()
    if categoryname == "other":
        return "Other Services"
    return categoryname.replace("_", " ").title()


async def translate_category_name(english: str) -> str:
    """Translate an English category name to Tamil through madlad_translate_url, the gateway AI search v2 uses.

    Args:
        english: The English display name.

    Returns:
        The Tamil name, or an empty string when the gateway returns none.

    Raises:
        httpx.HTTPError: When the gateway call fails or returns a non-2xx status.
    """
    headers = {"Content-Type": "application/json"}
    if settings.madlad_api_key:
        headers["X-API-Key"] = settings.madlad_api_key
    async with get_http_client() as client:
        response = await client.post(
            settings.madlad_translate_url,
            json={"text": english, "target_lang": "ta", "source_lang": "en", "max_new_tokens": _TRANSLATE_MAX_NEW_TOKENS},
            headers=headers,
            timeout=_TRANSLATE_TIMEOUT_SECONDS,
        )
    response.raise_for_status()
    translations = response.json().get("translations") or []
    first = translations[0] if isinstance(translations, list) and translations else {}
    translated = first.get("translated_text") if isinstance(first, dict) else None
    return translated.strip() if isinstance(translated, str) else ""


async def store_category_tamil_name(categoryname: str) -> Optional[str]:
    """Translate a category's English display name to Tamil and record it as displayname_ta when the row still has none.

    Args:
        categoryname: The etown_category.categoryname to translate.

    Returns:
        The stored Tamil name, or None when it could not be stored.
    """
    try:
        row = await Database.fetchrow(
            "SELECT displayname FROM public.etown_category WHERE categoryname = $1", categoryname
        )
        if row is None:
            return None
        tamil = await translate_category_name(category_english_name(categoryname, row["displayname"]))
        if not tamil:
            return None
        await Database.execute(
            "UPDATE public.etown_category SET displayname_ta = $2, modifiedon = (now() AT TIME ZONE 'utc') "
            "WHERE categoryname = $1 AND NULLIF(BTRIM(displayname_ta), '') IS NULL",
            categoryname, tamil,
        )
        logger.info("category names: stored Tamil name %s for category %s", tamil, categoryname)
        return tamil
    except Exception:
        logger.warning("category names: could not store a Tamil name for category %s", categoryname, exc_info=True)
        return None


async def fill_missing_category_tamil_names() -> int:
    """Store a Tamil name for every etown_category row whose displayname_ta is empty.

    Returns:
        The number of categories that received a Tamil name.
    """
    rows = await Database.fetch(
        "SELECT categoryname FROM public.etown_category WHERE NULLIF(BTRIM(displayname_ta), '') IS NULL ORDER BY categoryname"
    )
    filled = 0
    for row in rows:
        if await store_category_tamil_name(row["categoryname"]):
            filled += 1
    return filled


async def _store_icon_and_mirror(categoryname: str) -> None:
    """Store a new category's icon and Tamil name, then mirror its etown_category row to Dataverse; failures are logged and swallowed."""
    await store_category_icon(categoryname)
    await store_category_tamil_name(categoryname)
    try:
        row = await Database.fetchrow(
            "SELECT categoryid::text AS categoryid, categoryname, displayname, categoryimage "
            "FROM public.etown_category WHERE categoryname = $1",
            categoryname,
        )
    except Exception:
        logger.warning("category icons: could not read category %s to mirror it", categoryname, exc_info=True)
        return
    if row:
        await sync_category_to_dataverse(dict(row))


def _on_category_created(connection, pid: int, channel: str, payload: str) -> None:
    """Start storing the icon of, and mirroring, the category named in a CATEGORY_CREATED_CHANNEL notification."""
    if not payload:
        return
    task = asyncio.create_task(_store_icon_and_mirror(payload))
    _store_tasks.add(task)
    task.add_done_callback(_store_tasks.discard)


async def _listener_loop() -> None:
    """Hold a dedicated LISTEN connection open with periodic keepalives, reconnecting with exponential backoff."""
    backoff = 1
    while True:
        conn = None
        try:
            conn = await Database.open_listener_connection()
            await conn.add_listener(CATEGORY_CREATED_CHANNEL, _on_category_created)
            logger.info("category icon listener attached to '%s'", CATEGORY_CREATED_CHANNEL)
            backoff = 1
            while not conn.is_closed():
                await asyncio.sleep(_KEEPALIVE_SECONDS)
                await conn.execute("SELECT 1")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("category icon listener down, reconnecting in %ss: %s", backoff, exc)
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    logger.debug("category icon listener: failed to close the listener connection", exc_info=True)
        await asyncio.sleep(backoff)
        backoff = min(30, backoff * 2)


async def start_category_icon_listener() -> None:
    """Start the category icon listener task unless it is already running."""
    global _listener_task
    if _listener_task is not None and not _listener_task.done():
        return
    _listener_task = asyncio.create_task(_listener_loop())


async def stop_category_icon_listener() -> None:
    """Cancel and await the category icon listener task."""
    global _listener_task
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            logger.debug("category icon listener cancelled during shutdown")
    _listener_task = None
    logger.info("category icon listener stopped")
