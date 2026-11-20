"""Invoices Endpoints - HTTP routes for MDA subscription invoices and their GST price list.

HTTP layer for /api/invoices: checks the shared bearer token and delegates to invoices_utils to issue an invoice from a mobile number and subscription period, list the stored GST amounts and SAC code per subscription length, and fetch an issued invoice again. MDA renders the PDF from the values returned.

Endpoints:
    POST /: Issue an invoice for the business on a mobile number for a subscription period.
    GET /price: Every subscription length's taxable value, GST amounts and SAC code, and every state's tax rule.
    GET /preview: The PDF values and next invoice number for a mobile number and period, storing nothing.
    GET /{invoice_number}: An issued invoice's values, for a reprint.

Functions:
    create_subscription_invoice(): Issue an invoice and return the PDF values.
    list_invoice_prices(): Return the GST price list with the SAC code and the state tax rules.
    preview_subscription_invoice(): Return the PDF values and next invoice number without storing.
    get_subscription_invoice(): Return one issued invoice by number.

Dependencies:
    - app.core.security.verify_token: Shared bearer token dependency.
    - app.etown.invoices.invoices_utils: Customer lookup, GST pricing, numbering and storage.
    - app.schemas.api.CreateInvoiceRequest: Body for POST /.

Updates:
    22-Oct-2026: Created for the MDA invoice flow.
    23-Oct-2026: months with period_start calculates period_end on POST / and GET /preview.
    26-Oct-2026: POST / no longer refuses a second invoice for the same contact and period_start.
"""

from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.core.logging import logger
from app.core.security import verify_token
from app.etown.invoices.invoices_utils import (
    InvoiceConflictError,
    create_invoice,
    fetch_invoice_prices,
    fetch_invoice_state_taxes,
    get_invoice,
    preview_invoice,
)
from app.schemas.api import CreateInvoiceRequest

router = APIRouter(tags=["invoices"])

_INVOICE_EXAMPLE = {
    "invoice_number": "INV-2026-008",
    "invoice_date": "06-09-2026",
    "subscription_period": "08-Sep-2026 to 07-Sep-2027",
    "period_start": "2026-09-08",
    "period_end": "2027-09-07",
    "customer_id": "9f1d4b2e-3a77-4c10-8b55-1e0c6d2f7a41",
    "business_name": "Buy N Tastey",
    "mobile_number": "8428629919",
    "email": None,
    "address": "12/64, Kuzhivilai Thottam, Vanniyoor, Kanniyakumari Dt, Tamil Nadu -629163",
    "statecode": "TN",
    "state": "Tamil Nadu",
    "description": "App Subscription fee",
    "sac_code": "9983/9985",
    "months": 12,
    "taxable_value": 2117.8,
    "cgst": {"rate": 9.0, "amount": 190.6},
    "sgst": {"rate": 9.0, "amount": 190.6},
    "igst": None,
    "total_amount": 2499.0,
    "invoice_url": "https://etowndev.blob.core.windows.net/invoices/INV-2026-008.pdf",
    "send_to_customer": True,
    "whatsapp_status": "sent",
    "email_status": "skipped:no_email",
}


@router.post(
    "",
    summary="Issue a Subscription Invoice",
    description=(
        "Issue an invoice for the business registered on mobile_number and return every value "
        "the invoice PDF prints. invoice_number and invoice_url, the blob link of the PDF, "
        "are stored on the invoice as given. The period is months (1 to 12) counted from "
        "period_start, or from today when period_start is not sent, exactly as GET "
        "/invoices/preview computes it, or period_start and period_end; with months the end "
        "date is calculated and any period_end is ignored. The customer id (contactid), business name, email, address "
        "and state are read off the oldest provider profile on the number with deleted = 'false' and disabled = false, "
        "blank fields filled from the owner's contact; with no provider profile they come "
        "from the contact on the number. The period must run "
        "1 to 12 whole months, ending the day before the same date N months on - 08-Sep-2026 "
        "to 07-Sep-2027 is 12 - and that month count picks the stored GST amounts. The "
        "business's state picks its row in invoices.etown_invoice_state_tax (the OTHER row when it is "
        "not listed): a CGST_SGST state such as Tamil Nadu is charged CGST + SGST and igst is "
        "null; an IGST state is charged IGST and cgst and sgst are null. total_amount is the taxable value plus the taxes "
        "that apply. invoice_number is unique: a number already used by another invoice is a "
        "409. A contact may have several invoices for the same period under different "
        "numbers; resending the same request returns the stored invoice with already_issued true. send_to_customer ('true' or 'false', default "
        "'false') is stored on the invoice; when 'true', a newly stored invoice is sent to "
        "the customer: the etown_silver_subscription_invoice WhatsApp template with the PDF "
        "if the business is on WhatsApp, and an email with the PDF attached if it has an "
        "email. whatsapp_status and email_status report each channel - sent, failed or "
        "skipped:<reason> - and are null when nothing was sent. A failed send never fails "
        "the request; a repeat request sends nothing again."
    ),
    responses={
        200: {
            "description": "Invoice issued, or the one already issued for this request",
            "content": {"application/json": {"example": {**_INVOICE_EXAMPLE, "already_issued": False}}},
        },
        400: {"description": "Period not 1-12 whole months, business has no state, or no price for the length"},
        401: {"description": "Unauthorized - invalid or missing access token"},
        404: {"description": "Neither a provider profile nor a contact is registered on the mobile number"},
        409: {"description": "Invoice number already used by another invoice"},
        422: {"description": "Malformed body, invalid invoice number or invalid mobile number"},
        500: {"description": "Internal server error"},
    },
)
async def create_subscription_invoice(
    request: CreateInvoiceRequest,
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle POST /api/invoices: issue an invoice and return the PDF values.

    Args:
        request: Invoice number, mobile number, subscription period, PDF blob link and send_to_customer.
        token: Shared bearer token.

    Returns:
        The invoice payload from create_invoice.

    Raises:
        HTTPException: 400 on a rejected period or missing price/state, 404 when no business matches,
            409 on a clashing invoice number, 500 on failure.
    """
    try:
        return await create_invoice(
            request.invoice_number,
            request.mobile_number,
            request.period_start,
            request.period_end,
            request.invoice_url,
            send_to_customer=request.send_to_customer == "true",
            months=request.months,
        )
    except InvoiceConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"create_subscription_invoice failed | invoice_number={request.invoice_number} | "
            f"mobile_number={request.mobile_number} | "
            f"period={request.period_start}..{request.period_end} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to issue the invoice",
        )


@router.get(
    "/price",
    summary="Get the Subscription GST Price List and State Tax Rules",
    description=(
        "Return prices, every row of invoices.etown_invoice_price shortest subscription first - the "
        "taxable value, the CGST, SGST and IGST amounts and the SAC code printed on the "
        "invoice - and states, every row of invoices.etown_invoice_state_tax: whether the state is "
        "charged CGST_SGST or IGST and at what rates. The OTHER row applies to any state "
        "not listed."
    ),
    responses={
        200: {
            "description": "Price list and state tax rules retrieved",
            "content": {
                "application/json": {
                    "example": {
                        "prices": [
                            {
                                "months": 12,
                                "taxable_value": 2117.8,
                                "cgst_amount": 190.6,
                                "sgst_amount": 190.6,
                                "igst_amount": 381.2,
                                "sac_code": "9983/9985",
                                "is_active": True,
                            }
                        ],
                        "states": [
                            {
                                "statecode": "TN",
                                "state_name": "Tamil Nadu",
                                "tax_type": "CGST_SGST",
                                "cgst_rate": 9.0,
                                "sgst_rate": 9.0,
                                "igst_rate": None,
                                "is_active": True,
                            },
                            {
                                "statecode": "KL",
                                "state_name": "Kerala",
                                "tax_type": "IGST",
                                "cgst_rate": None,
                                "sgst_rate": None,
                                "igst_rate": 18.0,
                                "is_active": True,
                            },
                        ],
                    }
                }
            },
        },
        401: {"description": "Unauthorized - invalid or missing access token"},
        500: {"description": "Internal server error"},
    },
)
async def list_invoice_prices(token: str = Depends(verify_token)) -> Dict[str, Any]:
    """Handle GET /api/invoices/price: return the GST price list with the SAC code, and the state tax rules.

    Args:
        token: Shared bearer token.

    Returns:
        {"prices": [...], "states": [...]} from fetch_invoice_prices and fetch_invoice_state_taxes.

    Raises:
        HTTPException: 500 on failure.
    """
    try:
        return {
            "prices": await fetch_invoice_prices(),
            "states": await fetch_invoice_state_taxes(),
        }
    except Exception as e:
        logger.error(f"list_invoice_prices failed | error={e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve the invoice price list",
        )


@router.get(
    "/preview",
    summary="Preview an Invoice Before Generating the PDF",
    description=(
        "Return every value the invoice PDF prints for a mobile number and subscription "
        "period, and the next invoice number, without storing anything. The customer id "
        "(contactid), business name, email, address and state come from the oldest provider "
        "profile on the number with deleted = 'false' and disabled = false, blank fields filled from the owner's "
        "contact; with no provider profile they come from the contact on the number, its "
        "firstname as the business name, and customer_source says which ('provider' or "
        "'contact'). Give the period as months (1 to 12), counted from period_start or, "
        "without it, from today - months 2 with period_start 2026-10-22 is 22-Oct-2026 to "
        "21-Dec-2026, the end date calculated - or as period_start and period_end, "
        "which must run 1 to 12 whole months, ending the day before the same date N months "
        "on (08-Sep-2026 to 07-Sep-2027 is 12). With months any period_end is ignored. The month "
        "count picks the price (taxable_value), the GST amounts and the SAC code. The "
        "state picks the taxes: CGST + SGST for a CGST_SGST state such as Tamil Nadu (igst "
        "null), IGST for an IGST state (cgst and sgst null). total_amount is the taxable "
        "value plus the taxes that apply. invoice_number is INV-<year>-<nnn>, one past the "
        "highest stored or seeded this calendar year; it is a suggestion, not a reservation, "
        "so if POST /invoices answers 409 for it, preview again for a fresh one."
    ),
    responses={
        200: {
            "description": "Invoice values and the next invoice number",
            "content": {
                "application/json": {
                    "example": {
                        k: v for k, v in _INVOICE_EXAMPLE.items()
                        if k not in ("invoice_url", "send_to_customer", "whatsapp_status", "email_status")
                    }
                }
            },
        },
        400: {"description": "Neither months nor both dates, period not 1-12 whole months, no state, or no price for the length"},
        401: {"description": "Unauthorized - invalid or missing access token"},
        404: {"description": "Neither a provider profile nor a contact is registered on the mobile number"},
        422: {"description": "Missing or malformed query parameter, or months outside 1-12"},
        500: {"description": "Internal server error"},
    },
)
async def preview_subscription_invoice(
    mobile_number: str = Query(..., description="Mobile number of the paying business; +91 optional"),
    months: Optional[int] = Query(
        None, ge=1, le=12,
        description=(
            "Subscription length 1 to 12; the period runs from period_start, or today when it is "
            "not sent, and period_end is calculated. Overrides period_end."
        ),
    ),
    period_start: Optional[date] = Query(
        None, description="First day of the subscription, YYYY-MM-DD; with months defaults to today, else required"
    ),
    period_end: Optional[date] = Query(
        None,
        description=(
            "Last day of the subscription, YYYY-MM-DD, the day before the same date N months on; "
            "required when months is not sent"
        ),
    ),
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle GET /api/invoices/preview: return the PDF values and the next invoice number.

    Args:
        mobile_number: The paying business's mobile number.
        months: Subscription length counted from period_start or today; overrides period_end.
        period_start: First day of the subscription; today when omitted with months.
        period_end: Last day of the subscription, when months is not sent.
        token: Shared bearer token.

    Returns:
        The preview payload from preview_invoice.

    Raises:
        HTTPException: 400 on a rejected mobile, period or missing price/state, 404 when no business matches, 500 on failure.
    """
    try:
        return await preview_invoice(mobile_number, period_start, period_end, months)
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            f"preview_subscription_invoice failed | mobile_number={mobile_number} | "
            f"months={months} | period={period_start}..{period_end} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to preview the invoice",
        )


@router.get(
    "/{invoice_number}",
    summary="Get an Issued Invoice",
    description=(
        "Return the values of an invoice already issued, exactly as they were at issue time, "
        "so MDA can render the PDF again. A later price or profile change does not alter it."
    ),
    responses={
        200: {
            "description": "Invoice retrieved",
            "content": {"application/json": {"example": _INVOICE_EXAMPLE}},
        },
        401: {"description": "Unauthorized - invalid or missing access token"},
        404: {"description": "No invoice has this number"},
        500: {"description": "Internal server error"},
    },
)
async def get_subscription_invoice(
    invoice_number: str = Path(..., description="Invoice number, e.g. INV-2026-008"),
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle GET /api/invoices/{invoice_number}: return one issued invoice.

    Args:
        invoice_number: The number printed on the invoice.
        token: Shared bearer token.

    Returns:
        The invoice payload from get_invoice.

    Raises:
        HTTPException: 404 when no invoice has the number, 500 on failure.
    """
    try:
        invoice = await get_invoice(invoice_number)
        if invoice is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No invoice found with number {invoice_number}",
            )
        return invoice
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"get_subscription_invoice failed | invoice_number={invoice_number} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve the invoice",
        )
