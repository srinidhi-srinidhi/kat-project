"""Remember Preference Tool - explicit remember and forget handlers for Chaatie's long-term memory.

Tool handlers that store a user-stated fact verbatim in mem0 (refused when the user's memory switch is off) and forget a described fact by similarity search, deleting and quoting a confident match and asking for confirmation below the similarity floor.

Functions:
    handle_remember_preference(): Store one user-stated fact and confirm it
    handle_forget_preference(): Find and delete one stored fact, quoting what was removed

Dependencies:
    - app.etown.memory.memory_utils: the blocking mem0 helpers, the delete, and the switch
    - asyncio: to_thread, because mem0's client is synchronous and does network I/O

Updates:
    07-Sep-2026: Initial creation of remember and forget preference handlers.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.etown.memory.memory_utils import (
    _sync_find_memory_to_forget,
    _sync_remember_fact,
    FORGET_SIMILARITY_FLOOR,
    delete_memory,
    is_memory_enabled,
)

logger = logging.getLogger(__name__)

MEMORY_WRITE_TIMEOUT_SECONDS = 6.0

_MEMORY_OFF_MESSAGE = (
    "Memory is switched off for this account, so I can't save that. You can turn it back on in "
    "your settings, and then ask me again."
)


async def handle_remember_preference(
    fact: str,
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    user_name: Optional[str] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Store a fact the user explicitly asked to be remembered, unless memory is off or the write times out.

    Args:
        fact: Short third-person statement about the user, already phrased by the agent.
        contact_id: Caller's contact id, used when no profile id is available.
        user_profile_id: Caller's user-profile id; memories are keyed on this.
        conversation_id: Conversation the request came from, stored in the metadata.
        user_name: Display name, stored in the metadata.
        language: Language code of the turn, stored in the metadata.

    Returns:
        Dict with success, message and remembered; never raises.
    """
    owner = (user_profile_id or "").strip() or (contact_id or "").strip()
    cleaned = (fact or "").strip()

    if not owner:
        return {
            "success": False,
            "message": "I couldn't tell which account to save that against.",
            "remembered": None,
        }
    if not cleaned:
        return {
            "success": False,
            "message": "Tell me what you'd like me to remember and I'll save it.",
            "remembered": None,
        }

    if not await is_memory_enabled(owner):
        return {"success": False, "message": _MEMORY_OFF_MESSAGE, "remembered": None}

    metadata = {
        "user_id": owner,
        "user_name": user_name or "User",
        "conversation_id": conversation_id or "",
        "language": language or "en",
        "source": "explicit",
        "timestamp": datetime.now().isoformat(),
    }

    try:
        stored = await asyncio.wait_for(
            asyncio.to_thread(_sync_remember_fact, owner, cleaned, metadata),
            timeout=MEMORY_WRITE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("remember_preference timed out for %s: %r", owner, cleaned)
        return {
            "success": False,
            "message": "That took too long to save. Ask me again and I'll retry.",
            "remembered": None,
        }
    except Exception as exc:
        logger.error("remember_preference failed for %s: %s", owner, exc, exc_info=True)
        return {
            "success": False,
            "message": "I couldn't save that just now.",
            "remembered": None,
        }

    logger.info("Explicit memory stored for %s: %r", owner, stored)
    return {
        "success": True,
        "message": f"Saved — I'll remember that {stored[0].lower()}{stored[1:]}.",
        "remembered": stored,
    }


async def handle_forget_preference(
    description: str,
    contact_id: Optional[str] = None,
    user_profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete the stored memory closest to the user's description, or ask to confirm a weak match.

    Args:
        description: The user's own wording of what to forget.
        contact_id: Caller's contact id, used when no profile id is available.
        user_profile_id: Caller's user-profile id; memories are keyed on this.

    Returns:
        Dict with success, message and forgotten, plus needs_confirmation and candidate on weak matches.
    """
    owner = (user_profile_id or "").strip() or (contact_id or "").strip()
    cleaned = (description or "").strip()

    if not owner or not cleaned:
        return {
            "success": False,
            "message": "Tell me which detail you'd like me to forget.",
            "forgotten": None,
        }

    try:
        target = await asyncio.wait_for(
            asyncio.to_thread(_sync_find_memory_to_forget, owner, cleaned),
            timeout=MEMORY_WRITE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("forget_preference lookup timed out for %s: %r", owner, cleaned)
        return {
            "success": False,
            "message": "That took too long to look up. Ask me again and I'll retry.",
            "forgotten": None,
        }
    except Exception as exc:
        logger.error("forget_preference lookup failed for %s: %s", owner, exc, exc_info=True)
        return {
            "success": False,
            "message": "I couldn't check what I have stored just now.",
            "forgotten": None,
        }

    if not target:
        return {
            "success": False,
            "message": (
                "I don't have anything stored that matches that. You can see everything I "
                "remember in your settings."
            ),
            "forgotten": None,
        }

    if target["similarity"] < FORGET_SIMILARITY_FLOOR:
        logger.info(
            "forget_preference needs confirmation for %s: %r -> %r (similarity %.2f)",
            owner, cleaned, target["memory"], target["similarity"],
        )
        return {
            "success": False,
            "needs_confirmation": True,
            "candidate": target["memory"],
            "message": (
                f"The closest thing I have stored is \"{target['memory']}\". Shall I forget "
                "that one? If you meant something else, tell me and I'll look again."
            ),
            "forgotten": None,
        }

    try:
        removed = await delete_memory(owner, target["memory_id"])
    except Exception as exc:
        logger.error("forget_preference delete failed for %s: %s", owner, exc, exc_info=True)
        return {
            "success": False,
            "message": "I found it but couldn't remove it just now.",
            "forgotten": None,
        }

    if not removed:
        return {
            "success": False,
            "message": "I couldn't remove that — it may already be gone.",
            "forgotten": None,
        }

    logger.info(
        "Explicit memory forgotten for %s: %r (similarity %.2f)",
        owner, target["memory"], target["similarity"],
    )
    return {
        "success": True,
        "message": f"Done — I've forgotten that {target['memory'][0].lower()}{target['memory'][1:]}.",
        "forgotten": target["memory"],
    }
