"""Feed Notification Helpers - push fan-out for new and boosted feed posts.

Domain helpers that notify the poster's circle with one governance send per member under bounded concurrency, and broadcast a boosted post to every user through the central dispatcher once per feed_id.

Classes:
    No classes defined (module exposes the two fan-out orchestrator functions)

Functions:
    send_boosted_feed_broadcast(): broadcast a boosted feed post to every user
    notify_circle_on_new_feed(): per-recipient fan-out to the poster's circle

Dependencies:
    - asyncio: Bounds circle fan-out concurrency (Semaphore) and runs per-recipient sends
    - app.core.logging: Centralized application logging
    - app.etown.notifications.core.dispatcher: dispatch(), the BROADCAST push path
    - app.etown.notifications.core.models: NotificationRequest, Recipient, PushAudience
    - app.etown.notifications.core.registry: NotificationType
    - app.etown.notifications.ledger: claim_once(), de-dupes a broadcast per feed_id
    - app.etown.notifications.service: GovernanceRequest and send(), the per-recipient path
    - app.etown.notifications.fingerprint: once(), builds each recipient's dedupe key
    - app.etown.offers.offers_utils: get_provider_circle_members(), resolves the poster's circle

Updates:
    03-Jun-2026: Legacy direct-FCM helpers removed; routes through dispatch().
"""

import asyncio
from typing import Optional

from app.core.logging import logger
from app.etown.notifications.core.dispatcher import dispatch
from app.etown.notifications.core.models import NotificationRequest, Recipient, PushAudience
from app.etown.notifications.core.registry import NotificationType


async def send_boosted_feed_broadcast(
    *,
    oauth_token: Optional[str],
    poster_contact_id: str,
    poster_userprofile_id: Optional[str] = None,
    body: str,
    feed_id: str,
    image: Optional[str] = None,
    is_text_post: bool = False,
) -> None:
    """Broadcast a new-feed push to every user except the poster, at most once per feed id."""
    from app.etown.notifications import ledger
    if not await ledger.claim_once(f"broadcast-feed:{feed_id}", notification_type=NotificationType.FEED):
        logger.info("send_boosted_feed_broadcast: already broadcast for feed_id=%s — skipping", feed_id)
        return
    await dispatch(NotificationRequest(
        notification_type=NotificationType.FEED,
        recipient=Recipient(exclude_contact_id=poster_contact_id),
        context={
            "body": body,
            "feed_id": feed_id,
            "image": image,
            "userprofile_id": poster_userprofile_id,
            "is_text_post": is_text_post,
        },
        push_audience=PushAudience.BROADCAST,
        oauth_token=oauth_token,
    ))


async def notify_circle_on_new_feed(
    *,
    oauth_token: Optional[str],
    poster_contact_id: str,
    poster_userprofile_id: Optional[str] = None,
    poster_firstname: str,
    feed_id: str,
    image: Optional[str] = None,
    is_text_post: bool = False,
) -> None:
    """Send a deduplicated new-post push through governance to each member of the poster's circle."""
    from app.etown.offers.offers_utils import get_provider_circle_members
    from app.etown.notifications.service import GovernanceRequest, send as gov_send
    from app.etown.notifications import fingerprint

    members = await get_provider_circle_members(poster_contact_id)
    if not members:
        logger.info("notify_circle_on_new_feed: empty circle for feed_id=%s", feed_id)
        return

    body = f"{poster_firstname} shared a new post"
    sem = asyncio.Semaphore(20)

    async def _one(m: dict) -> None:
        async with sem:
            if m.get("contactid") == poster_contact_id:
                return
            await gov_send(GovernanceRequest(
                userprofileid=m["userprofileid"],
                contactid=m.get("contactid"),
                notification_type=NotificationType.FEED,
                ctx={
                    "body": body,
                    "feed_id": feed_id,
                    "image": image,
                    "userprofile_id": poster_userprofile_id,
                    "is_text_post": is_text_post,
                },
                oauth_token=oauth_token,
                dedupe_key=fingerprint.once("feed", feed_id, m["contactid"]),
                feature="feed",
            ))

    await asyncio.gather(*[_one(m) for m in members], return_exceptions=True)
