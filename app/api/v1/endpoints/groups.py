"""Groups Endpoints - HTTP routes for reading groups with members and managing group writes.

HTTP layer for /api/groups/groups: checks the shared bearer token, delegates group and member reads to groups_utils, and switches create, update and delete on actionmode with a background Dataverse mirror for each write.

Features:
    - Group discovery: Retrieve groups by user credentials or specific group ID
    - Member management: Access group member lists and details
    - Group metadata: Complete group information including name, image, creation details
    - Member counts: Real-time group membership statistics
    - Group writes: create, update and delete switched on actionmode
    - Background Dataverse mirror for every group write
    - Pagination support: page/limit query parameters (limit capped at 50)
    - Response organization: Structured data with nested member details
    - API activity tracking via track_api_call

Endpoints:
    GET  /groups: Retrieve groups with members by user or group identifier
    POST /groups: Manage a group (create / update / delete), switched on actionmode

Classes:
    No classes defined (module uses GroupManageRequest imported from app.schemas.api)

Functions:
    get_groups(): Fetch groups and members by various query parameters
    manage_groups(): Single write endpoint switched on actionmode (create/update/delete)

Dependencies:
    - fastapi: API framework and query parameter handling
    - app.core.security: verify_token shared bearer token check
    - app.core.logging: Centralized logging infrastructure
    - app.etown.groups.groups_utils: Group utility functions
    - app.etown.groups.groups_syncing: Background Dataverse mirror for group writes
    - app.schemas.api: Group API request model
    - app.services.api_activity: track_api_call() activity recording

Updates:
    16-Mar-2026: Initial creation with collections pattern implementation
    13-Jul-2026: Added the POST /groups write endpoint, switching create/update/delete on actionmode.
    26-Aug-2026: Logged previously-silent exception handlers with tracebacks.

Notes:
    Follows exact collections endpoint pattern for consistency.
    Optimized for performance with bulk queries and proper indexing.
    Supports both user-based and group-specific lookups.
    The write endpoint persists to Postgres synchronously, then schedules the Dataverse mirror.
"""

from fastapi import APIRouter, Query, Depends, HTTPException, BackgroundTasks, Body
from typing import Optional, Any

from app.core.security import verify_token
from app.core.logging import logger
from app.etown.groups.groups_utils import (
    handle_groups,
    create_group_workflow,
    update_group_workflow,
    delete_group_workflow,
)
from app.etown.groups.groups_syncing import (
    sync_group_create_to_dataverse,
    sync_group_update_to_dataverse,
    sync_group_delete_from_dataverse,
)
from app.schemas.api import GroupManageRequest
from app.services.api_activity import track_api_call

router = APIRouter()

@router.get("/groups", response_model=Any)
async def get_groups(
    contact_id: Optional[str] = Query(None, description="The contact ID of the user."),
    user_profile_id: Optional[str] = Query(None, description="The user profile ID of the user."),
    groupid: Optional[str] = Query(None, description="Optional: ID of a specific group to fetch."),
    groupuniqueid: Optional[str] = Query(None, description="Optional: Unique ID of a specific group to fetch."),
    page: int = Query(1, description="Page number for pagination", ge=1),
    limit: int = Query(20, description="Number of groups/members to return", ge=1, le=50),
    token: str = Depends(verify_token),
    background_tasks: BackgroundTasks = BackgroundTasks(),
):
    """Handle GET /api/groups/groups: return one group by id or unique id, else a page of the user's groups with members.

    Raises:
        HTTPException: 400 for invalid parameters, 500 on other failures.
    """
    track_api_call(background_tasks, "/api/groups/groups", contact_id, user_profile_id)
    try:
        offset = (page - 1) * limit
        
        return await handle_groups(
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            groupid=groupid,
            groupuniqueid=groupuniqueid,
            limit=limit,
            offset=offset
        )
    
    except ValueError as e:
        logger.error(f"Validation error in /groups: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in /groups: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.post(
    "/groups",
    response_model=Any,
    status_code=200,
    summary="Manage a group (create / update / delete)",
    description=(
        "Single group-management endpoint mirroring the PowerApps flow. Branches on "
        "`actionmode` (create | update | delete); `update` further branches on "
        "`groupupdatecondition` (groupupdate | groupitemsupdate | completegroupupdate). "
        "Returns `{groupid, groupname}` for create/update and `{message}` for delete."
    ),
    responses={
        200: {
            "description": "Group action completed",
            "content": {
                "application/json": {
                    "examples": {
                        "created_or_updated": {"value": {"groupid": "…", "groupname": "Family Group"}},
                        "deleted": {"value": {"message": "Group deleted successfully"}},
                    }
                }
            },
        },
        404: {"description": "Group not found (update)"},
        422: {"description": "Missing required field for the given actionmode"},
    },
)
async def manage_groups(
    background_tasks: BackgroundTasks,
    request: GroupManageRequest = Body(
        ...,
        openapi_examples={
            "create": {
                "summary": "Create a group with members",
                "value": {
                    "actionmode": "create",
                    "user_contactid": "00000000-0000-0000-0000-000000000001",
                    "user_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "groupname": "Family Group",
                    "groupimageurl": "<base64-or-null>",
                    "groupitems": [{"userprofile_id": "00000000-0000-0000-0000-000000000003", "name": "Asha"}],
                },
            },
            "update_group_only": {
                "summary": "Update group details only",
                "value": {
                    "actionmode": "update",
                    "groupupdatecondition": "groupupdate",
                    "groupid": "<group-id>",
                    "user_contactid": "00000000-0000-0000-0000-000000000001",
                    "user_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "groupname": "Renamed Group",
                    "groupimageurl": "<base64-or-null-to-keep-existing>",
                },
            },
            "update_items_only": {
                "summary": "Replace the member list only",
                "value": {
                    "actionmode": "update",
                    "groupupdatecondition": "groupitemsupdate",
                    "groupid": "<group-id>",
                    "user_contactid": "00000000-0000-0000-0000-000000000001",
                    "user_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "groupitems": [{"userprofile_id": "00000000-0000-0000-0000-000000000004", "name": "Ravi"}],
                },
            },
            "update_complete": {
                "summary": "Update group details and member list",
                "value": {
                    "actionmode": "update",
                    "groupupdatecondition": "completegroupupdate",
                    "groupid": "<group-id>",
                    "user_contactid": "00000000-0000-0000-0000-000000000001",
                    "user_userprofileid": "00000000-0000-0000-0000-000000000002",
                    "groupname": "Family Group",
                    "groupitems": [{"userprofile_id": "00000000-0000-0000-0000-000000000003", "name": "Asha"}],
                },
            },
            "delete": {
                "summary": "Delete a group and its members",
                "value": {"actionmode": "delete", "groupid": "<group-id>"},
            },
        },
    ),
    token: str = Depends(verify_token),
) -> Any:
    """Handle POST /api/groups/groups: create, update or delete a group by actionmode and mirror it to Dataverse.

    Raises:
        HTTPException: 404 when an updated group does not exist, 500 on other failures.
    """
    items = [item.model_dump() for item in request.groupitems]
    try:
        if request.actionmode == "create":
            result = await create_group_workflow(
                user_contactid=request.user_contactid,
                user_userprofileid=request.user_userprofileid,
                groupname=request.groupname,
                groupimageurl=request.groupimageurl,
                groupitems=items,
            )
            background_tasks.add_task(sync_group_create_to_dataverse, result, result["items"])
            return {"groupid": result["groupid"], "groupname": result["groupname"]}

        if request.actionmode == "update":
            result = await update_group_workflow(
                groupid=request.groupid,
                groupupdatecondition=request.groupupdatecondition,
                user_contactid=request.user_contactid,
                user_userprofileid=request.user_userprofileid,
                groupname=request.groupname,
                groupimageurl=request.groupimageurl,
                groupitems=items,
            )
            if result is None:
                raise HTTPException(status_code=404, detail=f"Group {request.groupid} not found")
            background_tasks.add_task(
                sync_group_update_to_dataverse, result, result["items"], result["items_replaced"]
            )
            return {"groupid": result["groupid"], "groupname": result["groupname"]}

        result = await delete_group_workflow(groupid=request.groupid)
        background_tasks.add_task(
            sync_group_delete_from_dataverse, result["groupid"], result["item_ids"]
        )
        return {"message": "Group deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in POST /groups (manage, actionmode={request.actionmode}): {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
