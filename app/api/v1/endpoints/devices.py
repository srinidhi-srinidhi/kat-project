"""Devices Endpoints - HTTP routes for saving push device tokens, the credsupdate flag and the uninstall log.

HTTP layer for /api/devices: checks the shared bearer token, upserts one device per call in etown_contactdevices (keyed by device_id, else push token) with a background Dataverse mirror, sets the credsupdate flag, and lists devices FCM has declared unreachable.

Features:
    - Save Device Token: Upsert push/VoIP device tokens for one device of a user
    - Multi-device: Other devices of the same user are preserved, never overwritten
    - Single-pass Upsert: Insert new record or update existing — null fields are preserved
    - Token Authentication: shared bearer token required via verify_token dependency
    - Input Validation: Pydantic V2 request body validation
    - Response Formatting: Structured JSON list of every device of the user
    - Uninstall Log: Read the devices FCM has declared unreachable, filtered and paged
    - Creds Update Flag: Set credsupdate per contact device token (frontend) or table-wide (powerapps)

Endpoints:
    POST /devices/save-device-token: Upsert device token data for a contact
    POST /devices/credsupdate: Set credsupdate for a contact's device token, or on every device row
    GET  /devices/uninstalls: List devices FCM has declared unreachable

Classes:
    No classes defined (module uses request/response models imported from app.schemas.api)

Functions:
    save_device_token_endpoint(): Handle POST /devices/save-device-token
    update_credsupdate_endpoint(): Handle POST /devices/credsupdate
    list_app_uninstalls(): Handle GET /devices/uninstalls

Dependencies:
    - fastapi: API framework, routing, and dependency injection
    - app.etown.devices.devices_utils: Business logic and DB operations for device tokens
    - app.schemas.api: Pydantic request/response models
    - app.core.security: shared bearer-token verification
    - app.etown.devices.devices_syncing: Dataverse background sync for device token records
    - app.etown.devices.uninstall_utils: Reads the app uninstall log
    - app.core.logging: Centralized logging infrastructure

Updates:
    02-Jul-2026: Initial creation, migrated from PowerApps SaveDeviceToken; added Dataverse background sync.
    28-Jul-2026: Multi-device support - upserts per device and returns every device of the pair.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    15-Sep-2026: Added GET /devices/uninstalls, the app uninstall log.
    14-Oct-2026: Added POST /devices/credsupdate."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.core.security import verify_token
from app.core.logging import logger
from app.etown.devices.devices_utils import save_device_token, update_credsupdate
from app.etown.devices.devices_syncing import sync_device_token_to_dataverse
from app.etown.devices.uninstall_utils import fetch_uninstalls
from app.schemas.api import (
    AppUninstallListResponse,
    CredsUpdateRequest,
    CredsUpdateResponse,
    SaveDeviceTokenRequest,
    SaveDeviceTokenResponse,
)

router = APIRouter()


@router.post(
    "/save-device-token",
    response_model=SaveDeviceTokenResponse,
    status_code=200,
    summary="Save Device Token",
    description=(
        "Upsert push and VoIP device tokens for ONE device of a contact/userprofile pair. "
        "The device is identified by `device_id` when supplied, otherwise by its push token, "
        "so re-registering a device updates its row while the user's other devices are kept — "
        "push notifications are then delivered to every registered device. "
        "Null fields in the request leave the existing database values unchanged."
    ),
    responses={
        200: {"description": "Device token saved successfully; returns every device of the pair"},
        500: {"description": "Internal server error"},
    },
)
async def save_device_token_endpoint(
    request: SaveDeviceTokenRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
):
    """Handle POST /api/devices/save-device-token: upsert one device's tokens, schedule the Dataverse sync, and return all devices."""
    try:
        records = await save_device_token(
            contact_id=request.contact_id,
            userprofile_id=request.userprofile_id,
            devicetoken=request.devicetoken,
            cc_user_id=request.cc_user_id,
            voip_device_token=request.voip_device_token,
            device_id=request.device_id,
        )
        logger.info(
            f"save_device_token - success contact_id={request.contact_id} "
            f"userprofile_id={request.userprofile_id} devices={len(records)}"
        )
        background_tasks.add_task(
            sync_device_token_to_dataverse,
            contact_id=request.contact_id,
            userprofile_id=request.userprofile_id,
            devicetoken=request.devicetoken,
            cc_user_id=request.cc_user_id,
            voip_device_token=request.voip_device_token,
            device_id=request.device_id,
        )
        return SaveDeviceTokenResponse(data=records)
    except Exception as e:
        logger.error(
            f"save_device_token - failed contact_id={request.contact_id} "
            f"userprofile_id={request.userprofile_id} error={e}"
        , exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save device token")


@router.post(
    "/credsupdate",
    response_model=CredsUpdateResponse,
    status_code=200,
    summary="Update Credsupdate Flag",
    description=(
        "Set etown_contactdevices.credsupdate. `action_mode` 'frontend' (the default) updates the "
        "rows of `devicetoken` belonging to the contact given by `contactid` and/or `mobilenumber`, "
        "including the token's copies on that contact's other profiles. 'powerapps' needs only "
        "`credsupdate` and updates every row in the table."
    ),
    responses={
        200: {"description": "credsupdate written; returns the number of rows updated"},
        400: {"description": "Unknown action_mode, invalid credsupdate, or missing frontend fields"},
        500: {"description": "Internal server error"},
    },
)
async def update_credsupdate_endpoint(
    request: CredsUpdateRequest,
    token: str = Depends(verify_token),
):
    """Handle POST /api/devices/credsupdate: set credsupdate for one contact's device token, or on every device row."""
    action_mode = (request.action_mode or "frontend").strip().lower()
    if action_mode not in ("frontend", "powerapps"):
        raise HTTPException(status_code=400, detail="action_mode must be 'frontend' or 'powerapps'")

    flag = (request.credsupdate or "").strip().lower()
    if flag not in ("true", "false"):
        raise HTTPException(status_code=400, detail="credsupdate must be 'true' or 'false'")

    if action_mode == "frontend":
        if not (request.devicetoken or "").strip():
            raise HTTPException(status_code=400, detail="devicetoken is required when action_mode is 'frontend'")
        if not (request.contactid or "").strip() and not (request.mobilenumber or "").strip():
            raise HTTPException(
                status_code=400,
                detail="contactid or mobilenumber is required when action_mode is 'frontend'",
            )

    try:
        updated = await update_credsupdate(
            action_mode=action_mode,
            credsupdate=flag == "true",
            devicetoken=request.devicetoken,
            contactid=request.contactid,
            mobilenumber=request.mobilenumber,
        )
        logger.info(
            f"update_credsupdate - success action_mode={action_mode} credsupdate={flag} "
            f"contactid={request.contactid} updated={updated}"
        )
        return CredsUpdateResponse(action_mode=action_mode, credsupdate=flag == "true", updated_count=updated)
    except Exception as e:
        logger.error(
            f"update_credsupdate - failed action_mode={action_mode} contactid={request.contactid} error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to update credsupdate")


@router.get(
    "/uninstalls",
    response_model=AppUninstallListResponse,
    status_code=200,
    summary="List App Uninstalls",
    description=(
        "Devices FCM has declared unreachable — the app uninstall signal. One record per "
        "DEVICE, not per user: a person who removed the app from one handset but kept it on "
        "another still has a reachable device. Defaults to currently-uninstalled records; pass "
        "`include_reinstalled=true` to keep devices that have since re-registered. Devices that "
        "were ALREADY dead when detection was switched on are recorded as `baseline` and excluded "
        "unless `include_baseline=true`, because their detection date is the day we looked rather "
        "than the day they went. "
        "Note that a dead token also results from cleared app data, a factory reset, a restore "
        "from backup, or a delete-then-reinstall, so this measures unreachable devices rather "
        "than confirmed uninstalls."
    ),
    responses={
        200: {"description": "Uninstall records, newest detection first"},
        500: {"description": "Internal server error"},
    },
)
async def list_app_uninstalls(
    contact_id: Optional[str] = Query(None, description="Restrict to one contact's devices"),
    include_reinstalled: bool = Query(False, description="Keep devices that have since re-registered"),
    include_baseline: bool = Query(False, description="Keep the first sweep's pre-existing dead devices"),
    since_days: Optional[int] = Query(None, ge=1, le=3650, description="Only detections within this many days"),
    limit: int = Query(100, ge=1, le=500, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    token: str = Depends(verify_token),
):
    """Handle GET /api/devices/uninstalls: page through app uninstall records, newest detection first."""
    try:
        records = await fetch_uninstalls(
            contact_id=contact_id,
            include_reinstalled=include_reinstalled,
            include_baseline=include_baseline,
            since_days=since_days,
            limit=limit,
            offset=offset,
        )
        return AppUninstallListResponse(count=len(records), data=records)
    except Exception as e:
        logger.error(
            f"list_app_uninstalls - failed contact_id={contact_id} error={e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to read app uninstalls")
