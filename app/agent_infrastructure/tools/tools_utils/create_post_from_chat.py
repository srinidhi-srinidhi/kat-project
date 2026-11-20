"""Create Post From Chat Tool - drafts and publishes a feed post from a Chaatie conversation.

Member- and provider-agent tool handler with a draft/confirm flow: a draft returns a preview, and a confirm writes through create_feed_post, the same pipeline as /api/feeds/create, then notifies the poster's circle or broadcasts for boosted providers and mirrors the post to Dataverse in the background.

Features:
    - Draft/Confirm Flow: 'draft' returns a preview and writes nothing; 'confirm' calls create_feed_post
    - Role-aware Poster Identity: providers post under business name, members under display name
    - Notification Routing: boosted providers broadcast platform-wide; others notify their circle
    - Single Image Attachment: optional image_url fills the first image slot and flips is_text_post
    - Plan Limit Handling: PlanLimitError returns a friendly upgrade message
    - Asynchronous Dataverse Sync: insert_feed_to_dataverse runs as a fire-and-forget task

Classes:
    No classes defined (module exposes the create_post_from_chat draft/confirm handler)

Functions:
    handle_create_post_from_chat(): Draft/confirm handler for the create_post_from_chat tool.

Dependencies:
    - app.etown.feed.feed_utils: create_feed_post, FeedDatabase.get_feed_by_id
    - app.etown.feed.feed_syncing: insert_feed_to_dataverse
    - app.etown.feed.feed_notifications: notify_circle_on_new_feed, send_boosted_feed_broadcast
    - app.etown.offers.offers_utils: get_provider_businessname, is_provider_boosted
    - app.etown.plans.plans_utils: PlanLimitError

Updates:
    23-Jun-2026: Initial creation — DB-backed post creation with Dataverse sync and notifications.
    12-Aug-2026: Accepts image_url, stores it on feedfilelocationurl and flips is_text_post.
    18-Aug-2026: A provider plan limit now returns a friendly upgrade message, not a generic error.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

from app.etown.feed.feed_utils import create_feed_post, Database as FeedDatabase
from app.etown.feed.feed_syncing import insert_feed_to_dataverse
from app.etown.feed.feed_notifications import notify_circle_on_new_feed, send_boosted_feed_broadcast
from app.etown.offers.offers_utils import get_provider_businessname, is_provider_boosted
from app.etown.plans.plans_utils import PlanLimitError

logger = logging.getLogger(__name__)


async def handle_create_post_from_chat(
    *,
    contact_id: Optional[str],
    userprofile_id: Optional[str],
    role: int,
    user_name: Optional[str],
    oauth_token: Optional[str] = None,
    post_text: str,
    image_url: Optional[str] = None,
    action: str = "draft",
) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """Preview a Circle feed post on "draft", or publish it and notify the Circle on "confirm".

    Args:
        contact_id: Poster's contact id from agent context.
        userprofile_id: Poster's userprofile id from agent context.
        role: 1 for provider, 2 for member.
        user_name: Display name used for member posts.
        oauth_token: Token passed to the notification dispatcher.
        post_text: Text of the post.
        image_url: Optional blob path of one attached image, stored in the first image slot.
        action: "draft" to preview, anything else to publish.

    Returns:
        (assistant_text, implicit_output, response_type); response_type is None on errors.
    """
    if not contact_id or not userprofile_id:
        return (
            "I couldn't find your account details. Please make sure you're logged in before creating a post.",
            None,
            None,
        )

    if not post_text or not post_text.strip():
        return (
            "What would you like your post to say? Share the text you'd like to post.",
            None,
            None,
        )

    if role == 1:
        businessname = await get_provider_businessname(userprofile_id)
        if not businessname:
            return (
                "I couldn't find your business profile. Please complete your provider profile and try again.",
                None,
                None,
            )
        poster_display_name = businessname
        servicename: Optional[str] = businessname
    else:
        poster_display_name = user_name or "User"
        servicename = None

    if action == "draft":
        preview = {
            "post_text": post_text,
            "posted_by": poster_display_name,
            "audience": "My Circle",
            "image_url": image_url,
        }
        image_line = "\nYour generated image will be attached." if image_url else ""
        text = (
            f"Here's a preview of your post:\n\n"
            f"**{poster_display_name}**\n{post_text}{image_line}\n\n"
            f"It will be shared with your Circle. Ready to post it?"
        )
        return text, {"stage": "preview", "post": preview}, "create_post_from_chat"

    try:
        feed_id = await create_feed_post(
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            role=role,
            feed_text=post_text,
            servicename=servicename,
            audience="mycircle",
            feedfilelocationurl=image_url,
            feedfilelocation2=None,
            feedfilelocation3=None,
            feedfilelocation4=None,
            feedfilelocation5=None,
            feedvideofilelocation=None,
            thumbnailfileurl=None,
            serviceimageurl=None,
            orientation=None,
        )

        row = await FeedDatabase.get_feed_by_id(
            feed_id=feed_id,
            current_contact_id=contact_id,
            current_user_profile_id=userprofile_id,
        )

        if row:
            asyncio.create_task(insert_feed_to_dataverse(dict(row)))

        boosted = role == 1 and await is_provider_boosted(userprofile_id)
        if boosted:
            asyncio.create_task(
                send_boosted_feed_broadcast(
                    oauth_token=oauth_token,
                    poster_contact_id=contact_id,
                    poster_userprofile_id=userprofile_id,
                    body=f"{poster_display_name} shared a new post",
                    feed_id=feed_id,
                    is_text_post=not image_url,
                )
            )
        else:
            asyncio.create_task(
                notify_circle_on_new_feed(
                    oauth_token=oauth_token,
                    poster_contact_id=contact_id,
                    poster_userprofile_id=userprofile_id,
                    poster_firstname=poster_display_name,
                    feed_id=feed_id,
                    is_text_post=not image_url,
                )
            )

    except PlanLimitError as exc:
        logger.info("create_post_from_chat: plan limit reached for userprofile_id=%s: %s", userprofile_id, exc)
        return (
            f"{exc} Upgrade your plan to post more, or adjust this post and try again.",
            None,
            None,
        )
    except Exception as exc:
        logger.error("create_post_from_chat: failed to publish post: %s", exc, exc_info=True)
        return (
            "Sorry, something went wrong while publishing your post. Please try again in a moment.",
            None,
            None,
        )

    text = f"✅ Your post is live and has been shared with your Circle!"
    return text, {"stage": "posted", "feed_id": feed_id}, "create_post_from_chat"
