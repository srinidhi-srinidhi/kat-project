"""WhatsApp Click Utilities - persists recipient button taps from the WhatsApp webhook.

Notification helper called by POST /api/whatsapp/webhook that inserts each quick-reply or interactive button tap into notifications.etown_whatsapp_button_clicks, logging and swallowing any DB error so the webhook response never fails.

Features:
    - Button-tap Persistence: Normalises "button" and "interactive" events into one row insert.
    - Fire-and-forget Safety: Any DB error is caught and logged as a warning.

Classes:
    No classes defined (module exposes a single persistence function)

Functions:
    record_button_click(): Persist a parsed button-click webhook event to DB.

Dependencies:
    - app.db.client: Async PostgreSQL connection pool
    - app.core.logging: Centralised logger

Updates:
    26-Jun-2026: Initial creation
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import logger
from app.db.client import Database


_INSERT_SQL = """
    INSERT INTO notifications.etown_whatsapp_button_clicks
        (wamid, context_wamid, from_number, phone_number_id,
         button_text, button_id, button_type, raw)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""


async def record_button_click(ev: dict[str, Any]) -> None:
    """Insert a WhatsApp button or button_reply click into etown_whatsapp_button_clicks, logging and swallowing any error."""
    try:
        msg_type: str = ev.get("type") or ""
        if msg_type not in ("button", "interactive"):
            return

        raw: dict = ev.get("raw") or {}

        if msg_type == "button":
            btn_data: dict = raw.get("button") or {}
            button_text: str | None = btn_data.get("text")
            button_id: str | None = btn_data.get("payload")
            button_type = "button"

        else:
            interactive: dict = raw.get("interactive") or {}
            if interactive.get("type") != "button_reply":
                return
            btn_reply: dict = interactive.get("button_reply") or {}
            button_text = btn_reply.get("title")
            button_id = btn_reply.get("id")
            button_type = "button_reply"

        context_wamid: str | None = (raw.get("context") or {}).get("id")

        raw_json: str | None = None
        try:
            raw_json = json.dumps(raw, ensure_ascii=False)
        except Exception:
            logger.warning("[WA_CLICK] raw payload is not JSON-serialisable; storing null", exc_info=True)

        await Database.execute(
            _INSERT_SQL,
            ev.get("message_id"),
            context_wamid,
            ev.get("from"),
            ev.get("phone_number_id"),
            button_text,
            button_id,
            button_type,
            raw_json,
        )
        logger.info(
            "[WA_CLICKS] Stored button click: type=%s text=%r id=%r from=%s wamid=%s context=%s",
            button_type,
            button_text,
            button_id,
            ev.get("from"),
            ev.get("message_id"),
            context_wamid,
        )

    except Exception as exc:
        logger.warning(
            "[WA_CLICKS] Failed to persist button click (type=%s wamid=%s): %s",
            ev.get("type"),
            ev.get("message_id"),
            exc,
        )
