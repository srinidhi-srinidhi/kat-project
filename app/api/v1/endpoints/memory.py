"""Memory Endpoints - HTTP routes to list, delete and switch off what Chaatie remembers.

HTTP layer for /api/memory behind the shared bearer token: pages, deletes and toggles the caller's etown_mem0_memories through memory_utils, keyed on user_profile_id with contact_id as fallback; deletes are owner-scoped in SQL, and switching memory off stops reads and writes without deleting anything.

Endpoints:
    GET /memory: Page the caller's stored memories, newest first.
    DELETE /memory/{memory_id}: Forget one fact.
    DELETE /memory: Forget everything stored about the caller.
    GET /memory/settings: Whether memory is on, and how much is on file.
    PATCH /memory/settings: Turn memory on or off.

Functions:
    _resolve_owner(): Return the owner id, preferring user_profile_id over contact_id.
    list_user_memories(): Page the caller's memories.
    forget_one_memory(): Delete a single memory the caller owns.
    forget_all_memories(): Delete every memory the caller owns.
    read_memory_settings(): Report the caller's memory switch and total.
    update_memory_settings(): Set the caller's memory switch.

Dependencies:
    - app.core.security.verify_token: Shared bearer token dependency.
    - app.etown.memory.memory_utils: Memory reads, deletes and the enabled switch.
    - app.schemas.api: MemoryListResponse, MemoryDeleteResponse, MemorySettings*.
    - app.core.logging: Application logger.

Updates:
    07-Sep-2026: Created so a user can audit, correct and switch off their own memory.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.logging import logger
from app.core.security import verify_token
from app.etown.memory.memory_utils import (
    delete_all_memories,
    delete_memory,
    is_memory_enabled,
    list_memories,
    set_memory_enabled,
)
from app.schemas.api import (
    MemoryDeleteResponse,
    MemoryListResponse,
    MemorySettingsRequest,
    MemorySettingsResponse,
)

router = APIRouter(tags=["memory"])


def _resolve_owner(user_profile_id: Optional[str], contact_id: Optional[str]) -> str:
    """Return the id memories are stored under, preferring the profile id over the contact id.

    Raises:
        HTTPException: 400 when neither id was supplied.
    """
    owner = (user_profile_id or "").strip() or (contact_id or "").strip()
    if not owner:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide user_profile_id or contact_id.",
        )
    return owner


@router.get("", response_model=MemoryListResponse, dependencies=[Depends(verify_token)])
async def list_user_memories(
    user_profile_id: Optional[str] = Query(None, description="Caller's user-profile id"),
    contact_id: Optional[str] = Query(None, description="Caller's contact id"),
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Rows to skip"),
):
    """Handle GET /api/memory: return a page of the caller's stored memories, the total and the switch state.

    Raises:
        HTTPException: 400 with neither id, 500 when the read fails.
    """
    owner = _resolve_owner(user_profile_id, contact_id)
    try:
        page = await list_memories(owner, limit=limit, offset=offset)
        enabled = await is_memory_enabled(owner)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to list memories for %s: %s", owner, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve memories")

    return MemoryListResponse(
        success=True,
        enabled=enabled,
        count=len(page["memories"]),
        total=page["total"],
        memories=page["memories"],
    )


@router.delete(
    "/{memory_id}", response_model=MemoryDeleteResponse, dependencies=[Depends(verify_token)]
)
async def forget_one_memory(
    memory_id: str,
    user_profile_id: Optional[str] = Query(None, description="Caller's user-profile id"),
    contact_id: Optional[str] = Query(None, description="Caller's contact id"),
):
    """Handle DELETE /api/memory/{memory_id}: delete one of the caller's stored memories.

    Raises:
        HTTPException: 400 with neither id, 404 when not the caller's, 500 on failure.
    """
    owner = _resolve_owner(user_profile_id, contact_id)
    try:
        removed = await delete_memory(owner, memory_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to delete memory %s for %s: %s", memory_id, owner, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete the memory")

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such memory for this user.",
        )

    logger.info("Deleted memory %s for %s", memory_id, owner)
    return MemoryDeleteResponse(success=True, deleted=1, message="Memory forgotten.")


@router.delete("", response_model=MemoryDeleteResponse, dependencies=[Depends(verify_token)])
async def forget_all_memories(
    user_profile_id: Optional[str] = Query(None, description="Caller's user-profile id"),
    contact_id: Optional[str] = Query(None, description="Caller's contact id"),
):
    """Handle DELETE /api/memory: delete all of the caller's memories, leaving the memory switch unchanged.

    Raises:
        HTTPException: 400 with neither id, 500 when the delete fails.
    """
    owner = _resolve_owner(user_profile_id, contact_id)
    try:
        removed = await delete_all_memories(owner)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to clear memories for %s: %s", owner, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to clear memories")

    logger.info("Cleared %d memories for %s", removed, owner)
    return MemoryDeleteResponse(
        success=removed > 0,
        deleted=removed,
        message=(
            f"Forgot {removed} {'memory' if removed == 1 else 'memories'}."
            if removed
            else "There was nothing stored to forget."
        ),
    )


@router.get(
    "/settings", response_model=MemorySettingsResponse, dependencies=[Depends(verify_token)]
)
async def read_memory_settings(
    user_profile_id: Optional[str] = Query(None, description="Caller's user-profile id"),
    contact_id: Optional[str] = Query(None, description="Caller's contact id"),
):
    """Handle GET /api/memory/settings: return whether memory is on for the caller and the stored count.

    Raises:
        HTTPException: 400 with neither id, 500 when the read fails.
    """
    owner = _resolve_owner(user_profile_id, contact_id)
    try:
        enabled = await is_memory_enabled(owner)
        page = await list_memories(owner, limit=1, offset=0)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to read memory settings for %s: %s", owner, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read memory settings")

    return MemorySettingsResponse(success=True, enabled=enabled, total=page["total"])


@router.patch(
    "/settings", response_model=MemorySettingsResponse, dependencies=[Depends(verify_token)]
)
async def update_memory_settings(request: MemorySettingsRequest):
    """Handle PATCH /api/memory/settings: turn the caller's long-term memory on or off without deleting stored facts.

    Raises:
        HTTPException: 400 with neither id, 500 when the write fails.
    """
    owner = _resolve_owner(request.user_profile_id, request.contact_id)
    try:
        enabled = await set_memory_enabled(owner, request.enabled)
        page = await list_memories(owner, limit=1, offset=0)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update memory settings for %s: %s", owner, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update memory settings")

    return MemorySettingsResponse(success=True, enabled=enabled, total=page["total"])
