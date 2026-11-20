"""Dataverse Schema Export Script - dumps entity-set fields and lookup bind keys from $metadata.

CLI script that reads DATAVERSE_URL, tenant, client id and secret from the environment or project .env files, downloads the Dataverse OData $metadata, and prints or writes JSON listing each requested entity set's scalar properties and @odata.bind navigation keys.

Functions:
    _load_project_env_files(): Loads the project .env files for credentials
    _required_env(): Returns a required env var or raises ValueError
    _get_access_token(): Acquires a client-credentials token for Dataverse
    _fetch_metadata_xml(): Downloads the OData $metadata document
    _strip_namespace(): Drops the XML namespace prefix from a tag or type
    _parse_metadata(): Extracts scalar and navigation properties per entity set
    _parse_args(): Parses the command-line arguments
    _main(): Runs the export and writes or prints the JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

DEFAULT_ENTITY_SETS = [
    "ets_allrequestses",
    "ets_requestservicelistses",
    "notifications",
    "ets_providerrelations",
    "ets_memberrelations",
    "adx_invitations",
]

NS = {
    "edmx": "http://docs.oasis-open.org/odata/ns/edmx",
    "edm": "http://docs.oasis-open.org/odata/ns/edm",
}


def _load_project_env_files() -> None:
    """Load the project-root .env files in config order without overriding variables already set."""
    if load_dotenv is None:
        return

    project_root = Path(__file__).resolve().parents[1]
    app_env = os.getenv("APP_ENV", "development").lower()

    env_files = [
        project_root / ".env.local",
        project_root / ".env",
        project_root / f".env.{app_env}.local",
        project_root / f".env.{app_env}",
    ]

    for env_file in env_files:
        if env_file.is_file():
            load_dotenv(dotenv_path=env_file, override=False)


def _required_env(name: str) -> str:
    """Return a stripped environment variable, raising ValueError when it is missing or blank."""
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


async def _get_access_token(
    *,
    dataverse_url: str,
    tenant_id: str,
    client_id: str,
    client_secret: str,
) -> str:
    """Return a client-credentials access token for Dataverse, raising when none is returned."""
    token_url =f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": f"{dataverse_url}/.default",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(token_url, data=payload)
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("Token response did not include access_token")
        return token


async def _fetch_metadata_xml(dataverse_url: str, access_token: str) -> str:
    """Download the Dataverse Web API $metadata document as XML text."""
    metadata_url = f"{dataverse_url.rstrip('/')}/api/data/v9.2/$metadata"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/xml",
        "OData-Version": "4.0",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(metadata_url, headers=headers)
        response.raise_for_status()
        return response.text


def _strip_namespace(value: str | None) -> str:
    """Return the last dotted segment of a qualified name, or "" when empty."""
    if not value:
        return ""
    return value.split(".")[-1]


def _parse_metadata(metadata_xml: str, requested_entity_sets: list[str]) -> dict[str, Any]:
    """Extract scalar and navigation properties for the requested entity sets, listing any missing ones."""
    root = ET.fromstring(metadata_xml)

    schema_nodes = root.findall(".//edm:Schema", NS)
    if not schema_nodes:
        raise ValueError("No EDM schema found in metadata")

    all_entity_types: dict[str, ET.Element] = {}
    entity_set_to_type: dict[str, str] = {}

    for schema in schema_nodes:
        namespace = schema.get("Namespace", "")

        for entity_type in schema.findall("edm:EntityType", NS):
            type_name = entity_type.get("Name")
            if not type_name:
                continue
            fq_name = f"{namespace}.{type_name}" if namespace else type_name
            all_entity_types[fq_name] = entity_type

        container = schema.find("edm:EntityContainer", NS)
        if container is not None:
            for entity_set in container.findall("edm:EntitySet", NS):
                set_name = entity_set.get("Name")
                entity_type = entity_set.get("EntityType")
                if set_name and entity_type:
                    entity_set_to_type[set_name] = entity_type

    result: dict[str, Any] = {
        "entity_sets": {},
        "missing_entity_sets": [],
    }

    for set_name in requested_entity_sets:
        fq_type = entity_set_to_type.get(set_name)
        if not fq_type:
            result["missing_entity_sets"].append(set_name)
            continue

        entity_type = all_entity_types.get(fq_type)
        if entity_type is None:
            result["missing_entity_sets"].append(set_name)
            continue

        scalar_properties = []
        for prop in entity_type.findall("edm:Property", NS):
            prop_name = prop.get("Name")
            if not prop_name:
                continue
            prop_entry = {
                "name": prop_name,
                "type": prop.get("Type"),
                "nullable": prop.get("Nullable", "true"),
            }
            max_length = prop.get("MaxLength")
            if max_length:
                prop_entry["max_length"] = max_length
            scalar_properties.append(prop_entry)

        navigation_properties = []
        for nav in entity_type.findall("edm:NavigationProperty", NS):
            nav_name = nav.get("Name")
            if not nav_name:
                continue
            nav_type = nav.get("Type", "")
            navigation_properties.append(
                {
                    "name": nav_name,
                    "type": nav_type,
                    "target_entity_type": _strip_namespace(nav_type.replace("Collection(", "").replace(")", "")),
                    "lookup_bind_key": f"{nav_name}@odata.bind",
                }
            )

        result["entity_sets"][set_name] = {
            "entity_type": _strip_namespace(fq_type),
            "fully_qualified_entity_type": fq_type,
            "scalar_properties": sorted(scalar_properties, key=lambda x: x["name"].lower()),
            "navigation_properties": sorted(navigation_properties, key=lambda x: x["name"].lower()),
        }

    return result


def _parse_args() -> argparse.Namespace:
    """Parse the --entity-sets, --out and --pretty command-line options."""
    parser = argparse.ArgumentParser(
        description="Export Dataverse scalar and lookup/navigation property names from OData metadata."
    )
    parser.add_argument(
        "--entity-sets",
        nargs="+",
        default=DEFAULT_ENTITY_SETS,
        help="Entity set names to inspect (default includes booking sync targets).",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output JSON file path.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


async def _main() -> int:
    """Export the Dataverse schema report to stdout or a file and return the process exit code."""
    args = _parse_args()

    try:
        _load_project_env_files()

        dataverse_url = _required_env("DATAVERSE_URL").rstrip("/")
        tenant_id = _required_env("DATAVERSE_TENANT_ID")
        client_id = _required_env("DATAVERSE_CLIENT_ID")
        client_secret = _required_env("DATAVERSE_CLIENT_SECRET")

        token = await _get_access_token(
            dataverse_url=dataverse_url,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        metadata_xml = await _fetch_metadata_xml(dataverse_url, token)
        report = _parse_metadata(metadata_xml, args.entity_sets)

        indent = 2 if args.pretty else None
        output = json.dumps(report, indent=indent)

        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(output)
            print(f"Wrote schema report to: {args.out}")
        else:
            print(output)

        if report["missing_entity_sets"]:
            print(
                "Warning: missing entity sets: "
                + ", ".join(report["missing_entity_sets"]),
                file=sys.stderr,
            )

        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(_main()))
