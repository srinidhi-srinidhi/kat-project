"""Plans Endpoints - HTTP routes for plan status, promotion, catalogue edits, payments and plan alerts.

HTTP layer for /api/plans: checks the shared bearer token and delegates to plans_utils to report a caller's plan and usage, promote providers, edit etown_subscription_plan, route payment screenshots to support, disable a business and dismiss expiry alerts, mirroring plan changes to Dataverse.

Endpoints:
    GET /my_plan: The caller's plan, feature flags and per-limit usage, by userprofileid and/or contactid.
    POST /promote: Move a contact's live providers (or member row) onto a plan_code.
    PATCH /catalogue/{plan_code}: Patch or create a plan's catalogue row.
    POST /payment_request: Resolve the payer by mobile, store the screenshot in blob, email support.
    POST /disable: Set disabled = true on the provider a contactid owns as a userprofileid.
    POST /alert/dismiss: Set alert = false on every provider a contactid owns.

Functions:
    my_plan(): Return the plan status for a userprofileid, a contactid, or both.
    promote_provider_plan(): Assign a contact's subscription plan.
    update_plan_catalogue(): Patch or create a plan's limits/feature flags.
    submit_plan_payment_request(): Email a plan payment submission to support.
    disable_provider_account(): Switch off one business by contactid + userprofileid.
    dismiss_plan_expiry_alert(): Close the plan-expiry notice for a contactid's businesses.

Dependencies:
    - app.core.config.settings: NOTIFICATIONS_GOVERNANCE_ENABLED, gating the promotion push.
    - app.core.security.verify_token: Shared bearer token dependency.
    - app.etown.plans.plans_utils: Plan resolution, assignment, catalogue editing, payment routing.
    - app.etown.plans.plans_syncing: Dataverse mirror of plan columns, the disabled flag and invoice rows.
    - app.etown.notifications.triggers: notify_plan_promoted() congratulation push.
    - app.schemas.api: request models for promote, catalogue, payment, disable and alert dismiss.

Updates:
    18-Aug-2026: Created so clients can read plan limits and usage.
    24-Aug-2026: features carry support_email; /promote moves every owned provider and mirrors to Dataverse.
    31-Aug-2026: Added /payment_request with blob screenshot link; /promote sends a congratulation push.
    01-Sep-2026: /payment_request resolves contactid from mobile; /promote takes contact_id; added /disable.
    02-Sep-2026: /my_plan reports remaining_days and alert; added POST /alert/dismiss.
    03-Sep-2026: /payment_request copies PLAN_PAYMENT_EXTRA_EMAILS and returns copied_to.
    17-Sep-2026: /my_plan accepts contactid, falls back to etown_member; /promote accepts member-only contacts.
    08-Oct-2026: /promote passes the request route to the promotion push.
    26-Oct-2026: /promote creates an ets_invoicegenerations row in the background.
    27-Oct-2026: /promote description documents etown_subscription_history rows.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Response, status

from app.core.config import settings
from app.core.logging import logger
from app.core.security import verify_token
from app.etown.plans.plans_syncing import (
    create_invoice_generation_in_dataverse,
    sync_member_plan_to_dataverse,
    sync_provider_disabled_to_dataverse,
    sync_provider_plan_to_dataverse,
)
from app.etown.plans.plans_utils import (
    disable_provider,
    dismiss_plan_alert,
    enable_providers_for_contact,
    get_plan_status,
    promote_contact,
    route_plan_payment_request,
    update_subscription_plan,
)
from app.schemas.api import (
    DisableProviderRequest,
    DismissPlanAlertRequest,
    PlanPaymentRequest,
    PromoteProviderPlanRequest,
    UpdateSubscriptionPlanRequest,
)

router = APIRouter(tags=["plans"])


@router.get(
    "/my_plan",
    summary="Get Subscription Plan Status",
    description=(
        "Return the provider's subscription plan, its feature flags, and every limit with "
        "what the provider currently holds. Name the subject with userprofileid, with "
        "contactid, or with both - at least one is required, and neither one alone is "
        "preferred over the other. A contactid on its own is answered through the oldest "
        "business that contact owns, which is enough because the tier, its dates and the "
        "expiry notice belong to the person and read the same on every business they own; "
        "only the per-business counts (listings, towns, offers) are that one business's own, "
        "so a client that cares about those should name the userprofileid. Sending both "
        "reads the profile named and checks it against the contact: a userprofileid that "
        "belongs to a different contactid is a 400, not a plan. The response carries "
        "contactid beside userprofileid, so a caller who asked by contactid can see which "
        "business answered. "
        "The plan is read off the business row first and off the member row second, and "
        "plan_source says which answered - 'provider' or 'member'. A plain member owns no "
        "business, and a contact whose only business has been deleted no longer owns a live "
        "one; the subscription is the person's either way, so it is reported instead of "
        "404ing someone who is paying. On a 'member' read the per-business counts are all "
        "zero or null, there being no business to have spent them, and the expiry notice "
        "follows the countdown alone: alert is true from remaining_days 7 down to 1 and "
        "POST /plans/alert/dismiss cannot quiet it, having no business row to record the "
        "dismissal on. "
        "A limit of null means unlimited; used is null "
        "for max_gallery_photos, which is a per-post cap with no running total. "
        "chattie_conversation counts the provider's own Chaatie messages so far today and "
        "resets at IST midnight; the max_*_month limits reset on the 1st. provider_onboard "
        "is the contact's, not this profile's: it counts every provider the contact holds "
        "and its limit comes from the best active pack on any of them, so two profiles under "
        "one contact report the same allowance. remaining_days counts the days expires_on "
        "still has: null when the plan carries no expiry and never lapses, 0 on the last "
        "day it is in force. alert is whether the client should raise the renewal notice: true "
        "on every read from remaining_days 7 down to 1 until the owner closes it with POST "
        "/plans/alert/dismiss, false on every read outside that week. It is not a restatement "
        "of remaining_days -- the same 5 reads alert false once dismissed -- so a client "
        "should render the notice on alert, not on the day count, and call "
        "/plans/alert/dismiss when the owner closes it. Nothing else stops it: undismissed, "
        "the notice comes back on every call. A dismissal covers the notice showing at the "
        "time, so closing it during the week goes quiet until remaining_days reaches 1, and "
        "the last day asks once more. "
        "Providers with no subscription resolve to "
        "the free plan. A deactivated business reports account_state='deactivated' with the "
        "free plan in force and the tier it still holds in subscribed_plan_code - none of "
        "that tier's limits, features or badge apply until it is reactivated."
    ),
    responses={
        200: {
            "description": "Plan status retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "userprofileid": "c86bc643-f6e9-57d6-b026-7c22f750a026",
                        "contactid": "9f1d4b2e-3a77-4c10-8b55-1e0c6d2f7a41",
                        "plan_source": "provider",
                        "plan_code": "silver",
                        "plan_name": "Silver",
                        "badge_label": "Silver Member",
                        "status": "active",
                        "account_state": "active",
                        "started_on": "2026-09-01",
                        "expires_on": "2027-09-01",
                        "remaining_days": 364,
                        "alert": False,
                        "features": {
                            "verified_badge": True,
                            "full_business_profile": True,
                            "post_audience_targeting": True,
                            "booking_insights": True,
                            "profile_view_insights": "weekly_monthly",
                            "support_channel": "email",
                            "support_response_hours": 72,
                            "support_email": "support@etown.com",
                        },
                        "limits": {
                            "max_listings": {"limit": 10, "used": 3, "unlimited": False, "remaining": 7},
                            "max_towns": {"limit": 2, "used": 1, "unlimited": False, "remaining": 1},
                            "max_active_offers": {"limit": 2, "used": 0, "unlimited": False, "remaining": 2},
                            "max_offers_per_month": {"limit": 10, "used": 4, "unlimited": False, "remaining": 6},
                            "max_chaatie_generations_month": {"limit": 5, "used": 1, "unlimited": False, "remaining": 4},
                            "max_posts_per_month": {"limit": None, "used": 0, "unlimited": True, "remaining": None},
                            "whatsapp_quota_month": {"limit": 100, "used": 0, "unlimited": False, "remaining": 100},
                            "chattie_conversation": {"limit": 20, "used": 6, "unlimited": False, "remaining": 14},
                            "provider_onboard": {"limit": 2, "used": 1, "unlimited": False, "remaining": 1},
                            "max_gallery_photos": {"limit": 5, "used": None, "unlimited": False, "remaining": None},
                        },
                    }
                }
            },
        },
        400: {"description": "Neither id was given, or the two name different contacts"},
        401: {"description": "Unauthorized - invalid or missing access token"},
        404: {"description": "Plan could not be resolved for this userprofileid or contactid"},
        500: {"description": "Internal server error"},
    },
)
async def my_plan(
    userprofileid: Optional[str] = Query(
        None, description="The provider's user profile ID. Required unless contactid is given."
    ),
    contactid: Optional[str] = Query(
        None,
        description=(
            "The owner's contact ID. On its own it reads the plan through the oldest "
            "business the contact owns; alongside userprofileid it is checked against it."
        ),
    ),
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle GET /api/plans/my_plan: return the plan, feature flags, per-limit usage and expiry alert.

    Args:
        userprofileid: Business to read the plan through; optional when contactid is given.
        contactid: Owner contact; alone it reads through the oldest owned business.
        token: Shared bearer token.

    Returns:
        The plan status dict from get_plan_status.

    Raises:
        HTTPException: 400 on missing or mismatched ids, 404 if unresolved, 500 on failure.
    """
    try:
        status_payload = await get_plan_status(userprofileid, contact_id=contactid)
        if status_payload is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No subscription plan could be resolved for this user profile.",
            )
        return status_payload
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(
            f"my_plan failed | userprofileid={userprofileid} | contactid={contactid} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve subscription plan status",
        )


@router.post(
    "/promote",
    summary="Promote a Contact's Providers onto a Subscription Plan",
    description=(
        "Move a contact, identified by contactid, onto plan_code and record the "
        "transition. The subscription belongs to the person, not to one business, so "
        "every live provider that contact owns moves together and each gets its own "
        "history row. The tier is shared; the allowances are not -- each business keeps "
        "the tier's full quota of listings, towns and offers. start_date defaults to "
        "today when the contact is moving onto a paid plan and holds none already; "
        "end_date left null means the plan never lapses. "
        "A contact who owns no business is promoted too, through their member row: the "
        "subscription belongs to the person, and etown_member carries the same four plan "
        "columns. subject in the response says which kind of row was moved - 'provider' or "
        "'member' - and for a member-only contact providers_updated is 0 with an empty "
        "providers list, because there was no business to move and so no history row to "
        "write. They are congratulated and mirrored to ets_members just the same. "
        "Every promotion onto a paid plan with status active, provider or member, also opens "
        "a row in etown_subscription_history (mobile number, plan_code, dates, status) and "
        "marks the contact's previous active row expired, so a resubscription is a new row."
    ),
    responses={
        400: {"description": "contact_id/provider_id/plan_code/status invalid, the two ids name different contacts, or the subject owns no live provider or member row"},
        401: {"description": "Unauthorized - invalid or missing access token"},
        500: {"description": "Internal server error"},
    },
)
async def promote_provider_plan(
    request: PromoteProviderPlanRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle POST /api/plans/promote: move a contact's providers or member row onto a plan and schedule mirrors.

    Args:
        request: Contact, optional provider, plan_code, status, dates and attribution.
        background_tasks: Queues Dataverse mirrors, provider re-enable and the promotion push.
        token: Shared bearer token.

    Returns:
        The before/after plan transition dict from promote_contact.

    Raises:
        HTTPException: 400 when the input or subject is rejected, 500 on unexpected failure.
    """
    try:
        result = await promote_contact(
            request.contact_id,
            request.plan_code,
            provider_id=request.provider_id,
            status=request.status,
            started_on=request.start_date,
            expires_on=request.end_date,
            changed_by=request.changed_by,
            reason=request.reason,
        )
        moved_ids = [
            p["providerid"] for p in result.get("providers", []) if p.get("providerid")
        ]
        promoted_contactid = result.get("contactid") or request.contact_id
        if promoted_contactid:
            background_tasks.add_task(sync_member_plan_to_dataverse, [promoted_contactid])
            background_tasks.add_task(
                create_invoice_generation_in_dataverse,
                promoted_contactid,
                (result.get("to") or {}).get("started_on"),
                (result.get("to") or {}).get("expires_on"),
            )
        if moved_ids:
            background_tasks.add_task(sync_provider_plan_to_dataverse, moved_ids)
            if promoted_contactid:
                background_tasks.add_task(
                    enable_providers_for_contact, promoted_contactid
                )
                background_tasks.add_task(
                    sync_provider_disabled_to_dataverse, moved_ids
                )
        if (
            settings.NOTIFICATIONS_GOVERNANCE_ENABLED
            and request.status == "active"
            and request.plan_code != "free"
        ):
            promoted_upids = [
                p["userprofileid"] for p in result.get("providers", [])
                if p.get("userprofileid") and p.get("from_plan_code") != request.plan_code
            ]
            if (
                not promoted_upids
                and result.get("subject") == "member"
                and result.get("userprofileid")
                and (result.get("from") or {}).get("plan_code") != request.plan_code
            ):
                promoted_upids = [result["userprofileid"]]
            if promoted_upids:
                from app.etown.notifications.triggers import notify_plan_promoted
                background_tasks.add_task(
                    notify_plan_promoted,
                    plan_code=request.plan_code,
                    userprofile_ids=promoted_upids,
                    contactid=result.get("contactid"),
                    route=request.route,
                )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"promote_provider_plan failed | contact_id={request.contact_id} | "
            f"provider_id={request.provider_id} | plan_code={request.plan_code} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to promote the contact onto the requested plan",
        )


@router.patch(
    "/catalogue/{plan_code}",
    summary="Update or Create a Subscription Plan's Limits and Feature Flags",
    description=(
        "Patch one or more columns of plan_code's etown_subscription_plan row, creating "
        "it if plan_code does not already exist. On an update, only fields present in "
        "the request body are written -- an omitted field keeps its stored value, a "
        "field sent as null clears it (null means unlimited for every limit column) -- "
        "and every provider currently on this plan is affected immediately. On a "
        "create, plan_name is required (the one column with no default), and every "
        "other omitted field takes the table's own default rather than 'unchanged'."
    ),
    responses={
        200: {"description": "Existing plan updated"},
        201: {"description": "plan_code did not exist and was created"},
        400: {
            "description": (
                "No fields to update, an unrecognised field, plan_name missing while "
                "creating a new plan_code, or the new values were rejected"
            )
        },
        401: {"description": "Unauthorized - invalid or missing access token"},
        500: {"description": "Internal server error"},
    },
)
async def update_plan_catalogue(
    request: UpdateSubscriptionPlanRequest,
    response: Response,
    plan_code: str = Path(..., description="The plan_code to update, or to create if it doesn't exist yet"),
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle PATCH /api/plans/catalogue/{plan_code}: update or create a subscription plan row.

    Returns:
        The etown_subscription_plan row, with status 201 when created and 200 when updated.

    Raises:
        HTTPException: 400 when the fields are rejected, 500 on unexpected failure.
    """
    plan_code = plan_code.strip().lower()
    updates = request.model_dump(exclude_unset=True, exclude={"changed_by"})
    try:
        row, created = await update_subscription_plan(
            plan_code, updates, changed_by=request.changed_by
        )
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return row
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"update_plan_catalogue failed | plan_code={plan_code} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update the subscription plan catalogue",
        )


@router.post(
    "/payment_request",
    summary="Email a Plan Payment Request to Support",
    description=(
        "Upload a plan payment's screenshot to blob storage, then email the payment to "
        "the support inbox (SUPPORT_EMAIL), plus anyone named in PLAN_PAYMENT_EXTRA_EMAILS, "
        "with the screenshot linked. Those extras come back as copied_to and are empty "
        "unless the env var names someone. mobilenumber is "
        "required and must be a valid Indian mobile (10 digits starting 6-9, optional +91) "
        "-- it is the only identifier the submission carries. screenshot and paymentid "
        "stay optional, and a blank string is treated exactly like an omitted field, reported as "
        "'not provided' in the email rather than dropped. paymentid is the payer's own "
        "transaction reference at their payment provider (UPI id, order id, bank reference); "
        "it is carried through unvalidated and echoed back, and is not the 'reference' this "
        "endpoint issues for the submission itself. Neither the payer's contactid nor their "
        "name is accepted from the client: both are resolved server-side from mobilenumber "
        "(verified-first ownership, deleted contacts ignored) and returned in the response as "
        "contactid and contactidname, so a number that owns no account reads as such in both "
        "the email and contactid=null rather than as a client that forgot to send it, and the "
        "name in the email is the one support finds when they look the payer up. "
        "screenshot is base64, plain or "
        "data-URL prefixed; an already-stored blob URL is passed through instead of being "
        "re-uploaded. No row is written for the submission -- the email is the record -- "
        "so a 200 means the mail left for the inbox and a send failure is a 502 rather "
        "than a silent success. A failed screenshot upload does not fail the request: the "
        "email still goes out, saying the screenshot could not be stored, and "
        "screenshot_uploaded comes back false so the client can ask for it again."
    ),
    responses={
        200: {
            "description": "Payment request emailed to the support inbox",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "reference": "20260831-142530",
                        "routed_to": "etowndev.service@etown.com",
                        "copied_to": [],
                        "contactid": "c86bc643-f6e9-57d6-b026-7c22f750a026",
                        "contactidname": "Ramesh Kumar",
                        "paymentid": "428713905566",
                        "screenshot_url": (
                            "https://etowndev01.blob.core.windows.net/etowndev/plan_payments/"
                            "c86bc643-f6e9-57d6-b026-7c22f750a026/20260831090530_PlanPayment.jpg"
                        ),
                        "screenshot_uploaded": True,
                        "message": "Your payment details have been sent to our team.",
                    }
                }
            },
        },
        401: {"description": "Unauthorized - invalid or missing access token"},
        422: {"description": "mobilenumber is missing or is not a valid Indian mobile number"},
        502: {"description": "The email could not be delivered to the support inbox"},
        500: {"description": "Internal server error"},
    },
)
async def submit_plan_payment_request(
    request: PlanPaymentRequest,
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle POST /api/plans/payment_request: store the payment screenshot and email the payment to support.

    Returns:
        The route_plan_payment_request result plus success and message.

    Raises:
        HTTPException: 502 when the email could not be sent.
    """
    try:
        result = await route_plan_payment_request(
            mobilenumber=request.mobilenumber,
            screenshot=request.screenshot,
            paymentid=request.paymentid,
        )
        return {
            "success": True,
            **result,
            "message": "Your payment details have been sent to our team.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"submit_plan_payment_request failed | mobilenumber={request.mobilenumber} | "
            f"error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to email the plan payment request. Please try again.",
        )


@router.post(
    "/disable",
    summary="Disable a Provider",
    description=(
        "Set disabled = true on the live provider that contactid owns as userprofileid. Both "
        "ids are required and both are matched: a contact can own several businesses, so "
        "contactid alone would switch off all of them, and userprofileid alone would let a "
        "caller disable a business without naming who owns it. A pair that matches no live "
        "provider -- an unknown id either side, a deleted provider, or two ids that belong to "
        "different accounts -- is a 404 rather than a silent no-op. The call is idempotent: a "
        "provider already disabled comes back 200 with already_disabled true and "
        "providers_disabled 0, and nothing is rewritten. "
        "This is not deactivation. It writes the one column and nothing else -- the business "
        "keeps its plan, its allowances and its visibility, because no read path consults "
        "disabled yet. To actually pause a business, hide its listings and drop it to the free "
        "tier, use POST /api/providers/lifecycle with action_mode='deactivate'."
    ),
    responses={
        200: {
            "description": "Provider disabled, or already was",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "contactid": "c86bc643-f6e9-57d6-b026-7c22f750a026",
                        "userprofileid": "a41f8d90-2b17-4e63-9c55-0f3d8a1b7e42",
                        "providers_disabled": 1,
                        "providers": [
                            {
                                "providerid": "7f2c0a55-9d61-4b8e-b0a4-1c93e6d2f508",
                                "businessname": "Sharma Electricals",
                                "already_disabled": False,
                            }
                        ],
                        "message": "Provider disabled.",
                    }
                }
            },
        },
        401: {"description": "Unauthorized - invalid or missing access token"},
        404: {"description": "No live provider matches that contactid and userprofileid"},
        422: {"description": "contactid or userprofileid missing or blank"},
        500: {"description": "Internal server error"},
    },
)
async def disable_provider_account(
    request: DisableProviderRequest,
    background_tasks: BackgroundTasks,
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle POST /api/plans/disable: set disabled on the provider matching both ids and mirror it to Dataverse.

    Returns:
        The echoed ids, matched providers, providers_disabled count and a message.

    Raises:
        HTTPException: 404 when no live provider matches both ids, 500 on write failure.
    """
    try:
        result = await disable_provider(request.contactid, request.userprofileid)
    except Exception as e:
        logger.error(
            f"disable_provider_account failed | contactid={request.contactid} | "
            f"userprofileid={request.userprofileid} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disable the provider",
        )

    if not result["found"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No live provider owned by contactid '{request.contactid}' with "
                f"userprofileid '{request.userprofileid}'."
            ),
        )

    matched_ids = [
        p["providerid"] for p in result.get("providers", []) if p.get("providerid")
    ]
    if matched_ids:
        background_tasks.add_task(sync_provider_disabled_to_dataverse, matched_ids)

    disabled_now = result["providers_disabled"]
    return {
        "success": True,
        "contactid": result["contactid"],
        "userprofileid": result["userprofileid"],
        "providers_disabled": disabled_now,
        "providers": result["providers"],
        "message": "Provider disabled." if disabled_now else "Provider was already disabled.",
    }


@router.post(
    "/alert/dismiss",
    summary="Dismiss a Contact's Plan Expiry Alert",
    description=(
        "Set alert = false on every live provider the contactid owns, closing the renewal "
        "notice GET /plans/my_plan raises from remaining_days 7 down to 1. Call it when the "
        "owner dismisses that notice: my_plan keeps reporting alert true on every read until "
        "this runs, so without it the notice comes back on each call for the rest of the "
        "week. Afterwards my_plan reports alert false, with remaining_days unchanged. "
        "contactid alone, unlike /plans/disable: the subscription and its expiry belong to "
        "the contact, and every business it owns shares that date, so one dismissal covers "
        "all of them rather than making the owner close the same notice per business. "
        "A contactid owning no live provider is a 404 rather than a silent no-op. Repeats "
        "while the same notice is up are idempotent: 200 with was_armed false and "
        "alerts_dismissed 0. "
        "The dismissal covers the notice showing at the time, and does not need undoing -- "
        "there is no re-arm endpoint. The last-day notice comes anyway, and a renewal resets "
        "it for the new term, so an owner who closes the week's warning is still asked once "
        "more at remaining_days 1. It writes the two alert columns: the plan, its allowances "
        "and the business's visibility are all untouched."
    ),
    responses={
        200: {
            "description": "Alert dismissed, or already was",
            "content": {
                "application/json": {
                    "example": {
                        "success": True,
                        "contactid": "c86bc643-f6e9-57d6-b026-7c22f750a026",
                        "alerts_dismissed": 1,
                        "providers": [
                            {
                                "providerid": "7f2c0a55-9d61-4b8e-b0a4-1c93e6d2f508",
                                "businessname": "Sharma Electricals",
                                "was_armed": True,
                            }
                        ],
                        "message": "Plan alert dismissed.",
                    }
                }
            },
        },
        401: {"description": "Unauthorized - invalid or missing access token"},
        404: {"description": "No live provider is owned by that contactid"},
        422: {"description": "contactid missing or blank"},
        500: {"description": "Internal server error"},
    },
)
async def dismiss_plan_expiry_alert(
    request: DismissPlanAlertRequest,
    token: str = Depends(verify_token),
) -> Dict[str, Any]:
    """Handle POST /api/plans/alert/dismiss: close the plan-expiry notice on every business a contact owns.

    Returns:
        The contactid, per-provider was_armed flags, alerts_dismissed count and a message.

    Raises:
        HTTPException: 404 when the contact owns no live provider, 500 on write failure.
    """
    try:
        result = await dismiss_plan_alert(request.contactid)
    except Exception as e:
        logger.error(
            f"dismiss_plan_expiry_alert failed | contactid={request.contactid} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to dismiss the plan alert",
        )

    if not result["found"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No live provider is owned by contactid '{request.contactid}'.",
        )

    dismissed_now = result["alerts_dismissed"]
    return {
        "success": True,
        "contactid": result["contactid"],
        "alerts_dismissed": dismissed_now,
        "providers": result["providers"],
        "message": (
            "Plan alert dismissed." if dismissed_now else "Plan alert was already dismissed."
        ),
    }
