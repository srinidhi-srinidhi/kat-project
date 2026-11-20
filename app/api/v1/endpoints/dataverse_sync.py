"""Dataverse Sync Endpoints - HTTP routes for back-office provider corrections mirrored to the CRM.

HTTP layer for /api/dataverse-sync: validates bearer-authenticated requests for verification, address, rename, source, unverified-listing, soft-delete, review-approval and town-image-review changes, delegates the Postgres writes to dataverse_sync_utils and schedules each Dataverse mirror as a background task.

Features:
    - Provider Verification Toggle: Verifies or un-verifies a provider across five tables in one transaction
    - Address Fan-out: Applies an address change to every table caching it; omitted fields untouched
    - Business-name Rename: Renames one listing by providerid across provider and both service tables
    - Source Correction: Rewrites the source of every listing on a mobile number
    - Linked Unverified: Finds unverified listings, links one to a member, or moves its mobile number
    - Provider Soft Delete: Flags a provider deleted by providerid or mobile plus business name
    - Provider Review Approval: Applies a worked review row in one transaction, then re-embeds
    - Town Image Review: Sets a town review's status; approval makes its image the town profile's townimage
    - Dataverse Mirror: Each change is mirrored to Dataverse as a BackgroundTask
    - Bearer Authentication: Shared bearer token required via verify_token dependency
    - Input Validation: Pydantic V2 request body validation
    - Response Formatting: Values written plus per-table row counts, for traceability

Endpoints:
    POST /dataverse-sync/verifiedproviders: Verify or un-verify a provider
    POST /dataverse-sync/address: Update a contact's address everywhere it is cached
    POST /dataverse-sync/businessname: Rename the provider named by providerid across the three provider tables
    POST /dataverse-sync/source: Rewrite the source of every listing on a mobile number
    POST /dataverse-sync/linked_unverified: action_mode 'member' links, 'update' moves the number, else 'find'
    DELETE /dataverse-sync/provider: Soft-delete a provider by providerid or mobile number plus business name
    POST /dataverse-sync/provider_review_approval: Apply a worked review row, re-embed, mirror to Dataverse
    POST /dataverse-sync/townreview: Set a town review's status; on approval set and mirror the town's image, notify the requester

Classes:
    VerifiedProviderRequest: Request body carrying the provider GUID and verification flag
    VerifiedProviderResponse: Result with the values written and per-table row counts
    AddressRequest: Request body carrying the contact GUID and optional address fields
    AddressResponse: Result with per-table row counts and the tables skipped
    BusinessNameRequest: Request body carrying the provider GUID and the new business name
    BusinessNameResponse: Result with the old and new name, row counts, and the tables skipped
    SourceRequest: Request body carrying the provider's mobile number and the new source value
    SourceResponse: Result with the previous sources, the listings updated, and row counts
    LinkedUnverifiedRequest: Request body carrying the action_mode, mobile number, business name
    LinkedUnverifiedProvider: One provider listing returned by a find
    LinkedUnverifiedResponse: Result with the business names, numbers, and verification state found
    MobileNumberUpdateResponse: Result of a mobile-number move, with per-table row counts
    MemberLinkResponse: Result of attaching a listing to a member account
    DeleteProviderRequest: Request body carrying providerid, or mobile number and business name
    DeleteProviderResponse: Result with per-table flag counts and what was kept
    TownReviewStatusRequest: Request body carrying townreviewid, townprofileid, imageurl and status
    TownReviewStatusResponse: Result with the status written, image set and per-table row counts

Functions:
    verified_providers_endpoint(): Handle POST /dataverse-sync/verifiedproviders
    address_endpoint(): Handle POST /dataverse-sync/address
    businessname_endpoint(): Handle POST /dataverse-sync/businessname
    source_endpoint(): Handle POST /dataverse-sync/source
    linked_unverified_endpoint(): Handle POST /dataverse-sync/linked_unverified, dispatching on action_mode
    _linked_unverified_update(): The mobile-number-update mode of /linked_unverified
    _linked_unverified_link_member(): The member-link mode of /linked_unverified
    delete_provider_endpoint(): Handle DELETE /dataverse-sync/provider
    provider_review_approval_endpoint(): Handle POST /dataverse-sync/provider_review_approval
    town_review_status_endpoint(): Handle POST /dataverse-sync/townreview

Dependencies:
    - fastapi: API framework, routing, and dependency injection
    - pydantic: Request/response model validation
    - app.core.security: Shared bearer token verification
    - app.core.logging: Centralized logging infrastructure
    - app.schemas.api: ProviderReviewApprovalRequest and ProviderReviewApprovalResponse models
    - app.etown.dataverse_sync.dataverse_sync_utils: Postgres writes, lookups and re-embedding
    - app.etown.dataverse_sync.syncing: Dataverse background mirrors for each change
    - app.etown.mytown.mytown_syncing: sync_town_profile_image_to_dataverse() for an approved town image
    - app.etown.notifications.triggers: notify_town_image_approved() pushing the requester on approval

Updates:
    04-Aug-2026: Created /dataverse-sync router; added POST /verifiedproviders, POST /address, DELETE /provider.
    06-Aug-2026: Added POST /linked_unverified with find, update and member-link modes.
    11-Aug-2026: Added POST /businessname, renaming a provider across the three tables.
    20-Aug-2026: VerifiedProviderResponse renames providertype to Etown_providertype.
    16-Sep-2026: Added POST /source, rewriting the source of every listing on a mobile number.
    17-Sep-2026: DELETE /provider is now a soft delete.
    21-Sep-2026: DELETE /provider also accepts a providerid.
    22-Sep-2026: Added POST /provider_review_approval for the provider-approval flow's Postgres writes.
    30-Sep-2026: POST /provider_review_approval takes interested.
    08-Oct-2026: Added POST /townreview, applying a town image review, setting the town's image and notifying the requester on approval; mirrors the review to ets_townreview.
    09-Oct-2026: POST /townreview no longer mirrors review status back to Dataverse; only the town profile image is synced.
"""

import uuid
from datetime import datetime
from typing import List, Optional, Union

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Security
from pydantic import BaseModel, Field, model_validator

from app.core.logging import logger
from app.core.security import verify_token
from app.schemas.api import (
    ProviderReviewApprovalRequest,
    ProviderReviewApprovalResponse,
)
from app.etown.dataverse_sync.dataverse_sync_utils import (
    FIND_PROVIDERS_LIMIT,
    apply_provider_review_approval,
    apply_town_review_status,
    delete_provider_by_mobile_and_business,
    find_providers,
    link_provider_to_member,
    refresh_address_embeddings,
    refresh_businessname_embeddings,
    refresh_content_vectors,
    set_provider_verification,
    update_address,
    update_provider_businessname,
    update_provider_mobilenumber,
    update_provider_source,
)
from app.etown.dataverse_sync.syncing import (
    sync_address_to_dataverse,
    sync_businessname_to_dataverse,
    sync_member_link_to_dataverse,
    sync_mobilenumber_to_dataverse,
    sync_provider_review_approval_to_dataverse,
    sync_provider_soft_delete_to_dataverse,
    sync_provider_verification_to_dataverse,
    sync_source_to_dataverse,
)
from app.etown.mytown.mytown_syncing import (
    sync_town_profile_image_to_dataverse,
)
from app.etown.notifications.triggers import notify_town_image_approved

router = APIRouter()


class VerifiedProviderRequest(BaseModel):
    """Request body for the verifiedproviders endpoint."""
    providerid: str = Field(..., min_length=1, description="Provider GUID whose verification state changes")
    verification: bool = Field(
        ...,
        description=(
            "true marks the provider verified ('etown_verified' / 'verified' / verified = true); "
            "false marks it non-verified ('etown_nonverified' / 'non-verified' / verified = false)."
        ),
    )


class VerifiedProviderResponse(BaseModel):
    """Result of changing a provider's verification state."""
    status: str = Field(..., description="'verified' or 'non-verified', matching the request")
    providerid: str = Field(..., description="Provider GUID that was updated")
    verification: bool = Field(..., description="Verification state that was applied")
    Etown_providertype: str = Field(..., description="providertype value written to the provider tables")
    publicsearchprovider: str = Field(..., description="publicsearchprovider value written to the provider tables")
    userprofileid: Optional[str] = Field(None, description="User profile linked to the provider, when present")
    contactid: Optional[str] = Field(None, description="Contact behind that user profile, when present")
    provider_updated: int = Field(..., description="etown_provider rows updated")
    providerservices_updated: int = Field(..., description="etown_providerservices rows updated")
    providerservices_copy_updated: int = Field(..., description="etown_providerservices_copy rows updated")
    userprofile_updated: int = Field(..., description="etown_userprofile rows whose verified flag was set")
    contact_updated: int = Field(..., description="etown_contact rows whose verified flag was set")
    dataverse_sync: str = Field(
        "scheduled",
        description="'scheduled' — the Dataverse mirror runs as a background task after the response",
    )


@router.post(
    "/verifiedproviders",
    response_model=VerifiedProviderResponse,
    status_code=200,
    summary="Verify or un-verify a provider",
    description=(
        "With verification = true, sets publicsearchprovider = 'verified' and "
        "providertype = 'etown_verified' on the provider and on all of its rows in "
        "etown_providerservices and etown_providerservices_copy, then sets verified = true on "
        "the user profile and the contact the provider row names — both ids read off that row, "
        "so the contact is written even when the listing carries no userprofileid. With "
        "verification = false the same rows are set to 'non-verified' / 'etown_nonverified' "
        "and verified = false. All writes happen in one transaction."
    ),
    responses={
        200: {"description": "Verification state applied; returns the rows updated per table"},
        404: {"description": "Provider not found"},
        500: {"description": "Internal server error"},
    },
)
async def verified_providers_endpoint(
    body: VerifiedProviderRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> VerifiedProviderResponse:
    """Handle POST /api/dataverse-sync/verifiedproviders: set a provider's verification cascade and schedule the Dataverse mirror.

    Raises:
        HTTPException: 422 blank providerid, 404 unknown provider, 500 database failure.
    """
    providerid = body.providerid.strip()
    if not providerid:
        raise HTTPException(status_code=422, detail="providerid must not be blank")

    try:
        result = await set_provider_verification(providerid, body.verification)
    except Exception as exc:
        logger.error(
            f"verified_providers - failed providerid={providerid} "
            f"verification={body.verification} error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to update provider verification")

    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"Provider '{providerid}' not found")

    background_tasks.add_task(
        sync_provider_verification_to_dataverse,
        providerid=providerid,
        verified=body.verification,
        userprofileid=result.get("userprofileid"),
        contactid=result.get("contactid"),
        providerservices_ids=result.get("providerservices_ids") or [],
    )

    return VerifiedProviderResponse(
        status="verified" if body.verification else "non-verified",
        providerid=providerid,
        verification=body.verification,
        Etown_providertype=result["providertype"],
        publicsearchprovider=result["publicsearchprovider"],
        userprofileid=result.get("userprofileid"),
        contactid=result.get("contactid"),
        provider_updated=result.get("provider_updated", 0),
        providerservices_updated=result.get("providerservices_updated", 0),
        providerservices_copy_updated=result.get("providerservices_copy_updated", 0),
        userprofile_updated=result.get("userprofile_updated", 0),
        contact_updated=result.get("contact_updated", 0),
        dataverse_sync="scheduled",
    )


class AddressRequest(BaseModel):
    """Request body for the address endpoint. Every address field is optional."""
    contactid: str = Field(..., min_length=1, description="Contact GUID whose address changes")
    address: Optional[str] = Field(None, description="Street address; omit or null to leave unchanged")
    city: Optional[str] = Field(None, description="City name; omit or null to leave unchanged")
    latitude: Optional[float] = Field(None, ge=-90, le=90, description="Latitude in degrees")
    longitude: Optional[float] = Field(None, ge=-180, le=180, description="Longitude in degrees")
    areaname: Optional[str] = Field(None, description="Area / locality name; omit or null to leave unchanged")
    townname: Optional[str] = Field(None, description="Town name; omit or null to leave unchanged")

    @model_validator(mode="after")
    def _require_one_field(self) -> "AddressRequest":
        """Reject a request that carries nothing to update."""
        if not any(
            value is not None
            for value in (
                self.address, self.city, self.latitude,
                self.longitude, self.areaname, self.townname,
            )
        ):
            raise ValueError(
                "Provide at least one of: address, city, latitude, longitude, areaname, townname"
            )
        return self


class AddressResponse(BaseModel):
    """Result of an address update across the tables that cache it."""
    status: str = Field(..., description="Always 'updated' on success")
    contactid: str = Field(..., description="Contact GUID that was updated")
    fields: List[str] = Field(..., description="Address fields supplied by the caller")
    contact_updated: int = Field(..., description="etown_contact rows updated")
    provider_updated: int = Field(..., description="etown_provider rows updated")
    providerservices_updated: int = Field(..., description="etown_providerservices rows updated")
    providerservices_copy_updated: int = Field(..., description="etown_providerservices_copy rows updated")
    etownprovider_updated: int = Field(..., description="etown_etownprovider rows updated (areaname/townname only)")
    skipped: List[str] = Field(
        default_factory=list,
        description=(
            "Tables not written: etown_userprofile and etown_member hold no address columns; "
            "others appear when none of their columns were supplied"
        ),
    )
    dataverse_sync: str = Field(
        "scheduled",
        description="'scheduled' — the Dataverse mirror runs as a background task after the response",
    )
    embeddings_refresh: str = Field(
        "not_needed",
        description=(
            "'scheduled' when address or city changed, so the content_vector of the rows that "
            "index them is regenerated in the background; 'not_needed' otherwise"
        ),
    )


@router.post(
    "/address",
    response_model=AddressResponse,
    status_code=200,
    summary="Update a contact's address everywhere it is cached",
    description=(
        "Applies the supplied address fields to every table that caches them for the contact: "
        "etown_contact (address, city, latitude, longitude), etown_provider (all six fields), "
        "etown_providerservices and etown_providerservices_copy (all but townname), and "
        "etown_etownprovider (areaname, townname). Omitted fields keep their current values. "
        "etown_userprofile and etown_member have no address columns and are reported in "
        "'skipped'. All writes happen in one transaction; the Dataverse mirror runs afterwards "
        "as a background task."
    ),
    responses={
        200: {"description": "Address applied; returns the rows updated per table"},
        404: {"description": "Contact not found"},
        422: {"description": "No address field supplied"},
        500: {"description": "Internal server error"},
    },
)
async def address_endpoint(
    body: AddressRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> AddressResponse:
    """Handle POST /api/dataverse-sync/address: update a contact's cached address everywhere, then schedule the mirror and embeddings refresh.

    Raises:
        HTTPException: 422 blank contactid, 404 unknown contact, 500 database failure.
    """
    contactid = body.contactid.strip()
    if not contactid:
        raise HTTPException(status_code=422, detail="contactid must not be blank")

    try:
        result = await update_address(
            contactid=contactid,
            address=body.address,
            city=body.city,
            latitude=body.latitude,
            longitude=body.longitude,
            areaname=body.areaname,
            townname=body.townname,
        )
    except Exception as exc:
        logger.error(
            f"address - failed contactid={contactid} error={exc}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to update address")

    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"Contact '{contactid}' not found")

    background_tasks.add_task(
        sync_address_to_dataverse,
        contactid=contactid,
        address=body.address,
        city=body.city,
        latitude=body.latitude,
        longitude=body.longitude,
        areaname=body.areaname,
        townname=body.townname,
        provider_ids=result.get("provider_ids") or [],
        providerservices_ids=result.get("providerservices_ids") or [],
    )

    embedded_fields = [f for f in result.get("fields", []) if f in ("address", "city")]
    background_tasks.add_task(
        refresh_address_embeddings,
        contactid=contactid,
        fields=result.get("fields", []),
        provider_ids=result.get("provider_ids") or [],
        providerservices_copy_ids=result.get("providerservices_copy_ids") or [],
    )

    return AddressResponse(
        status="updated",
        contactid=contactid,
        fields=result.get("fields", []),
        contact_updated=result.get("contact_updated", 0),
        provider_updated=result.get("provider_updated", 0),
        providerservices_updated=result.get("providerservices_updated", 0),
        providerservices_copy_updated=result.get("providerservices_copy_updated", 0),
        etownprovider_updated=result.get("etownprovider_updated", 0),
        skipped=result.get("skipped", []),
        dataverse_sync="scheduled",
        embeddings_refresh="scheduled" if embedded_fields else "not_needed",
    )


class BusinessNameRequest(BaseModel):
    """Request body for the businessname endpoint."""
    providerid: str = Field(..., min_length=1, description="Provider GUID whose business name changes")
    businessname: str = Field(
        ...,
        min_length=1,
        description="The new business name; stored trimmed and must not be blank or whitespace",
    )


class BusinessNameResponse(BaseModel):
    """Result of renaming a provider across the tables that cache its business name."""
    status: str = Field(..., description="Always 'updated' on success")
    providerid: str = Field(..., description="Provider GUID that was renamed")
    businessname: str = Field(..., description="New business name that was written")
    previous_businessname: Optional[str] = Field(
        None, description="Business name the listing held before the change"
    )
    contactid: Optional[str] = Field(None, description="Contact the listing belongs to, when it has one")
    userprofileid: Optional[str] = Field(None, description="User profile behind the listing, when present")
    provider_updated: int = Field(..., description="etown_provider rows updated")
    providerservices_updated: int = Field(..., description="etown_providerservices rows updated")
    providerservices_copy_updated: int = Field(
        ..., description="etown_providerservices_copy rows updated"
    )
    skipped: List[str] = Field(
        default_factory=list,
        description=(
            "Tables not written: etown_contact and etown_userprofile hold no business name; "
            "etown_etownprovider, etown_offers, etown_offerings and etown_ratings keep their "
            "cached copy of the old name"
        ),
    )
    dataverse_sync: str = Field(
        "scheduled",
        description="'scheduled' — the Dataverse mirror runs as a background task after the response",
    )
    embeddings_refresh: str = Field(
        "scheduled",
        description=(
            "'scheduled' — businessname leads the embedding text of etown_provider and "
            "etown_providerservices_copy, so their content_vector is regenerated in the background"
        ),
    )


@router.post(
    "/businessname",
    response_model=BusinessNameResponse,
    status_code=200,
    summary="Change a provider's business name",
    description=(
        "Writes the new business name to the provider identified by `providerid` and to its rows "
        "in etown_providerservices and etown_providerservices_copy — the three tables the listing "
        "is read from. Selecting by providerid renames exactly one listing, unlike the "
        "business-name-keyed operations on /linked_unverified, where a name shared by several "
        "listings touches all of them.\n\n"
        "Not written, and reported in `skipped`: etown_contact and etown_userprofile hold no "
        "business name (the contact carries the person's firstname, the profile its name), while "
        "etown_etownprovider.providerbusinessname, etown_offers, etown_offerings and "
        "etown_ratings keep their cached copy of the old name.\n\n"
        "`contactidname` is left alone on all three tables: it is filled from the contact in the "
        "onboarding path and from the business name in others, so rewriting it would overwrite a "
        "person's name with a business name on part of the data.\n\n"
        "`businessname` is the only column written — nothing else on the three rows changes, apart "
        "from the `modifieddate` stamp every write in this router sets.\n\n"
        "All writes happen in one transaction. Afterwards two background tasks run: the Dataverse "
        "mirror, which PATCHes `ets_businessname` on the provider and on each provider-service "
        "record and no other field, and an embedding refresh — businessname is the leading field "
        "of the embedding text for etown_provider and etown_providerservices_copy, so search would "
        "otherwise keep matching the old name."
    ),
    responses={
        200: {"description": "Business name applied; returns the rows updated per table"},
        404: {"description": "Provider not found"},
        422: {"description": "providerid or businessname is blank"},
        500: {"description": "Internal server error"},
    },
)
async def businessname_endpoint(
    body: BusinessNameRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> BusinessNameResponse:
    """Handle POST /api/dataverse-sync/businessname: rename one provider listing, then schedule the mirror and embeddings refresh.

    Raises:
        HTTPException: 422 blank providerid or businessname, 404 unknown provider, 500 database failure.
    """
    providerid = body.providerid.strip()
    businessname = body.businessname.strip()
    if not providerid:
        raise HTTPException(status_code=422, detail="providerid must not be blank")
    if not businessname:
        raise HTTPException(status_code=422, detail="businessname must not be blank")

    try:
        result = await update_provider_businessname(
            providerid=providerid,
            businessname=businessname,
        )
    except Exception as exc:
        logger.error(
            f"businessname - failed providerid={providerid} business='{businessname}' error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to update business name")

    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"Provider '{providerid}' not found")

    background_tasks.add_task(
        sync_businessname_to_dataverse,
        businessname=result["businessname"],
        provider_ids=[providerid],
        providerservices_ids=result.get("providerservices_ids") or [],
    )

    background_tasks.add_task(
        refresh_businessname_embeddings,
        provider_ids=[providerid],
        providerservices_copy_ids=result.get("providerservices_copy_ids") or [],
    )

    return BusinessNameResponse(
        status="updated",
        providerid=providerid,
        businessname=result["businessname"],
        previous_businessname=result.get("previous_businessname"),
        contactid=result.get("contactid"),
        userprofileid=result.get("userprofileid"),
        provider_updated=result.get("provider_updated", 0),
        providerservices_updated=result.get("providerservices_updated", 0),
        providerservices_copy_updated=result.get("providerservices_copy_updated", 0),
        skipped=result.get("skipped", []),
        dataverse_sync="scheduled",
        embeddings_refresh="scheduled",
    )


class SourceRequest(BaseModel):
    """Request body for the source endpoint."""
    mobilenumber: str = Field(
        ...,
        min_length=1,
        description="Provider's mobile number, any common format; only its last 10 digits are matched",
    )
    source: str = Field(
        ...,
        min_length=1,
        description=(
            "New source value, stored as sent (trimmed). The values already in the data are "
            "'Onboard' (POST /provider/onboard and the edit path) and 'FOS' (the field capture "
            "behind POST /provider/unverified_provider); the column is free text, so anything "
            "else is accepted and written verbatim."
        ),
    )


class SourceResponse(BaseModel):
    """Result of rewriting a provider's source across the tables that hold it."""
    status: str = Field(..., description="Always 'updated' on success")
    mobilenumber: str = Field(..., description="Mobile number used to match the providers")
    source: str = Field(..., description="New source value that was written")
    previous_sources: List[str] = Field(
        default_factory=list,
        description="Distinct source values the matched listings held before the change",
    )
    provider_ids: List[str] = Field(..., description="Provider GUIDs that were updated")
    businessnames: List[str] = Field(
        default_factory=list,
        description="Business names of the updated listings, so the caller can see what was touched",
    )
    provider_updated: int = Field(..., description="etown_provider rows updated")
    providerservices_updated: int = Field(..., description="etown_providerservices rows updated")
    providerservices_copy_updated: int = Field(
        ..., description="etown_providerservices_copy rows updated"
    )
    skipped: List[str] = Field(
        default_factory=list,
        description="Tables not written because they carry no source column",
    )
    dataverse_sync: str = Field(
        "scheduled",
        description="'scheduled' — the Dataverse mirror runs as a background task after the response",
    )


@router.post(
    "/source",
    response_model=SourceResponse,
    status_code=200,
    summary="Change a provider's source",
    description=(
        "Writes the new `source` to every provider listing on `mobilenumber` and to that "
        "listing's rows in etown_providerservices and etown_providerservices_copy — the three "
        "tables carrying a source column. etown_contact, etown_userprofile and "
        "etown_etownprovider have no such column and are reported in `skipped`.\n\n"
        "`source` records how a listing was captured: 'Onboard' for POST /provider/onboard and "
        "the edit path, 'FOS' for the field capture behind POST /provider/unverified_provider. "
        "The provider lookups read it back as a filter — a listing with `source = 'FOS'` is "
        "reported as 'added_by_fos' and is skipped by the verified-promotion path — so a value "
        "corrected in one table only would make the same listing answer differently depending on "
        "which table a query reads.\n\n"
        "The mobile number is the only selector, matched on its last 10 digits so "
        "'+91 90000 00000' and '09000000000' both find '9000000000'. A person holding several "
        "listings on one number has all of them updated; `provider_ids` and `businessnames` come "
        "back so the caller can see exactly which.\n\n"
        "The value is stored trimmed but otherwise verbatim — the column is free text and the "
        "rest of the codebase compares against 'Onboard' and 'FOS' exactly, so the case is not "
        "normalised. `source` is the only column written, apart from the `modifieddate` stamp "
        "every write in this router sets.\n\n"
        "All writes happen in one transaction. Afterwards the Dataverse mirror runs as a "
        "background task, PATCHing `ets_source` on the provider and on each provider-service "
        "record and no other field. No embedding refresh is scheduled: source is part of no "
        "table's embedding text, so search is unaffected."
    ),
    responses={
        200: {"description": "Source applied; returns the rows updated per table"},
        404: {"description": "No provider found on that mobile number"},
        422: {"description": "mobilenumber holds fewer than 10 digits, or source is blank"},
        500: {"description": "Internal server error"},
    },
)
async def source_endpoint(
    body: SourceRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> SourceResponse:
    """Handle POST /api/dataverse-sync/source: rewrite the source of every listing on a mobile number and schedule the mirror.

    Raises:
        HTTPException: 422 bad mobile or blank source, 404 no provider, 500 database failure.
    """
    mobilenumber = body.mobilenumber.strip()
    source = body.source.strip()
    if not source:
        raise HTTPException(status_code=422, detail="source must not be blank")

    try:
        result = await update_provider_source(
            mobilenumber=mobilenumber,
            source=source,
        )
    except Exception as exc:
        logger.error(
            f"source - failed mobile={mobilenumber} source='{source}' error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to update source")

    if result.get("bad_mobile"):
        raise HTTPException(
            status_code=422, detail="mobilenumber must contain at least 10 digits"
        )

    if not result.get("found"):
        raise HTTPException(
            status_code=404, detail=f"No provider found for mobile number '{mobilenumber}'"
        )

    background_tasks.add_task(
        sync_source_to_dataverse,
        source=result["source"],
        provider_ids=result.get("provider_ids") or [],
        providerservices_ids=result.get("providerservices_ids") or [],
    )

    return SourceResponse(
        status="updated",
        mobilenumber=mobilenumber,
        source=result["source"],
        previous_sources=result.get("previous_sources", []),
        provider_ids=result.get("provider_ids", []),
        businessnames=result.get("businessnames", []),
        provider_updated=result.get("provider_updated", 0),
        providerservices_updated=result.get("providerservices_updated", 0),
        providerservices_copy_updated=result.get("providerservices_copy_updated", 0),
        skipped=result.get("skipped", []),
        dataverse_sync="scheduled",
    )


class DeleteProviderRequest(BaseModel):
    """Request body for the provider delete endpoint. Two ways to name the provider.

    Send ``providerid`` for exactly the listing behind that GUID, or send ``mobilenumber`` and
    ``businessname`` together to match on the pair. ``providerid`` wins when both are supplied.
    """
    providerid: Optional[str] = Field(
        None,
        description="Provider GUID; identifies one listing on its own, and takes precedence over the pair below",
    )
    mobilenumber: Optional[str] = Field(
        None,
        description="Provider's mobile number, any common format; send together with businessname",
    )
    businessname: Optional[str] = Field(
        None,
        description="Provider's business name (case-insensitive match); send together with mobilenumber",
    )

    @model_validator(mode="after")
    def _require_an_identifier(self) -> "DeleteProviderRequest":
        """Reject a request that has neither a providerid nor both mobilenumber and businessname."""
        if (self.providerid or "").strip():
            return self
        if (self.mobilenumber or "").strip() and (self.businessname or "").strip():
            return self
        raise ValueError("Provide providerid, or both mobilenumber and businessname")


class DeleteProviderResponse(BaseModel):
    """Result of a provider soft-delete.

    The ``*_deleted`` counts keep their names so existing callers still parse the response, but
    every one of them counts rows FLAGGED, not rows removed: nothing is physically deleted.
    """
    status: str = Field(..., description="Always 'deleted' on success")
    mobilenumber: Optional[str] = Field(
        None,
        description=(
            "Mobile number of the provider that was flagged — the one sent, or the one read off "
            "the row when the request named a providerid"
        ),
    )
    businessname: Optional[str] = Field(
        None,
        description=(
            "Business name of the provider that was flagged — the one sent, or the one read off "
            "the row when the request named a providerid"
        ),
    )
    deletiondate: Optional[datetime] = Field(
        None,
        description="The IST timestamp written to deletiondate on every flagged row",
    )
    provider_ids: List[str] = Field(..., description="Provider GUIDs that were flagged deleted")
    provider_deleted: int = Field(..., description="etown_provider rows flagged deleted")
    providerservices_deleted: int = Field(..., description="etown_providerservices rows flagged deleted")
    providerservices_copy_deleted: int = Field(..., description="etown_providerservices_copy rows flagged deleted")
    etownprovider_deleted: int = Field(..., description="etown_etownprovider rows flagged deleted")
    provider_details_deleted: int = Field(0, description="etown_provider_details rows flagged deleted")
    userprofile_deleted: int = Field(..., description="etown_userprofile rows flagged deleted")
    userprofile_kept: List[str] = Field(
        default_factory=list,
        description="User profiles left live because another provider or a member row still needs them",
    )
    contacts_kept: List[str] = Field(
        default_factory=list,
        description="Contacts deliberately left live — the contact row backs the person's member account",
    )
    dataverse_sync: str = Field(
        "scheduled",
        description="'scheduled' — the Dataverse flag mirror runs as a background task after the response",
    )


class LinkedUnverifiedRequest(BaseModel):
    """Request body for the linked_unverified endpoint.

    Every field except ``action_mode`` changes role with the mode: on a find ``mobilenumber`` and
    ``businessname`` are optional filters, while both write modes require them — the number as the
    value to write, the business name to select the listing.
    """
    action_mode: str = Field(
        "find",
        description=(
            "Operation to perform (case-insensitive, matched on substrings). "
            "'find' (default) — read back at most "
            f"{FIND_PROVIDERS_LIMIT} matching non-verified listings; read-only. "
            "'linked with member' (any value containing 'member') — attach the listing to the "
            "member account owning the mobile number, writing mobilenumber + contactid. "
            "'update mobile number' (any value containing 'update') — write the mobile number to "
            "the listing and to every table caching it, leaving the account link as it is. "
            "'member' is tested first, so a value naming both re-links."
        ),
    )
    mobilenumber: Optional[str] = Field(
        None,
        description=(
            "On a find: an optional filter, matched on its last 10 digits; omit to search on "
            "business name alone, or omit both to page the non-verified listings. On "
            "'update mobile number': the new number to write. On "
            "'linked with member': the number identifying the member account, also written to the "
            "listing. Required by both write modes, and must hold at least 10 digits."
        ),
    )
    businessname: Optional[str] = Field(
        None,
        description=(
            "Business name of the listing (case-insensitive match). Optional on a find — omit to "
            "return every non-verified listing on the mobile number. Required by both write modes, "
            "where it "
            "selects the listing(s) being changed."
        ),
    )
    providerid: Optional[str] = Field(
        None,
        description=(
            "Optional provider GUID. On an update it pins the change to a single listing, which "
            "matters because business names are not unique — without it, every listing sharing the "
            "name is updated."
        ),
    )


class LinkedUnverifiedProvider(BaseModel):
    """One provider listing returned by a find."""
    businessname: Optional[str] = Field(None, description="etown_provider.businessname")
    mobilenumber: Optional[str] = Field(None, description="Mobile number as stored on the provider row")
    publicsearchprovider: Optional[str] = Field(
        None,
        description=(
            "The listing's public search state — always 'non-verified', a find returning only "
            "non-verified listings"
        ),
    )
    providerid: Optional[str] = Field(None, description="Provider GUID of the listing")


class LinkedUnverifiedResponse(BaseModel):
    """Result of a linked_unverified find."""
    status: str = Field(
        ...,
        description="'found' when at least one non-verified listing matched, else 'not_found'",
    )
    action_mode: str = Field(..., description="Normalised action_mode that was applied")
    mobilenumber: Optional[str] = Field(
        None, description="Mobile number filter that was applied, as supplied"
    )
    businessname: Optional[str] = Field(
        None, description="Business name filter that was applied, when the caller supplied one"
    )
    count: int = Field(0, description="Listings returned, capped at 'limit'")
    limit: int = Field(
        FIND_PROVIDERS_LIMIT, description="Maximum listings this endpoint returns per call"
    )
    businessnames: List[str] = Field(
        default_factory=list,
        description="Business names of the returned listings, in the same order as 'providers'",
    )
    publicsearchprovider: Optional[str] = Field(
        None,
        description=(
            "publicsearchprovider of the first listing; per-listing values are in 'providers' "
            "when more than one matched"
        ),
    )
    providers: List[LinkedUnverifiedProvider] = Field(
        default_factory=list,
        description=(
            "The matching provider rows — businessname, mobilenumber, publicsearchprovider, "
            "providerid each"
        ),
    )


class MobileNumberUpdateResponse(BaseModel):
    """Result of moving a provider's mobile number to a new value."""
    status: str = Field(..., description="Always 'updated' on success")
    action_mode: str = Field(..., description="Normalised action_mode that was applied")
    businessname: str = Field(..., description="Business name that selected the listing(s)")
    mobilenumber: str = Field(..., description="New mobile number that was written")
    previous_mobilenumbers: List[str] = Field(
        default_factory=list, description="Mobile numbers the matched listings held before the change"
    )
    provider_ids: List[str] = Field(..., description="Provider GUIDs whose number changed")
    userprofile_ids: List[str] = Field(
        default_factory=list, description="User profiles the listings are linked to"
    )
    contact_ids: List[str] = Field(
        default_factory=list, description="Contacts behind those profiles — the member accounts"
    )
    provider_updated: int = Field(..., description="etown_provider rows updated")
    userprofile_updated: int = Field(..., description="etown_userprofile rows updated")
    contact_updated: int = Field(..., description="etown_contact rows whose mobilephone was set")
    contact_userlogin_updated: int = Field(
        ...,
        description=(
            "etown_contact rows whose userlogin was also rewritten — only those that still held "
            "the old number, so an email login is left untouched"
        ),
    )
    providerservices_updated: int = Field(..., description="etown_providerservices rows updated")
    providerservices_copy_updated: int = Field(
        ..., description="etown_providerservices_copy rows updated"
    )
    dataverse_sync: str = Field(
        "scheduled",
        description="'scheduled' — the Dataverse mirror runs as a background task after the response",
    )


class MemberLinkResponse(BaseModel):
    """Result of re-pointing a provider listing at a member account."""
    status: str = Field(..., description="Always 'linked' on success")
    action_mode: str = Field(..., description="Normalised action_mode that was applied")
    businessname: str = Field(..., description="Business name that selected the listing(s)")
    mobilenumber: str = Field(..., description="Mobile number that was written to the listing(s)")
    member_contactid: str = Field(
        ..., description="Contact GUID of the member account the listing now belongs to"
    )
    member_userprofile_ids: List[str] = Field(
        default_factory=list,
        description="User profiles on that mobile number — how the member account was resolved",
    )
    provider_ids: List[str] = Field(..., description="Provider GUIDs that were re-linked")
    previous_contactids: List[str] = Field(
        default_factory=list, description="Contacts the listings pointed at before the change"
    )
    previous_mobilenumbers: List[str] = Field(
        default_factory=list, description="Mobile numbers the listings held before the change"
    )
    provider_updated: int = Field(..., description="etown_provider rows updated")
    providerservices_updated: int = Field(..., description="etown_providerservices rows updated")
    providerservices_copy_updated: int = Field(
        ..., description="etown_providerservices_copy rows updated"
    )
    userprofile_contactid_mismatch: bool = Field(
        False,
        description=(
            "True when the listing keeps a userprofile belonging to a different contact — only "
            "mobilenumber and contactid are written, so provider.userprofileid is left as it was"
        ),
    )
    skipped: List[str] = Field(
        default_factory=list,
        description=(
            "Tables deliberately not written: etown_contact is the member's own record, and "
            "etown_userprofile is the source of the link rather than a target"
        ),
    )
    dataverse_sync: str = Field(
        "scheduled",
        description="'scheduled' — the Dataverse mirror runs as a background task after the response",
    )


@router.post(
    "/linked_unverified",
    response_model=None,
    status_code=200,
    summary="Find provider listings, move a listing's mobile number, or link one to a member",
    description=(
        "One endpoint, three operations, selected by `action_mode` (case-insensitive, matched on "
        "substrings so the descriptive phrasing the clients already send keeps working).\n\n"
        "| action_mode | Matches on | What it does | Writes | Required fields |\n"
        "|---|---|---|---|---|\n"
        "| `find` | anything else (default) | Reads back the matching non-verified listings | "
        "nothing (read-only) | none — both filters optional |\n"
        "| `linked with member` | contains `member` | Attaches the listing to the member account "
        "owning the mobile number | `mobilenumber` + `contactid` on etown_provider, "
        "etown_providerservices, etown_providerservices_copy | `mobilenumber`, `businessname` |\n"
        "| `update mobile number` | contains `update` | Moves the listing's mobile number, "
        "account link unchanged | `mobilenumber` on etown_provider, etown_userprofile, both "
        "service tables; `mobilephone` (+ `userlogin`) on etown_contact | `mobilenumber`, "
        "`businessname` |\n\n"
        "`member` is tested before `update`, so a value naming both — \"update mobile number, "
        "linked with member account\" — takes the link path, that being the wider operation.\n\n"
        "---\n\n"
        "**`find`** — selects businessname, mobilenumber and publicsearchprovider from "
        f"etown_provider and returns at most {FIND_PROVIDERS_LIMIT} rows. Only listings whose "
        "`publicsearchprovider` is 'non-verified' are returned; verified listings are excluded in "
        "SQL, so they never take up one of the rows. Both caller filters are optional and are "
        "combined when both are given: mobilenumber alone returns every non-verified listing on "
        "that number, businessname alone every non-verified listing under that name, and neither "
        f"returns the first {FIND_PROVIDERS_LIMIT} non-verified providers ordered by business "
        "name. No match returns 200 with status 'not_found' and an empty list.\n\n"
        "**`linked with member`** — the member's contactid is read from "
        "etown_userprofile.mobilenumber, then mobilenumber + contactid are written to the three "
        "tables holding both columns. etown_contact is not touched (it is the member's own record) "
        "and contactidname is left alone (on the provider side it holds the business name, not the "
        "person's). provider.userprofileid is not re-pointed; when it belongs to another contact "
        "the response flags userprofile_contactid_mismatch. If no profile holds the number, or "
        "profiles on it span several contacts, nothing is written — 404 and 409 respectively, "
        "rather than guessing which person owns the listing.\n\n"
        "**`update mobile number`** — etown_contact takes the number as mobilephone, that table "
        "having no mobilenumber column. Its userlogin is rewritten too, but only where it still "
        "held the old number, so an email-based login survives untouched. The contact and "
        "userprofile come from the matched provider rows, so the listing stays with the same "
        "account.\n\n"
        "---\n\n"
        "Business names are not unique — every listing sharing the name is written unless "
        "`providerid` is supplied to pin one. Each write mode runs in one transaction, with the "
        "Dataverse mirror scheduled as a background task afterwards. Matching ignores phone "
        "formatting (the last 10 digits are compared, so '+91 90000 00000' and '09000000000' both "
        "match '9000000000') and business-name case."
    ),
    responses={
        200: {
            "description": (
                "On find: matching listings, or status 'not_found' with an empty list. "
                "On update: the rows changed per table."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "find": {
                            "summary": "action_mode = find",
                            "value": {
                                "status": "found",
                                "action_mode": "find",
                                "mobilenumber": "9000000000",
                                "businessname": None,
                                "count": 1,
                                "limit": FIND_PROVIDERS_LIMIT,
                                "businessnames": ["Ravi Electricals"],
                                "publicsearchprovider": "non-verified",
                                "providers": [
                                    {
                                        "businessname": "Ravi Electricals",
                                        "mobilenumber": "9000000000",
                                        "publicsearchprovider": "non-verified",
                                        "providerid": "00000000-0000-0000-0000-000000000005",
                                    }
                                ],
                            },
                        },
                        "update": {
                            "summary": "action_mode = update mobile number",
                            "value": {
                                "status": "updated",
                                "action_mode": "update mobile number",
                                "businessname": "Ravi Electricals",
                                "mobilenumber": "9111111111",
                                "previous_mobilenumbers": ["9000000000"],
                                "provider_ids": ["00000000-0000-0000-0000-000000000005"],
                                "userprofile_ids": ["00000000-0000-0000-0000-000000000004"],
                                "contact_ids": ["00000000-0000-0000-0000-000000000003"],
                                "provider_updated": 1,
                                "userprofile_updated": 1,
                                "contact_updated": 1,
                                "contact_userlogin_updated": 1,
                                "providerservices_updated": 3,
                                "providerservices_copy_updated": 3,
                                "dataverse_sync": "scheduled",
                            },
                        },
                        "link_member": {
                            "summary": "action_mode = linked with member",
                            "value": {
                                "status": "linked",
                                "action_mode": "linked with member",
                                "businessname": "Ravi Electricals",
                                "mobilenumber": "9111111111",
                                "member_contactid": "00000000-0000-0000-0000-000000000001",
                                "member_userprofile_ids": [
                                    "00000000-0000-0000-0000-000000000002"
                                ],
                                "provider_ids": ["00000000-0000-0000-0000-000000000005"],
                                "previous_contactids": [
                                    "00000000-0000-0000-0000-000000000003"
                                ],
                                "previous_mobilenumbers": ["9000000000"],
                                "provider_updated": 1,
                                "providerservices_updated": 3,
                                "providerservices_copy_updated": 3,
                                "userprofile_contactid_mismatch": True,
                                "skipped": ["etown_contact", "etown_userprofile"],
                                "dataverse_sync": "scheduled",
                            },
                        },
                    }
                }
            },
        },
        404: {
            "description": (
                "Write modes: no provider matches the business name; link mode also when no "
                "member profile holds the mobile number"
            )
        },
        409: {"description": "Link mode: profiles on that mobile number span several contacts"},
        422: {
            "description": (
                "mobilenumber has fewer than 10 digits, or mobilenumber / businessname is missing "
                "on a write mode"
            )
        },
        500: {"description": "Internal server error"},
    },
)
async def linked_unverified_endpoint(
    background_tasks: BackgroundTasks,
    body: LinkedUnverifiedRequest = Body(
        ...,
        openapi_examples={
            "find_by_mobile": {
                "summary": "find — every listing on a mobile number",
                "description": "Read-only. businessname omitted, so all listings on the number come back.",
                "value": {"action_mode": "find", "mobilenumber": "9000000000"},
            },
            "find_one_listing": {
                "summary": "find — one named listing",
                "description": "Both filters given, pinning a single listing on a shared number.",
                "value": {
                    "action_mode": "find",
                    "mobilenumber": "9000000000",
                    "businessname": "Ravi Electricals",
                },
            },
            "link_with_member": {
                "summary": "linked with member — attach a listing to a member account",
                "description": (
                    "Resolves the member contact from etown_userprofile.mobilenumber, then writes "
                    "mobilenumber + contactid to the provider and both service tables."
                ),
                "value": {
                    "action_mode": "linked with member",
                    "mobilenumber": "9111111111",
                    "businessname": "Ravi Electricals",
                },
            },
            "update_mobilenumber": {
                "summary": "update mobile number — move the number, keep the account",
                "description": (
                    "Writes the new number to the provider, userprofile, contact (mobilephone) and "
                    "both service tables. The account link is unchanged."
                ),
                "value": {
                    "action_mode": "update mobile number",
                    "mobilenumber": "9111111111",
                    "businessname": "Ravi Electricals",
                },
            },
            "update_one_listing": {
                "summary": "update mobile number — pinned to one listing by providerid",
                "description": (
                    "Business names are not unique; providerid confines the write to a single "
                    "listing instead of every listing sharing the name."
                ),
                "value": {
                    "action_mode": "update mobile number",
                    "mobilenumber": "9111111111",
                    "businessname": "Ravi Electricals",
                    "providerid": "00000000-0000-0000-0000-000000000005",
                },
            },
        },
    ),
    _token: str = Security(verify_token),
) -> Union[LinkedUnverifiedResponse, MobileNumberUpdateResponse, MemberLinkResponse]:
    """Handle POST /api/dataverse-sync/linked_unverified: find non-verified listings, link one to a member, or move its mobile number.

    Returns:
        LinkedUnverifiedResponse, MemberLinkResponse or MobileNumberUpdateResponse, per action_mode.

    Raises:
        HTTPException: 422 bad input, 404 no match, 409 ambiguous member, 500 database failure.
    """
    action_mode = (body.action_mode or "find").strip().lower()
    mobilenumber = (body.mobilenumber or "").strip() or None
    businessname = (body.businessname or "").strip() or None
    providerid = (body.providerid or "").strip() or None

    if "member" in action_mode:
        return await _linked_unverified_link_member(
            action_mode=action_mode,
            mobilenumber=mobilenumber,
            businessname=businessname,
            providerid=providerid,
            background_tasks=background_tasks,
        )

    if "update" in action_mode:
        return await _linked_unverified_update(
            action_mode=action_mode,
            mobilenumber=mobilenumber,
            businessname=businessname,
            providerid=providerid,
            background_tasks=background_tasks,
        )

    try:
        result = await find_providers(
            mobilenumber=mobilenumber,
            businessname=businessname,
            limit=FIND_PROVIDERS_LIMIT,
        )
    except Exception as exc:
        logger.error(
            f"linked_unverified - failed action_mode={action_mode} error={exc}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to look up provider")

    if result.get("partial_mobile"):
        raise HTTPException(status_code=422, detail="mobilenumber must contain at least 10 digits")

    providers = result.get("providers") or []
    return LinkedUnverifiedResponse(
        status="found" if providers else "not_found",
        action_mode=action_mode,
        mobilenumber=mobilenumber,
        businessname=businessname,
        count=len(providers),
        limit=result.get("limit", FIND_PROVIDERS_LIMIT),
        businessnames=[p["businessname"] for p in providers if p.get("businessname")],
        publicsearchprovider=providers[0].get("publicsearchprovider") if providers else None,
        providers=[
            LinkedUnverifiedProvider(
                businessname=p.get("businessname"),
                mobilenumber=p.get("mobilenumber"),
                publicsearchprovider=p.get("publicsearchprovider"),
                providerid=p.get("providerid"),
            )
            for p in providers
        ],
    )


async def _linked_unverified_update(
    action_mode: str,
    mobilenumber: Optional[str],
    businessname: Optional[str],
    providerid: Optional[str],
    background_tasks: BackgroundTasks,
) -> MobileNumberUpdateResponse:
    """Move a listing's mobile number for linked_unverified's update mode and schedule the Dataverse mirror.

    Args:
        action_mode: Normalised action_mode, echoed back in the response.
        mobilenumber: New mobile number to write; required here.
        businessname: Business name selecting the listing(s); required here.
        providerid: Optional provider GUID pinning the change to one listing.
        background_tasks: FastAPI background task queue for the Dataverse mirror.

    Returns:
        MobileNumberUpdateResponse with rows updated per table and the ids touched.

    Raises:
        HTTPException: 422 missing or short input, 404 no provider, 500 database failure.
    """
    if not mobilenumber:
        raise HTTPException(
            status_code=422, detail="mobilenumber is required to update a mobile number"
        )
    if not businessname:
        raise HTTPException(
            status_code=422, detail="businessname is required to update a mobile number"
        )

    try:
        result = await update_provider_mobilenumber(
            businessname=businessname,
            mobilenumber=mobilenumber,
            providerid=providerid,
        )
    except Exception as exc:
        logger.error(
            f"linked_unverified - update failed business='{businessname}' error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to update mobile number")

    if result.get("bad_mobile"):
        raise HTTPException(status_code=422, detail="mobilenumber must contain at least 10 digits")

    if not result.get("found"):
        raise HTTPException(
            status_code=404, detail=f"No provider found for business '{businessname}'"
        )

    background_tasks.add_task(
        sync_mobilenumber_to_dataverse,
        mobilenumber=result["mobilenumber"],
        provider_ids=result.get("provider_ids") or [],
        userprofile_ids=result.get("userprofile_ids") or [],
        contact_ids=result.get("contact_ids") or [],
        providerservices_ids=result.get("providerservices_ids") or [],
        update_userlogin=bool(result.get("contact_userlogin_updated")),
    )

    return MobileNumberUpdateResponse(
        status="updated",
        action_mode=action_mode,
        businessname=businessname,
        mobilenumber=result["mobilenumber"],
        previous_mobilenumbers=result.get("previous_mobilenumbers") or [],
        provider_ids=result.get("provider_ids") or [],
        userprofile_ids=result.get("userprofile_ids") or [],
        contact_ids=result.get("contact_ids") or [],
        provider_updated=result.get("provider_updated", 0),
        userprofile_updated=result.get("userprofile_updated", 0),
        contact_updated=result.get("contact_updated", 0),
        contact_userlogin_updated=result.get("contact_userlogin_updated", 0),
        providerservices_updated=result.get("providerservices_updated", 0),
        providerservices_copy_updated=result.get("providerservices_copy_updated", 0),
        dataverse_sync="scheduled",
    )


async def _linked_unverified_link_member(
    action_mode: str,
    mobilenumber: Optional[str],
    businessname: Optional[str],
    providerid: Optional[str],
    background_tasks: BackgroundTasks,
) -> MemberLinkResponse:
    """Attach a listing to a member account for linked_unverified's link mode and schedule the Dataverse mirror.

    Args:
        action_mode: Normalised action_mode, echoed back in the response.
        mobilenumber: Mobile number identifying the member account; required here.
        businessname: Business name selecting the listing(s); required here.
        providerid: Optional provider GUID pinning the change to one listing.
        background_tasks: FastAPI background task queue for the Dataverse mirror.

    Returns:
        MemberLinkResponse with the member contact, rows updated and any userprofile mismatch.

    Raises:
        HTTPException: 422 bad input, 404 no member or provider, 409 ambiguous member, 500 failure.
    """
    if not mobilenumber:
        raise HTTPException(
            status_code=422, detail="mobilenumber is required to link a provider to a member"
        )
    if not businessname:
        raise HTTPException(
            status_code=422, detail="businessname is required to link a provider to a member"
        )

    try:
        result = await link_provider_to_member(
            businessname=businessname,
            mobilenumber=mobilenumber,
            providerid=providerid,
        )
    except Exception as exc:
        logger.error(
            f"linked_unverified - member link failed business='{businessname}' error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to link provider to member")

    if result.get("bad_mobile"):
        raise HTTPException(status_code=422, detail="mobilenumber must contain at least 10 digits")

    if not result.get("member_found"):
        raise HTTPException(
            status_code=404,
            detail="No member account found for that mobile number",
        )

    if result.get("ambiguous_member"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"That mobile number is held by {len(result.get('member_contactids') or [])} "
                "different contacts; nothing was changed"
            ),
        )

    if not result.get("found"):
        raise HTTPException(
            status_code=404, detail=f"No provider found for business '{businessname}'"
        )

    background_tasks.add_task(
        sync_member_link_to_dataverse,
        mobilenumber=result["mobilenumber"],
        contactid=result["member_contactid"],
        provider_ids=result.get("provider_ids") or [],
        providerservices_ids=result.get("providerservices_ids") or [],
    )

    return MemberLinkResponse(
        status="linked",
        action_mode=action_mode,
        businessname=businessname,
        mobilenumber=result["mobilenumber"],
        member_contactid=result["member_contactid"],
        member_userprofile_ids=result.get("member_userprofile_ids") or [],
        provider_ids=result.get("provider_ids") or [],
        previous_contactids=result.get("previous_contactids") or [],
        previous_mobilenumbers=result.get("previous_mobilenumbers") or [],
        provider_updated=result.get("provider_updated", 0),
        providerservices_updated=result.get("providerservices_updated", 0),
        providerservices_copy_updated=result.get("providerservices_copy_updated", 0),
        userprofile_contactid_mismatch=bool(result.get("userprofile_contactid_mismatch")),
        skipped=["etown_contact", "etown_userprofile"],
        dataverse_sync="scheduled",
    )


@router.delete(
    "/provider",
    response_model=DeleteProviderResponse,
    status_code=200,
    summary="Soft-delete a provider by providerid, or by mobile number and business name",
    description=(
        "Retires a provider and all of its provider-scoped rows. Name the provider either way: "
        "send providerid for exactly the listing behind that GUID, or send mobilenumber + "
        "businessname together to match on the pair — which retires every live listing matching "
        "it, so a duplicated listing goes in one call. providerid wins when both are supplied, "
        "and the response echoes the number and name read off the row. NOTHING IS PHYSICALLY "
        "DELETED: each row is stamped "
        "deleted = 'true' plus an IST deletiondate, the same flag the account-deletion flow "
        "writes, and every read path that filters on it stops returning the listing. The rows "
        "stay, so the listing can be brought back by clearing the flag. Six tables are flagged "
        "in one transaction under one timestamp: etown_providerservices_copy, "
        "etown_providerservices, etown_etownprovider, etown_provider_details, etown_provider, "
        "then etown_userprofile. etown_provider_details is flagged explicitly because a flag "
        "does not travel the FK cascade the hard delete relied on. The user profile is kept "
        "when another provider or an etown_member row that is not itself flagged still "
        "references it. etown_contact is always kept — the same contact row backs the person's "
        "member account and login. Pair matching ignores phone formatting and business-name "
        "case, and either way a listing already flagged is skipped, so a repeat call 404s "
        "rather than re-stamping the date. The Dataverse mirror writes the same flags "
        "afterwards as a background task."
    ),
    responses={
        200: {"description": "Provider flagged deleted; returns the rows flagged per table"},
        404: {"description": "No live provider matches the providerid, or the mobile number and business name"},
        422: {"description": "Neither a providerid nor a complete mobilenumber + businessname pair was sent"},
        500: {"description": "Internal server error"},
    },
)
async def delete_provider_endpoint(
    body: DeleteProviderRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> DeleteProviderResponse:
    """Handle DELETE /api/dataverse-sync/provider: soft-delete a provider's rows and schedule the Dataverse flag mirror.

    Raises:
        HTTPException: 422 no identifier, 404 no live provider, 500 database failure.
    """
    providerid = (body.providerid or "").strip()
    mobilenumber = (body.mobilenumber or "").strip()
    businessname = (body.businessname or "").strip()
    if not providerid and not (mobilenumber and businessname):
        raise HTTPException(
            status_code=422,
            detail="Provide providerid, or both mobilenumber and businessname",
        )

    target = f"providerid='{providerid}'" if providerid else f"business='{businessname}'"

    try:
        result = await delete_provider_by_mobile_and_business(
            mobilenumber=mobilenumber or None,
            businessname=businessname or None,
            providerid=providerid or None,
        )
    except Exception as exc:
        logger.error(
            f"delete_provider - failed {target} error={exc}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to delete provider")

    if not result.get("found"):
        raise HTTPException(
            status_code=404,
            detail=(
                f"No live provider found for providerid '{providerid}'"
                if providerid
                else f"No provider found for business '{businessname}' with that mobile number"
            ),
        )

    flagged_userprofile_ids = [
        u for u in result.get("userprofile_ids") or []
        if u not in (result.get("userprofile_kept") or [])
    ]
    background_tasks.add_task(
        sync_provider_soft_delete_to_dataverse,
        provider_ids=result.get("provider_ids") or [],
        deletiondate=result.get("deletiondate"),
        userprofile_ids=flagged_userprofile_ids,
        providerservices_ids=result.get("providerservices_ids") or [],
    )

    return DeleteProviderResponse(
        status="deleted",
        mobilenumber=result.get("mobilenumber") or mobilenumber or None,
        businessname=result.get("businessname") or businessname or None,
        deletiondate=result.get("deletiondate"),
        provider_ids=result.get("provider_ids") or [],
        provider_deleted=result.get("provider_deleted", 0),
        providerservices_deleted=result.get("providerservices_deleted", 0),
        providerservices_copy_deleted=result.get("providerservices_copy_deleted", 0),
        etownprovider_deleted=result.get("etownprovider_deleted", 0),
        provider_details_deleted=result.get("provider_details_deleted", 0),
        userprofile_deleted=result.get("userprofile_deleted", 0),
        userprofile_kept=result.get("userprofile_kept") or [],
        contacts_kept=result.get("contact_ids") or [],
        dataverse_sync="scheduled",
    )


@router.post(
    "/provider_review_approval",
    response_model=ProviderReviewApprovalResponse,
    status_code=200,
    summary="Apply a worked provider-review row across the Postgres provider tables",
    description=(
        "Writes the approver's decision and the reviewed values to etown_provider_review, "
        "etown_provider, etown_providerservices, etown_providerservices_copy and etown_contact "
        "in one transaction, then rebuilds the content_vector of the three tables that carry "
        "one. Replaces the PostgreSQL connector actions of the Dataverse provider-approval "
        "flow, which cannot read or write a table holding a pgvector column. A field that is "
        "omitted, or sent blank, keeps its existing value. The same values are mirrored to "
        "the provider, its service rows and the contact in Dataverse as a background task, "
        "so the flow no longer needs its own Dataverse update scope."
    ),
    responses={
        200: {"description": "Values applied; returns the rows updated per table"},
        404: {"description": "Provider not found"},
        422: {"description": "Blank reviewid or providerid, or a coordinate out of range"},
        500: {"description": "Internal server error"},
    },
)
async def provider_review_approval_endpoint(
    body: ProviderReviewApprovalRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> ProviderReviewApprovalResponse:
    """Handle POST /api/dataverse-sync/provider_review_approval: apply a review row, then refresh vectors and mirror to Dataverse.

    Raises:
        HTTPException: 422 blank reviewid or providerid, 404 unknown provider, 500 database failure.
    """
    reviewid = body.reviewid.strip()
    providerid = body.providerid.strip()
    if not reviewid:
        raise HTTPException(status_code=422, detail="reviewid must not be blank")
    if not providerid:
        raise HTTPException(status_code=422, detail="providerid must not be blank")

    payload = body.model_dump(exclude={"reviewid", "providerid"})

    try:
        result = await apply_provider_review_approval(
            reviewid=reviewid,
            providerid=providerid,
            **payload,
        )
    except Exception as exc:
        logger.error(
            f"provider_review_approval - failed reviewid={reviewid} providerid={providerid} "
            f"error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to apply provider review approval")

    if not result.get("found"):
        raise HTTPException(status_code=404, detail=f"Provider '{providerid}' not found")

    targets = {
        "etown_provider": [providerid],
        "etown_providerservices_copy": result["providerservices_copy_ids"],
    }
    if result["contactid"]:
        targets["etown_contact"] = [result["contactid"]]

    background_tasks.add_task(refresh_content_vectors, targets)
    background_tasks.add_task(
        sync_provider_review_approval_to_dataverse,
        providerid=providerid,
        contactid=result["contactid"],
        providerservices_ids=result["providerservices_ids"],
        businessname=body.businessname,
        businesscategory=body.businesscategory,
        servicecategory=body.servicecategory,
        servicetype=body.servicetype,
        description=body.description,
        services=body.services,
        marketing_person_name=body.marketing_person_name,
        businesslogo=body.businesslogo,
        email=body.email,
        mobilenumber=body.mobilenumber,
        alternatemobile=body.alternatemobile,
        formattedaddress=body.formattedaddress,
        additionaladdressinfo=body.additionaladdressinfo,
        arealocation=body.arealocation,
        city=body.city,
        state=body.state,
        latitude=body.latitude,
        longitude=body.longitude,
        interested=body.interested,
    )

    return ProviderReviewApprovalResponse(
        reviewid=reviewid,
        providerid=providerid,
        contactid=result["contactid"],
        review_updated=result["review_updated"],
        provider_updated=result["provider_updated"],
        providerservices_updated=result["providerservices_updated"],
        providerservices_copy_updated=result["providerservices_copy_updated"],
        contact_updated=result["contact_updated"],
    )


class TownReviewStatusRequest(BaseModel):
    """Request body for the townreview endpoint."""
    townreviewid: str = Field(..., min_length=1, description="GUID of the etown_townreview row")
    townprofileid: str = Field(..., min_length=1, description="Town profile whose townimage is set on approval")
    imageurl: str = Field(..., min_length=1, description="Image written to the town's townimage when approved")
    status: str = Field(..., min_length=1, description="New review status; approved (case-insensitive) also writes imageurl")


class TownReviewStatusResponse(BaseModel):
    """Result of applying a town review status."""
    townreviewid: str = Field(..., description="GUID of the etown_townreview row")
    townprofileid: str = Field(..., description="Town profile from the request")
    status: str = Field(..., description="Status written, lowercased")
    previous_status: Optional[str] = Field(None, description="Status the review held before")
    imageurl: Optional[str] = Field(None, description="Image written to etown_townprofile.townimage on approval")
    townreview_updated: int = Field(..., description="etown_townreview rows updated")
    townprofile_updated: int = Field(..., description="etown_townprofile rows updated")
    dataverse_sync: str = Field(..., description="'scheduled' when ets_townimage is mirrored, else 'skipped'")


@router.post(
    "/townreview",
    response_model=TownReviewStatusResponse,
    status_code=200,
    summary="Apply a town image review status",
    description=(
        "Updates the etown_townreview row's status in Postgres. When status is approved "
        "(case-insensitive), also makes imageurl the etown_townprofile.townimage for "
        "townprofileid in the same transaction, then mirrors only ets_townimage to the town "
        "profile in Dataverse and sends the review's userprofileid a push and in-app "
        "'Town Image Change Approved' notification."
    ),
    responses={
        200: {"description": "Status applied; returns the rows updated per table"},
        404: {"description": "Town review or town profile not found"},
        422: {"description": "townreviewid is not a GUID, or a field is blank"},
        500: {"description": "Internal server error"},
    },
)
async def town_review_status_endpoint(
    body: TownReviewStatusRequest,
    background_tasks: BackgroundTasks,
    _token: str = Security(verify_token),
) -> TownReviewStatusResponse:
    """Handle POST /api/dataverse-sync/townreview: set a town review's status and, on approval, the town's image.

    Raises:
        HTTPException: 422 non-GUID townreviewid or a blank field, 404 unknown review or town, 500 database failure.
    """
    townreviewid = body.townreviewid.strip()
    townprofileid = body.townprofileid.strip()
    imageurl = body.imageurl.strip()
    status = body.status.strip().lower()
    try:
        uuid.UUID(townreviewid)
    except ValueError:
        raise HTTPException(status_code=422, detail="townreviewid must be a GUID")
    if not townprofileid:
        raise HTTPException(status_code=422, detail="townprofileid must not be blank")
    if not imageurl:
        raise HTTPException(status_code=422, detail="imageurl must not be blank")
    if not status:
        raise HTTPException(status_code=422, detail="status must not be blank")

    try:
        result = await apply_town_review_status(
            townreviewid=townreviewid,
            townprofileid=townprofileid,
            imageurl=imageurl,
            status=status,
        )
    except Exception as exc:
        logger.error(
            f"townreview - failed townreviewid={townreviewid} townprofileid={townprofileid} "
            f"status={status} error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to apply town review status")

    if not result["found"]:
        raise HTTPException(status_code=404, detail=f"Town review '{townreviewid}' not found")
    if status == "approved" and not result["town_found"]:
        raise HTTPException(status_code=404, detail=f"Town profile '{townprofileid}' not found")

    dataverse_sync = "skipped"
    if result["townprofile_updated"]:
        background_tasks.add_task(sync_town_profile_image_to_dataverse, townprofileid, result["imageurl"])
        background_tasks.add_task(notify_town_image_approved, townreviewid)
        dataverse_sync = "scheduled"

    return TownReviewStatusResponse(
        townreviewid=townreviewid,
        townprofileid=townprofileid,
        status=status,
        previous_status=result["previous_status"],
        imageurl=result["imageurl"],
        townreview_updated=result["townreview_updated"],
        townprofile_updated=result["townprofile_updated"],
        dataverse_sync=dataverse_sync,
    )
