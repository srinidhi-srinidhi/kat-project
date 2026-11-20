"""Booking Dataverse Sync - mirrors booking requests, relations and notifications from Postgres to the CRM.

Background Dataverse mirror for bookings created by the Chaatie request_provider tool and changed through /api/book: upserts allrequests, service-list, notification, relation and invitation rows by PATCH; Postgres stays authoritative and every call swallows its own errors.

Features:
    - AllRequests Sync: Insert and update ets_allrequestses records in Dataverse
    - RequestServiceList Sync: Insert ets_requestservicelistses rows per booked service
    - Notification Sync: Insert ets_notificationses booking notification rows
    - ProviderRelation Sync: Insert ets_providerrelations for new provider-member links
    - MemberRelation Sync: Insert ets_memberrelations for new member-provider links
    - Invitation Sync: Insert adx_invitations for first-contact booking invitations
    - OAuth Token Management: Acquires client-credentials tokens for Dataverse API auth
    - Lookup Resolution: Resolves PostgreSQL UUIDs to Dataverse entity lookup binds
    - Upsert via PATCH: Idempotent Dataverse record creation using HTTP PATCH
    - Instructions/Servicetype Patch: PATCH just one of those fields onto an ets_allrequestses row
    - Error Isolation: Logs Dataverse failures without raising to caller

Classes:
    No classes defined (module uses function-based sync utilities)

Functions:
    _ensure_db_pool(): Initialize the shared Database pool if it is not already open
    _resolve_lookup_id(): Resolve a PostgreSQL entity's primary key for an @odata.bind lookup
    get_dataverse_token(): Acquire OAuth2 client-credentials token for Dataverse
    _fmt_ts(): Format a datetime-like value as an ISO string for a Dataverse payload
    _upsert_dataverse_row(): Shared PATCH helper backing every insert_*_to_dataverse() call
    insert_allrequest_to_dataverse(): Sync etown_allrequests row to ets_allrequestses
    insert_requestservicelist_to_dataverse(): Sync etown_requestservicelists row
    insert_notification_to_dataverse(): Sync notification row to ets_notificationses
    _coerce_invite_relation_type(): Normalize invite_relationshiptype to its numeric code
    insert_providerrelation_to_dataverse(): Sync provider relation to ets_providerrelations
    insert_memberrelation_to_dataverse(): Sync member relation to ets_memberrelations
    _serialize_instructions(): Serialize the instructions jsonb value to a JSON string
    update_allrequest_instructions_to_dataverse(): PATCH just the instructions field
    update_allrequest_servicetype_to_dataverse(): PATCH just the servicetype field
    update_allrequest_to_dataverse(): Update existing ets_allrequestses record
    insert_invitation_to_dataverse(): Sync invitation row to adx_invitations

Dependencies:
    - json: Serializes the instructions payload for the Dataverse text column
    - datetime: Timestamp formatting for Dataverse payload fields
    - httpx: Async HTTP client for Dataverse Web API calls
    - app.core.config: Dataverse API configuration and OAuth credentials
    - app.core.logging: Operation tracking and debugging
    - app.db.client: Database operations for entity ID lookup resolution

Updates:
    29-May-2026: Initial creation with allrequest and requestservicelist sync helpers
    05-Jun-2026: Added update_allrequest_to_dataverse and invitation sync functions; updated documentation.
    20-Jul-2026: update_allrequest_to_dataverse syncs ets_bookingcancelledtime.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
"""

import json
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database

ALLREQUEST_ENTITY = "ets_allrequestses"
REQUEST_SERVICE_LIST_ENTITY = "ets_requestservicelistses"
NOTIFICATION_ENTITY = "ets_notificationses"
PROVIDER_RELATION_ENTITY = "ets_providerrelations"
MEMBER_RELATION_ENTITY = "ets_memberrelations"
INVITATION_ENTITY = "adx_invitations"


async def _ensure_db_pool() -> None:
    """Initialise the Database pool when it is missing or closed."""
    if Database._pool is None or Database._pool._closed:
        await Database.init()


async def _resolve_lookup_id(
    *,
    table_names: list[str],
    id_column: str,
    userprofile_id: str | None,
    contact_id: str | None,
) -> str | None:
    """Return the first non-blank id_column value matching the userprofile id, then contact id, across the given tables."""
    if not userprofile_id and not contact_id:
        return None

    await _ensure_db_pool()

    for table_name in table_names:
        if userprofile_id:
            rows = await Database.fetch(
                f"""
                SELECT {id_column}
                FROM {table_name}
                WHERE NULLIF(TRIM({id_column}), '') IS NOT NULL
                  AND LOWER(TRIM(userprofileid)) = LOWER(TRIM($1))
                LIMIT 1
                """,
                userprofile_id,
            )
            if rows:
                value = rows[0].get(id_column)
                if value:
                    return str(value)

        if contact_id:
            rows = await Database.fetch(
                                f"""
                                SELECT {id_column}
                                FROM {table_name}
                                WHERE NULLIF(TRIM({id_column}), '') IS NOT NULL
                                    AND LOWER(TRIM(contactid)) = LOWER(TRIM($1))
                                LIMIT 1
                                """,
                contact_id,
            )
            if rows:
                value = rows[0].get(id_column)
                if value:
                    return str(value)

    return None


async def get_dataverse_token() -> str:
    """Return a Dataverse client-credentials access token, raising when the response has none."""
    token_url = (
        f"https://login.microsoftonline.com/{settings.DATAVERSE_TENANT_ID}"
        "/oauth2/v2.0/token"
    )
    payload = {
        "grant_type": "client_credentials",
        "client_id": settings.DATAVERSE_CLIENT_ID,
        "client_secret": settings.DATAVERSE_CLIENT_SECRET,
        "scope": f"{settings.DATAVERSE_URL}/.default",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(token_url, data=payload)
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("Dataverse token response did not contain access_token")
        return token


def _fmt_ts(value: Any) -> str | None:
    """Return a timestamp as an ISO string, other values as str, and None unchanged."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def _upsert_dataverse_row(
    *,
    entity_name: str,
    row_id: str,
    payload: Dict[str, Any],
    log_prefix: str,
) -> None:
    """PATCH the non-None payload fields to a Dataverse record by id, logging and swallowing any failure."""
    if not row_id:
        return

    try:
        access_token = await get_dataverse_token()
    except Exception as exc:
        logger.error("%s | FAILED_TOKEN | id=%s | %s", log_prefix, row_id, exc, exc_info=True)
        return

    safe_payload = {k: v for k, v in payload.items() if v is not None}
    base_url = f"{settings.DATAVERSE_URL}/api/data/v9.2"
    upsert_url = f"{base_url}/{entity_name}({row_id})"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "OData-Version": "4.0",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.patch(upsert_url, json=safe_payload, headers=headers)
            if response.status_code == 204:
                logger.info("%s | SUCCESS | id=%s", log_prefix, row_id)
                return

            lookup_fields = {
                key: value
                for key, value in safe_payload.items()
                if key.endswith("@odata.bind")
            }

            logger.error(
                "%s | FAILED | id=%s | status=%s | lookups=%s | body=%s",
                log_prefix,
                row_id,
                response.status_code,
                lookup_fields,
                response.text,
            )
    except Exception as exc:
        logger.error("%s | ERROR | id=%s | %s", log_prefix, row_id, exc, exc_info=True)


async def insert_allrequest_to_dataverse(allrequest_row: Dict[str, Any]) -> None:
    """Upsert an etown_allrequests row to ets_allrequestses, binding the requestor's userprofile and provider or member record."""
    allrequest_id = allrequest_row.get("allrequestid")
    if not allrequest_id:
        return

    payload = {
        "ets_requestid": allrequest_row.get("requestid"),
        "ets_requestorid": allrequest_row.get("requestorid"),
        "ets_requestorname": allrequest_row.get("requestorname"),
        "ets_requestoraddress": allrequest_row.get("requestoraddress"),
        "ets_requestorlatitude": allrequest_row.get("requestorlatitude"),
        "ets_requestorlongitude": allrequest_row.get("requestorlongitude"),
        "ets_requestorrequeststatus": allrequest_row.get("requestorrequeststatus"),
        "ets_requeststatus": allrequest_row.get("requeststatus"),
        "ets_israting": allrequest_row.get("israting"),
        "ets_quantity": allrequest_row.get("quantity"),
        "ets_requestorfromdate": _fmt_ts(allrequest_row.get("requestorfromdate")),
        "ets_requestortodate": _fmt_ts(allrequest_row.get("requestortodate")),
        "ets_requestorservicedatetime": allrequest_row.get("requestorservicedatetime"),
        "ets_instructions": _serialize_instructions(allrequest_row.get("instructions")),
        "ets_requestorcontactnumber": allrequest_row.get("requestorcontactnumber"),
        "ets_requestoridentification": allrequest_row.get("requestoridentification"),
        "ets_requestorrequestfileurl": allrequest_row.get("requestorrequestfileurl"),
        "ets_requestorrole": allrequest_row.get("requestorrole"),
        "ets_servicetype": allrequest_row.get("servicetype"),
    }

    requestor_id = allrequest_row.get("requestorid")
    requestor_userprofile_id = allrequest_row.get("requestoruserprofileid")

    if requestor_userprofile_id:
        payload["ets_RequestorUserProfile@odata.bind"] = f"/ets_userprofiles({requestor_userprofile_id})"

        role_value = str(allrequest_row.get("requestorrole") or "").strip()
        if role_value == "1":
            provider_id = await _resolve_lookup_id(
                table_names=["etown_provider", "etown_providers"],
                id_column="providerid",
                userprofile_id=requestor_userprofile_id,
                contact_id=requestor_id,
            )
            if provider_id:
                payload["ets_RequestorProviderProfile@odata.bind"] = f"/ets_providers({provider_id})"
            else:
                logger.warning(
                    "DATAVERSE_INSERT_ALLREQUEST | LOOKUP_NOT_FOUND | field=ets_RequestorProviderProfile | userprofileid=%s | contactid=%s",
                    requestor_userprofile_id,
                    requestor_id,
                )
        elif role_value == "2":
            member_id = await _resolve_lookup_id(
                table_names=["etown_member"],
                id_column="memberid",
                userprofile_id=requestor_userprofile_id,
                contact_id=requestor_id,
            )
            if member_id:
                payload["ets_RequestorMemberProfile@odata.bind"] = f"/ets_members({member_id})"
            else:
                logger.warning(
                    "DATAVERSE_INSERT_ALLREQUEST | LOOKUP_NOT_FOUND | field=ets_RequestorMemberProfile | userprofileid=%s | contactid=%s",
                    requestor_userprofile_id,
                    requestor_id,
                )

    await _upsert_dataverse_row(
        entity_name=ALLREQUEST_ENTITY,
        row_id=allrequest_id,
        payload=payload,
        log_prefix="DATAVERSE_INSERT_ALLREQUEST",
    )


async def insert_requestservicelist_to_dataverse(service_row: Dict[str, Any]) -> None:
    """Upsert an etown_requestservicelists row to ets_requestservicelistses with booking, service and provider binds."""
    requestservicelists_id = service_row.get("requestservicelistsid")
    if not requestservicelists_id:
        return

    payload = {
        "ets_name": service_row.get("name"),
        "ets_quantity": service_row.get("quantity"),
        "ets_serviceprovideraddress": service_row.get("serviceprovideraddress"),
        "ets_serviceproviderlatitude": service_row.get("serviceproviderlatitude"),
        "ets_serviceproviderlongitude": service_row.get("serviceproviderlongitude"),
        "ets_serviceimage": service_row.get("serviceimageurl"),
        "ets_servicename": service_row.get("servicename"),
        "ets_serviceproviderid": service_row.get("serviceproviderid"),
        "ets_serviceid": service_row.get("providerservices"),
        "ets_price": service_row.get("price"),
    }

    booking_id = service_row.get("bookingid")
    provider_service_id = service_row.get("providerservices")
    service_provider_contact_id = service_row.get("serviceprovidercontactid")
    service_provider_userprofile_id = service_row.get("serviceprovideruserprofileid")

    if booking_id:
        payload["ets_BookingID@odata.bind"] = f"/{ALLREQUEST_ENTITY}({booking_id})"
    if provider_service_id:
        payload["ets_Providerservices@odata.bind"] = f"/ets_providerserviceses({provider_service_id})"
    if service_provider_contact_id:
        payload["ets_ProviderContact@odata.bind"] = f"/contacts({service_provider_contact_id})"
    if service_provider_userprofile_id:
        payload["ets_ProviderUserprofile@odata.bind"] = f"/ets_userprofiles({service_provider_userprofile_id})"

    await _upsert_dataverse_row(
        entity_name=REQUEST_SERVICE_LIST_ENTITY,
        row_id=requestservicelists_id,
        payload=payload,
        log_prefix="DATAVERSE_INSERT_REQUESTSERVICELIST",
    )


async def insert_notification_to_dataverse(notification_row: Dict[str, Any]) -> None:
    """Upsert an etown_notifications row to ets_notificationses with sender and receiver binds."""
    notification_id = notification_row.get("notificationsid")
    if not notification_id:
        return

    payload = {
        "ets_notificationcontent": notification_row.get("notificationcontent"),
        "ets_status": notification_row.get("notificationstatus"),
        "ets_type": notification_row.get("notificationtype"),
        "ets_role": notification_row.get("role"),
        "ets_name": notification_row.get("notificationtype") or "Booking",
    }

    sender_contact = notification_row.get("sendercontactid")
    sender_userprofile = notification_row.get("senderuserprofileid")
    receiver_contact = notification_row.get("receivercontactid")
    receiver_userprofile = notification_row.get("receiveruserprofileid")
    if sender_contact:
        payload["ets_SenderContactID@odata.bind"] = f"/contacts({sender_contact})"
    if sender_userprofile:
        payload["ets_SenderUserprofile@odata.bind"] = f"/ets_userprofiles({sender_userprofile})"
    if receiver_contact:
        payload["ets_ReceiverContactID@odata.bind"] = f"/contacts({receiver_contact})"
    if receiver_userprofile:
        payload["ets_ReceiverUserprofile@odata.bind"] = f"/ets_userprofiles({receiver_userprofile})"

    await _upsert_dataverse_row(
        entity_name=NOTIFICATION_ENTITY,
        row_id=notification_id,
        payload=payload,
        log_prefix="DATAVERSE_INSERT_NOTIFICATION",
    )


_INVITE_RELATION_TYPE_MAP = {"provider": 1, "member": 2}


def _coerce_invite_relation_type(raw: Any) -> Optional[int]:
    """Return the numeric invite relationship code from a digit string or a Provider/Member name."""
    if raw is None or isinstance(raw, int):
        return raw
    text = str(raw).strip()
    if text.isdigit():
        return int(text)
    return _INVITE_RELATION_TYPE_MAP.get(text.lower())


async def insert_providerrelation_to_dataverse(providerrelation_row: Dict[str, Any]) -> None:
    """Upsert an etown_providerrelation row to ets_providerrelations with provider, contact and userprofile binds."""
    providerrelation_id = providerrelation_row.get("providerrelationid")
    if not providerrelation_id:
        return

    pr_invite_rel_int = _coerce_invite_relation_type(
        providerrelation_row.get("invite_relationshiptype")
    )

    payload = {
        "ets_relationshiptype": providerrelation_row.get("relationshiptype"),
        "ets_inviterelationtype": pr_invite_rel_int,
        "ets_name": providerrelation_row.get("relationshiptypename"),
    }

    from_contact = providerrelation_row.get("fromcontactid")
    from_userprofile = providerrelation_row.get("fromuserprofileid")
    to_contact = providerrelation_row.get("tocontactid")
    to_userprofile = providerrelation_row.get("touserprofileid")
    from_provider = providerrelation_row.get("fromproviderid")
    if from_provider:
        payload["ets_FromProvider@odata.bind"] = f"/ets_providers({from_provider})"
    if from_contact:
        payload["ets_FromContactID@odata.bind"] = f"/contacts({from_contact})"
    if from_userprofile:
        payload["ets_FromUserProfile@odata.bind"] = f"/ets_userprofiles({from_userprofile})"
    if to_contact:
        payload["ets_ToContact@odata.bind"] = f"/contacts({to_contact})"
    if to_userprofile:
        payload["ets_ToUserProfile@odata.bind"] = f"/ets_userprofiles({to_userprofile})"

    await _upsert_dataverse_row(
        entity_name=PROVIDER_RELATION_ENTITY,
        row_id=providerrelation_id,
        payload=payload,
        log_prefix="DATAVERSE_INSERT_PROVIDERRELATION",
    )


async def insert_memberrelation_to_dataverse(memberrelation_row: Dict[str, Any]) -> None:
    """Upsert an etown_memberrelation row to ets_memberrelations with member, contact and userprofile binds."""
    memberrelation_id = memberrelation_row.get("memberrelationid")
    if not memberrelation_id:
        return

    invite_rel_int = _coerce_invite_relation_type(
        memberrelation_row.get("invite_relationshiptype")
    )

    payload = {
        "ets_relationshiptype": memberrelation_row.get("relationshiptype"),
        "ets_inviterelationtype": invite_rel_int,
        "ets_name": memberrelation_row.get("invite_relationshiptypename"),
    }

    from_member = memberrelation_row.get("from_memberid")
    userprofile = memberrelation_row.get("userprofileid")
    from_contact = memberrelation_row.get("from_contactid")
    from_userprofile = memberrelation_row.get("from_userprofile")
    to_contact = memberrelation_row.get("to_contactid")
    to_userprofile = memberrelation_row.get("to_userprofile")
    from_userprofile_id = from_userprofile or userprofile

    if from_userprofile and userprofile and str(from_userprofile) != str(userprofile):
        logger.warning(
            "Member relation has mismatched userprofile IDs | userprofileid=%s | from_userprofile=%s",
            userprofile,
            from_userprofile,
        )

    if from_member:
        payload["ets_FromMember@odata.bind"] = f"/ets_members({from_member})"
    if from_contact:
        payload["ets_FromContactID@odata.bind"] = f"/contacts({from_contact})"
    if from_userprofile_id:
        payload["ets_FromUserProfile@odata.bind"] = f"/ets_userprofiles({from_userprofile_id})"
    if to_contact:
        payload["ets_ToContact@odata.bind"] = f"/contacts({to_contact})"
    if to_userprofile:
        payload["ets_ToUserProfile@odata.bind"] = f"/ets_userprofiles({to_userprofile})"

    await _upsert_dataverse_row(
        entity_name=MEMBER_RELATION_ENTITY,
        row_id=memberrelation_id,
        payload=payload,
        log_prefix="DATAVERSE_INSERT_MEMBERRELATION",
    )


def _serialize_instructions(value: Any) -> str | None:
    """Return instructions as a JSON string, passing strings through and returning None when empty or unserialisable."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        logger.warning("booking mirror: instructions payload is not JSON-serialisable; storing null", exc_info=True)
        return None


async def update_allrequest_instructions_to_dataverse(
    allrequest_id: str, instructions: Any
) -> None:
    """Patch only ets_instructions on an ets_allrequestses record with the serialised instructions."""
    if not allrequest_id:
        return

    payload = {"ets_instructions": _serialize_instructions(instructions)}

    await _upsert_dataverse_row(
        entity_name=ALLREQUEST_ENTITY,
        row_id=allrequest_id,
        payload=payload,
        log_prefix="DATAVERSE_UPDATE_ALLREQUEST_INSTRUCTIONS",
    )


async def update_allrequest_servicetype_to_dataverse(
    allrequest_id: str, servicetype: Any
) -> None:
    """Patch only ets_servicetype on an ets_allrequestses record."""
    if not allrequest_id:
        return

    payload = {"ets_servicetype": servicetype if servicetype else None}

    await _upsert_dataverse_row(
        entity_name=ALLREQUEST_ENTITY,
        row_id=allrequest_id,
        payload=payload,
        log_prefix="DATAVERSE_UPDATE_ALLREQUEST_SERVICETYPE",
    )


async def update_allrequest_to_dataverse(allrequest_row: Dict[str, Any]) -> None:
    """Patch booking status, cancel reason, timestamps and rating flag from an etown_allrequests row to ets_allrequestses."""
    allrequest_id = allrequest_row.get("allrequestid")
    if not allrequest_id:
        return

    payload = {
        "ets_requeststatus": allrequest_row.get("requeststatus"),
        "ets_requestorrequeststatus": allrequest_row.get("requestorrequeststatus"),
        "ets_cancelreason": allrequest_row.get("cancelreason"),
        "ets_requestedbookingtime": _fmt_ts(allrequest_row.get("bookingacceptedtime")),
        "ets_acceptedbookingtime": _fmt_ts(allrequest_row.get("bookingcompletedtime")),
        "ets_bookingcancelledtime": _fmt_ts(allrequest_row.get("bookingcancelledtime")),
        "ets_israting": allrequest_row.get("israting"),
    }

    await _upsert_dataverse_row(
        entity_name=ALLREQUEST_ENTITY,
        row_id=allrequest_id,
        payload=payload,
        log_prefix="DATAVERSE_UPDATE_ALLREQUEST",
    )


async def insert_invitation_to_dataverse(invitation_row: Dict[str, Any]) -> None:
    """Upsert an etown_invitation row to adx_invitations with inviter, invitee and member binds."""
    invitation_id = invitation_row.get("invitationid")
    if not invitation_id:
        return

    raw_invite_role = invitation_row.get("new_inviterole")
    invite_role_int = int(raw_invite_role) if raw_invite_role is not None and str(raw_invite_role).strip().isdigit() else None

    payload = {
        "ets_invitationtype": invitation_row.get("invitationtype"),
        "ets_inviterole": invitation_row.get("invite_role"),
        "ets_invite_role": invite_role_int,
        "ets_invitestatus": invitation_row.get("invitestatus"),
        "ets_suffixid": invitation_row.get("invitationcode"),
        "ets_receivername": invitation_row.get("inviteuserprofilename"),
        "ets_dateofcreation": _fmt_ts(invitation_row.get("createdon")),
    }

    inviter_contact = invitation_row.get("invitercontact")
    inviter_userprofile = invitation_row.get("inviteruserprofile")
    invite_userprofile = invitation_row.get("inviteuserprofile")
    if inviter_userprofile:
        payload["ets_InviterUserProfile@odata.bind"] = f"/ets_userprofiles({inviter_userprofile})"
    if invite_userprofile:
        payload["ets_InviteContactUserProfile@odata.bind"] = f"/ets_userprofiles({invite_userprofile})"
    if inviter_contact:
        inviter_member_id = await _resolve_lookup_id(
            table_names=["etown_member"],
            id_column="memberid",
            userprofile_id=None,
            contact_id=inviter_contact,
        )
        if inviter_member_id:
            payload["ets_InviteTo@odata.bind"] = f"/ets_members({inviter_member_id})"
        else:
            logger.warning(
                "DATAVERSE_INSERT_INVITATION | LOOKUP_NOT_FOUND | field=ets_InviteTo | contactid=%s",
                inviter_contact,
            )

    await _upsert_dataverse_row(
        entity_name=INVITATION_ENTITY,
        row_id=invitation_id,
        payload=payload,
        log_prefix="DATAVERSE_INSERT_INVITATION",
    )
