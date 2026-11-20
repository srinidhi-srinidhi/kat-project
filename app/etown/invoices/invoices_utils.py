"""Invoices Utilities - Postgres logic for MDA subscription invoices: customer lookup, GST pricing and numbering.

Domain layer behind /api/invoices: turns the subscription period into a month count, resolves the paying business from etown_provider by mobile number (etown_contact when there is no provider profile), reads how that business's state is taxed from invoices.etown_invoice_state_tax and the length's GST amounts from invoices.etown_invoice_price, totals the taxes that apply, and stores the invoice in invoices.etown_invoice under the invoice number and PDF blob link MDA sends.

Features:
    - Month count derived from the period; anything but whole months from 1 to 12 is refused
    - Customer details read from the oldest etown_provider row with deleted = 'false' and disabled = false on the mobile number,
      blank fields filled from its etown_contact; with no provider profile, from etown_contact alone
    - State tax treatment (CGST+SGST or IGST, with rates) stored per statecode, OTHER as the fallback
    - GST amounts stored per length in invoices.etown_invoice_price, so a change is a row update, not a deploy
    - Total computed from the stored amounts that apply to the state
    - Invoice number taken from MDA and unique; a number already used by another invoice is a conflict
    - A contact may have several invoices for one period_start; resending the same request returns the stored invoice
    - Customer details, rates and amounts copied into invoices.etown_invoice, so later edits never alter an issued invoice
    - The blob link of the PDF MDA generated stored on the invoice row
    - send_to_customer stored on the row; when true the invoice goes out on WhatsApp and email, outcomes stored too

Classes:
    InvoiceConflictError(): The invoice number is already taken by another invoice.

Functions:
    subscription_months(): Return the whole-month length of a subscription period.
    fetch_state_tax(): Return the tax treatment for a statecode, falling back to the OTHER row.
    fetch_invoice_prices(): Return every invoices.etown_invoice_price row with its SAC code.
    fetch_invoice_state_taxes(): Return every invoices.etown_invoice_state_tax row.
    next_invoice_number(): Suggest the next INV-<year>-<nnn> from stored invoices and the seeded sequence.
    period_from_months(): Return the start and end of an N-month subscription from a start date or today.
    resolve_period(): Return the period from months and period_start or today, or from the two dates.
    preview_invoice(): Return the PDF values and the next invoice number for a mobile and period, storing nothing.
    create_invoice(): Resolve the customer, state tax and price, store the invoice and return its payload.
    get_invoice(): Return an issued invoice's payload by invoice number.

Dependencies:
    - app.db.client.Database: Shared asyncpg pool.
    - public.etown_provider: Business and owner name, contactid, email, address, statecode and whatsapp flag by mobile number.
    - public.etown_contact: Fallback for blank provider fields, and the customer when no provider profile exists.
    - invoices.etown_invoice_state_tax: Tax type and rates per customer state.
    - invoices.etown_invoice_price: GST amounts, SAC code and plan_code per subscription length.
    - public.etown_subscription_plan: plan_name for the price row's plan_code.
    - invoices.etown_invoice: Issued invoices.
    - invoices.etown_invoice_sequence: Last number issued outside the API per year, seeding the next-number suggestion.
    - app.utils.phone.normalize_indian_mobile: Reduces a number to its 10-digit national form.
    - app.utils.ist_clock.now_ist: Invoice date in IST.
    - app.etown.invoices.invoices_delivery.send_invoice_to_customer: WhatsApp and email delivery.

Updates:
    22-Oct-2026: Created for the MDA invoice flow; stores the PDF blob link and MDA's invoice number; state tax read from invoices.etown_invoice_state_tax; send_to_customer stored and acted on; added preview_invoice() and next_invoice_number(); provider lookup requires deleted = 'false' and disabled = false and falls back to etown_contact; price rows carry plan_code, named from etown_subscription_plan; preview_invoice() and create_invoice() take months counted from today.
    23-Oct-2026: months counts from period_start when sent, calculating period_end; period_from_today() renamed period_from_months().
    26-Oct-2026: Removed the one-invoice-per-contact-per-period_start check.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from dateutil.relativedelta import relativedelta

from app.core.logging import logger
from app.db.client import Database
from app.etown.invoices.invoices_delivery import send_invoice_to_customer
from app.utils.ist_clock import now_ist
from app.utils.phone import normalize_indian_mobile

INVOICE_DESCRIPTION = "App Subscription fee"
_FALLBACK_STATECODE = "OTHER"

# A provider field left blank falls back to the owner's etown_contact value; the address
# (with its additional info) is taken as a pair from whichever row has one.
_PROVIDER_BY_MOBILE_SQL = r"""
    SELECT p.contactid,
           COALESCE(NULLIF(btrim(p.businessname), ''), NULLIF(btrim(c.firstname), '')) AS businessname,
           COALESCE(NULLIF(btrim(p.firstname), ''), NULLIF(btrim(c.firstname), '')) AS firstname,
           COALESCE(NULLIF(btrim(p.mobilenumber), ''), c.mobilephone) AS mobilenumber,
           COALESCE(NULLIF(btrim(p.email), ''), NULLIF(btrim(c.email), '')) AS email,
           CASE WHEN NULLIF(btrim(p.address), '') IS NOT NULL THEN p.address ELSE c.address END AS address,
           CASE WHEN NULLIF(btrim(p.address), '') IS NOT NULL
                THEN p.additionaladdressinfo ELSE c.additionaladdressinfo END AS additionaladdressinfo,
           COALESCE(NULLIF(btrim(p.statecode), ''), NULLIF(btrim(c.statecode), '')) AS statecode,
           p.whatsapp,
           'provider' AS customer_source
      FROM etown_provider p
      LEFT JOIN etown_contact c ON c.contactid = p.contactid
     WHERE RIGHT(regexp_replace(COALESCE(p.mobilenumber, ''), '\D', '', 'g'), 10) = $1
       AND p.contactid IS NOT NULL
       AND COALESCE(p.deleted, 'false') = 'false'
       AND p.disabled = false
     ORDER BY p.creationdate ASC NULLS LAST, p.providerid ASC
     LIMIT 1
"""

# A member with no provider profile: the contact's own name stands in for the business name.
# etown_contact has no WhatsApp flag, so the send is attempted and Meta decides.
_CONTACT_BY_MOBILE_SQL = r"""
    SELECT contactid,
           NULLIF(btrim(firstname), '') AS businessname,
           NULLIF(btrim(firstname), '') AS firstname,
           mobilephone AS mobilenumber,
           NULLIF(btrim(email), '') AS email,
           address,
           additionaladdressinfo,
           NULLIF(btrim(statecode), '') AS statecode,
           true AS whatsapp,
           'contact' AS customer_source
      FROM etown_contact
     WHERE RIGHT(regexp_replace(COALESCE(mobilephone, ''), '\D', '', 'g'), 10) = $1
       AND COALESCE(deleted, 'false') = 'false'
     ORDER BY createdon ASC NULLS LAST, contactid ASC
     LIMIT 1
"""

_STATE_TAX_SQL = f"""
    SELECT statecode, state_name, tax_type, cgst_rate, sgst_rate, igst_rate
      FROM invoices.etown_invoice_state_tax
     WHERE is_active
       AND (upper(statecode) = upper($1)
            OR lower(state_name) = lower($1)
            OR statecode = '{_FALLBACK_STATECODE}')
     ORDER BY (statecode = '{_FALLBACK_STATECODE}') ASC
     LIMIT 1
"""

# plan_name is the subscription plan's catalogue name, or the code title-cased when the
# catalogue has no row for it.
_PRICE_BY_MONTHS_SQL = """
    SELECT ip.taxable_value, ip.cgst_amount, ip.sgst_amount, ip.igst_amount, ip.sac_code,
           ip.plan_code, COALESCE(NULLIF(btrim(sp.plan_name), ''), initcap(ip.plan_code)) AS plan_name
      FROM invoices.etown_invoice_price ip
      LEFT JOIN etown_subscription_plan sp ON sp.plan_code = ip.plan_code
     WHERE ip.months = $1 AND ip.is_active
"""

_INSERT_INVOICE_SQL = """
    INSERT INTO invoices.etown_invoice (
        invoice_number, invoice_date, contactid, business_name,
        mobile_number, email, address, statecode, state_name, description, sac_code,
        period_start, period_end, months, taxable_value,
        cgst_rate, cgst_amount, sgst_rate, sgst_amount, igst_rate, igst_amount, total_amount,
        invoice_url, send_to_customer
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
            $17, $18, $19, $20, $21, $22, $23, $24)
    ON CONFLICT DO NOTHING
    RETURNING *
"""

_RECORD_DELIVERY_SQL = """
    UPDATE invoices.etown_invoice
       SET whatsapp_status = $2, whatsapp_wamid = $3, email_status = $4, notified_at = now()
     WHERE invoice_id = $1
    RETURNING *
"""

_LAST_INVOICE_NO_SQL = r"""
    SELECT GREATEST(
        (SELECT last_no FROM invoices.etown_invoice_sequence WHERE year = $1),
        (SELECT max(substring(invoice_number FROM '(\d+)$')::int)
           FROM invoices.etown_invoice
          WHERE invoice_number LIKE $2 || '%')
    )
"""

_INVOICE_BY_NUMBER_SQL = """
    SELECT *
      FROM invoices.etown_invoice
     WHERE invoice_number = $1
"""


class InvoiceConflictError(Exception):
    """The invoice number is already used by another invoice."""


def subscription_months(period_start: date, period_end: date) -> int:
    """Return the number of whole months from period_start through period_end inclusive.

    08-Sep-2026 to 07-Sep-2027 is 12: the period must end the day before the same date
    N months on, so a part month never prices as a whole one.

    Raises:
        ValueError: The period is reversed, not whole months, or outside 1 to 12 months.
    """
    if period_end < period_start:
        raise ValueError("period_end is before period_start")
    for months in range(1, 13):
        if period_start + relativedelta(months=months) - timedelta(days=1) == period_end:
            return months
    raise ValueError(
        "Subscription period must be 1 to 12 whole months, ending the day before the "
        "same date N months on (e.g. 08-Sep-2026 to 07-Sep-2027)"
    )


def _money(value: Optional[Decimal]) -> Optional[float]:
    """Return a numeric column as a 2-decimal float, or None."""
    return None if value is None else float(Decimal(value).quantize(Decimal("0.01")))


def _rate(value: Optional[Decimal]) -> Optional[float]:
    """Return a rate column as a float, or None."""
    return None if value is None else float(value)


def _join_address(*parts: Optional[str]) -> Optional[str]:
    """Join the non-blank address parts with ', ', or return None when all are blank."""
    joined = ", ".join(p.strip() for p in parts if p and p.strip())
    return joined or None


def _tax(rate: Optional[Decimal], amount: Optional[Decimal]) -> Optional[Dict[str, float]]:
    """Return a {rate, amount} pair, or None when the tax does not apply to the invoice."""
    if amount is None:
        return None
    return {"rate": _rate(rate), "amount": _money(amount)}


def _invoice_payload(row: asyncpg.Record) -> Dict[str, Any]:
    """Shape an invoices.etown_invoice row into the values MDA prints on the PDF."""
    return {
        "invoice_number": row["invoice_number"],
        "invoice_date": row["invoice_date"].strftime("%d-%m-%Y"),
        "subscription_period": (
            f"{row['period_start'].strftime('%d-%b-%Y')} to {row['period_end'].strftime('%d-%b-%Y')}"
        ),
        "period_start": row["period_start"].isoformat(),
        "period_end": row["period_end"].isoformat(),
        "customer_id": row["contactid"],
        "business_name": row["business_name"],
        "mobile_number": row["mobile_number"],
        "email": row["email"],
        "address": row["address"],
        "statecode": row["statecode"],
        "state": row["state_name"],
        "description": row["description"],
        "sac_code": row["sac_code"],
        "months": row["months"],
        "taxable_value": _money(row["taxable_value"]),
        "cgst": _tax(row["cgst_rate"], row["cgst_amount"]),
        "sgst": _tax(row["sgst_rate"], row["sgst_amount"]),
        "igst": _tax(row["igst_rate"], row["igst_amount"]),
        "total_amount": _money(row["total_amount"]),
        "invoice_url": row["invoice_url"],
        "send_to_customer": row["send_to_customer"],
        "whatsapp_status": row["whatsapp_status"],
        "email_status": row["email_status"],
    }


async def fetch_state_tax(statecode: str) -> Optional[asyncpg.Record]:
    """Return the active invoices.etown_invoice_state_tax row for a statecode or state name, else the OTHER row, else None."""
    return await Database.fetchrow(_STATE_TAX_SQL, statecode.strip())


async def fetch_invoice_prices() -> List[Dict[str, Any]]:
    """Return every invoices.etown_invoice_price row, shortest first, with the SAC code."""
    rows = await Database.fetch(
        """
        SELECT months, plan_code, taxable_value, cgst_amount, sgst_amount, igst_amount, sac_code, is_active
          FROM invoices.etown_invoice_price
         ORDER BY months
        """
    )
    return [
        {
            "months": r["months"],
            "plan_code": r["plan_code"],
            "taxable_value": _money(r["taxable_value"]),
            "cgst_amount": _money(r["cgst_amount"]),
            "sgst_amount": _money(r["sgst_amount"]),
            "igst_amount": _money(r["igst_amount"]),
            "sac_code": r["sac_code"],
            "is_active": r["is_active"],
        }
        for r in rows
    ]


async def fetch_invoice_state_taxes() -> List[Dict[str, Any]]:
    """Return every invoices.etown_invoice_state_tax row, Tamil Nadu-style CGST_SGST states first."""
    rows = await Database.fetch(
        """
        SELECT statecode, state_name, tax_type, cgst_rate, sgst_rate, igst_rate, is_active
          FROM invoices.etown_invoice_state_tax
         ORDER BY tax_type, state_name
        """
    )
    return [
        {
            "statecode": r["statecode"],
            "state_name": r["state_name"],
            "tax_type": r["tax_type"],
            "cgst_rate": _rate(r["cgst_rate"]),
            "sgst_rate": _rate(r["sgst_rate"]),
            "igst_rate": _rate(r["igst_rate"]),
            "is_active": r["is_active"],
        }
        for r in rows
    ]


async def get_invoice(invoice_number: str) -> Optional[Dict[str, Any]]:
    """Return the issued invoice with this number, or None."""
    row = await Database.fetchrow(
        "SELECT * FROM invoices.etown_invoice WHERE invoice_number = $1", invoice_number.strip()
    )
    return _invoice_payload(row) if row else None


async def _existing_or_conflict(
    invoice_number: str, contactid: str, period_start: date
) -> Optional[Dict[str, Any]]:
    """Return the stored invoice when this exact request was already saved, None when nothing clashes.

    Raises:
        InvoiceConflictError: The number belongs to another invoice.
    """
    row = await Database.fetchrow(_INVOICE_BY_NUMBER_SQL, invoice_number)
    if row is None:
        return None
    if row["contactid"] == contactid and row["period_start"] == period_start:
        return {**_invoice_payload(row), "already_issued": True}
    raise InvoiceConflictError(f"Invoice number {invoice_number} is already used by another invoice")


async def _resolve_customer(
    mobile_number: str, period_start: date, period_end: date
) -> Tuple[int, asyncpg.Record, str]:
    """Return (months, customer row, 10-digit mobile) for a mobile number and subscription period.

    The customer row is the oldest provider profile on the number that is neither deleted nor disabled, its blank
    fields filled from the owner's contact; with no provider profile it is the non-deleted
    contact on the number, whose firstname stands in for the business name.

    Raises:
        ValueError: Bad mobile number or period.
        LookupError: Neither a provider profile nor a contact is registered on the number.
    """
    national = normalize_indian_mobile(mobile_number)
    if not national:
        raise ValueError("mobile_number is not a valid Indian mobile number")
    months = subscription_months(period_start, period_end)

    provider = await Database.fetchrow(_PROVIDER_BY_MOBILE_SQL, national)
    if provider is None:
        provider = await Database.fetchrow(_CONTACT_BY_MOBILE_SQL, national)
    if provider is None:
        raise LookupError(f"No provider profile or contact is registered on mobile number {national}")
    return months, provider, national


async def _quote(provider: asyncpg.Record, months: int) -> Dict[str, Any]:
    """Return the state, SAC code, taxable value, the taxes that apply and their total for a business and length.

    Raises:
        ValueError: No state on the business, no tax row for it, or no price for the length.
    """
    statecode = (provider["statecode"] or "").strip()
    if not statecode:
        raise ValueError(
            "The business has no state on record, so CGST/SGST or IGST cannot be decided"
        )
    state_tax = await fetch_state_tax(statecode)
    if state_tax is None:
        raise ValueError(f"No tax treatment is configured for state {statecode} and no OTHER row exists")

    price = await Database.fetchrow(_PRICE_BY_MONTHS_SQL, months)
    if price is None:
        raise ValueError(f"No active price is configured for a {months}-month subscription")

    q: Dict[str, Any] = {
        "statecode": statecode,
        "state_name": statecode if state_tax["statecode"] == _FALLBACK_STATECODE else state_tax["state_name"],
        "tax_type": state_tax["tax_type"],
        "sac_code": price["sac_code"],
        "plan_code": price["plan_code"],
        "plan_name": price["plan_name"],
        "taxable": price["taxable_value"],
        "cgst_rate": None, "cgst_amount": None,
        "sgst_rate": None, "sgst_amount": None,
        "igst_rate": None, "igst_amount": None,
    }
    if state_tax["tax_type"] == "CGST_SGST":
        q.update(
            cgst_rate=state_tax["cgst_rate"], cgst_amount=price["cgst_amount"],
            sgst_rate=state_tax["sgst_rate"], sgst_amount=price["sgst_amount"],
        )
        q["total"] = q["taxable"] + q["cgst_amount"] + q["sgst_amount"]
    else:
        q.update(igst_rate=state_tax["igst_rate"], igst_amount=price["igst_amount"])
        q["total"] = q["taxable"] + q["igst_amount"]
    return q


async def next_invoice_number(year: int) -> str:
    """Return the next INV-<year>-<nnn> number: one past the highest stored or seeded for the year.

    Only a suggestion - nothing is reserved. Two previews can get the same number; the
    second POST /invoices with it is a 409 and the caller previews again.
    """
    last_no = await Database.fetchval(_LAST_INVOICE_NO_SQL, str(year), f"INV-{year}-")
    return f"INV-{year}-{(last_no or 0) + 1:03d}"


def period_from_months(months: int, start: Optional[date] = None) -> Tuple[date, date]:
    """Return (start, end) for a subscription of `months` starting on start, or today in IST.

    Args:
        months: Subscription length 1 to 12.
        start: First day of the subscription; today in IST when None.

    Returns:
        (start, end), the end being the day before the same date N months on.

    Raises:
        ValueError: months is not 1 to 12.
    """
    if not 1 <= months <= 12:
        raise ValueError("months must be between 1 and 12")
    start = start or now_ist().date()
    return start, start + relativedelta(months=months) - timedelta(days=1)


def resolve_period(
    period_start: Optional[date], period_end: Optional[date], months: Optional[int]
) -> Tuple[date, date]:
    """Return the subscription's (start, end): months from period_start or today when given, else the two dates.

    Raises:
        ValueError: months outside 1 to 12, or neither months nor both dates.
    """
    if months is not None:
        return period_from_months(months, period_start)
    if period_start is None or period_end is None:
        raise ValueError("Send months (1 to 12), or both period_start and period_end")
    return period_start, period_end


async def preview_invoice(
    mobile_number: str,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    months: Optional[int] = None,
) -> Dict[str, Any]:
    """Return everything the invoice PDF needs, plus the next invoice number, without storing anything.

    The period is either ``months`` counted from period_start (today when it is not sent),
    or period_start and period_end as given. When months is given period_end is ignored.

    Args:
        mobile_number: The paying business's mobile number, any Indian format.
        period_start: First day of the subscription; today when omitted with months.
        period_end: Last day of the subscription; needed only without months.
        months: Subscription length 1 to 12, counted from period_start or today.

    Returns:
        Invoice number, customer details from etown_provider or etown_contact, SAC code, taxable value, the
        taxes for the business's state (null when they do not apply) and the total.

    Raises:
        ValueError: Bad mobile number or period, neither months nor both dates, no state on the
            business, or no tax/price row.
        LookupError: Neither a provider profile nor a contact is registered on the number.
    """
    period_start, period_end = resolve_period(period_start, period_end, months)
    months, provider, national = await _resolve_customer(mobile_number, period_start, period_end)
    q = await _quote(provider, months)
    invoice_date = now_ist().date()
    return {
        "invoice_number": await next_invoice_number(invoice_date.year),
        "invoice_date": invoice_date.strftime("%d-%m-%Y"),
        "subscription_period": f"{period_start.strftime('%d-%b-%Y')} to {period_end.strftime('%d-%b-%Y')}",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "months": months,
        "customer_id": provider["contactid"],
        "customer_source": provider["customer_source"],
        "business_name": provider["businessname"],
        "mobile_number": provider["mobilenumber"] or national,
        "email": provider["email"],
        "address": _join_address(provider["address"], provider["additionaladdressinfo"]),
        "statecode": q["statecode"],
        "state": q["state_name"],
        "description": INVOICE_DESCRIPTION,
        "plan_code": q["plan_code"],
        "plan_name": q["plan_name"],
        "sac_code": q["sac_code"],
        "taxable_value": _money(q["taxable"]),
        "cgst": _tax(q["cgst_rate"], q["cgst_amount"]),
        "sgst": _tax(q["sgst_rate"], q["sgst_amount"]),
        "igst": _tax(q["igst_rate"], q["igst_amount"]),
        "total_amount": _money(q["total"]),
    }


async def create_invoice(
    invoice_number: str,
    mobile_number: str,
    period_start: Optional[date],
    period_end: Optional[date],
    invoice_url: str,
    send_to_customer: bool = False,
    months: Optional[int] = None,
) -> Dict[str, Any]:
    """Store an invoice for the business on mobile_number and return the PDF values.

    Order: period to month count, mobile to business, business state to tax treatment,
    month count to GST amounts, then the total of the taxes that apply. With
    send_to_customer the new invoice then goes to the customer on WhatsApp and by email,
    and each channel's outcome is stored on the row. Resending the same invoice_number for
    the same contact and period_start returns the stored invoice with ``already_issued``
    true and sends nothing again; its stored invoice_url and delivery statuses are kept.
    The period is ``months`` counted from period_start (today when it is not sent) when
    given, else period_start and period_end, worked out exactly as preview_invoice() does.

    Args:
        invoice_number: Number MDA printed on the invoice; unique across invoices.
        mobile_number: The paying business's mobile number, any Indian format.
        period_start: First day of the subscription; today when omitted with months.
        period_end: Last day of the subscription; needed only without months.
        invoice_url: Blob link of the invoice PDF, stored on the row.
        send_to_customer: Stored on the row; when true the invoice is sent to the customer.
        months: Subscription length 1 to 12, counted from period_start or today; overrides period_end.

    Returns:
        The invoice payload plus ``already_issued``.

    Raises:
        ValueError: Bad mobile number or period, neither months nor both dates, no state on the
            business, or no tax/price row.
        LookupError: Neither a provider profile nor a contact is registered on the number.
        InvoiceConflictError: The number is taken by another invoice.
    """
    period_start, period_end = resolve_period(period_start, period_end, months)
    months, provider, national = await _resolve_customer(mobile_number, period_start, period_end)
    contactid = provider["contactid"]

    existing = await _existing_or_conflict(invoice_number, contactid, period_start)
    if existing is not None:
        logger.info(
            "INVOICE | ALREADY_ISSUED | invoice_number=%s | contactid=%s | period_start=%s",
            invoice_number, contactid, period_start,
        )
        return existing

    q = await _quote(provider, months)
    statecode, total = q["statecode"], q["total"]

    invoice_date = now_ist().date()
    row = await Database.fetchrow(
        _INSERT_INVOICE_SQL,
        invoice_number,
        invoice_date,
        contactid,
        provider["businessname"],
        provider["mobilenumber"] or national,
        provider["email"],
        _join_address(provider["address"], provider["additionaladdressinfo"]),
        statecode,
        q["state_name"],
        INVOICE_DESCRIPTION,
        q["sac_code"],
        period_start,
        period_end,
        months,
        q["taxable"],
        q["cgst_rate"], q["cgst_amount"],
        q["sgst_rate"], q["sgst_amount"],
        q["igst_rate"], q["igst_amount"],
        total,
        invoice_url,
        send_to_customer,
    )
    if row is None:
        # A concurrent request stored a clashing row between the check above and this insert.
        existing = await _existing_or_conflict(invoice_number, contactid, period_start)
        if existing is None:
            raise InvoiceConflictError(f"Invoice number {invoice_number} could not be stored")
        return existing

    logger.info(
        "INVOICE | ISSUED | invoice_number=%s | contactid=%s | months=%s | statecode=%s | tax_type=%s | total=%s",
        row["invoice_number"], contactid, months, statecode, q["tax_type"], total,
    )

    if send_to_customer:
        try:
            delivery = await send_invoice_to_customer(
                _invoice_payload(row),
                customer_name=(provider["firstname"] or provider["businessname"] or "Customer").strip(),
                invoice_date=invoice_date,
                on_whatsapp=bool(provider["whatsapp"]),
                plan_name=q["plan_name"],
            )
        except Exception as exc:
            # The invoice is already stored; a delivery fault is recorded, not raised.
            logger.error(
                "INVOICE | DELIVERY_ERROR | invoice_number=%s | %s", invoice_number, exc, exc_info=True,
            )
            delivery = {"whatsapp_status": "failed", "whatsapp_wamid": None, "email_status": "failed"}
        row = await Database.fetchrow(
            _RECORD_DELIVERY_SQL,
            row["invoice_id"],
            delivery["whatsapp_status"],
            delivery["whatsapp_wamid"],
            delivery["email_status"],
        )
    return {**_invoice_payload(row), "already_issued": False}
