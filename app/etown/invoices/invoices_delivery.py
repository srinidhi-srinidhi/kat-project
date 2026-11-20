"""Invoices Delivery - sends an issued subscription invoice to the customer on WhatsApp and by email.

Delivery layer behind POST /api/invoices when send_to_customer is "true": sends the etown_silver_subscription_invoice WhatsApp template with the invoice PDF as its document header to a business whose etown_provider row says it is on WhatsApp, and emails the same message with the PDF attached when the business has an email. Neither channel raises; each returns a status the caller stores on the invoice row.

Features:
    - WhatsApp only when etown_provider.whatsapp is true for the business
    - Template body filled positionally: name, plan name, invoice id, business, plan, period, amount, payment date
    - PDF sent as the template's document header by its public blob URL
    - Email only when the business has an email; the PDF is downloaded from blob and attached
    - Both channels sent concurrently; each reports sent, failed or skipped:<reason>
    - A WhatsApp rejection is reported as failed:<Meta error code> <message>

Functions:
    plan_title(): "Silver Plan" from the plan name "Silver".
    plan_label(): "<Plan> - Annual" for 12 months, else "<Plan> - N Months".
    send_invoice_to_customer(): Send the invoice on WhatsApp and by email and return each channel's status.

Dependencies:
    - app.core.config.settings: WHATSAPP_SUBSCRIPTION_INVOICE_TEMPLATE_NAME.
    - app.utils.whatsapp: Template send with a document header.
    - app.etown.notifications.whatsapp_template_config.is_template_enabled: DB on/off switch for the template (lazy import).
    - app.utils.blob_storage: Public URL for the PDF and its download for the email attachment.
    - app.utils.http_client.get_http_client: Fallback download when the link is not in our blob account.
    - app.services.email_service.send_email: Microsoft Graph sender with attachments.
    - app.services.email_templates: Branded HTML shell.

Updates:
    22-Oct-2026: Created for POST /api/invoices send_to_customer; a WhatsApp failure carries Meta's code and message; template body takes the plan name as {{2}} (8 params), from the price row's plan_code.
"""

from __future__ import annotations

import asyncio
import html
from datetime import date
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.services.email_service import send_email
from app.services.email_templates import bullet_list, paragraph, render_email
from app.utils.blob_storage import download_blob_bytes, to_public_blob_url
from app.utils.http_client import get_http_client
from app.utils.whatsapp import WhatsAppError, send_whatsapp_template_message

SENT = "sent"
FAILED = "failed"


def plan_title(plan_name: str) -> str:
    """Return 'Silver Plan' for 'Silver', leaving a name that already ends in 'Plan' as it is."""
    name = (plan_name or "").strip() or "Etown"
    return name if name.lower().endswith("plan") else f"{name} Plan"


def plan_label(plan_name: str, months: int) -> str:
    """Return the plan line shown to the customer: '<Plan> - Annual' for 12 months, else '<Plan> - N Months'."""
    title = plan_title(plan_name)
    if months == 12:
        return f"{title} - Annual"
    return f"{title} - {months} Month{'s' if months != 1 else ''}"


def _human_date(value: date) -> str:
    """Return a date as '08 Sep 2026'."""
    return value.strftime("%d %b %Y")


def _rupees(amount: float) -> str:
    """Return an amount as '₹2,499.00'."""
    return f"₹{amount:,.2f}"


def _message_fields(
    invoice: Dict[str, Any], customer_name: str, invoice_date: date, plan_name: str
) -> Dict[str, str]:
    """Return the values both channels show, in the template's body order ({{1}} to {{8}})."""
    period_start = date.fromisoformat(invoice["period_start"])
    period_end = date.fromisoformat(invoice["period_end"])
    return {
        "customer_name": customer_name,
        "plan_name": plan_title(plan_name),
        "invoice_number": invoice["invoice_number"],
        "business_name": invoice["business_name"] or "-",
        "plan": plan_label(plan_name, invoice["months"]),
        "period": f"{_human_date(period_start)} to {_human_date(period_end)}",
        "amount": _rupees(invoice["total_amount"]),
        "payment_date": _human_date(invoice_date),
    }


async def _send_whatsapp(
    invoice: Dict[str, Any], fields: Dict[str, str], on_whatsapp: bool
) -> Tuple[str, Optional[str]]:
    """Send the invoice template with the PDF header and return (status, wamid)."""
    if not on_whatsapp:
        return "skipped:no_whatsapp", None
    template_name = settings.WHATSAPP_SUBSCRIPTION_INVOICE_TEMPLATE_NAME
    if not template_name:
        return "skipped:template_not_configured", None

    from app.etown.notifications.whatsapp_template_config import is_template_enabled
    if not await is_template_enabled(template_name):
        return "skipped:template_disabled", None

    try:
        resp = await send_whatsapp_template_message(
            mobile_number=invoice["mobile_number"],
            template_name=template_name,
            header_document_url=to_public_blob_url(invoice["invoice_url"]),
            header_document_filename=f"{invoice['invoice_number']}.pdf",
            body_parameters=list(fields.values()),
        )
    except WhatsAppError as exc:
        logger.warning(
            "INVOICE_DELIVERY | WHATSAPP_FAILED | invoice_number=%s | mobile=%s | code=%s | %s",
            invoice["invoice_number"], invoice["mobile_number"], exc.error_code, exc,
        )
        # Meta's code and message go on the row so a rejection can be diagnosed from the table.
        return f"{FAILED}:{exc.error_code or exc.status_code or ''} {exc}"[:300], None
    except Exception as exc:
        logger.error(
            "INVOICE_DELIVERY | WHATSAPP_ERROR | invoice_number=%s | %s",
            invoice["invoice_number"], exc, exc_info=True,
        )
        return FAILED, None

    messages = resp.get("messages") if isinstance(resp, dict) else None
    wamid = messages[0].get("id") if messages else None
    logger.info(
        "INVOICE_DELIVERY | WHATSAPP_SENT | invoice_number=%s | mobile=%s | wamid=%s",
        invoice["invoice_number"], invoice["mobile_number"], wamid,
    )
    return SENT, wamid


async def _download_pdf(invoice_url: str) -> bytes:
    """Return the PDF's bytes from our blob account, or over HTTP when the link points elsewhere."""
    try:
        return await download_blob_bytes(invoice_url)
    except Exception as exc:
        logger.info("INVOICE_DELIVERY | BLOB_DOWNLOAD_FALLBACK | url=%s | %s", invoice_url, exc)
    async with get_http_client() as client:
        resp = await client.get(to_public_blob_url(invoice_url), timeout=30)
        resp.raise_for_status()
        return resp.content


def _email_body(fields: Dict[str, str]) -> str:
    """Return the branded HTML invoice email, the WhatsApp message's wording."""
    e = {k: html.escape(v) for k, v in fields.items()}
    content = (
        paragraph(f"Hello {e['customer_name']},")
        + paragraph(f"Thank you for subscribing to the Etown {e['plan_name']}!")
        + paragraph("Your subscription payment has been confirmed. Here are your invoice details:")
        + bullet_list([
            f"Invoice ID: {e['invoice_number']}",
            f"Business Name: {e['business_name']}",
            f"Subscription Plan: {e['plan']}",
            f"Subscription Period: {e['period']}",
            f"Amount Paid: {e['amount']}",
            f"Payment Date: {e['payment_date']}",
        ])
        + paragraph("Your invoice is attached to this email for your records.")
        + paragraph("Thank you for being a part of Etown!")
        + paragraph("Team etown")
    )
    return render_email(
        heading="Your Etown subscription invoice",
        content=content,
        preheader=f"Invoice {fields['invoice_number']} for {fields['amount']}",
        footer="This is an automated subscription invoice notification.",
    )


async def _send_email(invoice: Dict[str, Any], fields: Dict[str, str]) -> str:
    """Email the invoice with the PDF attached and return the status."""
    email = (invoice.get("email") or "").strip()
    if not email:
        return "skipped:no_email"
    try:
        pdf = await _download_pdf(invoice["invoice_url"])
    except Exception as exc:
        logger.warning(
            "INVOICE_DELIVERY | PDF_UNAVAILABLE | invoice_number=%s | url=%s | %s",
            invoice["invoice_number"], invoice["invoice_url"], exc,
        )
        return "failed:pdf_unavailable"
    try:
        await send_email(
            to=email,
            subject=f"Your Etown invoice {fields['invoice_number']}",
            body=_email_body(fields),
            attachments=[(f"{invoice['invoice_number']}.pdf", "application/pdf", pdf)],
        )
    except Exception as exc:
        logger.error(
            "INVOICE_DELIVERY | EMAIL_FAILED | invoice_number=%s | to=%s | %s",
            invoice["invoice_number"], email, exc, exc_info=True,
        )
        return FAILED
    return SENT


async def send_invoice_to_customer(
    invoice: Dict[str, Any],
    customer_name: str,
    invoice_date: date,
    on_whatsapp: bool,
    plan_name: str,
) -> Dict[str, Optional[str]]:
    """Send the invoice on WhatsApp and by email at once and return each channel's outcome.

    Args:
        invoice: The invoice payload create_invoice returns.
        customer_name: Name the greeting addresses.
        invoice_date: Shown as the payment date.
        on_whatsapp: etown_provider.whatsapp for the business.
        plan_name: Plan name of the price row, e.g. Silver; shown as {{2}} and in {{5}}.

    Returns:
        {"whatsapp_status", "whatsapp_wamid", "email_status"}; statuses are sent, failed or skipped:<reason>.
    """
    fields = _message_fields(invoice, customer_name, invoice_date, plan_name)
    (whatsapp_status, wamid), email_status = await asyncio.gather(
        _send_whatsapp(invoice, fields, on_whatsapp),
        _send_email(invoice, fields),
    )
    return {"whatsapp_status": whatsapp_status, "whatsapp_wamid": wamid, "email_status": email_status}
