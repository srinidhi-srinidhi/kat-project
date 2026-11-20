"""Provider Services Utilities - Postgres business logic to add, edit and delete a provider's services.

Domain layer behind the /api/provider and /api/offerings service routes: enforces the plan's listing cap, uploads images, writes etown_providerservices and its _copy table, keeps etown_offerings and serviceitems converged, and runs category, embedding and Dataverse sync work in the background.

Features:
    - Service Addition Workflow: validate, upload images, insert, then background category/embedding/sync
    - Non-verified Addition: FOS add-services chain stamped with the capturer's source
    - Service Edit Workflow: partial updates kept converged with etown_offerings and serviceitems
    - Service Deletion Workflow: hard DELETE from etown_providerservices and its _copy table
    - Listing Cap: check_listing_cap enforces the plan's max_listings
    - AI Category Generation: Automatic service category assignment using LLM inference
    - Image Upload: Base64 image decoding and Azure Blob Storage upload for up to 3 images
    - Vector Embeddings: Automatic embedding generation for semantic search indexing
    - Concurrent Processing: Semaphore-controlled parallel service inserts
    - Dataverse Sync: background sync after a successful insert or edit

Classes:
    ProviderServiceManagementService: Static-method service class for provider service CRUD workflows

Functions:
    _iter_items(): Yield every OfferingItem across all categories of an offering group
    _derive_price_range(): Derive a 'min - max' price range string from item prices
    _summarize_items(): Build a short fallback description from the first few item names
    map_parsed_offerings_to_service_details(): Flatten parsed offering groups into ServiceDetailRequest rows
    assign_pydantic_offering_ids(): Assign stable UUIDs to every node (offering/category/item) in a parsed offering tree
    upload_parsed_business_media(): Upload all base64 *_image fields across the parsed tree to Azure Blob (in-place URL replacement)
    check_listing_cap(): Raise PlanLimitError when a request's new service names would breach max_listings
    ProviderServiceManagementService._get_contact(): Fetch contact record by ID
    ProviderServiceManagementService._get_userprofile_name(): Fetch userprofile name by ID
    ProviderServiceManagementService._get_provider(): Fetch provider record by ID
    ProviderServiceManagementService._upload_service_images(): Upload service images to Azure Blob
    ProviderServiceManagementService._generate_category(): AI-generate service category
    ProviderServiceManagementService._insert_service_row(): Insert (or update) a service row into both providerservices tables
    ProviderServiceManagementService._process_one_service(): Process a single service end-to-end (images + insert)
    ProviderServiceManagementService.add_services_to_provider_workflow(): Add multiple services (DB inserts only, no AI)
    ProviderServiceManagementService._insert_unverified_service_row(): _insert_service_row()'s non-verified twin
    ProviderServiceManagementService._process_one_unverified_service(): _process_one_service()'s non-verified twin
    ProviderServiceManagementService.add_services_to_unverified_provider_workflow(): Add multiple non-verified services, uncapped
    ProviderServiceManagementService.run_background_enhancements(): Background category/embedding generation and Dataverse sync for added services
    ProviderServiceManagementService.delete_service_workflow(): Delete a provider service from both providerservices tables
    ProviderServiceManagementService.edit_service_workflow(): Partial-update an existing provider service
    ProviderServiceManagementService._sync_offering_ops_to_catalogue(): Apply offering_operations to etown_offerings.items and mirror the tree onto serviceitems
    ProviderServiceManagementService.run_edit_enhancements(): Background offerings convergence + Dataverse sync for edits

Dependencies:
    - asyncio: Concurrent service processing with semaphore
    - uuid: UUID generation for blob naming and service IDs
    - datetime: Timestamp generation for records
    - typing: Type hints for complex data structures
    - app.core.config: Azure Blob Storage and settings configuration
    - app.core.logging: Centralized application logging
    - app.db.client: Async PostgreSQL database access
    - app.etown.plans.plans_utils: Subscription plan resolution and the max_listings cap
    - app.etown.services.services_utils: Category generation logic
    - app.schemas.api: Pydantic models for request/response validation
    - app.utils.blob_storage: Azure Blob Storage upload helpers
    - app.utils.embeddings: Vector embedding generation
    - app.etown.provider.provider_syncing: Dataverse sync after a service add/edit
    - app.etown.offers.offerings_utils: Offering-tree operations and serviceitems mirroring
    - app.etown.provider.provider_utils: Shared businesscategory normalization

Updates:
    26-May-2026: Added module docstring.
    29-Jun-2026: Service addition workflow processes services concurrently.
    30-Jun-2026: Reworked /add_services for the parsed-business payload, with synchronous media upload.
    01-Jul-2026: edit_service_workflow uploads images synchronously and supports category_operations.
    07-Jul-2026: _normalize_biz_category applied to businesscategory.
    10-Jul-2026: Local _is_stored_image_url replaced by is_stored_media_url.
    23-Jul-2026: Added serviceitems_has_items().
    29-Jul-2026: edit_service_workflow keeps etown_offerings.items and serviceitems converged.
    18-Aug-2026: add_services enforces max_listings via check_listing_cap, with a post-insert recount.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
    15-Sep-2026: Added non-verified add-services chain for POST /provider/unverified_provider, stamped source='FOS'.
    23-Sep-2026: add_services_to_unverified_provider_workflow() stamps the capturer's staff role as source.
    29-Sep-2026: run_background_enhancements() stores the generated generalcategory on both tables.
    22-Oct-2026: Verified service insert and run_background_enhancements() keep a sent service_type as businesscategory.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.config import settings
from app.core.logging import logger
from app.db.client import Database
from app.etown.services.services_utils import generate_category_logic
from app.schemas.api import (
    CategoryGenerationResponse,
    ParsedBusinessDetails,
    ParsedOfferingData,
    ServiceDetailRequest,
)
from app.utils.blob_storage import (
    current_timestamp_prefix as _current_timestamp_prefix,
    is_stored_media_url as _is_stored_media_url,
    normalize_container_name as _normalize_container_name,
    to_bytes as _to_bytes,
    upload_blob_bytes as _upload_blob_bytes,
)
from app.utils.embeddings import generate_embedding
from app.etown.provider.provider_syncing import sync_add_service_to_dataverse
from app.etown.offers.offerings_utils import (
    apply_offering_operations,
    assign_node_ids,
    mirror_offering_tree_to_serviceitems,
)
from app.etown.provider.provider_utils import _normalize_biz_category
from app.etown.plans.plans_utils import record_refusal, POINT_IN_TIME_LIMITS, PlanLimitError, resolve_plan

_MAX_CONCURRENT_SERVICES = 10

_MAX_SUMMARY_ITEMS = 8


def _iter_items(categories: List[Any]):
    """Yield every OfferingItem across all categories of an offering group."""
    for category in categories or []:
        for item in getattr(category, "items", None) or []:
            yield item


def _derive_price_range(categories: List[Any]) -> Optional[str]:
    """Return a currency-prefixed 'min - max' item price range, a single price, or None when unpriced."""
    prices: List[float] = []
    currency: str = ""
    for item in _iter_items(categories):
        price = getattr(item, "item_price", None)
        if price is None:
            continue
        prices.append(price)
        if not currency and getattr(item, "item_currency", None):
            currency = str(item.item_currency)
    if not prices:
        return None

    def _fmt(value: float) -> str:
        text = f"{value:g}"
        return f"{currency}{text}" if currency else text

    low, high = min(prices), max(prices)
    return _fmt(low) if low == high else f"{_fmt(low)} - {_fmt(high)}"


def _summarize_items(categories: List[Any]) -> Optional[str]:
    """Return a comma-joined list of up to eight item names, or None when there are none."""
    names: List[str] = []
    for item in _iter_items(categories):
        name = (getattr(item, "item_name", None) or "").strip()
        if name:
            names.append(name)
        if len(names) >= _MAX_SUMMARY_ITEMS:
            break
    return ", ".join(names) if names else None


def map_parsed_offerings_to_service_details(
    offering_data: List[ParsedOfferingData],
) -> List[ServiceDetailRequest]:
    """Map each named parsed offering group to one ServiceDetailRequest with derived description and price range."""
    result: List[ServiceDetailRequest] = []
    for off in offering_data or []:
        name = (off.offering_name or "").strip()
        if not name:
            logger.warning("[add_services] Skipping offering group with no name")
            continue
        description = (
            off.offering_description
            or off.offering_description_ai_generated
            or _summarize_items(off.categories)
        )
        pricerange = _derive_price_range(off.categories)
        serviceitems = (
            [c.model_dump() for c in off.categories] if off.categories else None
        )
        result.append(
            ServiceDetailRequest(
                service_name=name,
                description=description or None,
                pricerange=pricerange or None,
                quantity=None,
                serviceimage1=off.offering_image,
                serviceimage2=None,
                serviceimage3=None,
                serviceitems=serviceitems,
            )
        )
    return result


def assign_pydantic_offering_ids(offering_data: List[ParsedOfferingData]) -> None:
    """Fill missing offering, category and item ids in the parsed tree with new UUIDs, in place."""
    for off in offering_data or []:
        if not off.offering_id:
            off.offering_id = str(uuid.uuid4())
        for cat in (off.categories or []):
            if not cat.category_id:
                cat.category_id = str(uuid.uuid4())
            for item in (cat.items or []):
                if not item.item_id:
                    item.item_id = str(uuid.uuid4())


async def upload_parsed_business_media(
    business_details: ParsedBusinessDetails,
    offering_data: List[ParsedOfferingData],
) -> None:
    """Upload base64 business, offering, category and item images to Azure Blob, replacing each field with its URL.

    Args:
        business_details: Parsed business whose business_image is uploaded.
        offering_data: Parsed offering tree whose images are uploaded.

    Returns:
        None.

    Raises:
        RuntimeError: When the blob container is unset or any image fails to upload.
    """
    container = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
    if not container:
        logger.error("[add_services] AZURE_BLOB_CONTAINER is not configured — cannot upload images")
        raise RuntimeError("[add_services] AZURE_BLOB_CONTAINER is not configured — cannot upload images")

    timestamp = _current_timestamp_prefix()
    key = (business_details.providerid or business_details.contactid or "unknown").strip() or "unknown"

    targets: List[Tuple[Any, str, str]] = []

    def _add(obj: Any, attr: str, suffix: str) -> None:
        value = getattr(obj, attr, None)
        if not value or str(value).strip().lower() == "null":
            return
        if _is_stored_media_url(value):
            return
        targets.append((obj, attr, suffix))

    _add(business_details, "business_image", "BusinessImg")
    for i, off in enumerate(offering_data or []):
        _add(off, "offering_image", f"Off{i}Img")
        for j, cat in enumerate(off.categories or []):
            _add(cat, "category_image", f"Off{i}_Cat{j}Img")
            for k, item in enumerate(cat.items or []):
                _add(item, "item_image", f"Off{i}_Cat{j}_Item{k}Img")

    if not targets:
        return

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SERVICES)
    failed: List[str] = []

    async def _upload(obj: Any, attr: str, suffix: str) -> None:
        async with semaphore:
            try:
                content = _to_bytes(getattr(obj, attr))
                if not content:
                    return
                blob_name = f"{key}/{timestamp}_{uuid.uuid4().hex[:8]}_{suffix}.png"
                url = await _upload_blob_bytes(container, blob_name, content, "image/png")
                if url:
                    setattr(obj, attr, url)
                else:
                    logger.error(f"[add_services] Upload returned empty URL for {suffix}")
                    failed.append(suffix)
            except Exception as e:
                logger.error(f"[add_services] Image upload failed for {suffix}: {e}", exc_info=True)
                failed.append(suffix)

    await asyncio.gather(*(_upload(obj, attr, suffix) for obj, attr, suffix in targets))

    if failed:
        raise RuntimeError(f"[add_services] {len(failed)} image(s) failed to upload: {', '.join(failed)}")


async def check_listing_cap(
    userprofile_id: str,
    provider_id: str,
    service_names: List[Optional[str]],
) -> Tuple[Optional[Dict[str, Any]], Optional[int], Optional[Set[str]]]:
    """Raise PlanLimitError when the request's genuinely new service names would push listings past max_listings.

    Args:
        userprofile_id: Provider's user profile id; the key the plan resolves on.
        provider_id: Provider whose existing service names the request is matched against.
        service_names: Every service name the request carries; blanks are ignored.

    Returns:
        (plan, listing_cap, existing_names); cap None if unlimited, names None if the query failed.

    Raises:
        PlanLimitError: When live listings plus new names would exceed max_listings.
    """
    plan = await resolve_plan(userprofile_id)
    listing_cap = plan.get("max_listings") if plan else None
    if listing_cap is None:
        return (plan, None, None)

    requested = {name for name in service_names if name}
    try:
        used = await Database.fetchval(
            POINT_IN_TIME_LIMITS["max_listings"]["sql"], userprofile_id
        ) or 0
        existing_names = {
            row["services"]
            for row in await Database.fetch(
                "SELECT services FROM etown_providerservices "
                "WHERE providerid = $1 AND services = ANY($2::text[])",
                provider_id, list(requested),
            )
        }
    except Exception as e:
        logger.warning(
            f"[add_services] Listing cap check failed for {userprofile_id}, allowing: {e}"
        )
        return (plan, listing_cap, None)

    new_listings = len(requested - existing_names)
    if new_listings and used + new_listings > listing_cap:
        err = PlanLimitError(
            f"Your {plan['plan_name']} plan allows {listing_cap} listings. "
            f"You have {used} and this request adds {new_listings}.",
            plan_code=plan["plan_code"], limit=listing_cap, used=used,
            userprofile_id=userprofile_id, limit_column="max_listings",
        )
        await record_refusal(err)
        raise err
    return (plan, listing_cap, existing_names)


class ProviderServiceManagementService:
    """Service class for managing additions of new services to existing providers."""

    @staticmethod
    async def _get_contact(contact_id: str) -> Optional[Dict[str, Any]]:
        """Return the contact's id, access token, first name and email, or None when missing."""
        row = await Database.fetchrow(
            "SELECT contactid, accesstoken, firstname, email FROM etown_contact WHERE contactid = $1",
            contact_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def _get_userprofile_name(userprofile_id: str) -> Optional[str]:
        """Return the name on a userprofile, or None when missing."""
        return await Database.fetchval(
            "SELECT name FROM etown_userprofile WHERE userprofileid = $1",
            userprofile_id,
        )

    @staticmethod
    async def _get_provider(provider_id: str) -> Optional[Dict[str, Any]]:
        """Return the provider fields needed to build service rows, or None when missing."""
        row = await Database.fetchrow(
            """
            SELECT providerid, contactid, businessname, businesscategory,
                   address, additionaladdressinfo, areaname, city, statecode,
                   latitude, longitude, mobilenumber, availability,
                   serviceimageurl,
                   contactidname, userprofileid, userprofileidname, email
            FROM etown_provider
            WHERE providerid = $1
            """,
            provider_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def _upload_service_images(
        contact_id: str, service_detail: ServiceDetailRequest
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Upload up to three base64 service images to Azure Blob, returning a URL or None per slot."""
        container = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
        timestamp = _current_timestamp_prefix()

        async def _one(index: int, value: Optional[str]) -> Optional[str]:
            if not value or str(value).strip().lower() == "null":
                return None
            if _is_stored_media_url(value):
                return value
            try:
                content = _to_bytes(value)
                if not content:
                    return None
                blob_name = f"{contact_id}/{timestamp}_{uuid.uuid4().hex[:8]}_ServiceImg{index}.png"
                return await _upload_blob_bytes(container, blob_name, content, "image/png")
            except Exception as e:
                logger.warning(f"[add_services] Image{index} upload failed: {e}")
                return None

        return await asyncio.gather(
            _one(1, service_detail.serviceimage1),
            _one(2, service_detail.serviceimage2),
            _one(3, service_detail.serviceimage3),
        )

    @staticmethod
    async def _generate_category(
        description: Optional[str],
    ) -> Optional[CategoryGenerationResponse]:
        """Generate a category from a service description, or return None when blank or generation fails."""
        if not description or not description.strip():
            return None
        try:
            return await generate_category_logic(description)
        except Exception as e:
            logger.warning(f"[add_services] Category generation failed: {e}")
            return None

    @staticmethod
    async def _insert_service_row(
        provider: Dict[str, Any],
        contact_id: str,
        userprofile_id: str,
        service_detail: ServiceDetailRequest,
        image_urls: Tuple[Optional[str], Optional[str], Optional[str]],
        category: Optional[CategoryGenerationResponse],
    ) -> Optional[Tuple[str, str]]:
        """Insert a verified service into both providerservices tables, or update the same-named existing row.

        Args:
            provider: Provider row supplying business, address and contact fields.
            contact_id: Owning contact id.
            userprofile_id: Owning userprofile id.
            service_detail: Service name, description, price, quantity and items.
            image_urls: Uploaded URLs for the three service image slots.
            category: Generated category, or None to fall back to the provider's category.

        Returns:
            (service_id, copy_id) on success, or None when the main insert fails.
        """
        service_id = str(uuid.uuid4())
        copy_id = service_id
        created_on = datetime.now(timezone.utc).replace(tzinfo=None)

        business_category = _normalize_biz_category(
            getattr(service_detail, "service_type", None)
            or (category.business_type if category else None)
            or provider.get("businesscategory")
        )
        service_category = category.subcategory if category else None

        img1, img2, img3 = image_urls

        service_items_json = (
            json.dumps(service_detail.serviceitems)
            if getattr(service_detail, "serviceitems", None)
            else None
        )

        latitude_text = str(provider["latitude"]) if provider.get("latitude") is not None else None
        longitude_text = str(provider["longitude"]) if provider.get("longitude") is not None else None
        latitude_num = provider.get("latitude")
        longitude_num = provider.get("longitude")

        existing = await Database.fetchrow(
            "SELECT providerservicesid FROM etown_providerservices WHERE providerid = $1 AND services = $2",
            provider["providerid"],
            service_detail.service_name,
        )
        if existing:
            service_id = existing["providerservicesid"]
            copy_id = service_id
            update_sql = """
                servicedescription = $1, pricerange = $2, quantity = $3,
                serviceimageurl1 = $4, serviceimageurl2 = $5, serviceimageurl3 = $6,
                serviceitems = $7::jsonb,
                businessname = $8, address = $9, additionaladdressinfo = $10,
                areaname = $11, city = $12, statecode = $13,
                latitude = $14, longitude = $15,
                mobilenumber = $16, email = $17, availability = $18,
                businesscategory = COALESCE($20, businesscategory),
                modifieddate = NOW()
            """
            shared = (
                service_detail.description, service_detail.pricerange, service_detail.quantity,
                img1, img2, img3, service_items_json,
                provider.get("businessname"), provider.get("address"), provider.get("additionaladdressinfo"),
                provider.get("areaname"), provider.get("city"), provider.get("statecode"),
            )
            service_type = getattr(service_detail, "service_type", None)
            await Database.execute(
                f"UPDATE etown_providerservices SET {update_sql} WHERE providerservicesid = $19",
                *shared, latitude_text, longitude_text,
                provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
                service_id, service_type,
            )
            await Database.execute(
                f"UPDATE etown_providerservices_copy SET {update_sql} WHERE providerservicesid = $19",
                *shared, latitude_num, longitude_num,
                provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
                service_id, service_type,
            )
            logger.info(
                f"[add_services] Service '{service_detail.service_name}' updated (id={service_id}) for provider {provider['providerid']}"
            )
            return (service_id, copy_id)

        insert_main = """
            INSERT INTO etown_providerservices (
                providerservicesid, providerid, contactid, contactidname,
                userprofileid, userprofileidname,
                businessname, businesscategory, servicecategory,
                address, additionaladdressinfo, areaname, city, statecode,
                latitude, longitude, mobilenumber, email, availability,
                services, servicedescription, pricerange, quantity,
                serviceimageurl, serviceimageurl1, serviceimageurl2, serviceimageurl3,
                providertype, publicsearchprovider, createdon, serviceitems, source
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7, $8, $9,
                $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19,
                $20, $21, $22, $23,
                $24, $25, $26, $27,
                'etown_verified', 'verified', $28, $29::jsonb, 'Onboard'
            )
        """

        main_args = (
            service_id, provider["providerid"],
            contact_id, provider.get("contactidname"),
            userprofile_id, provider.get("userprofileidname"),
            provider.get("businessname"), business_category, service_category,
            provider.get("address"), provider.get("additionaladdressinfo"),
            provider.get("areaname"), provider.get("city"), provider.get("statecode"),
            latitude_text, longitude_text,
            provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
            service_detail.service_name, service_detail.description,
            service_detail.pricerange, service_detail.quantity,
            provider.get("serviceimageurl"), img1, img2, img3,
            created_on, service_items_json,
        )

        content_vector_literal: Optional[str] = None

        insert_copy = """
            INSERT INTO etown_providerservices_copy (
                providerservicesid, providerid, contactid, contactidname,
                userprofileid, userprofileidname,
                businessname, businesscategory, servicecategory,
                address, additionaladdressinfo, areaname, city, statecode,
                latitude, longitude, mobilenumber, email, availability,
                services, servicedescription, pricerange, quantity,
                serviceimageurl, serviceimageurl1, serviceimageurl2, serviceimageurl3,
                providertype, publicsearchprovider, createdon, content_vector, serviceitems, source
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7, $8, $9,
                $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19,
                $20, $21, $22, $23,
                $24, $25, $26, $27,
                'etown_verified', 'verified', $28, $29::vector, $30::jsonb, 'Onboard'
            )
        """

        copy_args = (
            copy_id, provider["providerid"],
            contact_id, provider.get("contactidname"),
            userprofile_id, provider.get("userprofileidname"),
            provider.get("businessname"), business_category, service_category,
            provider.get("address"), provider.get("additionaladdressinfo"),
            provider.get("areaname"), provider.get("city"), provider.get("statecode"),
            latitude_num, longitude_num,
            provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
            service_detail.service_name, service_detail.description,
            service_detail.pricerange, service_detail.quantity,
            provider.get("serviceimageurl"), img1, img2, img3,
            created_on, content_vector_literal, service_items_json,
        )

        try:
            await Database.execute(insert_main, *main_args)
        except Exception as e:
            logger.error(f"[add_services] Insert into etown_providerservices failed for {service_id}: {e}", exc_info=True)
            return None

        try:
            await Database.execute(insert_copy, *copy_args)
        except Exception as e:
            logger.error(f"[add_services] Insert into etown_providerservices_copy failed for {copy_id}: {e}", exc_info=True)

        logger.info(f"[add_services] Service '{service_detail.service_name}' inserted (id={service_id}) for provider {provider['providerid']}")
        return (service_id, copy_id)

    @staticmethod
    async def _process_one_service(
        provider: Dict[str, Any],
        contact_id: str,
        userprofile_id: str,
        service_detail: ServiceDetailRequest,
        semaphore: asyncio.Semaphore,
    ) -> Optional[Tuple[str, str, "ServiceDetailRequest"]]:
        """Upload one service's images and insert its verified row under the semaphore, returning None on failure."""
        async with semaphore:
            try:
                img1, img2, img3 = await ProviderServiceManagementService._upload_service_images(
                    contact_id, service_detail
                )
                service_detail.serviceimage1 = img1
                service_detail.serviceimage2 = img2
                service_detail.serviceimage3 = img3

                result = await ProviderServiceManagementService._insert_service_row(
                    provider=provider,
                    contact_id=contact_id,
                    userprofile_id=userprofile_id,
                    service_detail=service_detail,
                    image_urls=(img1, img2, img3),
                    category=None,
                )
                if result is None:
                    return None
                service_id, copy_id = result
                return (service_id, copy_id, service_detail)
            except Exception as e:
                logger.error(
                    f"[add_services] Failed to process service '{service_detail.service_name}': {e}",
                    exc_info=True,
                )
                return None

    @staticmethod
    async def add_services_to_provider_workflow(
        contact_id: str,
        userprofile_id: str,
        provider_id: str,
        servicedetails: List[ServiceDetailRequest],
    ) -> Tuple[str, List[Tuple[str, str, ServiceDetailRequest]], Optional[Dict[str, Any]]]:
        """Insert or update verified services for a provider under its listing cap, rolling back on concurrent overshoot.

        Args:
            contact_id: Contact adding the services.
            userprofile_id: Userprofile adding the services.
            provider_id: Provider receiving the services.
            servicedetails: Services to insert or update.

        Returns:
            (message, enhancements, provider) for scheduling run_background_enhancements.

        Raises:
            PlanLimitError: When new listings would exceed the plan's max_listings.
        """
        if not servicedetails:
            return ("No service details provided", [], None)

        contact = await ProviderServiceManagementService._get_contact(contact_id)
        if not contact:
            logger.warning(f"[add_services] Contact not found: {contact_id}")
            return ("User Not Found", [], None)

        provider = await ProviderServiceManagementService._get_provider(provider_id)
        if not provider:
            logger.warning(f"[add_services] Provider not found: {provider_id}")
            return ("Provider not found", [], None)

        if not provider.get("contactidname"):
            provider["contactidname"] = (
                contact.get("firstname") or provider.get("businessname")
            )
        if not provider.get("userprofileidname"):
            provider["userprofileidname"] = (
                await ProviderServiceManagementService._get_userprofile_name(userprofile_id)
                or contact.get("firstname")
                or provider.get("businessname")
            )
        if not provider.get("email"):
            provider["email"] = contact.get("email")

        cap_userprofile_id = provider.get("userprofileid") or userprofile_id
        if provider.get("userprofileid") and provider["userprofileid"] != userprofile_id:
            logger.warning(
                "[add_services] userprofile_id=%s does not own provider %s (owner=%s); "
                "capping against the owner",
                userprofile_id, provider["providerid"], provider["userprofileid"],
            )

        plan, listing_cap, existing_names = await check_listing_cap(
            cap_userprofile_id,
            provider["providerid"],
            [sd.service_name for sd in servicedetails],
        )

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SERVICES)
        results = await asyncio.gather(
            *[
                ProviderServiceManagementService._process_one_service(
                    provider=provider,
                    contact_id=contact_id,
                    userprofile_id=userprofile_id,
                    service_detail=sd,
                    semaphore=semaphore,
                )
                for sd in servicedetails
            ],
            return_exceptions=False,
        )

        enhancements: List[Tuple[str, str, ServiceDetailRequest]] = [r for r in results if r is not None]

        if listing_cap is not None and existing_names is not None and enhancements:
            try:
                used_after = await Database.fetchval(
                    POINT_IN_TIME_LIMITS["max_listings"]["sql"], cap_userprofile_id
                ) or 0
            except Exception as e:
                logger.warning(
                    f"[add_services] Listing cap recount failed for {cap_userprofile_id}, keeping rows: {e}"
                )
            else:
                if used_after > listing_cap:
                    created = [
                        (service_id, copy_id)
                        for service_id, copy_id, sd in enhancements
                        if sd.service_name not in existing_names
                    ]
                    if created:
                        await Database.execute(
                            "DELETE FROM etown_providerservices WHERE providerservicesid = ANY($1::text[])",
                            [service_id for service_id, _ in created],
                        )
                        await Database.execute(
                            "DELETE FROM etown_providerservices_copy WHERE providerservicesid = ANY($1::text[])",
                            [copy_id for _, copy_id in created if copy_id],
                        )
                    logger.warning(
                        f"[add_services] Concurrent write took {userprofile_id} to {used_after}/"
                        f"{listing_cap} listings; rolled back {len(created)} row(s)"
                    )
                    err = PlanLimitError(
                        f"Your {plan['plan_name']} plan allows {listing_cap} listings.",
                        plan_code=plan["plan_code"], limit=listing_cap, used=used_after,
                        userprofile_id=cap_userprofile_id, limit_column="max_listings",
                    )
                    await record_refusal(err)
                    raise err

        added = len(enhancements)
        logger.info(
            f"[add_services] Provider {provider_id}: {added}/{len(servicedetails)} services inserted"
        )
        message = "Services added successfully" if added > 0 else "No services were added"
        return (message, enhancements, provider)

    @staticmethod
    async def _insert_unverified_service_row(
        provider: Dict[str, Any],
        contact_id: str,
        userprofile_id: str,
        service_detail: ServiceDetailRequest,
        image_urls: Tuple[Optional[str], Optional[str], Optional[str]],
        category: Optional[CategoryGenerationResponse],
    ) -> Optional[Tuple[str, str]]:
        """Insert a non-verified service into both providerservices tables, or update the same-named existing row.

        Args:
            provider: Provider row supplying business, address, contact and source fields.
            contact_id: Owning contact id.
            userprofile_id: Owning userprofile id.
            service_detail: Service name, description, price, quantity and items.
            image_urls: Uploaded URLs for the three service image slots.
            category: Generated category, or None to fall back to the provider's category.

        Returns:
            (service_id, copy_id) on success, or None when the main insert fails.
        """
        service_id = str(uuid.uuid4())
        copy_id = service_id
        created_on = datetime.now(timezone.utc).replace(tzinfo=None)

        business_category = _normalize_biz_category(
            (category.business_type if category else None) or provider.get("businesscategory")
        )
        service_category = category.subcategory if category else None

        img1, img2, img3 = image_urls

        service_items_json = (
            json.dumps(service_detail.serviceitems)
            if getattr(service_detail, "serviceitems", None)
            else None
        )

        latitude_text = str(provider["latitude"]) if provider.get("latitude") is not None else None
        longitude_text = str(provider["longitude"]) if provider.get("longitude") is not None else None
        latitude_num = provider.get("latitude")
        longitude_num = provider.get("longitude")

        existing = await Database.fetchrow(
            "SELECT providerservicesid FROM etown_providerservices WHERE providerid = $1 AND services = $2",
            provider["providerid"],
            service_detail.service_name,
        )
        if existing:
            service_id = existing["providerservicesid"]
            copy_id = service_id
            update_sql = """
                servicedescription = $1, pricerange = $2, quantity = $3,
                serviceimageurl1 = $4, serviceimageurl2 = $5, serviceimageurl3 = $6,
                serviceitems = $7::jsonb,
                businessname = $8, address = $9, additionaladdressinfo = $10,
                areaname = $11, city = $12, statecode = $13,
                latitude = $14, longitude = $15,
                mobilenumber = $16, email = $17, availability = $18,
                modifieddate = NOW()
            """
            shared = (
                service_detail.description, service_detail.pricerange, service_detail.quantity,
                img1, img2, img3, service_items_json,
                provider.get("businessname"), provider.get("address"), provider.get("additionaladdressinfo"),
                provider.get("areaname"), provider.get("city"), provider.get("statecode"),
            )
            await Database.execute(
                f"UPDATE etown_providerservices SET {update_sql} WHERE providerservicesid = $19",
                *shared, latitude_text, longitude_text,
                provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
                service_id,
            )
            await Database.execute(
                f"UPDATE etown_providerservices_copy SET {update_sql} WHERE providerservicesid = $19",
                *shared, latitude_num, longitude_num,
                provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
                service_id,
            )
            logger.info(
                f"[add_unverified_services] Service '{service_detail.service_name}' updated (id={service_id}) for provider {provider['providerid']}"
            )
            return (service_id, copy_id)

        insert_main = """
            INSERT INTO etown_providerservices (
                providerservicesid, providerid, contactid, contactidname,
                userprofileid, userprofileidname,
                businessname, businesscategory, servicecategory,
                address, additionaladdressinfo, areaname, city, statecode,
                latitude, longitude, mobilenumber, email, availability,
                services, servicedescription, pricerange, quantity,
                serviceimageurl, serviceimageurl1, serviceimageurl2, serviceimageurl3,
                providertype, publicsearchprovider, createdon, serviceitems, source
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7, $8, $9,
                $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19,
                $20, $21, $22, $23,
                $24, $25, $26, $27,
                'etown_nonverified', 'non-verified', $28, $29::jsonb, $30
            )
        """

        source = provider.get("source") or "FOS"

        main_args = (
            service_id, provider["providerid"],
            contact_id, provider.get("contactidname"),
            userprofile_id, provider.get("userprofileidname"),
            provider.get("businessname"), business_category, service_category,
            provider.get("address"), provider.get("additionaladdressinfo"),
            provider.get("areaname"), provider.get("city"), provider.get("statecode"),
            latitude_text, longitude_text,
            provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
            service_detail.service_name, service_detail.description,
            service_detail.pricerange, service_detail.quantity,
            provider.get("serviceimageurl"), img1, img2, img3,
            created_on, service_items_json, source,
        )

        content_vector_literal: Optional[str] = None

        insert_copy = """
            INSERT INTO etown_providerservices_copy (
                providerservicesid, providerid, contactid, contactidname,
                userprofileid, userprofileidname,
                businessname, businesscategory, servicecategory,
                address, additionaladdressinfo, areaname, city, statecode,
                latitude, longitude, mobilenumber, email, availability,
                services, servicedescription, pricerange, quantity,
                serviceimageurl, serviceimageurl1, serviceimageurl2, serviceimageurl3,
                providertype, publicsearchprovider, createdon, content_vector, serviceitems, source
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6,
                $7, $8, $9,
                $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19,
                $20, $21, $22, $23,
                $24, $25, $26, $27,
                'etown_nonverified', 'non-verified', $28, $29::vector, $30::jsonb, $31
            )
        """

        copy_args = (
            copy_id, provider["providerid"],
            contact_id, provider.get("contactidname"),
            userprofile_id, provider.get("userprofileidname"),
            provider.get("businessname"), business_category, service_category,
            provider.get("address"), provider.get("additionaladdressinfo"),
            provider.get("areaname"), provider.get("city"), provider.get("statecode"),
            latitude_num, longitude_num,
            provider.get("mobilenumber"), provider.get("email"), provider.get("availability"),
            service_detail.service_name, service_detail.description,
            service_detail.pricerange, service_detail.quantity,
            provider.get("serviceimageurl"), img1, img2, img3,
            created_on, content_vector_literal, service_items_json, source,
        )

        try:
            await Database.execute(insert_main, *main_args)
        except Exception as e:
            logger.error(f"[add_unverified_services] Insert into etown_providerservices failed for {service_id}: {e}", exc_info=True)
            return None

        try:
            await Database.execute(insert_copy, *copy_args)
        except Exception as e:
            logger.error(f"[add_unverified_services] Insert into etown_providerservices_copy failed for {copy_id}: {e}", exc_info=True)

        logger.info(f"[add_unverified_services] Service '{service_detail.service_name}' inserted (id={service_id}) for provider {provider['providerid']}")
        return (service_id, copy_id)

    @staticmethod
    async def _process_one_unverified_service(
        provider: Dict[str, Any],
        contact_id: str,
        userprofile_id: str,
        service_detail: ServiceDetailRequest,
        semaphore: asyncio.Semaphore,
    ) -> Optional[Tuple[str, str, "ServiceDetailRequest"]]:
        """Upload one service's images and insert its non-verified row under the semaphore, returning None on failure."""
        async with semaphore:
            try:
                img1, img2, img3 = await ProviderServiceManagementService._upload_service_images(
                    contact_id, service_detail
                )
                service_detail.serviceimage1 = img1
                service_detail.serviceimage2 = img2
                service_detail.serviceimage3 = img3

                result = await ProviderServiceManagementService._insert_unverified_service_row(
                    provider=provider,
                    contact_id=contact_id,
                    userprofile_id=userprofile_id,
                    service_detail=service_detail,
                    image_urls=(img1, img2, img3),
                    category=None,
                )
                if result is None:
                    return None
                service_id, copy_id = result
                return (service_id, copy_id, service_detail)
            except Exception as e:
                logger.error(
                    f"[add_unverified_services] Failed to process service '{service_detail.service_name}': {e}",
                    exc_info=True,
                )
                return None

    @staticmethod
    async def add_services_to_unverified_provider_workflow(
        contact_id: str,
        userprofile_id: str,
        provider_id: str,
        servicedetails: List[ServiceDetailRequest],
        source: str = "FOS",
    ) -> Tuple[str, List[Tuple[str, str, ServiceDetailRequest]], Optional[Dict[str, Any]]]:
        """Insert or update non-verified services for a provider, stamped with the capture source and no listing cap.

        Args:
            contact_id: Contact adding the services.
            userprofile_id: Userprofile adding the services.
            provider_id: Provider receiving the services.
            servicedetails: Services to insert or update.
            source: Capturer role stamped on every row, 'FOS' by default.

        Returns:
            (message, enhancements, provider) for scheduling run_background_enhancements.
        """
        if not servicedetails:
            return ("No service details provided", [], None)

        contact = await ProviderServiceManagementService._get_contact(contact_id)
        if not contact:
            logger.warning(f"[add_unverified_services] Contact not found: {contact_id}")
            return ("User Not Found", [], None)

        provider = await ProviderServiceManagementService._get_provider(provider_id)
        if not provider:
            logger.warning(f"[add_unverified_services] Provider not found: {provider_id}")
            return ("Provider not found", [], None)

        if not provider.get("contactidname"):
            provider["contactidname"] = (
                contact.get("firstname") or provider.get("businessname")
            )
        if not provider.get("userprofileidname"):
            provider["userprofileidname"] = (
                await ProviderServiceManagementService._get_userprofile_name(userprofile_id)
                or contact.get("firstname")
                or provider.get("businessname")
            )
        if not provider.get("email"):
            provider["email"] = contact.get("email")

        provider["providertype"] = "etown_nonverified"
        provider["publicsearchprovider"] = "non-verified"
        provider["source"] = source

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SERVICES)
        results = await asyncio.gather(
            *[
                ProviderServiceManagementService._process_one_unverified_service(
                    provider=provider,
                    contact_id=contact_id,
                    userprofile_id=userprofile_id,
                    service_detail=sd,
                    semaphore=semaphore,
                )
                for sd in servicedetails
            ],
            return_exceptions=False,
        )

        enhancements: List[Tuple[str, str, ServiceDetailRequest]] = [r for r in results if r is not None]

        added = len(enhancements)
        logger.info(
            f"[add_unverified_services] Provider {provider_id}: {added}/{len(servicedetails)} services inserted"
        )
        message = "Services added successfully" if added > 0 else "No services were added"
        return (message, enhancements, provider)

    @staticmethod
    async def run_background_enhancements(
        provider: Dict[str, Any],
        contact_id: str,
        userprofile_id: str,
        service_enhancements: List[Tuple[str, str, ServiceDetailRequest]],
    ) -> None:
        """Generate category and embedding for each added service, update both tables, and mirror it to Dataverse."""
        business_category_fallback = provider.get("businesscategory")

        for service_id, copy_id, sd in service_enhancements:
            try:
                category = await ProviderServiceManagementService._generate_category(sd.description)
                img1, img2, img3 = sd.serviceimage1, sd.serviceimage2, sd.serviceimage3

                business_category = _normalize_biz_category(
                    getattr(sd, "service_type", None)
                    or (category.business_type if category else None)
                    or business_category_fallback
                )
                service_category = category.subcategory if category else None
                general_category = category.generalcategory if category else None

                embedding_text = " ".join(
                    filter(None, [
                        sd.service_name,
                        sd.description,
                        business_category,
                        service_category,
                        provider.get("businessname"),
                        provider.get("address"),
                    ])
                )
                content_vector_literal: Optional[str] = None
                try:
                    vector = await generate_embedding(embedding_text)
                    if vector:
                        content_vector_literal = "[" + ",".join(str(v) for v in vector) + "]"
                except Exception as emb_err:
                    logger.warning(f"[add_services_bg] Embedding failed for {service_id}: {emb_err}")

                await Database.execute(
                    """
                    UPDATE etown_providerservices
                    SET businesscategory = $1,
                        servicecategory  = $2,
                        serviceimageurl1 = $3,
                        serviceimageurl2 = $4,
                        serviceimageurl3 = $5,
                        generalcategory  = COALESCE($7, generalcategory),
                        modifieddate     = NOW()
                    WHERE providerservicesid = $6
                    """,
                    business_category, service_category, img1, img2, img3, service_id,
                    general_category,
                )

                await Database.execute(
                    """
                    UPDATE etown_providerservices_copy
                    SET businesscategory = $1,
                        servicecategory  = $2,
                        serviceimageurl1 = $3,
                        serviceimageurl2 = $4,
                        serviceimageurl3 = $5,
                        content_vector   = $6::vector,
                        generalcategory  = COALESCE($8, generalcategory),
                        modifieddate     = NOW()
                    WHERE providerservicesid = $7
                    """,
                    business_category, service_category, img1, img2, img3,
                    content_vector_literal, copy_id, general_category,
                )

                logger.info(
                    f"[add_services_bg] Enhanced service '{sd.service_name}' "
                    f"(id={service_id}, cat={business_category}/{service_category})"
                )

                service_items_value = getattr(sd, "serviceitems", None)
                await sync_add_service_to_dataverse(
                    service_id=service_id,
                    provider=provider,
                    contact_id=contact_id,
                    userprofile_id=userprofile_id,
                    service_name=sd.service_name,
                    service_description=sd.description,
                    business_category=business_category,
                    service_category=service_category,
                    img1=img1,
                    img2=img2,
                    img3=img3,
                    service_items=service_items_value,
                    source=provider.get("source") or 'Onboard',
                )
            except Exception as e:
                logger.error(
                    f"[add_services_bg] Enhancement failed for service_id={service_id}: {e}",
                    exc_info=True,
                )

    @staticmethod
    async def delete_service_workflow(
        contact_id: str,
        provider_id: str,
        providerservices_id: str,
    ) -> str:
        """Delete a provider-owned service from both providerservices tables, matching its twin by name and description.

        Args:
            contact_id: Contact that must own the provider.
            provider_id: Provider the service belongs to.
            providerservices_id: Service id in either table.

        Returns:
            Dict with the result message and providerservices_orig_id of the main-table row.
        """
        contact = await ProviderServiceManagementService._get_contact(contact_id)
        if not contact:
            logger.warning(f"[delete_service] Contact not found: {contact_id}")
            return {"message": "User Not Found", "providerservices_orig_id": None}

        provider = await ProviderServiceManagementService._get_provider(provider_id)
        if not provider:
            logger.warning(f"[delete_service] Provider not found: {provider_id}")
            return {"message": "Provider not found", "providerservices_orig_id": None}

        if provider.get("contactid") and provider["contactid"] != contact_id:
            logger.warning(
                f"[delete_service] Ownership mismatch: contact {contact_id} does not own provider {provider_id}"
            )
            return {"message": "Unauthorized", "providerservices_orig_id": None}
        copy_row = await Database.fetchrow(
            """
            SELECT providerservicesid, services, servicedescription
            FROM etown_providerservices_copy
            WHERE providerservicesid = $1 AND providerid = $2
            """,
            providerservices_id,
            provider_id,
        )
        main_row = await Database.fetchrow(
            """
            SELECT providerservicesid, services, servicedescription
            FROM etown_providerservices
            WHERE providerservicesid = $1 AND providerid = $2
            """,
            providerservices_id,
            provider_id,
        )

        if not copy_row and not main_row:
            logger.warning(
                f"[delete_service] Service not found: providerservices_id={providerservices_id} provider_id={provider_id}"
            )
            return {"message": "Service not found", "providerservices_orig_id": None}
        found_row = copy_row or main_row
        svc_name = found_row["services"]
        svc_desc = found_row["servicedescription"]

        if copy_row:
            orig_row = await Database.fetchrow(
                """
                SELECT providerservicesid FROM etown_providerservices
                WHERE providerid = $1
                  AND services IS NOT DISTINCT FROM $2
                  AND servicedescription IS NOT DISTINCT FROM $3
                LIMIT 1
                """,
                provider_id, svc_name, svc_desc,
            )
            providerservices_orig_id = orig_row["providerservicesid"] if orig_row else None
        else:
            providerservices_orig_id = providerservices_id

        try:
            if copy_row:
                await Database.execute(
                    "DELETE FROM etown_providerservices_copy WHERE providerservicesid = $1",
                    providerservices_id,
                )
                await Database.execute(
                    """
                    DELETE FROM etown_providerservices
                    WHERE providerid = $1
                      AND services IS NOT DISTINCT FROM $2
                      AND servicedescription IS NOT DISTINCT FROM $3
                    """,
                    provider_id, svc_name, svc_desc,
                )
            else:
                await Database.execute(
                    "DELETE FROM etown_providerservices WHERE providerservicesid = $1",
                    providerservices_id,
                )
                await Database.execute(
                    """
                    DELETE FROM etown_providerservices_copy
                    WHERE providerid = $1
                      AND services IS NOT DISTINCT FROM $2
                      AND servicedescription IS NOT DISTINCT FROM $3
                    """,
                    provider_id, svc_name, svc_desc,
                )
        except Exception as e:
            logger.error(f"[delete_service] Delete failed for {providerservices_id}: {e}", exc_info=True)
            return {"message": "Service not found", "providerservices_orig_id": None}

        logger.info(f"[delete_service] Service deleted: id={providerservices_id} provider={provider_id}")
        return {"message": "Service deleted successfully", "providerservices_orig_id": providerservices_orig_id}

    @staticmethod
    async def edit_service_workflow(
        contact_id: str,
        provider_id: Optional[str],
        providerservices_id: str,
        updates: Dict[str, Any],
        userprofile_id: Optional[str] = None,
    ) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
        """Apply non-null partial updates to an owned service in both tables in one statement, uploading images first.

        Args:
            contact_id: Contact that must own the service, when the row has one.
            provider_id: Provider that must own the service, or None to skip the check.
            providerservices_id: Service to update.
            updates: Partial fields plus is_description and offering_operations flags.
            userprofile_id: Userprofile that must own the service, or None to skip the check.

        Returns:
            (message, provider, bg_payload, replaced serviceitems or None).
        """
        is_description = bool(updates.pop("is_description", None))
        off_ops_raw = updates.pop("offering_operations", None)

        clear_flags: Dict[int, bool] = {1: False, 2: False, 3: False}
        slot_urls: Dict[int, Optional[str]] = {1: None, 2: None, 3: None}
        to_upload: Dict[int, str] = {}
        for slot in (1, 2, 3):
            raw = updates.get(f"serviceimage{slot}")
            if raw is None:
                continue
            token = str(raw).strip().lower()
            if token in ("", "null"):
                continue
            if token == "deleted":
                clear_flags[slot] = True
            elif _is_stored_media_url(raw):
                slot_urls[slot] = raw
            else:
                to_upload[slot] = raw
        if to_upload:
            tmp_sd = ServiceDetailRequest(
                service_name=updates.get("service_name") or "service",
                serviceimage1=to_upload.get(1),
                serviceimage2=to_upload.get(2),
                serviceimage3=to_upload.get(3),
            )
            uploaded = await ProviderServiceManagementService._upload_service_images(contact_id, tmp_sd)
            for slot in (1, 2, 3):
                if slot in to_upload and uploaded[slot - 1]:
                    slot_urls[slot] = uploaded[slot - 1]

        business_category: Optional[str] = None
        service_category: Optional[str] = None
        general_category: Optional[str] = None
        if is_description:
            category = await ProviderServiceManagementService._generate_category(
                updates.get("description")
            )
            if category:
                business_category = _normalize_biz_category(category.business_type)
                service_category = category.subcategory
                general_category = category.generalcategory

        new_items = updates.get("serviceitems")
        serviceitems_replaced = new_items is not None
        if serviceitems_replaced and new_items:
            assign_node_ids([{"categories": new_items}])

        row = await Database.fetchrow(
            """
            WITH found AS (
                SELECT providerservicesid, providerid, userprofileid, contactid,
                       services, servicedescription, businessname, businesscategory,
                       address, city, areaname, statecode, mobilenumber, email,
                       latitude, longitude, availability, additionaladdressinfo,
                       serviceimageurl, source, providertype, publicsearchprovider
                FROM etown_providerservices
                WHERE providerservicesid = $1
                LIMIT 1
            ),
            target AS (
                SELECT * FROM found
                WHERE ($2::text IS NULL OR contactid IS NULL OR contactid = $2)
                  AND ($3::text IS NULL OR providerid IS NULL OR providerid = $3)
                  AND ($4::text IS NULL OR userprofileid IS NULL OR userprofileid = $4)
            ),
            upd_main AS (
                UPDATE etown_providerservices ps SET
                    services           = COALESCE($5,  ps.services),
                    servicedescription = COALESCE($6,  ps.servicedescription),
                    pricerange         = COALESCE($7,  ps.pricerange),
                    quantity           = COALESCE($8,  ps.quantity),
                    businesscategory   = COALESCE($9,  ps.businesscategory),
                    servicecategory    = COALESCE($10, ps.servicecategory),
                    generalcategory    = COALESCE($11, ps.generalcategory),
                    serviceimageurl1   = CASE WHEN $12::boolean THEN NULL ELSE COALESCE($13, ps.serviceimageurl1) END,
                    serviceimageurl2   = CASE WHEN $14::boolean THEN NULL ELSE COALESCE($15, ps.serviceimageurl2) END,
                    serviceimageurl3   = CASE WHEN $16::boolean THEN NULL ELSE COALESCE($17, ps.serviceimageurl3) END,
                    serviceitems       = COALESCE($18::jsonb, ps.serviceitems),
                    modifieddate       = NOW()
                FROM target t
                WHERE ps.providerservicesid = t.providerservicesid
                RETURNING ps.providerservicesid, ps.services, ps.servicedescription,
                          ps.businesscategory, ps.servicecategory, ps.generalcategory,
                          ps.pricerange, ps.quantity, ps.serviceimageurl1,
                          ps.serviceimageurl2, ps.serviceimageurl3, ps.serviceitems
            ),
            upd_copy AS (
                UPDATE etown_providerservices_copy psc SET
                    services           = COALESCE($5,  psc.services),
                    servicedescription = COALESCE($6,  psc.servicedescription),
                    pricerange         = COALESCE($7,  psc.pricerange),
                    quantity           = COALESCE($8,  psc.quantity),
                    businesscategory   = COALESCE($9,  psc.businesscategory),
                    servicecategory    = COALESCE($10, psc.servicecategory),
                    generalcategory    = COALESCE($11, psc.generalcategory),
                    serviceimageurl1   = CASE WHEN $12::boolean THEN NULL ELSE COALESCE($13, psc.serviceimageurl1) END,
                    serviceimageurl2   = CASE WHEN $14::boolean THEN NULL ELSE COALESCE($15, psc.serviceimageurl2) END,
                    serviceimageurl3   = CASE WHEN $16::boolean THEN NULL ELSE COALESCE($17, psc.serviceimageurl3) END,
                    serviceitems       = COALESCE($18::jsonb, psc.serviceitems),
                    modifieddate       = NOW()
                FROM target t
                WHERE psc.providerservicesid = t.providerservicesid
                   OR (NOT EXISTS (
                           SELECT 1 FROM etown_providerservices_copy x
                           WHERE x.providerservicesid = t.providerservicesid
                       )
                       AND psc.providerid = t.providerid
                       AND t.services IS NOT NULL
                       AND psc.services = t.services
                       AND psc.servicedescription IS NOT DISTINCT FROM t.servicedescription)
                RETURNING psc.providerservicesid
            )
            SELECT f.*,
                   EXISTS (SELECT 1 FROM target)                        AS owned,
                   m.services            AS new_services,
                   m.servicedescription  AS new_servicedescription,
                   m.businesscategory    AS new_businesscategory,
                   m.servicecategory     AS new_servicecategory,
                   m.generalcategory     AS new_generalcategory,
                   m.pricerange          AS new_pricerange,
                   m.quantity            AS new_quantity,
                   m.serviceimageurl1    AS new_serviceimageurl1,
                   m.serviceimageurl2    AS new_serviceimageurl2,
                   m.serviceimageurl3    AS new_serviceimageurl3,
                   m.serviceitems        AS new_serviceitems,
                   ARRAY(SELECT providerservicesid FROM upd_copy)       AS copy_ids
            FROM found f
            LEFT JOIN upd_main m ON m.providerservicesid = f.providerservicesid
            """,
            providerservices_id,
            contact_id,
            provider_id,
            userprofile_id,
            updates.get("service_name"),
            updates.get("description"),
            updates.get("pricerange"),
            updates.get("quantity"),
            business_category,
            service_category,
            general_category,
            clear_flags[1], slot_urls[1],
            clear_flags[2], slot_urls[2],
            clear_flags[3], slot_urls[3],
            json.dumps(new_items) if serviceitems_replaced else None,
        )

        if not row:
            logger.warning(
                f"[edit_service] Service not found: providerservices_id={providerservices_id}"
            )
            return ("Service not found", None, None, None)
        if not row["owned"]:
            logger.warning(
                f"[edit_service] Ownership mismatch: contact={contact_id} provider={provider_id} "
                f"userprofile={userprofile_id} does not own service {providerservices_id}"
            )
            return ("Unauthorized", None, None, None)

        provider = {
            "providerid": row["providerid"],
            "businessname": row["businessname"],
            "businesscategory": row["new_businesscategory"],
            "address": row["address"],
            "city": row["city"],
            "areaname": row["areaname"],
            "statecode": row["statecode"],
            "mobilenumber": row["mobilenumber"],
            "email": row["email"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "availability": row["availability"],
            "additionaladdressinfo": row["additionaladdressinfo"],
            "serviceimageurl": row["serviceimageurl"],
            "providertype": row["providertype"],
            "publicsearchprovider": row["publicsearchprovider"],
        }

        raw_new_items = row["new_serviceitems"]
        if isinstance(raw_new_items, str):
            try:
                raw_new_items = json.loads(raw_new_items)
            except (ValueError, TypeError):
                logger.warning("provider services: new_serviceitems is not valid JSON; treating it as null", exc_info=True)
                raw_new_items = None

        bg_payload = {
            "service_id": providerservices_id,
            "userprofile_id": row["userprofileid"],
            "contact_id": row["contactid"] or contact_id,
            "old_name": row["services"],
            "service_name": row["new_services"],
            "description": row["new_servicedescription"],
            "renamed": bool(
                updates.get("service_name") is not None
                and updates["service_name"] != row["services"]
            ),
            "serviceitems_replaced": serviceitems_replaced,
            "serviceitems": new_items,
            "offering_operations": [
                op if isinstance(op, dict) else op.model_dump() for op in (off_ops_raw or [])
            ],
            "business_category": row["new_businesscategory"],
            "service_category": row["new_servicecategory"],
            "general_category": row["new_generalcategory"],
            "pricerange": row["new_pricerange"],
            "quantity": row["new_quantity"],
            "img1": row["new_serviceimageurl1"],
            "img2": row["new_serviceimageurl2"],
            "img3": row["new_serviceimageurl3"],
            "current_serviceitems": raw_new_items,
            "source": row["source"],
            "copy_ids": [str(i) for i in row["copy_ids"]],
        }

        logger.info(
            f"[edit_service] Service updated: id={providerservices_id} "
            f"provider={row['providerid']} copies={len(row['copy_ids'])} "
            f"fields={sorted(k for k, v in updates.items() if v is not None)} "
            f"is_description={is_description} offering_ops={bool(off_ops_raw)}"
        )
        return (
            "Service updated successfully",
            provider,
            bg_payload,
            new_items if serviceitems_replaced else None,
        )

    @staticmethod
    async def _sync_offering_ops_to_catalogue(
        userprofile_id: str,
        ops: List[Dict[str, Any]],
        target_providerservices_id: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Apply offering operations to the etown_offerings tree, upload new images, and mirror it to serviceitems.

        Args:
            userprofile_id: Provider's userprofileid, the unique key for etown_offerings.
            ops: Plain-dict list of OfferingOperation.
            target_providerservices_id: Edited service row whose mirrored categories are returned.

        Returns:
            Categories written onto the target row, or None when nothing matched or it failed.
        """
        target_categories: Optional[List[Dict[str, Any]]] = None
        try:
            row = await Database.fetchrow(
                "SELECT items FROM etown_offerings WHERE userprofileid = $1",
                userprofile_id,
            )
            if not row:
                logger.warning(
                    "[edit_service] No etown_offerings row for userprofileid=%s — skipping tree sync",
                    userprofile_id,
                )
                return None

            raw = row["items"]
            tree = json.loads(raw) if isinstance(raw, str) else (raw or [])
            if not isinstance(tree, list):
                tree = []

            tree = apply_offering_operations(tree, ops)

            container = _normalize_container_name(settings.AZURE_BLOB_CONTAINER)
            if container:
                ts = _current_timestamp_prefix()
                sem = asyncio.Semaphore(_MAX_CONCURRENT_SERVICES)

                async def _upload_node_image(node: Dict[str, Any], field: str, suffix: str) -> None:
                    val = node.get(field)
                    if not val or _is_stored_media_url(val):
                        return
                    async with sem:
                        try:
                            content = _to_bytes(val)
                            if not content:
                                return
                            blob_name = f"{userprofile_id}/{ts}_{uuid.uuid4().hex[:8]}_{suffix}.png"
                            url = await _upload_blob_bytes(container, blob_name, content, "image/png")
                            if url:
                                node[field] = url
                        except Exception as _exc:
                            logger.error("[edit_service] Image upload failed for %s: %s", suffix, _exc, exc_info=True)

                upload_tasks = []
                for oi, offering in enumerate(tree):
                    if not isinstance(offering, dict):
                        continue
                    upload_tasks.append(_upload_node_image(offering, "offering_image", f"Off{oi}Img"))
                    for ci, cat in enumerate(offering.get("categories") or []):
                        if not isinstance(cat, dict):
                            continue
                        upload_tasks.append(_upload_node_image(cat, "category_image", f"Off{oi}Cat{ci}Img"))
                        for ii, item in enumerate(cat.get("items") or []):
                            if not isinstance(item, dict):
                                continue
                            upload_tasks.append(_upload_node_image(item, "item_image", f"Off{oi}Cat{ci}_Item{ii}Img"))
                if upload_tasks:
                    await asyncio.gather(*upload_tasks)
            else:
                logger.error("[edit_service] AZURE_BLOB_CONTAINER not configured — images saved as-is")

            await Database.execute(
                "UPDATE etown_offerings SET items = $1::jsonb, modifieddate = NOW() "
                "WHERE userprofileid = $2",
                json.dumps(tree),
                userprofile_id,
            )

            written = await mirror_offering_tree_to_serviceitems(
                userprofile_id, tree, sole_row_fallback=True
            )
            if target_providerservices_id:
                target_categories = written.get(target_providerservices_id)
        except Exception as exc:
            logger.error(
                "[edit_service] _sync_offering_ops_to_catalogue failed for %s: %s",
                userprofile_id,
                exc,
                exc_info=True,
            )

        return target_categories

    @staticmethod
    async def run_edit_enhancements(
        provider: Dict[str, Any],
        contact_id: str,
        bg_payload: Dict[str, Any],
    ) -> None:
        """Converge the etown_offerings tree with an edited service and upsert the service to Dataverse.

        Args:
            provider: Provider-shaped dict built from the edited service row.
            contact_id: Contact the service belongs to, for the Dataverse lookup binding.
            bg_payload: Deferred work assembled by edit_service_workflow.

        Returns:
            None.
        """
        service_id = bg_payload["service_id"]
        userprofile_id = bg_payload["userprofile_id"]

        try:
            if bg_payload.get("offering_operations"):
                await ProviderServiceManagementService._sync_offering_ops_to_catalogue(
                    userprofile_id=userprofile_id,
                    ops=bg_payload["offering_operations"],
                    target_providerservices_id=service_id,
                )

            old_name = bg_payload.get("old_name")
            new_name = bg_payload.get("service_name")
            items_replaced = bg_payload.get("serviceitems_replaced", False)
            if items_replaced or bg_payload.get("renamed"):
                off_row = await Database.fetchrow(
                    "SELECT items FROM etown_offerings WHERE userprofileid = $1",
                    userprofile_id,
                )
                if not off_row:
                    logger.info(
                        "[edit_service_bg] No etown_offerings row for userprofileid=%s — serviceitems "
                        "is this provider's only catalogue store", userprofile_id,
                    )
                else:
                    raw_off = off_row["items"]
                    off_tree = json.loads(raw_off) if isinstance(raw_off, str) else (raw_off or [])
                    if not isinstance(off_tree, list):
                        off_tree = []
                    keys = {
                        " ".join(str(n or "").lower().split())
                        for n in (old_name, new_name) if n
                    }
                    target_offering = next(
                        (
                            o for o in off_tree
                            if isinstance(o, dict)
                            and " ".join(str(o.get("offering_name") or "").lower().split()) in keys
                        ),
                        None,
                    )
                    if target_offering is None and items_replaced:
                        target_offering = {"offering_name": old_name}
                        off_tree.append(target_offering)
                    if target_offering is not None:
                        target_offering["offering_name"] = new_name
                        if items_replaced:
                            target_offering["categories"] = bg_payload.get("serviceitems") or []
                        assign_node_ids(off_tree)
                        await Database.execute(
                            "UPDATE etown_offerings SET items = $1::jsonb, modifieddate = NOW() "
                            "WHERE userprofileid = $2",
                            json.dumps(off_tree), userprofile_id,
                        )
                        logger.info(
                            "[edit_service_bg] offerings tree synced from the service row "
                            "(userprofileid=%s, offering=%r, items_replaced=%s, renamed=%s)",
                            userprofile_id, new_name, items_replaced, bg_payload.get("renamed"),
                        )

            await sync_add_service_to_dataverse(
                service_id=service_id,
                provider=provider,
                contact_id=contact_id,
                userprofile_id=userprofile_id,
                service_name=bg_payload.get("service_name"),
                service_description=bg_payload.get("description"),
                business_category=bg_payload.get("business_category"),
                service_category=bg_payload.get("service_category"),
                img1=bg_payload.get("img1"),
                img2=bg_payload.get("img2"),
                img3=bg_payload.get("img3"),
                service_items=bg_payload.get("current_serviceitems"),
                source=bg_payload.get("source"),
                general_category=bg_payload.get("general_category"),
                pricerange=bg_payload.get("pricerange"),
                quantity=bg_payload.get("quantity"),
            )
            logger.info(f"[edit_service_bg] Enhancements applied for service_id={service_id}")
        except Exception as e:
            logger.error(
                f"[edit_service_bg] Enhancement failed for service_id={service_id}: {e}",
                exc_info=True,
            )
