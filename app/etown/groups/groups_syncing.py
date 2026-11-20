"""Group Dataverse Sync - mirrors group create, update and delete from Postgres to the CRM.

Background Dataverse mirror for the POST /api/groups/groups write route: upserts ets_groups and ets_groupitemses on their Postgres GUIDs and deletes them on group removal; Postgres stays authoritative and every call swallows its own errors.

Features:
    - Group Upsert Sync: PATCH-upsert an ets_groups row on its Postgres GUID
    - Group Items Upsert Sync: PATCH-upsert each ets_groupitemses row on its Postgres GUID
    - Group Items Replace Sync: Delete a group's existing Dataverse items before re-upsert
    - Group Delete Sync: Delete a group's items then the group in Dataverse
    - BackgroundTask Integration: All functions are non-blocking and log but never raise

Classes:
    No classes defined (module uses function-based sync utilities).

Functions:
    _build_context(): Return the base URL and auth headers for Dataverse Web API calls
    _patch(): PATCH-upsert one record, dropping/truncating a rejected field and retrying
    _delete(): DELETE one record, treating an already-missing record (404) as success
    _list_group_item_ids(): List a group's existing ets_groupitemses record ids in Dataverse
    _group_payload(): Build the ets_groups PATCH payload for a group
    _item_payload(): Build the ets_groupitemses PATCH payload for one group member
    _upsert_items(): Upsert every group item concurrently
    sync_group_create_to_dataverse(): Upsert a new group + its members
    sync_group_update_to_dataverse(): Upsert group details and/or replace its members
    sync_group_delete_from_dataverse(): Delete a group and all its members

Dependencies:
    - asyncio: Concurrent item upserts
    - re: Parses invalid-property / over-length errors so the PATCH can retry
    - httpx: Async Dataverse Web API client
    - app.core.config: Dataverse URL / OAuth credentials
    - app.core.logging: Centralized logging
    - app.etown.provider.provider_syncing: Shared OAuth token acquisition

Updates:
    13-Jul-2026: Initial creation for the Groups AddUpdateDeleteGroups migration
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import asyncio
import re
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.etown.provider.provider_syncing import get_dataverse_token

_ENTITY_GROUPS = "ets_groups"
_ENTITY_GROUP_ITEMS = "ets_groupitemses"
_LOOKUP_GROUP_ID = "_ets_groupid_value"

_INVALID_PROP_PATTERN = re.compile(r"Invalid property '([^']+)' was found")
_LENGTH_LIMIT_PATTERN = re.compile(
    r"length of the '([^']+)' attribute .*?exceeded the maximum allowed length of '(\d+)'"
)
_MAX_NET_RETRIES = 3
_RETRY_DELAYS = [2.0, 5.0, 10.0]


def _build_context(access_token: str) -> tuple:
    """Return (base_url, headers) for Dataverse Web API calls."""
    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
        "OData-MaxVersion": "4.0",
    }
    return base_url, headers


async def _patch(client: httpx.AsyncClient, url: str, payload: Dict[str, Any], headers: Dict[str, str], label: str) -> bool:
    """PATCH-upsert one record, dropping invalid or truncating over-long props on 400 and retrying transient errors."""
    clean = {
        k: v for k, v in payload.items()
        if v is not None and not (k.endswith("@odata.bind") and "(None)" in str(v))
    }
    net_attempts = 0
    for _ in range(len(clean) + 1):
        try:
            resp = await client.patch(url, json=clean, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            net_attempts += 1
            if net_attempts <= _MAX_NET_RETRIES:
                delay = _RETRY_DELAYS[net_attempts - 1]
                logger.warning(
                    "DATAVERSE_GROUP | %s | TRANSIENT_ERROR | attempt=%d/%d | retrying in %.0fs | %s",
                    label, net_attempts, _MAX_NET_RETRIES, delay, exc,
                )
                await asyncio.sleep(delay)
                continue
            logger.error("DATAVERSE_GROUP | %s | ERROR | url=%s | exhausted %d retries | %s", label, url, _MAX_NET_RETRIES, exc, exc_info=True)
            return False
        except Exception as exc:
            logger.error("DATAVERSE_GROUP | %s | ERROR | url=%s | %s", label, url, exc, exc_info=True)
            return False

        if resp.status_code in (200, 204):
            logger.info("DATAVERSE_GROUP | %s | SUCCESS | url=%s", label, url)
            return True

        if resp.status_code == 400:
            invalid = _INVALID_PROP_PATTERN.search(resp.text)
            if invalid and invalid.group(1) in clean:
                logger.warning("DATAVERSE_GROUP | %s | DROP_INVALID_PROP | prop=%s", label, invalid.group(1))
                del clean[invalid.group(1)]
                continue
            length_match = _LENGTH_LIMIT_PATTERN.search(resp.text)
            if length_match:
                bad_prop, max_len = length_match.group(1), int(length_match.group(2))
                value = clean.get(bad_prop)
                if isinstance(value, str) and len(value) > max_len:
                    logger.warning("DATAVERSE_GROUP | %s | TRUNCATE_OVERLONG_PROP | prop=%s | max=%d", label, bad_prop, max_len)
                    clean[bad_prop] = value[:max_len]
                    continue

        logger.error("DATAVERSE_GROUP | %s | FAILED | status=%s | body=%s", label, resp.status_code, resp.text)
        return False

    logger.error("DATAVERSE_GROUP | %s | FAILED | url=%s | exhausted invalid-prop retries", label, url)
    return False


async def _delete(client: httpx.AsyncClient, url: str, headers: Dict[str, str], label: str) -> bool:
    """DELETE one record and return success, treating 404 as already gone and never raising."""
    try:
        resp = await client.delete(url, headers=headers)
        if resp.status_code in (200, 204, 404):
            logger.info("DATAVERSE_GROUP | %s | SUCCESS | url=%s | status=%s", label, url, resp.status_code)
            return True
        logger.error("DATAVERSE_GROUP | %s | FAILED | status=%s | body=%s", label, resp.status_code, resp.text)
        return False
    except Exception as exc:
        logger.error("DATAVERSE_GROUP | %s | ERROR | url=%s | %s", label, url, exc, exc_info=True)
        return False


async def _list_group_item_ids(client: httpx.AsyncClient, base_url: str, headers: Dict[str, str], groupid: str) -> List[str]:
    """Return the ets_groupitemsid of every Dataverse group item for a group."""
    url = f"{base_url}/{_ENTITY_GROUP_ITEMS}?$select=ets_groupitemsid&$filter={_LOOKUP_GROUP_ID} eq {groupid}"
    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.error("DATAVERSE_GROUP | LIST_ITEMS | FAILED | status=%s | body=%s", resp.status_code, resp.text)
            return []
        return [row["ets_groupitemsid"] for row in resp.json().get("value", []) if row.get("ets_groupitemsid")]
    except Exception as exc:
        logger.error("DATAVERSE_GROUP | LIST_ITEMS | ERROR | groupid=%s | %s", groupid, exc, exc_info=True)
        return []


def _group_payload(group: Dict[str, Any]) -> Dict[str, Any]:
    """Build the ets_groups Dataverse payload for a group, binding owner contact and profile when present."""
    payload: Dict[str, Any] = {
        "ets_groupname": group.get("groupname"),
        "ets_groupprofileimageurl": group.get("groupimageurl"),
        "ets_groupunicid": group.get("groupunicid"),
    }
    if group.get("user_contactid"):
        payload["ets_usercontactid@odata.bind"] = f"/contacts({group['user_contactid']})"
    if group.get("user_userprofileid"):
        payload["ets_useruserprofileid@odata.bind"] = f"/ets_userprofiles({group['user_userprofileid']})"
    return payload


def _item_payload(item: Dict[str, Any], group: Dict[str, Any]) -> Dict[str, Any]:
    """Build the Dataverse group-item payload binding the item to its group, member profile and owner."""
    payload: Dict[str, Any] = {
        "ets_name": item.get("name"),
        "ets_groupid@odata.bind": f"/{_ENTITY_GROUPS}({group['groupid']})",
    }
    if item.get("userprofile_id"):
        payload["ets_mycircleuserprofileid@odata.bind"] = f"/ets_userprofiles({item['userprofile_id']})"
    if group.get("user_contactid"):
        payload["ets_usercontactid@odata.bind"] = f"/contacts({group['user_contactid']})"
    if group.get("user_userprofileid"):
        payload["ets_useruserprofileid@odata.bind"] = f"/ets_userprofiles({group['user_userprofileid']})"
    return payload


async def _upsert_items(client: httpx.AsyncClient, base_url: str, headers: Dict[str, str], group: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    """Upsert every group item that carries a groupitemsid to Dataverse concurrently."""
    if not items:
        return
    await asyncio.gather(*[
        _patch(
            client,
            f"{base_url}/{_ENTITY_GROUP_ITEMS}({it['groupitemsid']})",
            _item_payload(it, group),
            headers,
            f"UPSERT_ITEM:{it.get('groupitemsid')}",
        )
        for it in items if it.get("groupitemsid")
    ])


async def sync_group_create_to_dataverse(group: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    """Upsert a new group to Dataverse, then its items when the group upsert succeeds."""
    try:
        token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_GROUP | CREATE | TOKEN_ERROR | groupid=%s | %s", group.get("groupid"), exc, exc_info=True)
        return
    base_url, headers = _build_context(token)
    async with httpx.AsyncClient(timeout=30.0) as client:
        ok = await _patch(client, f"{base_url}/{_ENTITY_GROUPS}({group['groupid']})", _group_payload(group), headers, f"UPSERT_GROUP:{group['groupid']}")
        if not ok:
            return
        await _upsert_items(client, base_url, headers, group, items)


async def sync_group_update_to_dataverse(group: Dict[str, Any], items: List[Dict[str, Any]], items_replaced: bool) -> None:
    """Mirror a group update to Dataverse, upserting the group on detail changes and replacing items when replaced."""
    try:
        token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_GROUP | UPDATE | TOKEN_ERROR | groupid=%s | %s", group.get("groupid"), exc, exc_info=True)
        return
    base_url, headers = _build_context(token)
    condition = group.get("condition")
    groupid = group["groupid"]
    async with httpx.AsyncClient(timeout=30.0) as client:
        if condition in ("groupupdate", "completegroupupdate"):
            await _patch(client, f"{base_url}/{_ENTITY_GROUPS}({groupid})", _group_payload(group), headers, f"UPSERT_GROUP:{groupid}")
        if items_replaced:
            existing_ids = await _list_group_item_ids(client, base_url, headers, groupid)
            if existing_ids:
                await asyncio.gather(*[
                    _delete(client, f"{base_url}/{_ENTITY_GROUP_ITEMS}({iid})", headers, f"DELETE_ITEM:{iid}")
                    for iid in existing_ids
                ])
            await _upsert_items(client, base_url, headers, group, items)


async def sync_group_delete_from_dataverse(groupid: str, item_ids: Optional[List[str]] = None) -> None:
    """Delete a group's Dataverse items, falling back to the given IDs, then delete the group row."""
    try:
        token = await get_dataverse_token()
    except Exception as exc:
        logger.error("DATAVERSE_GROUP | DELETE | TOKEN_ERROR | groupid=%s | %s", groupid, exc, exc_info=True)
        return
    base_url, headers = _build_context(token)
    async with httpx.AsyncClient(timeout=30.0) as client:
        ids = await _list_group_item_ids(client, base_url, headers, groupid) or (item_ids or [])
        if ids:
            await asyncio.gather(*[
                _delete(client, f"{base_url}/{_ENTITY_GROUP_ITEMS}({iid})", headers, f"DELETE_ITEM:{iid}")
                for iid in ids
            ])
        await _delete(client, f"{base_url}/{_ENTITY_GROUPS}({groupid})", headers, f"DELETE_GROUP:{groupid}")
