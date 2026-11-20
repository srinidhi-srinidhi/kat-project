"""Legacy Provider Utilities - dormant provider onboarding code plus the live state-code helper.

Domain module not wired to any endpoint: only convert_state_name_to_code() is imported elsewhere, by provider_review_utils, and enhance_provider_data() delegates to provider_utils.ProviderService; the registration and uniqueness checks duplicate what provider_utils does live.

Features:
    - Provider Onboarding (dormant): Transactional multi-table provider registration
    - Plan Inheritance (dormant): New provider rows inherit the contact's active subscription
    - Business Validation (dormant): Business-name and phone-number uniqueness checks
    - Delegated Enhancement: enhance_provider_data() forwards to provider_utils.ProviderService
    - State Codes: convert_state_name_to_code(), used by provider_review_utils

Classes:
    ProviderService(): Legacy provider service class; only enhance_provider_data() is live (a delegate)

Functions:
    convert_state_name_to_code(): Convert an Indian state name to its 2-letter state code
    ProviderService.enhance_provider_data(): Delegates to provider_utils.ProviderService.enhance_provider_data()
    ProviderService.check_business_name_exists(): Check business-name uniqueness across provider tables
    ProviderService.check_phone_exists(): Check if a phone number is already registered
    ProviderService.onboard_provider_in_db(): Register a new provider with transactional multi-table inserts
    ProviderService.validate_phone_format(): Validate a phone number is 8-15 digits

Dependencies:
    - app.db.client: Database connection pool access, with_retry decorator
    - app.etown.services.services_utils: get_location_details_logic() reverse-geocodes onboard coordinates
    - app.etown.plans.plans_utils: CONTACT_PLAN_* SQL fragments for inherited plan fields
    - app.etown.provider.provider_utils: Canonical ProviderService.enhance_provider_data(), delegated to
    - app.utils.embeddings: Vector embedding generation
    - fastapi: HTTPException for validation/conflict errors

Updates:
    21-Apr-2026: Initial creation with provider onboarding operations
    24-Aug-2026: New provider rows inherit the contact's active subscription instead of defaulting to free.
    26-Aug-2026: Reverse-geocode log now names the coordinates and resolved address, not the raw dict.
"""

from typing import Optional, Dict, Any, List
import time
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException

from app.db.client import Database, with_retry
from app.core.logging import logger
from app.etown.services.services_utils import (
    enhance_business_description_logic,
    generate_services_logic,
    generate_category_logic,
    generate_general_category_logic,
    get_location_details_logic
)
from app.utils.embeddings import generate_embedding
from app.etown.plans.plans_utils import (
    CONTACT_PLAN_COLUMNS,
    CONTACT_PLAN_CTE,
    CONTACT_PLAN_VALUES,
)

def convert_state_name_to_code(state_name: str) -> str:
    """Map an Indian state name to its two-letter code, falling back to the first two letters uppercased."""
    if not state_name:
        return None
        
    state_mapping = {
        'andhra pradesh': 'AP', 'arunachal pradesh': 'AR', 'assam': 'AS',
        'bihar': 'BR', 'chhattisgarh': 'CG', 'goa': 'GA', 'gujarat': 'GJ',
        'haryana': 'HR', 'himachal pradesh': 'HP', 'jharkhand': 'JH',
        'karnataka': 'KA', 'kerala': 'KL', 'madhya pradesh': 'MP',
        'maharashtra': 'MH', 'manipur': 'MN', 'meghalaya': 'ML',
        'mizoram': 'MZ', 'nagaland': 'NL', 'odisha': 'OR', 'punjab': 'PB',
        'rajasthan': 'RJ', 'sikkim': 'SK', 'tamil nadu': 'TN',
        'telangana': 'TS', 'tripura': 'TR', 'uttar pradesh': 'UP',
        'uttarakhand': 'UK', 'west bengal': 'WB',
        'andaman and nicobar islands': 'AN', 'chandigarh': 'CH',
        'dadra and nagar haveli': 'DN', 'daman and diu': 'DD',
        'lakshadweep': 'LD', 'delhi': 'DL', 'puducherry': 'PY'
    }
    
    normalized_name = state_name.lower().strip()
    
    if normalized_name in state_mapping:
        return state_mapping[normalized_name]
    
    for full_name, code in state_mapping.items():
        if normalized_name in full_name or full_name in normalized_name:
            return code
    
    if len(state_name) == 2 and state_name.isalpha():
        return state_name.upper()
    
    return state_name[:2].upper()


class ProviderService:
    """Service class for provider management operations."""

    @staticmethod
    async def enhance_provider_data(
        business_name: str,
        description: str,
        place_types: Optional[List[str]] = None,
        reviews: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Forward to provider_utils.ProviderService.enhance_provider_data and return its enhanced description, services, categories and offerings."""
        from app.etown.provider.provider_utils import ProviderService as CanonicalProviderService

        return await CanonicalProviderService.enhance_provider_data(
            business_name=business_name,
            description=description,
            place_types=place_types,
            reviews=reviews,
        )

    @staticmethod
    @with_retry()
    async def check_business_name_exists(business_name: str) -> bool:
        """Return whether the business name exists, case- and whitespace-insensitively, in etown_provider or etown_providerservices_copy."""
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            try:
                query = """
                    SELECT 1 FROM (
                        SELECT businessname FROM etown_provider WHERE LOWER(TRIM(businessname)) = LOWER(TRIM($1))
                        UNION ALL
                        SELECT businessname FROM etown_providerservices_copy WHERE LOWER(TRIM(businessname)) = LOWER(TRIM($1))
                    ) AS combined_business_names
                    LIMIT 1
                """
                result = await con.fetchrow(query, business_name)
                return result is not None
                
            except Exception as e:
                logger.error(f"Error checking business name existence: {e}", exc_info=True)
                raise

    @staticmethod
    @with_retry()
    async def check_phone_exists(phone_number: str) -> bool:
        """Return whether any etown_contact row has this exact mobilephone."""
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            try:
                query = "SELECT 1 FROM etown_contact WHERE mobilephone = $1 LIMIT 1"
                result = await con.fetchrow(query, phone_number)
                return result is not None
                
            except Exception as e:
                logger.error(f"Error checking phone number existence: {e}", exc_info=True)
                raise

    @staticmethod
    @with_retry()
    async def onboard_provider_in_db(
        business_name: str,
        phone_number: str,
        description: str,
        city: str,
        first_name: Optional[str] = None,
        email: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None
    ) -> Dict[str, Any]:
        """Register a provider with AI-enhanced details across contact, userprofile, provider and both providerservices tables in one transaction.

        Args:
            business_name: Business name; must be unique across provider tables.
            phone_number: Contact mobile number; must not already be registered.
            description: Business description fed to AI enhancement.
            city: Fallback address when reverse geocoding yields nothing.
            first_name: Contact first name; defaults to the business name.
            email: Optional contact email.
            latitude: Optional latitude used for reverse geocoding.
            longitude: Optional longitude used for reverse geocoding.

        Returns:
            Contact, userprofile, provider, providerservice and ai_enhancements sections.

        Raises:
            HTTPException: 400 for a missing required field, 409 for a duplicate name or phone.
            RuntimeError: If the database pool is not initialized.
            Exception: If the registration transaction fails.
        """
        if Database._pool is None or Database._pool._closed:
            raise RuntimeError("Database pool is not initialized or is closed.")
        
        if not business_name or not business_name.strip():
            raise HTTPException(status_code=400, detail="Business name is required")
        if not phone_number or not phone_number.strip():
            raise HTTPException(status_code=400, detail="Phone number is required")
        if not description or not description.strip():
            raise HTTPException(status_code=400, detail="Description is required")
        if not city or not city.strip():
            raise HTTPException(status_code=400, detail="City is required")

        if not first_name:
            first_name = business_name.strip()

        if await ProviderService.check_business_name_exists(business_name):
            raise HTTPException(
                status_code=409,
                detail=f"Business name '{business_name}' already exists"
            )

        if await ProviderService.check_phone_exists(phone_number):
            raise HTTPException(
                status_code=409,
                detail=f"Phone number '{phone_number}' is already registered"
            )

        enhanced_data = await ProviderService.enhance_provider_data(business_name, description)
        
        location_data = {
            'areaname': None,
            'townname': None, 
            'statecode': None,
            'address': city
        }
        
        if latitude is not None and longitude is not None:
            try:
                location_details = await get_location_details_logic(latitude, longitude)
                if hasattr(location_details, 'parsed_components') and location_details.parsed_components:
                    components = location_details.parsed_components
                    location_data['areaname'] = (
                        components.get('sublocality_level_1') or
                        components.get('sublocality') or
                        components.get('locality')
                    )
                    location_data['townname'] = (
                        components.get('locality') or
                        components.get('administrative_area_level_3') or
                        components.get('administrative_area_level_2')
                    )
                    state_name = components.get('administrative_area_level_1')
                    if state_name:
                        location_data['statecode'] = convert_state_name_to_code(state_name)
                    
                    address_parts = []
                    if location_data['areaname']:
                        address_parts.append(location_data['areaname'])
                    if location_data['townname'] and location_data['townname'] != location_data['areaname']:
                        address_parts.append(location_data['townname'])
                    if location_data['statecode']:
                        address_parts.append(location_data['statecode'])
                    
                    if address_parts:
                        location_data['address'] = ', '.join(address_parts)
                    
                    logger.info(
                        "onboard_provider: reverse geocode resolved | lat=%s lon=%s address=%r",
                        latitude, longitude, location_data.get("address"),
                    )
            except Exception as e:
                logger.warning(f"Failed to extract location details for coordinates {latitude}, {longitude}: {e}")
        
        content_vector = None
        try:
            embedding_text = f"Business: {business_name}"
            if enhanced_data['business_category']:
                embedding_text += f" Category: {enhanced_data['business_category']}"
            if enhanced_data['service_category']:
                embedding_text += f" Service Category: {enhanced_data['service_category']}"
            if enhanced_data['enhanced_description']:
                embedding_text += f" Description: {enhanced_data['enhanced_description']}"
            if enhanced_data['services']:
                embedding_text += f" Services: {enhanced_data['services']}"
                
            embedding_vector = await generate_embedding(embedding_text)
            content_vector = '[' + ','.join(map(str, embedding_vector)) + ']'
            logger.info(f"Generated content vector for {business_name}")
        except Exception as e:
            logger.warning(f"Failed to generate content vector for {business_name}: {e}")

        acquire_start = time.perf_counter()
        async with Database._pool.acquire(timeout=Database._acquire_timeout_seconds) as con:
            acquire_ms = (time.perf_counter() - acquire_start) * 1000
            logger.info("[provider_onboard] EtownDB acquire_time_ms=%.2f", acquire_ms)
            
            async with con.transaction():
                try:
                    existing_check = await con.fetchrow("""
                        SELECT 
                            c.contactid, c.firstname, c.mobilephone, c.email, c.createdon, c.userlogin, c.city,
                            up.userprofileid, up.role, up.name as userprofile_name,
                            p.providerid, p.businessname, p.businesscategory, p.description, p.publicsearchprovider,
                            ps.providerservicesid, ps.services, ps.servicecategory
                        FROM etown_contact c
                        LEFT JOIN etown_userprofile up ON up.contactid = c.contactid AND up.role = 1
                        LEFT JOIN etown_provider p ON p.contactid = c.contactid  
                        LEFT JOIN etown_providerservices_copy ps ON ps.providerid = p.providerid
                        WHERE c.mobilephone = $1 AND c.rolecode = 1
                        LIMIT 1
                    """, phone_number)
                    
                    if existing_check:
                        logger.info(f"Provider with phone {phone_number} already exists, returning existing data")
                        response = {
                            "contact_info": {
                                "contactid": existing_check['contactid'],
                                "firstname": existing_check['firstname'],
                                "mobilephone": existing_check['mobilephone'],
                                "email": existing_check.get('email'),
                                "createdon": existing_check['createdon'].isoformat() + "Z" if existing_check['createdon'] else None,
                                "userlogin": existing_check['userlogin'],
                                "city": existing_check['city']
                            },
                            "userprofile_info": {
                                "userprofileid": existing_check['userprofileid'],
                                "contactid": existing_check['contactid'],
                                "role": existing_check['role'],
                                "name": existing_check['userprofile_name'] or existing_check['firstname']
                            },
                            "provider_info": {
                                "providerid": existing_check['providerid'],
                                "businessname": existing_check['businessname'],
                                "businesscategory": existing_check['businesscategory'],
                                "contactid": existing_check['contactid'],
                                "userprofileid": existing_check['userprofileid'],
                                "city": existing_check['city'],
                                "description": existing_check['description'],
                                "publicsearchprovider": existing_check['publicsearchprovider']
                            },
                            "providerservice_info": {
                                "providerservicesid": existing_check['providerservicesid'],
                                "providerid": existing_check['providerid'],
                                "businessname": existing_check['businessname'],
                                "servicedescription": existing_check['description'],
                                "services": existing_check['services'],
                                "businesscategory": existing_check['businesscategory'],
                                "servicecategory": existing_check['servicecategory']
                            },
                            "ai_enhancements": {
                                "original_description": description,
                                "enhanced_description": existing_check['description'] or description,
                                "generated_services": existing_check['services'].split(', ') if existing_check['services'] else [],
                                "business_category": existing_check['businesscategory'] or 'General Services',
                                "service_category": existing_check['servicecategory'] or 'Business Services',
                                "general_category": 'Services'
                            }
                        }
                        return response

                    contact_id = str(uuid.uuid4())
                    userprofile_id = str(uuid.uuid4()) 
                    provider_id = str(uuid.uuid4())
                    providerservice_id = str(uuid.uuid4())
                    providerservice_orig_id = str(uuid.uuid4())
                    current_timestamp = datetime.now(timezone.utc).replace(tzinfo=None)

                    register_query = f"""
                        WITH {CONTACT_PLAN_CTE.format(contact='$1')},
                        inserted_contact AS (
                            INSERT INTO etown_contact (
                                contactid, firstname, mobilephone, email, userlogin,
                                createdon, rolecode, rolecodename, city, content_vector, address, statecode, latitude, longitude, publicsearchstatus
                            )
                            VALUES ($1, $2, $3, $23, 'mobile', $4, 1, 'Provider', $5, $22::vector, $24, $16, $17, $18, 'non-verified')
                            ON CONFLICT (contactid) DO UPDATE SET 
                                firstname = EXCLUDED.firstname,
                                email = EXCLUDED.email,
                                city = EXCLUDED.city,
                                createdon = EXCLUDED.createdon,
                                rolecode = EXCLUDED.rolecode,
                                rolecodename = EXCLUDED.rolecodename,
                                content_vector = EXCLUDED.content_vector,
                                address = EXCLUDED.address,
                                statecode = EXCLUDED.statecode,
                                latitude = EXCLUDED.latitude,
                                longitude = EXCLUDED.longitude,
                                publicsearchstatus = EXCLUDED.publicsearchstatus
                            RETURNING contactid, firstname, mobilephone, email, createdon, userlogin, city
                        ),
                        inserted_userprofile AS (
                            INSERT INTO etown_userprofile (
                                userprofileid, contactid, contactidname,
                                role, rolename, name, mobilenumber, createdon
                            )
                            SELECT $6, ic.contactid, ic.firstname,
                                   1, 'Provider', ic.firstname, $3, $4
                            FROM inserted_contact ic
                            ON CONFLICT (userprofileid) DO UPDATE SET
                                contactidname = EXCLUDED.contactidname,
                                name = EXCLUDED.name,
                                mobilenumber = EXCLUDED.mobilenumber,
                                createdon = EXCLUDED.createdon
                            RETURNING userprofileid, contactid, role, name, mobilenumber
                        ),
                        inserted_provider AS (
                            INSERT INTO etown_provider (
                                providerid, businessname, businesscategory, contactid, userprofileid,
                                contactidname, userprofileidname, firstname, email, mobilenumber, city, description,
                                providertype, publicsearchprovider, role, rolename, creationdate,
                                areaname, townname, statecode, latitude, longitude, content_vector, address,
                                {CONTACT_PLAN_COLUMNS}
                            )
                            SELECT $7, $13, $11, iup.contactid, iup.userprofileid,
                                   $2, $2, $2, $23, $3, $5, $8,
                                   'etown_nonverified', 'non-verified', 1, 'Provider', $4,
                                   $14, $15, $16, $17, $18, $19::vector, $24,
                                   {CONTACT_PLAN_VALUES}
                            FROM inserted_userprofile iup
                            ON CONFLICT (providerid) DO UPDATE SET
                                businessname = EXCLUDED.businessname,
                                businesscategory = EXCLUDED.businesscategory,
                                contactidname = EXCLUDED.contactidname,
                                userprofileidname = EXCLUDED.userprofileidname,
                                description = EXCLUDED.description,
                                city = EXCLUDED.city,
                                areaname = EXCLUDED.areaname,
                                townname = EXCLUDED.townname,
                                statecode = EXCLUDED.statecode,
                                latitude = EXCLUDED.latitude,
                                longitude = EXCLUDED.longitude,
                                content_vector = EXCLUDED.content_vector,
                                address = EXCLUDED.address
                            RETURNING providerid, businessname, businesscategory, contactid, userprofileid, 
                                     city, description, publicsearchprovider, contactidname, userprofileidname
                        ),

                        inserted_providerservice_copy AS (
                            INSERT INTO etown_providerservices_copy (
                                providerservicesid, businessname, businesscategory, servicecategory,
                                providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                                servicedescription, services, city, address,
                                providertype, publicsearchprovider, createdon, content_vector,
                                areaname, statecode, latitude, longitude
                            )
                            SELECT $9, $13, $11, $12,
                                   ip.providerid, ip.contactid, $2, ip.userprofileid, $2, $23, $3,
                                   $8, $10, ip.city, $24,
                                   'etown_nonverified', 'non-verified', $4, $20::vector,
                                   $14, $16, $17, $18
                            FROM inserted_provider ip
                            ON CONFLICT (providerservicesid) DO UPDATE SET
                                businessname = EXCLUDED.businessname,
                                businesscategory = EXCLUDED.businesscategory,
                                servicecategory = EXCLUDED.servicecategory,
                                servicedescription = EXCLUDED.servicedescription,
                                services = EXCLUDED.services,
                                contactidname = EXCLUDED.contactidname,
                                userprofileidname = EXCLUDED.userprofileidname,
                                email = EXCLUDED.email,
                                content_vector = EXCLUDED.content_vector,
                                address = EXCLUDED.address,
                                areaname = EXCLUDED.areaname,
                                statecode = EXCLUDED.statecode,
                                latitude = EXCLUDED.latitude,
                                longitude = EXCLUDED.longitude
                            RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                                     businesscategory, servicecategory
                        ),
                        inserted_providerservice AS (
                            INSERT INTO etown_providerservices (
                                providerservicesid, businessname, businesscategory, servicecategory,
                                providerid, contactid, contactidname, userprofileid, userprofileidname, email, mobilenumber,
                                servicedescription, services, city, address,
                                providertype, publicsearchprovider, createdon,
                                areaname, statecode, latitude, longitude
                            )
                            SELECT $21, $13, $11, $12,
                                   ip.providerid, ip.contactid, $2, ip.userprofileid, $2, $23, $3,
                                   $8, $10, ip.city, $24,
                                   'etown_nonverified', 'non-verified', $4,
                                   $14, $16, $17, $18
                            FROM inserted_provider ip
                            ON CONFLICT (providerservicesid) DO UPDATE SET
                                businessname = EXCLUDED.businessname,
                                businesscategory = EXCLUDED.businesscategory,
                                servicecategory = EXCLUDED.servicecategory,
                                servicedescription = EXCLUDED.servicedescription,
                                services = EXCLUDED.services,
                                contactidname = EXCLUDED.contactidname,
                                userprofileidname = EXCLUDED.userprofileidname,
                                email = EXCLUDED.email,
                                address = EXCLUDED.address,
                                areaname = EXCLUDED.areaname,
                                statecode = EXCLUDED.statecode,
                                latitude = EXCLUDED.latitude,
                                longitude = EXCLUDED.longitude
                            RETURNING providerservicesid, providerid, businessname, servicedescription, services,
                                     businesscategory, servicecategory
                        )
                        SELECT
                            ic.contactid, ic.firstname, ic.mobilephone, ic.email, ic.createdon, ic.userlogin, ic.city,
                            iup.userprofileid, iup.role,
                            ip.providerid, ip.businessname, ip.businesscategory, ip.description, ip.publicsearchprovider,
                            ipsc.providerservicesid, ipsc.services, ipsc.servicecategory,
                            ips.providerservicesid as providerservicesid_orig
                        FROM inserted_contact ic
                        JOIN inserted_userprofile iup ON iup.contactid = ic.contactid
                        JOIN inserted_provider ip ON ip.contactid = ic.contactid
                        JOIN inserted_providerservice_copy ipsc ON ipsc.providerid = ip.providerid
                        JOIN inserted_providerservice ips ON ips.providerid = ip.providerid
                    """

                    query_start = time.perf_counter()
                    registration_result = await con.fetchrow(
                        register_query,
                        contact_id,
                        first_name,
                        phone_number,
                        current_timestamp,
                        city,
                        userprofile_id,
                        provider_id,
                        enhanced_data['enhanced_description'],
                        providerservice_id,
                        enhanced_data['services'],
                        enhanced_data['business_category'],
                        enhanced_data['service_category'],
                        business_name,
                        location_data['areaname'],
                        location_data['townname'],
                        location_data['statecode'],
                        latitude,
                        longitude,
                        content_vector,
                        content_vector,
                        providerservice_orig_id,
                        content_vector,
                        email,
                        location_data['address']
                    )
                    query_ms = (time.perf_counter() - query_start) * 1000
                    logger.info("[provider_onboard] EtownDB query_time_ms=%.2f", query_ms)

                    if not registration_result:
                        raise Exception("Provider registration failed - no result returned")
                    
                    response = {
                        "contact_info": {
                            "contactid": registration_result['contactid'],
                            "firstname": registration_result['firstname'],
                            "mobilephone": registration_result['mobilephone'],
                            "email": registration_result.get('email'),
                            "createdon": registration_result['createdon'].isoformat() + "Z",
                            "userlogin": registration_result['userlogin'],
                            "city": registration_result['city']
                        },
                        "userprofile_info": {
                            "userprofileid": registration_result['userprofileid'],
                            "contactid": registration_result['contactid'],
                            "role": registration_result['role'],
                            "name": registration_result['firstname']
                        },
                        "provider_info": {
                            "providerid": registration_result['providerid'],
                            "businessname": registration_result['businessname'],
                            "businesscategory": registration_result['businesscategory'],
                            "contactid": registration_result['contactid'],
                            "userprofileid": registration_result['userprofileid'],
                            "city": registration_result['city'],
                            "description": registration_result['description'],
                            "publicsearchprovider": registration_result['publicsearchprovider']
                        },
                        "providerservice_info": {
                            "providerservicesid": registration_result['providerservicesid'],
                            "providerid": registration_result['providerid'],
                            "businessname": registration_result['businessname'],
                            "servicedescription": registration_result['description'],
                            "services": registration_result['services'],
                            "businesscategory": registration_result['businesscategory'],
                            "servicecategory": registration_result['servicecategory'],
                            "providerservicesid_orig": registration_result['providerservicesid_orig']
                        },
                        "ai_enhancements": {
                            "original_description": enhanced_data['original_description'],
                            "enhanced_description": enhanced_data['enhanced_description'],
                            "generated_services": enhanced_data['services_list'],
                            "business_category": enhanced_data['business_category'],
                            "service_category": enhanced_data['service_category'],
                            "general_category": enhanced_data['general_category']
                        }
                    }
                    
                    logger.info(f"Provider '{business_name}' successfully onboarded with ID: {provider_id}")
                    return response
                    
                except Exception as e:
                    logger.error(f"Provider registration transaction failed: {e}", exc_info=True)
                    if isinstance(e, HTTPException):
                        raise
                    raise Exception(f"Provider registration failed: {str(e)}")

    @staticmethod
    async def validate_phone_format(phone_number: str) -> bool:
        """Return whether a phone string contains 8 to 15 digits."""
        if not phone_number or not isinstance(phone_number, str):
            return False
        
        digits_only = ''.join(filter(str.isdigit, phone_number))
        return 8 <= len(digits_only) <= 15