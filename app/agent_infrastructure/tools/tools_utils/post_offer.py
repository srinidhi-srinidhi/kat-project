"""Post Offer Tool - drafts and posts a provider offer from a Chaatie conversation.

Provider-agent tool behind create_offer_from_chat: previews a draft after an allowance check, then on confirm posts through create_offer_full, the same pipeline and plan limits as /api/offers/create, always to the provider's own Circle.

Features:
    - Two-step Draft/Confirm: "draft" previews after an allowance pre-check; "confirm" posts via create_offer_full
    - Limits: Plan caps on active/monthly offers; 1-per-24h cap only when no plan resolves
    - Image Attachment: An optional image_url is passed through as offerfile1
    - Guardrails: Missing account, empty description or no business profile return a guiding message

Classes:
    No classes defined (module exposes the draft/confirm handler and a rate-limit message helper)

Functions:
    _rate_limit_message(): Formats the "try again at HH:MM AM/PM IST" rate-limit message
    handle_create_offer_from_chat(): Draft/confirm handler for the create_offer_from_chat tool

Dependencies:
    - app.etown.offers.offers_utils: offer creation, business name, rate-limit info, boost check
    - app.etown.plans.plans_utils: resolve_plan, PlanLimitError

Updates:
    16-Jun-2026: Initial creation of the conversational draft/confirm offer-posting tool.
    09-Jul-2026: Added image_url support, attached as offerfile1.
    24-Aug-2026: Plan caps govern offer limits; 24h cap as no-plan fallback; PlanLimitError surfaced.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

import logging
from datetime import timezone, timedelta
from typing import Any, Dict, Optional, Tuple

from app.etown.offers.offers_utils import (
    create_offer_full,
    get_provider_businessname,
    get_offer_rate_limit_info,
    is_provider_boosted,
    OfferRateLimitError,
)
from app.etown.plans.plans_utils import PlanLimitError, resolve_plan

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _rate_limit_message(retry_after_utc) -> str:
    """Return a human-friendly rate-limit message with the exact IST retry time."""
    if retry_after_utc is not None:
        retry_ist = retry_after_utc.astimezone(_IST)
        return (
            f"You can only post one offer every 24 hours. "
            f"You'll be able to post again at {retry_ist.strftime('%I:%M %p')} IST today."
        )
    return "You can only post one offer every 24 hours. Please try again later."


async def handle_create_offer_from_chat(
    *,
    contact_id: Optional[str],
    userprofile_id: Optional[str],
    oauth_token: Optional[str] = None,
    description: str,
    title: Optional[str] = None,
    validity: Optional[str] = None,
    scheduled_time: Optional[str] = None,
    image_url: Optional[str] = None,
    action: str = "draft",
) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """Preview a provider's offer on draft, or post it to their Circle otherwise, enforcing the free-tier rate limit.

    Returns:
        Tuple of (assistant text, implicit output with stage preview or posted, response type).
    """
    if not contact_id or not userprofile_id:
        return (
            "I couldn't find your provider account details. Please make sure you're "
            "logged in with your provider account before posting an offer.",
            None,
            None,
        )

    if not description or not description.strip():
        return (
            "What would you like your offer to say? Tell me the deal or promotion "
            "(for example, '20% off all cakes this weekend').",
            None,
            None,
        )

    businessname = await get_provider_businessname(userprofile_id)
    if not businessname:
        return (
            "I couldn't find your business profile, so I'm unable to post an offer yet. "
            "Please complete your provider profile and try again.",
            None,
            None,
        )

    if await resolve_plan(userprofile_id) is None and not await is_provider_boosted(userprofile_id):
        recent_offer_count, retry_after_utc = await get_offer_rate_limit_info(userprofile_id)
        if recent_offer_count >= 1:
            return (_rate_limit_message(retry_after_utc), None, None)

    if action == "draft":
        preview = {
            "businessname": businessname,
            "title": title,
            "description": description,
            "validity": validity or "Valid",
            "scheduled_time": scheduled_time,
            "audience": "My Circle",
            "image_url": image_url,
        }
        heading = title or businessname
        image_line = "\nYour generated image will be attached." if image_url else ""
        text = (
            f"Here's a preview of your offer:\n\n"
            f"**{heading}**\n{description}{image_line}\n\n"
            f"It will be shared with your Circle. Want me to post it?"
        )
        return text, {"stage": "preview", "offer": preview}, "create_offer_from_chat"

    try:
        formatted_offer = await create_offer_full(
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            businessname=businessname,
            description=description,
            title=title,
            validity=validity,
            scheduled_time=scheduled_time,
            offerfile1=image_url,
            offer_type="1",
            tags=f"{userprofile_id}_my_circle",
            oauth_token=oauth_token,
        )
    except OfferRateLimitError:
        logger.info("post_offer: offer creation blocked by the offer rate limit")
        _, retry_after_utc = await get_offer_rate_limit_info(userprofile_id)
        return (_rate_limit_message(retry_after_utc), None, None)
    except PlanLimitError as exc:
        logger.info("post_offer: offer creation blocked by the plan limit: %s", exc)
        return (str(exc), None, None)
    except Exception as exc:
        logger.error("create_offer_from_chat: failed to post offer: %s", exc, exc_info=True)
        return (
            "Sorry, something went wrong while posting your offer. Please try again in a moment.",
            None,
            None,
        )

    text = (
        f"✅ Your offer from {businessname} is live and has been shared with your Circle!"
    )
    return text, {"stage": "posted", "offer": formatted_offer}, "create_offer_from_chat"
