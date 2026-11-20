"""Provider Review Utilities - Postgres review queue for field-agent provider captures.

Domain layer behind POST /api/provider/unverified_provider: records every create and every unapplied edit verbatim in etown_provider_review, emails support and mirrors each row to Dataverse, and supplies the SQL predicates that hide pending providers from search until the review is approved.

Features:
    - Verbatim Capture: every request field stored as-is; only marketing_person_name is resolved
    - Format-Insensitive Matching: last-ten-digit comparison, shared with the mobile-number lookups
    - Support Notification: one branded mail per row to settings.SUPPORT_EMAIL
    - Edit Comparison: the edit mail shows submitted values beside the live listing, field by field
    - Search Exclusion: pending rows hide their provider from search until review is approved
    - Non-Blocking on Create: a failure after the committed onboard is logged and swallowed
    - Action Modes: first-time 'create' captures are approved; duplicate creates and edits await review
    - Review Status: first-time creates start approved; duplicate creates and edits start NULL
    - Two Review Vocabularies: exclusions treat both 'Approved' and the CRM's '1' as approved
    - Field-Agent Lookups: fetch_providers_by_mobile() and unverified_onboarded still see pending rows

Functions:
    review_pending_provider_ids_cte(): The held-back provider ids as a CTE, for queries opening with WITH
    review_pending_exclusion(): NOT EXISTS dropping held-back providers, keyed on a providerid column
    review_pending_userprofile_exclusion(): The same, for a query that only has the userprofileid
    _resolve_marketing_person_name(): The capturing field agent's firstname, from their contactid
    _national_mobile(): Last ten digits of a mobile number, or None
    find_live_providers_by_mobile(): Every live etown_provider carrying a mobile number, newest first
    _detail_rows(): Render label/value pairs as the mail's detail table, skipping empties
    _submitted_text(): Normalise a body value; None, '' and 'Null' mean keep
    _same_value(): Whether a submitted value leaves the live one unchanged
    fetch_edit_changes(): Compare an edit body against the live listing, field by field, for the mail
    _comparison_table(): Render current vs submitted rows as a three-column table
    _service_detail_lines(): One line per submitted servicedetails entry, images counted
    send_provider_review_notification_email(): Mail settings.SUPPORT_EMAIL about one review row
    record_provider_review(): Insert one review row, notify support and mirror it to Dataverse
    fetch_provider_review_snapshot(): Read a provider's stored contact/profile/service rows, unchanged

Dependencies:
    - app.db.client: Shared asyncpg Database pool
    - app.schemas.api: UnverifiedProviderOnboardingRequest, the body stored here
    - app.core.config: SUPPORT_EMAIL, the inbox the notification goes to
    - app.services.email_service / email_templates: Graph send and the branded shell
    - app.utils.phone: normalize_indian_mobile, for the last-ten-digit key
    - app.etown.provider.provider_review_syncing: mirrors each stored row to Dataverse
    - scripts/migrations/2026_09_provider_review.sql: creates etown_provider_review
    - scripts/migrations/2026_09_provider_review_any_action_mode.sql: rebuilds the partial index without action_mode
    - scripts/migrations/2026_09_provider_review_servicedetails_text.sql: retypes servicedetails to text
    - scripts/migrations/2026_09_provider_interested.sql: adds interested to etown_provider and etown_provider_review

Updates:
    17-Sep-2026: Added record_provider_review() and review_pending_exclusion(); support mail; Dataverse mirror.
    21-Sep-2026: Exclusion ignores action_mode and covers more readers; servicedetails retyped to text.
    22-Sep-2026: Exclusions accept review '1'; fetch_providers_by_mobile() and _fetch_unverified_onboarded() no longer excluded.
    24-Sep-2026: review_pending_exclusion() takes create_only; edit mail compares against the live listing.
    25-Sep-2026: Edit-review email says the changes await approval.
    30-Sep-2026: record_provider_review() stores interested; support mail shows it.
    09-Oct-2026: First-time creates are approved automatically when no live account matches the mobile.
"""

from typing import Any, Dict, List, Optional
import html as _html
import json
import re
import uuid

from fastapi import BackgroundTasks

from app.db.client import Database
from app.core.config import settings
from app.core.logging import logger
from app.etown.provider.provider_review_syncing import sync_provider_review_to_dataverse
from app.schemas.api import UnverifiedProviderOnboardingRequest
from app.utils.phone import normalize_indian_mobile

ACTION_CREATE = "create"
ACTION_EDIT = "edit"

REVIEW_APPROVED = "approved"
REVIEW_APPROVED_CHOICE = "1"
_REVIEW_APPROVED_SQL = f"'{REVIEW_APPROVED}', '{REVIEW_APPROVED_CHOICE}'"

_REVIEW_PENDING_PROVIDERS = f"""
    SELECT DISTINCT providerid
      FROM etown_provider_review
     WHERE providerid IS NOT NULL
       AND lower(btrim(COALESCE(review, ''))) NOT IN ({_REVIEW_APPROVED_SQL})
"""


def review_pending_provider_ids_cte(name: str = "review_pending_ids") -> str:
    """Return a materialized CTE fragment named name that selects provider ids awaiting review."""
    return f"{name} AS MATERIALIZED ({_REVIEW_PENDING_PROVIDERS}    )"


def review_pending_exclusion(provider_column: str, alias: str = "rvw", create_only: bool = False) -> str:
    """Return a NOT EXISTS predicate excluding providers with an unapproved review row.

    Args:
        provider_column: Qualified SQL expression for the provider id, e.g. 'ps.providerid'.
        alias: Subquery table alias, changed only to avoid an outer collision.
        create_only: When True, only pending 'create' rows exclude a provider.

    Returns:
        The NOT EXISTS predicate with no leading AND.
    """
    action_filter = (
        f"                   AND {alias}.action_mode = '{ACTION_CREATE}'\n" if create_only else ""
    )
    return (
        f"NOT EXISTS (\n"
        f"                SELECT 1 FROM etown_provider_review {alias}\n"
        f"                 WHERE {alias}.providerid = {provider_column}\n"
        f"                   AND lower(btrim(COALESCE({alias}.review, ''))) NOT IN ({_REVIEW_APPROVED_SQL})\n"
        f"{action_filter}"
        f"              )"
    )


def review_pending_userprofile_exclusion(userprofile_column: str, alias: str = "rvw") -> str:
    """Return a NOT EXISTS predicate excluding userprofiles whose provider has an unapproved review row."""
    provider_alias = f"{alias}_p"
    return (
        f"NOT EXISTS (\n"
        f"                SELECT 1 FROM etown_provider_review {alias}\n"
        f"                  JOIN etown_provider {provider_alias}\n"
        f"                    ON {provider_alias}.providerid = {alias}.providerid\n"
        f"                 WHERE {provider_alias}.userprofileid = {userprofile_column}\n"
        f"                   AND lower(btrim(COALESCE({alias}.review, ''))) NOT IN ({_REVIEW_APPROVED_SQL})\n"
        f"              )"
    )

_NEW_CAPTURE_SUBJECT = "New provider capture on {mobile} — auto-approved"
_NEW_CAPTURE_EXPLANATION = (
    "A field agent onboarded a new provider on a mobile number with no other live listing. "
    "<strong>The provider and its review row were approved automatically</strong> because no "
    "existing account uses this number."
)

_REVIEW_SUBJECTS = {
    ACTION_CREATE: "Duplicate provider capture on {mobile} — review needed",
    ACTION_EDIT: "Provider edit awaiting review on {mobile}",
}
_REVIEW_EXPLANATIONS = {
    ACTION_CREATE: (
        "A field agent onboarded a provider on a mobile number that already carried a live "
        "listing. <strong>The new provider was created but stays out of search and listings "
        "until this review row is approved</strong> — compare the two and decide whether they "
        "are the same business."
    ),
    ACTION_EDIT: (
        "A field agent submitted changes to the listing on this mobile number. "
        "<strong>These changes are not live yet</strong> — they are waiting for approval, and the "
        "provider, its services, its contact and its user profile still show their current values "
        "until someone approves or rejects the edit. The submitted values are stored in "
        "etown_provider_review."
    ),
}

_LIVE_PROVIDERS_BY_MOBILE = """
    SELECT providerid, contactid, userprofileid, businessname, providertype,
           publicsearchprovider, source, creationdate
      FROM etown_provider
     WHERE RIGHT(regexp_replace(COALESCE(mobilenumber, ''), '[^0-9]', '', 'g'), 10) = $1
       AND lower(COALESCE(deleted, 'false')) <> 'true'
     ORDER BY creationdate DESC NULLS LAST
"""


_MARKETING_PERSON_NAME = """
    SELECT firstname
      FROM etown_contact
     WHERE contactid = $1
       AND deleted IS DISTINCT FROM 'true'
"""


async def _resolve_marketing_person_name(marketing_contact_id: Optional[str]) -> Optional[str]:
    """Return the field agent's firstname from their live contact, or None when absent or unmatched."""
    if not marketing_contact_id:
        return None

    name = await Database.fetchval(_MARKETING_PERSON_NAME, marketing_contact_id)
    if not name:
        logger.warning(
            "[provider_review] marketing_contactid %s matched no live contact (or it has no "
            "firstname) - the review row is stored with no marketing_person_name",
            marketing_contact_id,
        )
    return name


def _national_mobile(phone_number: Optional[str]) -> Optional[str]:
    """Return the ten-digit national mobile number, or None when fewer than ten digits are present."""
    digits = re.sub(r"\D+", "", phone_number or "")
    return normalize_indian_mobile(phone_number) or (digits[-10:] if len(digits) >= 10 else None)


async def find_live_providers_by_mobile(phone_number: Optional[str]) -> List[Dict[str, Any]]:
    """Return every non-deleted etown_provider row whose mobile matches on the last ten digits, newest first."""
    national = _national_mobile(phone_number)
    if not national:
        return []

    rows = await Database.fetch(_LIVE_PROVIDERS_BY_MOBILE, national)
    return [dict(row) for row in rows]


def _detail_rows(pairs: List[tuple]) -> str:
    """Render non-blank label/value pairs as an HTML detail table, or '' when all are blank."""
    rows = ""
    for label, value in pairs:
        text = str(value).strip() if value is not None else ""
        if not text:
            continue
        rows += (
            '<tr>'
            '<td style="padding:6px 12px 6px 0;font-family:Helvetica,Arial,sans-serif;'
            'font-size:14px;color:#6b6b6b;vertical-align:top;white-space:nowrap;">'
            f"{_html.escape(label)}</td>"
            '<td style="padding:6px 0;font-family:Helvetica,Arial,sans-serif;'
            'font-size:14px;color:#1a1a1a;vertical-align:top;">'
            f"{_html.escape(text)}</td>"
            "</tr>"
        )
    if not rows:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;margin:0 0 20px;">{rows}</table>'
    )


_LIVE_EDIT_VALUES = """
    SELECT p.businessname, p.description, p.businesscategory, p.email, p.city, p.address,
           p.areaname, p.statecode, p.latitude, p.longitude, p.additionaladdressinfo,
           p.servicetype, p.serviceimageurl, p.interested,
           psc.servicecategory, psc.services,
           c.alternatemobile
      FROM etown_provider p
      LEFT JOIN etown_contact c ON c.contactid = p.contactid
      LEFT JOIN etown_providerservices_copy psc ON psc.providerid = p.providerid
     WHERE p.providerid = $1
     LIMIT 1
"""

_EDIT_COMPARISON_FIELDS = [
    ("Business name", "businessname", "businessname", "text"),
    ("Description", "description", "description", "text"),
    ("Business category", "business_category", "businesscategory", "text"),
    ("Service category", "service_category", "servicecategory", "text"),
    ("Services", "services", "services", "text"),
    ("Service type", "servicetype", "servicetype", "text"),
    ("Business email", "email", "email", "text"),
    ("Alternate mobile", "alternatemobile", "alternatemobile", "mobile"),
    ("Interested", "interested", "interested", "text"),
    ("Address", "formattedAddress", "address", "text"),
    ("Additional address info", "additionaladdressinfo", "additionaladdressinfo", "text"),
    ("Area", "area_location", "areaname", "text"),
    ("City", "city", "city", "text"),
    ("State", "state", "statecode", "state"),
    ("Latitude", "latitude", "latitude", "number"),
    ("Longitude", "longitude", "longitude", "number"),
]


def _submitted_text(value: Any) -> Optional[str]:
    """Return a stripped body value, or None when it is None, blank or 'null'."""
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() == "null" else text


def _same_value(kind: str, current: Optional[str], submitted: str) -> bool:
    """Return True when a submitted value equals the live one under number, mobile, state or text rules."""
    if current is None or not str(current).strip():
        return False
    current = str(current).strip()
    if kind == "number":
        try:
            return abs(float(current) - float(submitted)) < 1e-6
        except ValueError:
            pass
    elif kind == "mobile":
        return normalize_indian_mobile(current) == normalize_indian_mobile(submitted)
    elif kind == "state":
        from app.etown.services.provider_service import convert_state_name_to_code
        submitted = convert_state_name_to_code(submitted) or submitted
    return " ".join(current.split()).casefold() == " ".join(submitted.split()).casefold()


async def fetch_edit_changes(
    provider_data: UnverifiedProviderOnboardingRequest,
    provider_id: str,
) -> Optional[List[tuple]]:
    """Compare each submitted edit field and the logo against the live listing without raising.

    Returns:
        (label, current, submitted, changed) per submitted field, or None when unreadable.
    """
    try:
        live = await Database.fetchrow(_LIVE_EDIT_VALUES, provider_id)
    except Exception as exc:
        logger.warning(
            "[provider_review] Could not read provider %s for the edit comparison: %s",
            provider_id, exc, exc_info=True,
        )
        return None
    if not live:
        return None

    changes: List[tuple] = []
    for label, attribute, column, kind in _EDIT_COMPARISON_FIELDS:
        submitted = _submitted_text(getattr(provider_data, attribute, None))
        if submitted is None:
            continue
        current = _submitted_text(live[column])
        changes.append((label, current, submitted, not _same_value(kind, current, submitted)))

    logo = _submitted_text(provider_data.businesslogo)
    if logo and logo.lower() not in ("no image",):
        current_logo = _submitted_text(live["serviceimageurl"])
        changes.append((
            "Business logo",
            "Has a logo" if current_logo else None,
            "New logo submitted",
            logo != current_logo,
        ))
    return changes


def _comparison_table(changes: List[tuple]) -> str:
    """Render edit changes as an HTML field/current/submitted table with changed rows marked, or ''."""
    if not changes:
        return ""
    cell = (
        "padding:6px 10px 6px 0;font-family:Helvetica,Arial,sans-serif;font-size:14px;"
        "vertical-align:top;border-bottom:1px solid #e6e6e6;"
    )
    head = "".join(
        f'<th align="left" style="{cell}color:#6b6b6b;font-weight:600;">{title}</th>'
        for title in ("Field", "Current (live)", "Submitted")
    )
    rows = ""
    for label, current, submitted, changed in changes:
        colour = "#1a1a1a" if changed else "#9a9a9a"
        weight = "600" if changed else "400"
        marker = ' <span style="color:#c0392b;">&#9679; changed</span>' if changed else ""
        rows += (
            "<tr>"
            f'<td style="{cell}color:{colour};white-space:nowrap;">{_html.escape(label)}{marker}</td>'
            f'<td style="{cell}color:{colour};">{_html.escape(current or "—")}</td>'
            f'<td style="{cell}color:{colour};font-weight:{weight};">{_html.escape(submitted)}</td>'
            "</tr>"
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="width:100%;margin:0 0 20px;border-collapse:collapse;"><tr>{head}</tr>{rows}</table>'
    )


def _service_detail_lines(provider_data: UnverifiedProviderOnboardingRequest) -> List[str]:
    """Return one line per servicedetails entry with name, price, quantity, description and image count."""
    lines = []
    for service in provider_data.servicedetails:
        parts = [service.service_name]
        if _submitted_text(service.pricerange):
            parts.append(f"price {service.pricerange.strip()}")
        if _submitted_text(service.quantity):
            parts.append(f"qty {service.quantity.strip()}")
        if _submitted_text(service.description):
            parts.append(service.description.strip())
        images = sum(
            1 for image in (service.serviceimage1, service.serviceimage2, service.serviceimage3)
            if _submitted_text(image)
        )
        if images:
            parts.append(f"{images} image{'s' if images != 1 else ''}")
        lines.append(" — ".join(parts))
    return lines


async def send_provider_review_notification_email(
    review_id: str,
    mobilenumber: Optional[str],
    businessname: Optional[str],
    action_mode: str,
    marketing_person: Optional[str],
    city: Optional[str],
    formatted_address: Optional[str],
    email: Optional[str],
    service_count: int,
    matched_summaries: List[str],
    provider_id: Optional[str],
    changes: Optional[List[tuple]] = None,
    service_lines: Optional[List[str]] = None,
    current_businessname: Optional[str] = None,
    interested: Optional[str] = None,
) -> bool:
    """Email SUPPORT_EMAIL about a stored provider review row, returning True when sent and never raising."""
    from app.services.email_service import send_email
    from app.services.email_templates import bullet_list, paragraph, render_email

    recipient = (settings.SUPPORT_EMAIL or "").strip()
    if not recipient:
        logger.warning(
            "[provider_review] SUPPORT_EMAIL is unset — review %s (%s) was stored but nobody "
            "was notified", review_id, action_mode,
        )
        return False

    mobile = (mobilenumber or "").strip() or "an unknown number"
    first_capture = action_mode == ACTION_CREATE and not matched_summaries
    subject = (
        _NEW_CAPTURE_SUBJECT if first_capture
        else _REVIEW_SUBJECTS.get(action_mode, "Provider capture on {mobile} — review needed")
    ).format(mobile=mobile)
    explanation = (
        _NEW_CAPTURE_EXPLANATION if first_capture
        else _REVIEW_EXPLANATIONS.get(action_mode, "A provider capture needs a look.")
    )

    if changes is not None:
        changed_count = sum(1 for change in changes if change[3])
        name = (businessname or current_businessname or "").strip() or "unnamed business"
        subject = (
            f"Provider edit awaiting review — {name} ({mobile}) — "
            f"{changed_count} change{'s' if changed_count != 1 else ''}"
        )
        content = (
            paragraph(explanation)
            + _detail_rows([
                ("Mobile number", mobilenumber),
                ("Business", current_businessname),
                ("Submitted by", marketing_person),
                ("Listing to edit", provider_id),
                ("Review id", review_id),
            ])
        )
        if changed_count:
            content += paragraph(
                f"<strong>{changed_count} field{'s' if changed_count != 1 else ''} would "
                "change</strong> if this edit is approved:"
            ) + _comparison_table(changes)
        else:
            content += paragraph(
                "<strong>Every submitted value matches the live listing</strong> — approving "
                "this edit would change nothing."
                + (" Submitted values:" if changes else "")
            ) + _comparison_table(changes)
    else:
        content = (
            paragraph(explanation)
            + _detail_rows([
                ("Mobile number", mobilenumber),
                ("Business name", businessname),
                ("Submitted as", action_mode),
                ("Field agent", marketing_person),
                ("City", city),
                ("Address", formatted_address),
                ("Business email", email),
                ("Interested", interested),
                ("Services submitted", service_count if service_count else None),
                (
                    "New provider created" if action_mode == "create" else "Listing to edit",
                    provider_id,
                ),
                ("Review id", review_id),
            ])
        )

    if service_lines:
        content += paragraph(
            f"<strong>Services submitted</strong> ({len(service_lines)}):"
        ) + bullet_list([_html.escape(line) for line in service_lines])

    if matched_summaries:
        content += paragraph(
            f"<strong>Already on {_html.escape(mobile)}"
            f"</strong> ({len(matched_summaries)} live listing"
            f"{'s' if len(matched_summaries) != 1 else ''}):"
        ) + bullet_list([_html.escape(line) for line in matched_summaries])

    content += paragraph(
        "The full submission, including anything not shown above, is the "
        f"etown_provider_review row with reviewid <strong>{_html.escape(review_id)}</strong>.",
        muted=True,
    )

    try:
        await send_email(
            to=recipient,
            subject=subject,
            body=render_email(
                heading="Provider review needed",
                content=content,
                preheader=f"{action_mode} — {mobile}",
                footer=(
                    "Etown Services<br>This is an automated message from Etown. "
                    "Please do not reply to this email."
                ),
            ),
        )
        logger.info(
            "[provider_review] Notified %s | reviewid=%s action_mode=%s mobile=%s",
            recipient, review_id, action_mode, mobilenumber,
        )
        return True
    except Exception as exc:
        logger.error(
            "[provider_review] Support notification failed | reviewid=%s action_mode=%s "
            "mobile=%s to=%s: %s",
            review_id, action_mode, mobilenumber, recipient, exc, exc_info=True,
        )
        return False


async def record_provider_review(
    provider_data: UnverifiedProviderOnboardingRequest,
    provider_id: Optional[str] = None,
    matched_providers: Optional[List[Dict[str, Any]]] = None,
    background_tasks: Optional[BackgroundTasks] = None,
) -> str:
    """Insert an unverified-provider body into etown_provider_review, then email support and mirror to Dataverse.

    Args:
        provider_data: The request body to store as submitted.
        provider_id: Listing the row is about; None when a create wrote nothing.
        matched_providers: Live providers on the same mobile, listed in the support mail only.
        background_tasks: Queue for the mail and mirror; when None both are awaited inline.

    Returns:
        The reviewid of the row written.

    Raises:
        asyncpg.PostgresError: When the insert fails.
    """
    matched = matched_providers or []
    review = (
        REVIEW_APPROVED
        if provider_data.action_mode == ACTION_CREATE and not matched
        else None
    )
    review_id = str(uuid.uuid4())
    marketing_person_name = await _resolve_marketing_person_name(provider_data.marketing_contactid)

    await Database.execute(
        """
        INSERT INTO etown_provider_review (
            reviewid, action_mode, mobilenumber, providerid,
            marketing_person_name, businessname, description, types, formattedaddress,
            latitude, longitude, businesslogo, business_category, service_category,
            city, additionaladdressinfo, services, area_location, servicetype,
            state, language, email, servicedetails, alternatemobile,
            interested, review
        )
        VALUES (
            $1, $2, $3, $4,
            $5, $6, $7, $8, $9,
            $10, $11, $12, $13, $14,
            $15, $16, $17, $18, $19,
            $20, $21, $22, $23, $24,
            $25, $26
        )
        """,
        review_id,
        provider_data.action_mode,
        provider_data.mobilenumber,
        provider_id,
        marketing_person_name,
        provider_data.businessname,
        provider_data.description,
        provider_data.types,
        provider_data.formattedAddress,
        provider_data.latitude,
        provider_data.longitude,
        provider_data.businesslogo,
        provider_data.business_category,
        provider_data.service_category,
        provider_data.city,
        provider_data.additionaladdressinfo,
        provider_data.services,
        provider_data.area_location,
        provider_data.servicetype,
        provider_data.state,
        provider_data.language,
        provider_data.email,
        json.dumps([service.model_dump() for service in provider_data.servicedetails]),
        provider_data.alternatemobile,
        provider_data.interested,
        review,
    )

    logger.info(
        "[provider_review] Stored action_mode='%s' reviewid=%s mobile=%s providerid=%s "
        "others_on_number=%d services=%d",
        provider_data.action_mode, review_id, provider_data.mobilenumber, provider_id,
        len(matched), len(provider_data.servicedetails),
    )

    notification_args = (
        review_id,
        provider_data.mobilenumber,
        provider_data.businessname,
        provider_data.action_mode,
        marketing_person_name or provider_data.marketing_contactid,
        provider_data.city,
        provider_data.formattedAddress,
        provider_data.email,
        len(provider_data.servicedetails),
        [
            f"{row.get('businessname') or 'unnamed'} "
            f"({row.get('providertype') or 'no providertype'}, providerid {row['providerid']})"
            for row in matched
        ],
        provider_id,
    )
    notification_kwargs: Dict[str, Any] = {
        "service_lines": _service_detail_lines(provider_data),
        "interested": provider_data.interested,
    }
    if provider_data.action_mode == ACTION_EDIT and provider_id:
        notification_kwargs["changes"] = await fetch_edit_changes(provider_data, provider_id)
        target = next(
            (row for row in matched if str(row.get("providerid")) == str(provider_id)), None,
        )
        notification_kwargs["current_businessname"] = target.get("businessname") if target else None

    if background_tasks is not None:
        background_tasks.add_task(
            send_provider_review_notification_email, *notification_args, **notification_kwargs,
        )
        background_tasks.add_task(sync_provider_review_to_dataverse, review_id)
    else:
        await send_provider_review_notification_email(*notification_args, **notification_kwargs)
        await sync_provider_review_to_dataverse(review_id)

    return review_id


async def fetch_provider_review_snapshot(provider_id: str) -> Optional[Dict[str, Any]]:
    """Return the provider's live contact, profile, provider and service rows in the onboarding response shape, or None."""
    row = await Database.fetchrow(
        """
        SELECT p.providerid, p.businessname, p.businesscategory, p.description,
               p.publicsearchprovider, p.serviceimageurl, p.city AS provider_city,
               p.contactid, p.userprofileid,
               c.firstname, c.mobilephone, c.email, c.createdon, c.userlogin, c.city AS contact_city,
               up.role, up.name AS userprofile_name,
               psc.providerservicesid, psc.services, psc.servicecategory,
               psc.servicedescription, psc.generalcategory
          FROM etown_provider p
          LEFT JOIN etown_contact c      ON c.contactid = p.contactid
          LEFT JOIN etown_userprofile up ON up.userprofileid = p.userprofileid
          LEFT JOIN etown_providerservices_copy psc ON psc.providerid = p.providerid
         WHERE p.providerid = $1
         LIMIT 1
        """,
        provider_id,
    )
    if not row:
        return None

    contact_id = str(row["contactid"]) if row["contactid"] else ""
    userprofile_id = str(row["userprofileid"]) if row["userprofileid"] else ""
    business_name = row["businessname"] or ""
    description = row["description"] or ""
    business_category = row["businesscategory"] or ""
    service_category = row["servicecategory"] or ""

    return {
        "contact_info": {
            "contactid": contact_id,
            "firstname": row["firstname"],
            "mobilephone": row["mobilephone"],
            "email": row["email"],
            "createdon": row["createdon"].isoformat() + "Z" if row["createdon"] else None,
            "userlogin": row["userlogin"],
            "city": row["contact_city"],
        },
        "userprofile_info": {
            "userprofileid": userprofile_id,
            "contactid": contact_id,
            "role": row["role"] if row["role"] is not None else 1,
            "name": row["userprofile_name"] or business_name,
        },
        "provider_info": {
            "providerid": str(row["providerid"]),
            "businessname": business_name,
            "businesscategory": row["businesscategory"],
            "contactid": contact_id,
            "userprofileid": userprofile_id,
            "city": row["provider_city"],
            "description": row["description"],
            "publicsearchprovider": row["publicsearchprovider"] or "non-verified",
            "serviceimageurl": row["serviceimageurl"],
        },
        "providerservice_info": {
            "providerservicesid": str(row["providerservicesid"]) if row["providerservicesid"] else "",
            "providerid": str(row["providerid"]),
            "businessname": business_name,
            "servicedescription": row["servicedescription"] or description,
            "services": row["services"] or "",
            "businesscategory": business_category,
            "servicecategory": service_category,
        },
        "ai_enhancements": {
            "original_description": description,
            "enhanced_description": description,
            "generated_services": [],
            "business_category": business_category,
            "service_category": service_category,
            "general_category": row["generalcategory"] or "",
        },
    }
