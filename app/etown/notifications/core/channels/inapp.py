"""In-App Channel Adapter - writes notification rows into etown_notifications.

Notification channel adapter plus insert_notification_row(), the single canonical writer for etown_notifications, which inserts only the supplied columns and returns the new row; the in-app adapter and the booking flow both write through it.

Features:
    - Sparse Column INSERT: writes only supplied columns plus notificationsid/createdon
    - Role Coercion: maps an int, numeric string or role name to the integer role column
    - Shadow-Mode Skip: in shadow mode, logs and returns success without writing a row
    - Unread Marker: the adapter sets statuscode='1' so the row counts as unread in get_notification_count

Classes:
    InAppAdapter(): ChannelAdapter that writes a row into etown_notifications

Functions:
    insert_notification_row(): Insert one etown_notifications row and return it, or None on failure

Dependencies:
    - app.db.client: Async PostgreSQL database client
    - app.core.logging: Centralized application logging
    - app.etown.notifications.core.config: Shadow-mode flag check
    - app.etown.notifications.core.channels.base: ChannelAdapter base class
    - app.etown.notifications.core.models: Channel enum, NotificationRequest
    - app.etown.notifications.core.registry: TypeConfig

Updates:
    12-Aug-2026: insert_notification_row() coerces role to the integer the column actually is.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.logging import logger
from app.db.client import Database
from app.etown.notifications.core import config as ncfg
from app.etown.notifications.core.channels.base import ChannelAdapter
from app.etown.notifications.core.models import Channel, NotificationRequest
from app.etown.notifications.core.registry import TypeConfig

ROLE_NAMES = {"provider": 1, "member": 2}


async def insert_notification_row(
    *,
    sender_contact_id: Optional[str] = None,
    sender_userprofile_id: Optional[str] = None,
    receiver_contact_id: Optional[str] = None,
    receiver_userprofile_id: Optional[str] = None,
    content: Optional[str] = None,
    notification_type: Optional[str] = None,
    role: Optional[Any] = None,
    notificationstatus: Optional[str] = None,
    statuscode: Optional[str] = None,
    statecode: str = "Active",
) -> Optional[Dict[str, Any]]:
    """Insert an etown_notifications row with only the non-None columns and return it, or None on failure."""
    columns = ["notificationsid"]
    placeholders = ["gen_random_uuid()::text"]
    params: list = []

    def _add(col: str, val: Any) -> None:
        params.append(val)
        columns.append(col)
        placeholders.append(f"${len(params)}")

    if sender_contact_id is not None:
        _add("sendercontactid", sender_contact_id)
    if sender_userprofile_id is not None:
        _add("senderuserprofileid", sender_userprofile_id)
    if receiver_contact_id is not None:
        _add("receivercontactid", receiver_contact_id)
    if receiver_userprofile_id is not None:
        _add("receiveruserprofileid", receiver_userprofile_id)
    if content is not None:
        _add("notificationcontent", content)
    if notification_type is not None:
        _add("notificationtype", notification_type)
    if role is not None:
        text = str(role).strip()
        if isinstance(role, int) and not isinstance(role, bool):
            coerced = role
        elif text.lstrip("-").isdigit():
            coerced = int(text)
        else:
            coerced = ROLE_NAMES.get(text.lower())
        if coerced is None:
            logger.warning("insert_notification_row: unmappable role %r — writing NULL", role)
        else:
            _add("role", coerced)
    if notificationstatus is not None:
        _add("notificationstatus", notificationstatus)
    if statuscode is not None:
        _add("statuscode", statuscode)
    if statecode is not None:
        _add("statecode", statecode)

    columns.append("createdon")
    placeholders.append("NOW()")

    sql = (
        f"INSERT INTO public.etown_notifications ({', '.join(columns)}) "
        f"VALUES ({', '.join(placeholders)}) RETURNING *"
    )
    try:
        row = await Database.fetchrow(sql, *params)
        return dict(row) if row else None
    except Exception as exc:
        logger.warning("insert_notification_row failed: %s", exc)
        return None


class InAppAdapter(ChannelAdapter):
    channel = Channel.INAPP

    async def deliver(self, request: NotificationRequest, cfg: TypeConfig) -> bool:
        """Write the in-app notification row for the recipient, or only log it in shadow mode."""
        r = request.recipient
        ctx = request.context
        content = ctx.get("inapp_content") or ctx.get("body") or ctx.get("title")
        if not content or not (r.userprofile_id or r.contact_id):
            logger.info("%s inapp: missing content or receiver, skipping", cfg.key)
            return False
        ntype = ctx.get("inapp_type") or ctx.get("data_type") or cfg.key
        if ncfg.is_shadow_mode():
            logger.info(
                "[SHADOW] %s inapp would insert | receiver_upid=%s receiver_cid=%s type=%s",
                cfg.key, r.userprofile_id, r.contact_id, ntype,
            )
            return True
        row = await insert_notification_row(
            sender_contact_id=ctx.get("sender_contact_id"),
            sender_userprofile_id=ctx.get("sender_userprofile_id"),
            receiver_contact_id=r.contact_id,
            receiver_userprofile_id=r.userprofile_id,
            content=content,
            notification_type=ntype,
            role=ctx.get("role"),
            statuscode="1",
        )
        return row is not None
