"""Backfill Category Icons - store a Lucide SVG icon for every etown_category row without a categoryimage.

One-off script that selects categories with an empty categoryimage, picks a Lucide icon by name, downloads the SVG from LUCIDE_STATIC_URL, uploads it with AZURE_BLOB_CONNECTION_STRING to AZURE_BLOB_CONTAINER and records the blob path.

Features:
    - Missing-only: categories that already have a categoryimage are never touched
    - Dry run: --dry-run lists each category and the icon it would get without uploading
    - Any environment: --env-file loads e.g. .env.production over .env before settings are built

Usage:
1. .venv/Scripts/python.exe scripts/backfill_category_icons.py --dry-run                              # dev (.env)
2. .venv/Scripts/python.exe scripts/backfill_category_icons.py                                        # dev, upload
3. .venv/Scripts/python.exe scripts/backfill_category_icons.py --env-file .env.production             # prod

Functions:
    _load_env_file_override(): Load the --env-file over existing variables before settings are built
    main(): Parse arguments, open the pool and store each missing category icon

Dependencies:
    - argparse: Command-line options
    - python-dotenv: Loads the --env-file with override
    - app.core.config: settings for DB, LUCIDE_STATIC_URL and Azure Blob storage
    - app.db.client: Database pool and the etown_category query
    - app.etown.mytown.category_icons: choose_category_icon and fill_missing_category_icons
    - app.core.logging: Progress logging

Updates:
    16-Oct-2026: Created backfill for categories without a categoryimage.
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

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

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.mytown.category_icons import choose_category_icon, fill_missing_category_icons


async def main() -> None:
    """Parse arguments, open the pool and store an icon for each category without one."""
    parser = argparse.ArgumentParser(description="Store a Lucide icon for every etown_category row without a categoryimage.")
    parser.add_argument("--dry-run", action="store_true", help="List each category and its icon without uploading")
    parser.add_argument(
        "--env-file", default=None,
        help="Env file loaded over .env, e.g. .env.production (default: the app's normal APP_ENV loading)",
    )
    args = parser.parse_args()

    if not settings.LUCIDE_STATIC_URL or not settings.AZURE_BLOB_CONNECTION_STRING or not settings.AZURE_BLOB_CONTAINER:
        raise SystemExit("LUCIDE_STATIC_URL, AZURE_BLOB_CONNECTION_STRING and AZURE_BLOB_CONTAINER must be set")
    logger.info(
        "backfill_category_icons - env_file=%s db=%s/%s container=%s lucide=%s",
        ENV_FILE_OVERRIDE or "default", settings.DBHOST, settings.DATABASENAME,
        settings.AZURE_BLOB_CONTAINER, settings.LUCIDE_STATIC_URL,
    )
    await Database.init(min_size=1, max_size=2)
    try:
        if args.dry_run:
            rows = await Database.fetch(
                "SELECT categoryname FROM public.etown_category "
                "WHERE NULLIF(BTRIM(categoryimage), '') IS NULL ORDER BY categoryname"
            )
            for row in rows:
                icon = await choose_category_icon(row["categoryname"])
                logger.info("backfill_category_icons - would store %s for category %s", icon, row["categoryname"])
            logger.info("backfill_category_icons - %s category(ies) without an icon", len(rows))
            return
        filled = await fill_missing_category_icons()
        logger.info("backfill_category_icons - stored %s icon(s)", filled)
    finally:
        await Database.close()


if __name__ == "__main__":
    asyncio.run(main())
