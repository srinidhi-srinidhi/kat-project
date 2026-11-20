"""Users Endpoints - HTTP routes for registration, existence checks, profiles and locations.

HTTP layer for /api/users: validates requests, checks the shared bearer token and delegates registration, existence, location, consent, login, profile, address, listing and staff operations to user_utils, scheduling the users_syncing Dataverse mirror as background tasks.

Features:
    - User Registration: Multi-table user registration that issues an auth session on success
    - Existence Check: Mobile and/or email user lookup with staff and FOS flags and a device token's credsupdate
    - Location Management: Add/edit/delete a user location record, plus a bulk retrieval endpoint
    - Profile & Address Edits: Partial profile and address updates, mirrored to Dataverse in the background
    - Consent Tracking: Per-contact aisearch_consent / chattie_consent flags
    - Login Lifecycle: Last-login/signup status tracking with Dataverse sync and cross-profile reset
    - Service and Business Lists: Live, approved, non-deleted listings for a profile or number
    - Alternate Mobile Check: Whether an alternate number can join an existing account
    - API Documentation: Automatic OpenAPI documentation generation

Endpoints:
    POST /register: Register a new user with complete profile information
    GET /exists: Check if user exists by mobile number and/or email query parameters
    GET /active: List contacts whose lasttriggeredtime falls on a given date
    GET /userlocation_details: Retrieve all location records for a contact
    GET /service_list: Retrieve the service list for a user profile
    POST /consent: Update aisearch_consent and/or chattie_consent for a contact
    POST /lastlogin: Update the last-login/signup status for a user profile
    POST /locationdetails: Add, edit, or delete a user location record
    POST /edit_profile: Update a member/contact profile and its related records
    POST /update_address: Update address fields on a contact
    GET /business_list: List the live businesses registered against a mobile number
    POST /check_alternate_mobile: Check whether an alternate number can join an original number's account
    POST /staff_member: Create, edit or find etown_staff_numbers rows by mobile number

Classes:
    No classes defined (module exposes FastAPI router endpoints only)

Functions:
    register_user(): Handle POST /register endpoint for new user registration
    check_user_exists(): Handle GET /exists endpoint for user existence validation by mobile/email
    get_active_users(): Handle GET /active — contacts whose lasttriggeredtime matches a given date
    get_userlocation_details(): Handle GET /userlocation_details — all location rows for a contact
    get_service_list(): Handle GET /service_list — a user's service listing
    update_user_consent(): Handle POST /consent — update aisearch_consent/chattie_consent
    update_last_login(): Handle POST /lastlogin — update signup/login status and mirror it to Dataverse
    location_details(): Handle POST /locationdetails — add/edit/delete a user location record
    edit_member_profile(): Handle POST /edit_profile — update etown_contact and propagate name/email/mobile changes
    update_address(): Handle POST /update_address — update address fields on etown_contact
    get_business_list(): Handle GET /business_list — businessname/serviceimageurl/disabled for a number
    check_alternate_mobile(): Handle POST /check_alternate_mobile — no record / separate account / linked elsewhere
    upsert_staff_member(): Handle POST /staff_member — create, edit or find staff roster rows

Dependencies:
    - fastapi: Router, BackgroundTasks and Security dependency injection
    - app.etown.user.user_utils: UserService plus location/consent/profile/address business logic
    - app.etown.user.users_syncing: Background Dataverse sync tasks (lastlogin, location, profile, address)
    - app.etown.auth: auth_service — issues an access/refresh session right after registration
    - app.core.security: verify_token bearer-token dependency
    - app.schemas.api: Pydantic models for request/response validation
    - app.core.logging: Centralized logging infrastructure
    - app.services.api_activity: track_api_call activity tracking

Updates:
    09-Mar-2026: Initial creation with user registration and existence check endpoints
    11-Mar-2026: Removed redundant registration validation checks.
    05-Jun-2026: Strengthened /exists validation - mobile, email and ISO date checks.
    03-Jul-2026: Added POST /edit_profile, migrated from the UpdateProfile flow.
    14-Jul-2026: On-login promotion moved from /lastlogin and /exists to /otp/numberverify.
    06-Aug-2026: POST /register accepts an optional from_socialmedia field.
    20-Aug-2026: GET /service_list and GET /exists records include Etown_providertype.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
    02-Sep-2026: GET /service_list adds Etown_disabled and hides disabled providers; added GET /business_list.
    16-Sep-2026: GET /exists reports Etown_staff.
    21-Sep-2026: GET /service_list hides providers with an unapproved etown_provider_review row.
    23-Sep-2026: GET /exists adds Etown_FOS from etown_staff_numbers; added POST /check_alternate_mobile.
    24-Sep-2026: GET /service_list excludes deleted providers.
    25-Sep-2026: Added POST /staff_member.
    01-Oct-2026: GET /exists reports unverified_onboarded_today for staff, CSR and FOS numbers.
    14-Oct-2026: GET /exists takes an optional devicetoken and reports its credsupdate.
"""

import datetime
import re
import time
from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query, Security
from fastapi.responses import JSONResponse
from typing import Optional

from app.etown.user.user_utils import UserService, fetch_user_location_details, fetch_service_list, fetch_provider_business_list, check_alternate_mobile_status, update_last_triggered_time, fetch_active_users_by_date, update_last_login as update_last_login_in_db, reset_other_profiles_after_lastlogin, manage_user_location_details, update_consent, update_member_profile_in_db, update_contact_address, upsert_etown_staff_member, fetch_etown_staff_members
from app.etown.user.users_syncing import sync_userprofile_lastlogin_to_dataverse, sync_userlocation_to_dataverse, sync_member_profile_update_to_dataverse, sync_address_update_to_dataverse
from app.schemas.api import (
    UserRegistrationRequest,
    UserRegistrationResponse, 
    UserExistsDetailedResponse,
    LastLoginRequest,
    LocationDetailsRequest,
    ConsentRequest,
    ConsentResponse,
    EditMemberProfileRequest,
    EditMemberProfileResponse,
    UpdateAddressRequest,
    ProviderBusinessItem,
    ProviderBusinessListResponse,
    AlternateMobileCheckRequest,
    AlternateMobileCheckResponse,
    StaffMemberItem,
    StaffMemberUpsertRequest,
    StaffMemberUpsertResponse,
)
from app.core.logging import logger
from app.core.security import verify_token
from app.etown.auth import auth_service
from app.services.api_activity import track_api_call

router = APIRouter()


@router.post(
    "/register",
    response_model=UserRegistrationResponse,
    status_code=201,
    summary="Register New User",
    description="Register a new user in EtownDB with complete profile information",
    responses={
        201: {
            "description": "User registered successfully",
            "model": UserRegistrationResponse
        },
        400: {
            "description": "Invalid input data",
            "content": {
                "application/json": {
                    "example": {"detail": "Validation error message"}
                }
            }
        },
        409: {
            "description": "User already exists",
            "content": {
                "application/json": {
                    "example": {"detail": "User with mobile number 8281530034 already exists"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Registration failed due to internal server error"}
                }
            }
        }
    }
)
async def register_user(user_data: UserRegistrationRequest) -> UserRegistrationResponse:
    """Handle POST /api/users/register: register a new user and attach session tokens when a session can be issued."""
    logger.info(f"Processing user registration request for mobile: {user_data.mobilenumber}")

    try:
        result = await UserService.register_new_user(user_data)

        contact_id = result.contact_info[0].contactid if result.contact_info else None
        if contact_id:
            try:
                session = await auth_service.issue_session_for_contact(contactid=str(contact_id))
                result.access_token = session["access_token"]
                result.refresh_token = session["refresh_token"]
                result.token_type = session["token_type"]
                result.expires_in = session["expires_in"]
            except Exception as e:
                logger.error(f"Registration succeeded but session issue failed for {contact_id}: {e}", exc_info=True)

        logger.info(f"User registration completed successfully for mobile: {user_data.mobilenumber}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during user registration: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Registration failed due to internal server error"
        )


@router.get(
    "/exists",
    response_model=UserExistsDetailedResponse,
    status_code=200,
    summary="Check User Existence",
    description="Check if a user exists in EtownDB by mobile number and/or email",
    responses={
        200: {
            "description": "User existence check completed",
            "model": UserExistsDetailedResponse
        },
        400: {
            "description": "Invalid parameters or missing required fields",
            "content": {
                "application/json": {
                    "example": {"detail": "At least one of mobile number or email must be provided"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "User existence check failed due to internal server error"}
                }
            }
        }
    }
)
async def check_user_exists(
    background_tasks: BackgroundTasks,
    mobile: Optional[int] = Query(
        None, 
        description="Mobile number to check for existence",
        examples=[9876543210],
        gt=0
    ),
    email: Optional[str] = Query(
        None,
        description="Email address to check for existence",
        examples=["user@example.com"]
    ),
    devicetoken: Optional[str] = Query(
        None,
        description="Push device token whose etown_contactdevices credsupdate flag is returned as credsupdate",
    )
    ) -> UserExistsDetailedResponse | JSONResponse:
    """Handle GET /api/users/exists: look up a user by mobile or email and schedule a last-triggered-time update when found."""
    logger.info(f"Processing user existence check for mobile: {mobile}, email: {email}")
    request_start = time.perf_counter()

    track_api_call(background_tasks, "/api/users/exists")

    try:
        if not mobile and not email:
            raise HTTPException(
                status_code=400,
                detail="At least one of mobile number or email must be provided"
            )
        
        if mobile is not None and len(str(mobile)) != 10:
            raise HTTPException(
                status_code=400,
                detail="Invalid mobile number format. Mobile number must be exactly 10 digits."
            )

        if email is not None and "@" not in email:
            raise HTTPException(
                status_code=400,
                detail="Invalid email format. Email must contain '@'."
            )
        
        db_start = time.perf_counter()
        result = await UserService.check_user_existence(mobile=mobile, email=email, devicetoken=devicetoken)
        database_time_ms = (time.perf_counter() - db_start) * 1000

        if not result.ContactInfo:
            response_time_seconds = time.perf_counter() - request_start
            logger.info(f"User existence check completed for mobile: {mobile}, email: {email}, found: False")
            return JSONResponse(
                status_code=200,
                content={
                    "Result": "User Not Found",
                    "Email": email or "",
                    "MobilePhone": str(mobile) if mobile is not None else "",
                    "unverified_onboarded": result.unverified_onboarded,
                    "Etown_staff": result.Etown_staff,
                    "Etown_FOS": result.Etown_FOS,
                    "unverified_onboarded_today": result.unverified_onboarded_today,
                    "credsupdate": result.credsupdate,
                    "performance": {
                        "response_time_seconds": round(response_time_seconds, 3),
                        "database_time_ms": round(database_time_ms, 1),
                        "external_api_time_ms": 0,
                        "cache_hit": False,
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "endpoint_name": "users_exists",
                    },
                },
            )
        
        if result.ContactInfo and result.ContactInfo[0].contactid:
            background_tasks.add_task(
                update_last_triggered_time,
                str(result.ContactInfo[0].contactid),
                str(result.ContactInfo[0].mobilephone) if result.ContactInfo[0].mobilephone else None,
            )

        logger.info(f"User existence check completed for mobile: {mobile}, email: {email}, found: {len(result.ContactInfo) > 0}")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during user existence check: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="User existence check failed due to internal server error"
        )


@router.get(
    "/active",
    status_code=200,
    summary="List Active Users by Date",
    description="Returns all contacts whose lasttriggeredtime falls on the given date (yyyy-mm-dd)",
    responses={
        200: {
            "description": "Active users retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "data": {"John": "9876543210", "Jane": "8765432109"},
                        "performance_metrics": {"query_time_ms": 12.5, "total_time_ms": 13.1, "result_count": 2}
                    }
                }
            }
        },
        400: {
            "description": "Missing or invalid date parameter",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid date format. Expected yyyy-mm-dd"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve active users"}
                }
            }
        }
    }
)
async def get_active_users(
    date: str = Query(..., description="Date in yyyy-mm-dd format", examples={"example": {"value": "2026-05-25"}}),
) -> JSONResponse:
    """Handle GET /api/users/active: map firstname to mobile for contacts whose lasttriggeredtime falls on a yyyy-mm-dd date."""
    import re
    import datetime as _dt
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Expected yyyy-mm-dd"
        )
    try:
        _dt.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date format. Expected yyyy-mm-dd"
        )

    logger.info(f"Active users request | date={date}")

    t_total = time.perf_counter()
    try:
        t_query = time.perf_counter()
        result = await fetch_active_users_by_date(date)
        query_ms = (time.perf_counter() - t_query) * 1000
        total_ms = (time.perf_counter() - t_total) * 1000

        return JSONResponse(status_code=200, content={
            "data": result,
            "performance_metrics": {
                "query_time_ms": round(query_ms, 2),
                "total_time_ms": round(total_ms, 2),
                "result_count": len(result),
            },
        })
    except Exception as e:
        logger.error(f"Failed to retrieve active users | date={date} | error={e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve active users"
        )


@router.get(
    "/userlocation_details",
    status_code=200,
    summary="Get User Location Details",
    description="Retrieve all location records for a given contact",
    responses={
        200: {
            "description": "User location details retrieved successfully",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "Etown_contactid": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                            "Etown_latitude": "11.0168",
                            "Etown_longitude": "76.9558",
                            "Etown_type": "home",
                            "Etown_homeaddress": "123 Main Street",
                            "Etown_workaddress": None,
                            "Etown_landmark": "Near bus stand",
                            "Etown_statecode": "TN",
                            "Etown_city": "Coimbatore",
                            "Etown_createdon": "2026-01-01T10:00:00",
                            "Etown_userlocationid": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
                            "Etown_additionaladdressinfo": None
                        }
                    ]
                }
            }
        },
        400: {
            "description": "Missing required parameters",
            "content": {
                "application/json": {
                    "example": {"detail": "contact_id is required"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve user location details"}
                }
            }
        }
    }
)
async def get_userlocation_details(
    contact_id: str = Query(..., description="The contact's unique identifier"),
):
    """Handle GET /api/users/userlocation_details: return every saved location record for a contact."""
    logger.info(
        f"User location details request | contact_id={contact_id}"
    )

    try:
        results = await fetch_user_location_details(contact_id)
        return JSONResponse(status_code=200, content=results)

    except Exception as e:
        logger.error(
            f"Failed to retrieve user location details | contact_id={contact_id} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve user location details"
        )


@router.get(
    "/service_list",
    status_code=200,
    summary="Get Service List",
    description=(
        "Retrieve the service list for a user profile. Each record carries Etown_disabled, "
        "the provider's disabled flag (null for member profiles). When is_sameuser is not "
        "true, providers with disabled = true are left out of the list altogether — only the "
        "owner sees their own disabled businesses. A provider minted by a field capture whose "
        "action_mode='create' etown_provider_review row is still unapproved is left out "
        "whatever is_sameuser says, since an unapproved capture is nobody's business yet. A "
        "pending action_mode='edit' row hides nothing here: the edit never touched "
        "etown_provider, so the listing is returned exactly as it is stored."
    ),
    responses={
        200: {
            "description": "Service list retrieved successfully"
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve service list"}
                }
            }
        }
    }
)
async def get_service_list(
    getcontact_id: Optional[str] = Query(None, description="Contact ID of the user whose services to fetch"),
    contact_id: Optional[str] = Query(None, description="Contact ID of the requesting user"),
    userprofile_id: Optional[str] = Query(None, description="User profile ID"),
    is_sameuser: Optional[str] = Query(None, description="Flag indicating if the requesting user is the same as the target user"),
    mobilenumber: Optional[str] = Query(None, description="Mobile number of the user"),
):
    """Handle GET /api/users/service_list: return a user's service list, hiding disabled businesses from other viewers."""
    logger.info(
        f"Service list request | getcontact_id={getcontact_id} "
        f"| contact_id={contact_id} "
        f"| userprofile_id={userprofile_id} "
        f"| is_sameuser={is_sameuser} "
        f"| mobilenumber={mobilenumber}"
    )

    try:
        results = await fetch_service_list(
            getcontact_id=getcontact_id,
            contact_id=contact_id,
            userprofile_id=userprofile_id,
            is_sameuser=is_sameuser,
            mobilenumber=mobilenumber,
        )
        return JSONResponse(status_code=200, content=results)

    except Exception as e:
        logger.error(
            f"Failed to retrieve service list | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve service list"
        )


@router.post(
    "/consent",
    response_model=ConsentResponse,
    status_code=200,
    summary="Update User Consent",
    description="Update aisearch_consent and/or chattie_consent for a contact",
    responses={
        200: {
            "description": "Consent updated successfully",
            "model": ConsentResponse
        },
        400: {
            "description": "Invalid input — no consent fields provided or contact not found",
            "content": {
                "application/json": {
                    "example": {"detail": "At least one of aisearch_consent or chattie_consent must be provided"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to update consent"}
                }
            }
        }
    }
)
async def update_user_consent(request: ConsentRequest) -> ConsentResponse:
    """Handle POST /api/users/consent: update a contact's aisearch and/or chattie consent flags."""
    logger.info(
        f"Consent update request | contactid={request.contactid} "
        f"| aisearch_consent={request.aisearch_consent} "
        f"| chattie_consent={request.chattie_consent}"
    )

    if request.aisearch_consent is None and request.chattie_consent is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of aisearch_consent or chattie_consent must be provided"
        )

    try:
        result = await update_consent(
            contactid=request.contactid,
            aisearch_consent=request.aisearch_consent,
            chattie_consent=request.chattie_consent,
        )
        logger.info(f"Consent updated successfully | contactid={request.contactid}")
        return ConsentResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update consent | contactid={request.contactid} | error={e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to update consent"
        )


@router.post(
    "/lastlogin",
    status_code=200,
    summary="Update Last Login",
    description="Etown - Mobile API - User Profile - Update Last Login API",
    responses={
        200: {
            "description": "Last login updated successfully"
        },
        400: {
            "description": "Invalid input data",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid request body"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to update last login"}
                }
            }
        }
    }
)
async def update_last_login(request: LastLoginRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    """Handle POST /api/users/lastlogin: record a profile's login and schedule the Dataverse mirror and other-profile reset."""
    logger.info(
        f"Last login update request | contact_id={request.contact_id} "
        f"| user_profile_id={request.user_profile_id}"
    )

    try:
        result = await update_last_login_in_db(
            contact_id=request.contact_id,
            user_profile_id=request.user_profile_id,
            lastsignup_status=request.lastsignup_status,
            membersignup_status=request.membersignup_status,
        )

        if result:
            background_tasks.add_task(
                sync_userprofile_lastlogin_to_dataverse,
                str(result.get("Etown_userprofileid") or request.user_profile_id),
                result.get("Etown_lastsignupstatus"),
                result.get("Etown_membersignup"),
            )

        if request.membersignup_status is None and request.contact_id:
            background_tasks.add_task(
                reset_other_profiles_after_lastlogin,
                request.contact_id,
                request.user_profile_id,
            )

        logger.info(f"Last login updated successfully | contact_id={request.contact_id}")
        return JSONResponse(status_code=200, content=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to update last login | contact_id={request.contact_id} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to update last login"
        )


@router.post(
    "/locationdetails",
    status_code=200,
    summary="User Location Details",
    description="Etown - Mobile API - User Location Details - Add,Edit and Delete API",
    responses={
        200: {
            "description": "Location action completed successfully"
        },
        400: {
            "description": "Invalid input data",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid request body"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to process location details"}
                }
            }
        }
    }
)
async def location_details(request: LocationDetailsRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    """Handle POST /api/users/locationdetails: add, edit or delete a user location and schedule its Dataverse mirror."""
    logger.info(
        f"Location details request | contact_id={request.contact_id} "
        f"| requiredaction={request.requiredaction}"
    )

    try:
        result = await manage_user_location_details(
            requiredaction=request.requiredaction,
            contact_id=request.contact_id,
            latitude=request.latitude,
            longitude=request.longitude,
            additionaladdressinfo=request.additionaladdressinfo,
            type=request.type,
            homeaddress=request.homeaddress,
            workaddress=request.workaddress,
            city=request.city,
            statecode=request.statecode,
            landmark=request.landmark,
            editlocationrow_id=request.editlocationrow_id,
            deletelocationrow_id=request.deletelocationrow_id,
        )
        logger.info(
            f"Location details action completed | contact_id={request.contact_id} "
            f"| requiredaction={request.requiredaction}"
        )
        action = request.requiredaction.lower().strip()
        location_row_id = request.editlocationrow_id if action == "edit" else request.deletelocationrow_id
        background_tasks.add_task(
            sync_userlocation_to_dataverse,
            action,
            result if action in ("add", "edit") else None,
            location_row_id,
        )
        return JSONResponse(status_code=200, content=result)
    except ValueError as e:
        logger.warning(f"Invalid location details request | error={e}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to process location details | contact_id={request.contact_id} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to process location details"
        )

@router.post(
    "/edit_profile",
    response_model=EditMemberProfileResponse,
    status_code=200,
    summary="Edit Member Profile",
    description=(
        "Update a member/contact profile in etown_contact. Only the fields supplied are changed. "
        "For profile_image: pass 'no image' to clear the existing image, omit or pass null to "
        "retain the existing image, or pass a base64 string to upload a new image to Azure Blob. "
        "Name, email, and mobile number changes are propagated to etown_userprofile and "
        "etown_member. A name change also moves the person-name snapshots those tables and "
        "the feed keep: etown_userprofile.contactidname (member profiles only - a provider "
        "profile can hold the business name there), etown_member contactidname and "
        "userprofileidname, and the frozen copies on etown_feed, etown_feedlikes and "
        "etown_feedcomments. The feed itself resolves the name live, so the post header "
        "changes with this call regardless. Dataverse sync (contacts, ets_userprofiles, "
        "ets_members) runs as a background task after the response is returned."
    ),
    responses={
        200: {
            "description": "Profile update completed",
            "model": EditMemberProfileResponse,
            "content": {"application/json": {"examples": {
                "success": {"value": {"message": "Profile updated successfully"}},
                "not_found": {"value": {"message": "Contact not found"}},
            }}},
        },
        401: {"description": "Missing or invalid bearer token"},
        422: {"description": "No editable field supplied"},
        500: {"description": "Internal server error"},
    },
)
async def edit_member_profile(
    background_tasks: BackgroundTasks,
    request: EditMemberProfileRequest = Body(...),
    _token: str = Security(verify_token),
) -> EditMemberProfileResponse:
    """Handle POST /api/users/edit_profile: update the supplied contact profile fields and schedule the Dataverse mirror."""
    try:
        updates = request.model_dump(
            exclude_unset=True,
            exclude={"contact_id"},
        )

        logger.info(
            f"Edit member profile request | contact_id={request.contact_id} "
            f"fields={sorted(updates.keys())}"
        )

        result = await update_member_profile_in_db(
            contact_id=request.contact_id,
            updates=updates,
        )

        if result is None:
            raise HTTPException(status_code=404, detail="Contact not found")

        background_tasks.add_task(
            sync_member_profile_update_to_dataverse,
            contact_id=request.contact_id,
            contact_payload=result["contact_payload"],
            userprofile_ids=result["userprofile_ids"],
            member_ids=result["member_ids"],
        )

        updated_row = result.get("updated_row", {})
        
        return EditMemberProfileResponse(
            message="Profile updated successfully",
            status="success",
            firstname=updated_row.get("firstname"),
            emailaddress1=updated_row.get("email"),
            address1_composite=updated_row.get("address"),
            address1_line1=updated_row.get("address"),
            ets_additionaladdressinfo=updated_row.get("additionaladdressinfo"),
            ets_profileimage=updated_row.get("profileimage"),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            f"edit_member_profile error | contact_id={request.contact_id} | error={exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error") from exc


@router.post(
    "/update_address",
    status_code=200,
    summary="Update Contact Address",
    description=(
        "Update address fields on a contact in etown_contact. Every field is optional; "
        "only the fields supplied are changed. The 'address' value is written to both the "
        "'address' and 'addresscomp' columns. Returns the full updated contact row."
    ),
    responses={
        200: {"description": "Address updated successfully; returns the full updated contact row"},
        400: {
            "description": "Invalid input — missing contact_id, no updatable field, or bad coordinate",
            "content": {
                "application/json": {
                    "example": {"detail": "contact_id is required"}
                }
            },
        },
        404: {
            "description": "Contact not found",
            "content": {
                "application/json": {
                    "example": {"detail": "Contact not found"}
                }
            },
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to update address"}
                }
            },
        },
    },
)
async def update_address(request: UpdateAddressRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    """Handle POST /api/users/update_address: update a contact's supplied address fields and schedule the Dataverse mirror."""
    logger.info(f"Update address request | contact_id={request.contact_id}")

    try:
        result = await update_contact_address(
            contact_id=request.contact_id,
            address=request.address,
            additionaladdressinfo=request.additionaladdressinfo,
            latitude=request.latitude,
            longitude=request.longitude,
            statecode=request.statecode,
            city=request.city,
        )

        if result is None:
            raise HTTPException(status_code=404, detail="Contact not found")

        background_tasks.add_task(
            sync_address_update_to_dataverse,
            contact_id=request.contact_id,
            address=request.address,
            addresscomp=request.address,
            additionaladdressinfo=request.additionaladdressinfo,
            latitude=request.latitude,
            longitude=request.longitude,
            statecode=request.statecode,
            city=request.city,
        )

        logger.info(f"Address updated successfully | contact_id={request.contact_id}")
        return JSONResponse(status_code=200, content=result)
    except ValueError as e:
        logger.warning(f"Invalid update address request | error={e}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to update address | contact_id={request.contact_id} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to update address"
        )


@router.get(
    "/business_list",
    response_model=ProviderBusinessListResponse,
    status_code=200,
    summary="List a number's businesses",
    description=(
        "Return the businesses registered against a mobile number — business name, service "
        "image URL and the disabled flag. A business is listed when it has not been deleted "
        "and is not currently deactivated (never deactivated, or reactivated since). "
        "`disabled` is the operator switch rather than the owner's deactivation, so a disabled "
        "business is still returned with the flag set to true."
    ),
    responses={
        200: {
            "description": "Businesses retrieved successfully",
            "model": ProviderBusinessListResponse
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to retrieve business list"}
                }
            }
        }
    }
)
async def get_business_list(
    mobilenumber: str = Query(..., min_length=1, description="Mobile number to match against etown_provider.mobilenumber"),
) -> ProviderBusinessListResponse:
    """Handle GET /api/users/business_list: list the non-deleted, non-deactivated businesses registered to a mobile number."""
    logger.info(f"Business list request | mobilenumber={mobilenumber}")

    try:
        businesses = await fetch_provider_business_list(mobilenumber)
        return ProviderBusinessListResponse(
            success=True,
            count=len(businesses),
            businesses=[ProviderBusinessItem(**business) for business in businesses],
        )
    except Exception as e:
        logger.error(
            f"Failed to retrieve business list | mobilenumber={mobilenumber} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve business list"
        )


_ALTERNATE_MOBILE_MESSAGES = {
    "no_record": "No record found for this number",
    "separate_account": "This number already has a separate account",
    "linked_to_another": "This number is linked with another mobile number",
    "same_as_original": "Alternate number cannot be the same as the original number",
}


@router.post(
    "/check_alternate_mobile",
    response_model=AlternateMobileCheckResponse,
    status_code=200,
    summary="Check alternate mobile number",
    description=(
        "Etown - Mobile API - Check whether an alternate mobile number can be attached to the "
        "account of an original number. Numbers are compared on their last 10 digits and "
        "deleted contacts are ignored. Checked in order: `separate_account` when the number is "
        "some account's primary mobilephone, `linked_to_another` when it is the alternate of a "
        "different primary number, otherwise `no_record` - which includes a number already "
        "stored as this same number's alternate."
    ),
    responses={
        200: {
            "description": "Check completed; read `status` for the outcome",
            "model": AlternateMobileCheckResponse
        },
        400: {
            "description": "A number is not a valid 10-digit mobile number",
            "content": {
                "application/json": {
                    "example": {"detail": "A valid 10-digit alternatemobile is required"}
                }
            }
        },
        500: {
            "description": "Internal server error",
            "content": {
                "application/json": {
                    "example": {"detail": "Failed to check alternate mobile number"}
                }
            }
        }
    }
)
async def check_alternate_mobile(request: AlternateMobileCheckRequest) -> AlternateMobileCheckResponse:
    """Handle POST /api/users/check_alternate_mobile: classify whether an alternate number can attach to the original account."""
    logger.info(
        f"Alternate mobile check | mobilenumber={request.mobilenumber} "
        f"| alternatemobile={request.alternatemobile}"
    )

    original = re.sub(r"\D", "", request.mobilenumber)[-10:]
    alternate = re.sub(r"\D", "", request.alternatemobile)[-10:]
    if len(original) != 10:
        raise HTTPException(status_code=400, detail="A valid 10-digit mobilenumber is required")
    if len(alternate) != 10:
        raise HTTPException(status_code=400, detail="A valid 10-digit alternatemobile is required")

    try:
        if original == alternate:
            status = "same_as_original"
        else:
            status = await check_alternate_mobile_status(original, alternate)
    except Exception as e:
        logger.error(
            f"Failed to check alternate mobile | mobilenumber={request.mobilenumber} "
            f"| alternatemobile={request.alternatemobile} | error={e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to check alternate mobile number"
        )

    logger.info(f"Alternate mobile check result | alternatemobile={alternate} | status={status}")
    return AlternateMobileCheckResponse(
        success=True,
        status=status,
        message=_ALTERNATE_MOBILE_MESSAGES[status],
    )


_STAFF_MEMBER_ERRORS = {
    "not_found": (404, "No staff member found for this number"),
    "role_ambiguous": (409, "This number holds several roles; pass role to choose one"),
    "role_exists": (409, "This number already holds that role"),
}


@router.post(
    "/staff_member",
    response_model=StaffMemberUpsertResponse,
    status_code=200,
    summary="Create, edit or find a staff member",
    description=(
        "Etown - Mobile API - Create, edit or find etown_staff_numbers rows by mobilenumber. "
        "`action_mode` is `create`, `edit` (default) or `find`. create inserts a new row and "
        "needs `role`; `is_active` defaults to true. edit changes only the fields sent on the "
        "number's row with `role`, or on its only row - where `role` then sets a new role. "
        "find writes nothing and returns the number's rows, filtered by `role` and "
        "`is_active` when sent; no match is a 200 with an empty `staff`. "
        "`name` is always taken from the firstname of the contact with this number."
    ),
    responses={
        200: {"description": "Rows created, updated or found; read `action`", "model": StaffMemberUpsertResponse},
        401: {"description": "Missing or invalid bearer token"},
        404: {"description": "edit: no staff member for this number"},
        409: {"description": "The role is already held, or edit on a multi-role number without a matching role"},
        422: {"description": "Invalid mobile number, role or action_mode, or create without role"},
        500: {"description": "Internal server error"},
    },
)
async def upsert_staff_member(
    request: StaffMemberUpsertRequest = Body(...),
    _token: str = Security(verify_token),
) -> StaffMemberUpsertResponse:
    """Handle POST /api/users/staff_member: create, edit or find etown_staff_numbers rows by mobile number."""
    logger.info(
        f"Staff member {request.action_mode} | mobilenumber={request.mobilenumber} "
        f"| role={request.role} | is_active={request.is_active}"
    )

    try:
        if request.action_mode == "find":
            rows = await fetch_etown_staff_members(
                request.mobilenumber,
                role=request.role,
                is_active=request.is_active,
            )
        else:
            rows = [await upsert_etown_staff_member(
                request.mobilenumber,
                action_mode=request.action_mode,
                role=request.role,
                is_active=request.is_active,
            )]
    except ValueError as e:
        if str(e) not in _STAFF_MEMBER_ERRORS:
            raise
        status_code, detail = _STAFF_MEMBER_ERRORS[str(e)]
        raise HTTPException(status_code=status_code, detail=detail)
    except Exception as e:
        logger.error(
            f"Failed to save staff member | mobilenumber={request.mobilenumber} | error={e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Failed to save staff member")

    action = {"create": "created", "edit": "updated", "find": "found"}[request.action_mode]
    if action == "found":
        message = f"{len(rows)} staff member record(s) found" if rows else "No staff member found for this number"
    else:
        message = f"Staff member {action}"
    logger.info(f"Staff member {action} | mobilenumber={request.mobilenumber} | count={len(rows)}")
    return StaffMemberUpsertResponse(
        success=True,
        action=action,
        message=message,
        count=len(rows),
        staff=[StaffMemberItem(**{**r, "id": str(r["id"])}) for r in rows],
    )
