"""Backfill Town Images - fill etown_townprofile.townimage for towns that have none.

One-off script that selects town profiles with an empty townimage, generates and saves a description where one is missing, then runs the MyTown town card pipeline (best Google photo at TOWN_PHOTO_MIN_RELEVANCE, else AI image) and mirrors the result to Dataverse.

Features:
    - Missing-only: towns that already have a townimage are never touched
    - Relevant photos only: Places search uses town, city, reverse-geocoded state and coordinates; a photo needs TOWN_PHOTO_MIN_RELEVANCE (default 80)
    - Busiest first: towns are processed in descending apihit order
    - Description fallback: a town with no description gets a gpt41 one saved before its card is made
    - Bounded concurrency: --concurrency caps simultaneous Google and image-model calls
    - Dry run: --dry-run lists the towns that would be filled without calling any model
    - Any environment: --env-file loads e.g. .env.production over .env before settings are built

Usage:
1. .venv/Scripts/python.exe scripts/backfill_town_images.py --dry-run                                  # dev (.env)
2. .venv/Scripts/python.exe scripts/backfill_town_images.py --env-file .env.production --dry-run       # prod
3. .venv/Scripts/python.exe scripts/backfill_town_images.py --env-file .env.production --limit 100

Dependencies:
    - argparse: Command-line options
    - python-dotenv: Loads the --env-file with override
    - app.core.config: settings for DB, Key Vault, Google, Gemini, image server and Dataverse
    - asyncio: Semaphore-bounded concurrent backfill
    - app.db.client: Database pool and queries against etown_townprofile
    - app.etown.mytown.mytown_utils: _generate_and_store_town_card pipeline and the description prompt
    - app.agent_infrastructure.infrastructure.llm_clients: gpt41 for missing descriptions
    - app.core.logging: Progress and failure logging

Updates:
    12-Oct-2026: Created backfill for towns without a townimage; --env-file selects the target environment.
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _load_env_file_override() -> Optional[Path]:
    """Load the --env-file given on the command line over any existing variables, before app settings are built.

    Raises:
        SystemExit: When the named env file does not exist.
    """
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", default=None)
    known, _ = pre_parser.parse_known_args()
    if not known.env_file:
        return None
    env_path = Path(known.env_file)
    if not env_path.is_absolute():
        env_path = PROJECT_ROOT / env_path
    if not env_path.is_file():
        raise SystemExit(f"--env-file not found: {env_path}")
    load_dotenv(dotenv_path=env_path, override=True)
    return env_path


ENV_FILE_OVERRIDE = _load_env_file_override()

from app.agent_infrastructure.infrastructure.llm_clients import gpt41
from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.mytown.mytown_utils import (
    _generate_and_store_town_card,
    generate_city_description_prompt,
)


def _to_float(value: Any) -> Optional[float]:
    """Parse a stored text coordinate, returning None when it is blank or invalid."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


async def fetch_towns_without_image(limit: int, townprofileid: Optional[str]) -> List[Dict[str, Any]]:
    """Read town profiles whose townimage is empty, busiest first.

    Args:
        limit: Maximum number of towns to return.
        townprofileid: Optional single town to restrict the backfill to.
    """
    rows = await Database.fetch(
        """
        SELECT townprofileid, townname, cityname, towndescription, townlatitude, townlongitude
        FROM public.etown_townprofile
        WHERE NULLIF(TRIM(townimage), '') IS NULL
          AND NULLIF(TRIM(townname), '') IS NOT NULL
          AND ($2::text IS NULL OR townprofileid = $2)
        ORDER BY COALESCE(apihit, 0) DESC, townname
        LIMIT $1
        """,
        limit, townprofileid,
    )
    return [dict(row) for row in rows]


async def ensure_description(town: Dict[str, Any]) -> Optional[str]:
    """Return a town's description, generating and saving one when it is missing.

    Args:
        town: Town profile row from fetch_towns_without_image.
    """
    description = (town.get("towndescription") or "").strip()
    if description:
        return description

    result = await gpt41.ainvoke(generate_city_description_prompt(town["townname"]))
    description = (result.content or "").strip() if result else ""
    if not description:
        return None

    await Database.execute(
        "UPDATE public.etown_townprofile SET towndescription = $2 WHERE townprofileid = $1",
        town["townprofileid"], description,
    )
    logger.info("backfill_town_images - saved a generated description for town='%s'", town["townname"])
    return description


async def backfill_town(town: Dict[str, Any], semaphore: asyncio.Semaphore) -> bool:
    """Fill one town's image through the town card pipeline.

    Args:
        town: Town profile row from fetch_towns_without_image.
        semaphore: Limits how many towns are processed at once.

    Returns:
        True when the town has a townimage afterwards.
    """
    async with semaphore:
        try:
            description = await ensure_description(town)
        except Exception as e:
            logger.error(
                "backfill_town_images - description failed for town='%s': %s", town["townname"], e, exc_info=True,
            )
            return False
        if not description:
            logger.warning("backfill_town_images - no description for town='%s'; skipped", town["townname"])
            return False

        await _generate_and_store_town_card(
            townprofileid=town["townprofileid"],
            townname=town["townname"],
            cityname=town.get("cityname"),
            statecode=None,
            towndescription=description,
            latitude=_to_float(town.get("townlatitude")),
            longitude=_to_float(town.get("townlongitude")),
        )
        stored = await Database.fetchval(
            "SELECT NULLIF(TRIM(townimage), '') FROM public.etown_townprofile WHERE townprofileid = $1",
            town["townprofileid"],
        )
        return bool(stored)


async def main() -> None:
    """Parse arguments, open the pool and backfill each selected town."""
    parser = argparse.ArgumentParser(description="Fill etown_townprofile.townimage for towns that have none.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum towns to process (default 50)")
    parser.add_argument("--concurrency", type=int, default=2, help="Towns processed at once (default 2)")
    parser.add_argument("--townprofileid", default=None, help="Backfill only this town")
    parser.add_argument("--dry-run", action="store_true", help="List the towns without generating anything")
    parser.add_argument(
        "--env-file", default=None,
        help="Env file loaded over .env, e.g. .env.production (default: the app's normal APP_ENV loading)",
    )
    args = parser.parse_args()

    logger.info(
        "backfill_town_images - env_file=%s db=%s/%s",
        ENV_FILE_OVERRIDE or "default", settings.DBHOST, settings.DATABASENAME,
    )
    await Database.init(min_size=1, max_size=max(2, args.concurrency + 1))
    try:
        towns = await fetch_towns_without_image(args.limit, args.townprofileid)
        logger.info("backfill_town_images - %s town(s) without a townimage selected", len(towns))
        if args.dry_run:
            for town in towns:
                has_description = "yes" if (town.get("towndescription") or "").strip() else "no"
                logger.info(
                    "backfill_town_images - would fill town='%s' city='%s' id=%s description=%s",
                    town["townname"], town.get("cityname"), town["townprofileid"], has_description,
                )
            return

        semaphore = asyncio.Semaphore(max(1, args.concurrency))
        results = await asyncio.gather(*(backfill_town(town, semaphore) for town in towns))
        filled = sum(1 for ok in results if ok)
        logger.info("backfill_town_images - filled %s of %s town(s)", filled, len(towns))
    finally:
        await Database.close()


if __name__ == "__main__":
    asyncio.run(main())
