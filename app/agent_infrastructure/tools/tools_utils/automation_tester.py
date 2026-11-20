"""Automation Tester Tool - runs register, login, booking and invite scenarios against live services.

Automation-agent tool that validates a test payload, then drives the flow named by test_scenario through the real auth, user, booking, search and invite code paths without mocks, failing searches whose top result misses the query terms.

Features:
    - Real Backend Execution: register/login/booking/invite flows run against live services, not mocks
    - Scenario Dispatch: test_scenario selects the register, login, booking/search or invite flow
    - Input Validation: required-field, Indian-mobile and email-domain checks run before any call
    - Native Auth Path: register/login call auth_service for OTP and session minting
    - Dynamic "Book ... From ..." Parsing: finds the named provider in the query and books it
    - Search Relevance Guard: is_search_relevant() fails a test whose top result misses the query terms

Classes:
    No classes defined (module exposes the automation_tester tool and validation helpers)

Functions:
    validate_required_fields(): Returns the required keys missing or empty in the test payload
    validate_indian_mobile(): Normalizes and validates a 10-digit Indian mobile number
    validate_email(): Validates email format and enforces the ALLOWED_EMAIL_DOMAINS whitelist
    is_search_relevant(): Checks the top search result contains a key term from the query
    automation_tester(): LangChain tool entrypoint; dispatches to the flow named by test_scenario

Dependencies:
    - requests: HTTP client for the remaining PowerApps app-tour call
    - re: Regular expressions for mobile/email validation and provider-name parsing
    - app.etown.auth.auth_service: native OTP issue/verify and session minting
    - langchain_core.tools: the @tool decorator for automation_tester
    - app.agent_infrastructure.tools.testing_context_manager: get_test_context_value for the Flutter message
    - app.etown.user.user_utils: UserService.register_new_user / check_user_exists_in_db
    - app.etown.mytown.mytown_utils: handle_crud_collections (creates the new user's collection)
    - app.etown.devices.devices_utils: save_device_token
    - app.agent_infrastructure.tools.tools_utils: search/request/invite handlers exercised by the flows
    - app.schemas.api.UserRegistrationRequest: payload for the register flow

Updates:
    27-Feb-2026: Completely updated the documentation with a detailed module description
    16-Jul-2026: Register/login scenarios call the native in-process functions instead of PowerApps flows.
    02-Aug-2026: Infisign HTTP auth replaced by in-process auth_service; tokens are Etown-issued JWTs.
    26-Aug-2026: Logged previously-silent exception handlers and attached tracebacks to error logs.
"""
import json
import logging
import re
import os
import requests
from typing import Annotated, List, Dict, Any
from langchain_core.tools import tool
from datetime import datetime

from fastapi import HTTPException

from app.agent_infrastructure.tools.testing_context_manager import get_test_context_value
from app.agent_infrastructure.tools.tools_utils.search_provider import handle_search_provider
from app.agent_infrastructure.tools.tools_utils.booknow_provider import handle_booknow_provider
from app.agent_infrastructure.tools.tools_utils.request_provider import handle_request_provider
from app.agent_infrastructure.tools.tools_utils.internal_invite import handle_internal_invite
from app.agent_infrastructure.tools.tools_utils.external_invite_for_members import (
    handle_external_email_invite,
    handle_external_mobile_invite
)
from app.etown.auth import auth_service
from app.etown.user.user_utils import UserService, check_user_exists_in_db
from app.etown.mytown.mytown_utils import handle_crud_collections
from app.etown.devices.devices_utils import save_device_token
from app.schemas.api import UserRegistrationRequest

logger = logging.getLogger(__name__)

ALLOWED_EMAIL_DOMAINS = {
    "gmail.com", 
    "outlook.com", 
    "hotmail.com", 
    "live.com", 
    "yahoo.com", 
    "icloud.com", 
    "etown.in"
}

def validate_required_fields(data: Dict[str, Any], required_keys: List[str]) -> List[str]:
    """Return the required keys whose values are missing, None or an empty string."""
    return [key for key in required_keys if data.get(key) is None or data.get(key) == ""]

def validate_indian_mobile(mobile_number: Any) -> Dict[str, str]:
    """Return an error dict unless the number is a 10-digit Indian mobile starting 6-9, else None."""
    if not mobile_number:
        return None
    
    s_mobile = str(mobile_number).strip()
    
    if s_mobile.startswith("+91"):
        s_mobile = s_mobile[3:]
    elif s_mobile.startswith("91") and len(s_mobile) == 12:
        s_mobile = s_mobile[2:]
        
    if not re.match(r"^[6-9]\d{9}$", s_mobile):
        return {
            "Status": "error", 
            "Message": "Invalid Phone Number"
        }
    return None

def validate_email(email: str) -> Dict[str, str]:
    """Return an error dict if the email is malformed or its domain is not whitelisted, else None."""
    if not email:
        return None
    
    email_regex = r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)"
    if not re.match(email_regex, email):
        return {
            "Status": "error", 
            "Message": "Invalid Email."
        }
    domain = email.split("@")[-1].lower()
    if domain not in ALLOWED_EMAIL_DOMAINS:
        return {
            "Status": "error", 
            "Message": "Invalid Email."
        }
    return None

def is_search_relevant(query: str, results: List[Dict]) -> bool:
    """Return True if any non-stop-word query term appears in the top result's category, name or service."""
    if not results:
        return False
    stop_words = {"book", "a", "an", "the", "to", "for", "from", "service", "provider", "please", "i", "want", "need"}
    query_terms = [word.lower() for word in query.split() if word.lower() not in stop_words and len(word) > 2]
    if not query_terms:
        return True
    top_result = results[0]
    result_text = (
        str(top_result.get("businesscategory", "")) + " " + 
        str(top_result.get("businessname", "")) + " " + 
        str(top_result.get("servicename", ""))
    ).lower()
    for term in query_terms:
        if term in result_text:
            return True
    return False

@tool
async def automation_tester(
    test_scenario: Annotated[str, "The test scenario to execute. Examples: 'login_flow', 'register_flow', 'booking_flow', 'invite_flow'"]
) -> str:
    """
    Strict Automation testing tool. Executes REAL backend logic and External APIs.
    Requires ALL parameters (IDs, Tokens, User Location, Provider Location) to be present.
    """
    print(f"--- AUTOMATION TESTER (STRICT) CALLED: {test_scenario} ---")
    
    flutter_data = get_test_context_value("full_message", default={})
    user_query = flutter_data.get("user_query", "").strip()
    print(f"Flutter Data: {flutter_data}")
    print(f"User Query: {user_query}")
    
    if not flutter_data:
         return json.dumps({
            "Status": "error", 
            "Message": "No Flutter Message received. All context data (IDs, tokens) is missing."
        }, indent=2)

    scenario = test_scenario.lower()
    response = {}

    CRM_KEY = os.getenv("powerapps_accesskey")

    URL_APP_TOUR = os.getenv("etown_app_tour_url")

    if "register" in scenario:
        required_reg = ["mobile_number", "givenName", "emailId", "address", "latitude", "longitude"]
        missing = validate_required_fields(flutter_data, required_reg)
        if missing:
             return json.dumps({"Status": "error", "Message": f"Missing fields for Registration: {', '.join(missing)}"}, indent=2)

        mobile_error = validate_indian_mobile(flutter_data.get("mobile_number"))
        if mobile_error: return json.dumps(mobile_error, indent=2)
        email_error = validate_email(flutter_data.get("emailId"))
        if email_error: return json.dumps(email_error, indent=2)
        mobile_number = flutter_data.get("mobile_number")
        otp = flutter_data.get("otp")

        try:
            if not otp:
                step = "1. Send Onboard OTP"
                print(f"Executing {step} for mobile {mobile_number}")
                try:
                    handle = await auth_service.request_login(
                        identifier=str(mobile_number),
                        channel="sms",
                        method="otp",
                        purpose="verify",
                        language_code=flutter_data.get("language_code", "en"),
                    )
                except HTTPException as he:
                    logger.warning("automation_tester: login OTP request failed for %s", mobile_number, exc_info=True)
                    return json.dumps({"Status": "error", "Message": f"Failed to send OTP: {he.detail}"}, indent=2)
                return json.dumps({"status": "success", "Message": "OTP sent", **handle}, indent=2)

            else:
                step = "2. Verify OTP"
                try:
                    verify_json = await auth_service.verify_login(
                        otp=str(otp), identifier=str(mobile_number), channel="sms"
                    )
                except HTTPException as he:
                    logger.warning("automation_tester: OTP verification failed for %s", mobile_number, exc_info=True)
                    return json.dumps({"Status": "error", "Message": f"OTP verification failed: {he.detail}"}, indent=2)
                print(f"{step} Response: {json.dumps(verify_json, default=str)}")

                step = "4. Register User Details"
                reg_request = UserRegistrationRequest(
                    firstname=flutter_data.get("givenName"),
                    email=flutter_data.get("emailId"),
                    profileImage=flutter_data.get("profileImage", ""),
                    mobilenumber=int(mobile_number),
                    latitude=flutter_data.get("latitude"),
                    longitude=flutter_data.get("longitude"),
                    address=flutter_data.get("address"),
                    additionaladdressinfo=flutter_data.get("additionaladdressinfo", ""),
                    City=flutter_data.get("City", ""),
                    state_code=flutter_data.get("state_code", ""),
                    dateofbirth=flutter_data.get("dateofbirth", ""),
                    role=2,
                    user_login="mobile",
                    MobilenumberAccesstoken="",
                    membersignup_status="true",
                    lastsignup_status=0,
                    country_code=flutter_data.get("country_code", "+91"),
                )
                print(f"Executing {step} for mobile {mobile_number}")
                try:
                    reg_resp = await UserService.register_new_user(reg_request)
                except HTTPException as he:
                    logger.warning("automation_tester: registration failed for %s", mobile_number, exc_info=True)
                    return json.dumps({"Status": "error", "Message": f"Registration failed: {he.detail}"}, indent=2)

                reg_data = reg_resp.model_dump(by_alias=True)
                contact_list = reg_data.get("ContactInfo") or []
                profile_list = reg_data.get("UserprofileInfo") or []
                contact_id = contact_list[0].get("contactid") if contact_list else None
                userprofile_id = profile_list[0].get("ets_userprofileid") if profile_list else None

                if not contact_id or not userprofile_id:
                     return json.dumps({"Status": "error", "Message": "Failed to retrieve Contact/Profile IDs after registration."}, indent=2)

                session = await auth_service.issue_session_for_contact(
                    contactid=contact_id,
                    device_id=flutter_data.get("devicetoken"),
                )
                access_token = session["access_token"]

                step = "5. Create Collection"
                await handle_crud_collections(
                    user_contactid=contact_id,
                    user_userprofileid=userprofile_id,
                    collection_type="Private",
                    collectionname=f"{flutter_data.get('givenName')}'s etown",
                    isMytown="Yes",
                    actionmode="create",
                )

                step = "6. Register Device"
                if flutter_data.get("devicetoken"):
                    print(f"Executing {step} for contact {contact_id}")
                    await save_device_token(
                        contact_id=contact_id,
                        userprofile_id=userprofile_id,
                        devicetoken=flutter_data.get("devicetoken"),
                        cc_user_id=str(flutter_data.get("cc_user_id", 0)),
                        voip_device_token=flutter_data.get("voip_device_token"),
                    )

                step = "7. App Tour"
                headers_coll = {
                    "Content-Type": "application/json",
                    "MobilenumberAccesstoken": access_token,
                    "accesskey": CRM_KEY
                }
                payload_tour = {"contact_id": contact_id, "role": "2"}
                print(f"Executing {step}: {payload_tour}")
                resp_tour = requests.post(URL_APP_TOUR, json=payload_tour, headers=headers_coll, timeout=10)
                try:
                    tour_json = resp_tour.json()
                    print(f"{step} Response: {json.dumps(tour_json)}")
                except Exception as e:
                    logger.warning("automation_tester: App Tour response was not JSON", exc_info=True)
                    tour_json = {"raw_response": resp_tour.text, "error": f"App Tour response is not JSON: {str(e)}"}
                    print(f"{step} Response (non-JSON): {resp_tour.text}")

                return json.dumps({
                    "Status": "complete",
                    "Message": "Registration Successful",
                    "Auth": {
                        "AccessToken": access_token,
                        "RefreshToken": session["refresh_token"],
                        "ExpiresIn": session["expires_in"],
                    },
                    "IDs": {"contact_id": contact_id, "userprofile_id": userprofile_id},
                    "AppTour": tour_json
                }, indent=2)

        except Exception as e:
            logger.error(f"Register Flow Error at {step}: {e}", exc_info=True)
            return json.dumps({"Status": "error", "Message": f"Error at {step}: {str(e)}"}, indent=2)

    elif "login" in scenario:
        mobile_number = flutter_data.get("mobile_number")
        mobile_error = validate_indian_mobile(mobile_number)
        if mobile_error: return json.dumps(mobile_error, indent=2)
        otp = flutter_data.get("otp")
        
        if not mobile_number:
             return json.dumps({"Status": "error", "Message": "Missing required field: mobile_number"}, indent=2)

        try:
            if not otp:
                step = "1. Send OTP"
                print(f"Executing {step} for mobile {mobile_number}")
                try:
                    handle = await auth_service.request_login(
                        identifier=str(mobile_number),
                        channel="sms",
                        method="otp",
                        language_code=flutter_data.get("language_code", "en"),
                    )
                except HTTPException as he:
                    logger.warning("automation_tester: login OTP request failed for %s", mobile_number, exc_info=True)
                    return json.dumps({"Status": "error", "Message": f"Failed to send OTP: {he.detail}"}, indent=2)
                return json.dumps({"status": "success", "Message": "OTP sent", **handle}, indent=2)

            else:
                step = "2. Verify OTP"
                try:
                    session = await auth_service.verify_login(
                        otp=str(otp),
                        identifier=str(mobile_number),
                        channel="sms",
                        device_id=flutter_data.get("devicetoken"),
                    )
                except HTTPException as he:
                    logger.error(f"OTP Verification Failed: {he.detail}", exc_info=True)
                    return json.dumps({"Status": "error", "Message": f"OTP verification failed: {he.detail}"}, indent=2)

                access_token = session.get("access_token")
                if session.get("registration") or not session.get("contactid"):
                    return json.dumps({"Status": "error", "Message": "Not a registered user"}, indent=2)

                step = "3. Check User"
                crm_json = await check_user_exists_in_db(mobilenumber=str(mobile_number))
                print(f"{step} Response JSON: {json.dumps(crm_json, indent=2, default=str)}")

                if not crm_json.get("ContactInfo"):
                    return json.dumps({
                        "Status": "error",
                        "Message": "Not a registered user",
                        "CRM_Response": crm_json
                    }, indent=2)

                final_response = {
                    "Status": "complete",
                    "Message": "Login and Data Fetch Successful",
                    "Auth": {
                        "AccessToken": access_token,
                        "RefreshToken": session["refresh_token"],
                        "ExpiresIn": session["expires_in"],
                    },
                    "Profile": {
                        "contactid": session.get("contactid"),
                        "userprofileid": session.get("userprofileid"),
                        "role": session.get("role"),
                    },
                    "CRMData": crm_json
                }
                return json.dumps(final_response, indent=2)

        except Exception as e:
            logger.error(f"Login API Error: {e}", exc_info=True)
            return json.dumps({"Status": "error", "Message": f"Exception during login flow: {str(e)}"}, indent=2)

    elif "booking" in scenario or "search" in scenario or "book" in scenario:
        
        if flutter_data.get("confirmed") or flutter_data.get("provider_item_id"):
            
            required = [
                "contact_id", "user_profile_id", "provider_item_id", 
                "address", "user_name", "latitude", "longitude", 
                "provider_latitude", "provider_longitude", 
                "mobilenumber_accesstoken"
            ]
            missing = validate_required_fields(flutter_data, required)
            
            if missing:
                return json.dumps({
                    "Status": "error", 
                    "Message": f"Missing fields for Booking Request: {', '.join(missing)}"
                }, indent=2)

            print("Executing Real Provider Request...")
            
            provider_type = flutter_data.get("provider_type", "verified")
            category = flutter_data.get("category", "service")
            oauth_token = flutter_data.get("oauth_token")

            req_msg, req_output = await handle_request_provider(
                contact_id=flutter_data["contact_id"],
                user_profile_id=flutter_data["user_profile_id"],
                provider_item_id=flutter_data["provider_item_id"],
                service_datetime=flutter_data.get("date", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                user_address=flutter_data["address"],
                detailed_request=flutter_data.get("details", "Automation Test Booking"),
                first_name=flutter_data["user_name"],
                user_latitude=float(flutter_data["latitude"]),
                user_longitude=float(flutter_data["longitude"]),
                provider_latitude=float(flutter_data["provider_latitude"]), 
                provider_longitude=float(flutter_data["provider_longitude"]),
                mobilenumber_accesstoken=flutter_data["mobilenumber_accesstoken"],
                oauth_token=oauth_token,
                provider_type=provider_type,
                category=category,
                user_role=2
            )
            
            status = "complete" if req_output and req_output.get("ets_requestid") else "error"
            
            response = {
                "Action": "BookingSuccess" if status == "complete" else "BookingFailed",
                "Message": req_msg,
                "Status": status,
                "BookingData": req_output
            }

        elif "book" in user_query.lower() and "from" in user_query.lower():
            match = re.search(r"from\s+(.+)", user_query, re.IGNORECASE)
            
            if match:
                provider_name_query = match.group(1).strip()
                print(f"Dynamic Booking Detected. Searching for provider: {provider_name_query}")
                
                required = ["latitude", "longitude", "contact_id", "user_profile_id", "mobilenumber_accesstoken"]
                missing = validate_required_fields(flutter_data, required)
                if missing:
                    return json.dumps({"Status": "error", "Message": f"Missing fields for Dynamic Booking: {', '.join(missing)}"}, indent=2)

                results, _ = await handle_search_provider(
                    search_term=provider_name_query,
                    user_latitude=float(flutter_data["latitude"]),
                    user_longitude=float(flutter_data["longitude"]),
                    limit=1
                )
                
                if not results:
                     return json.dumps({"Status": "error", "Message": f"Could not find provider '{provider_name_query}' nearby."}, indent=2)

                target_provider = results[0]
                
                p_id = target_provider.get("providerservicesid") or target_provider.get("providerid") or target_provider.get("contactid")
                
                p_lat = target_provider.get("latitude")
                p_lon = target_provider.get("longitude")
                
                if not p_id or not p_lat or not p_lon:
                    return json.dumps({"Status": "error", "Message": "Found provider but missing ID or Location data."}, indent=2)

                print(f"Found Provider: {target_provider.get('businessname')} (ID: {p_id})")
                
                oauth_token = flutter_data.get("oauth_token")
                
                req_msg, req_output = await handle_request_provider(
                    contact_id=flutter_data["contact_id"],
                    user_profile_id=flutter_data["user_profile_id"],
                    provider_item_id=str(p_id),
                    service_datetime=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    user_address=flutter_data["address"],
                    detailed_request=flutter_data.get("details", f"Booking from {provider_name_query}"),
                    first_name=flutter_data["user_name"],
                    user_latitude=float(flutter_data["latitude"]),
                    user_longitude=float(flutter_data["longitude"]),
                    provider_latitude=float(p_lat),
                    provider_longitude=float(p_lon),
                    mobilenumber_accesstoken=flutter_data["mobilenumber_accesstoken"],
                    oauth_token=oauth_token,
                    provider_type="verified",
                    category="service",
                    user_role=2
                )

                status = "complete" if req_output and req_output.get("ets_requestid") else "error"
                response = {
                    "Action": "DynamicBookingExecuted",
                    "Message": req_msg,
                    "ProviderFound": target_provider.get("businessname"),
                    "Status": status,
                    "BookingData": req_output
                }
            else:
                 response = {"Status": "error", "Message": "Could not parse provider name from 'Book ... from ...' query."}

        else:
            required = ["latitude", "longitude"]
            missing = validate_required_fields(flutter_data, required)
            
            if missing:
                 return json.dumps({
                    "Status": "error", 
                    "Message": f"Missing fields for Search: {', '.join(missing)}"
                }, indent=2)
            
            search_query = "business"
            
            if "book" in user_query.lower():
                clean_q = re.sub(r"book\s+(a\s+|an\s+)?", "", user_query, flags=re.IGNORECASE).strip()
                if clean_q:
                    search_query = clean_q
            elif flutter_data.get("query"):
                search_query = flutter_data.get("query")
            
            print(f"Executing Real Search for: {search_query}")
            results, desc = await handle_search_provider(
                search_term=search_query,
                user_latitude=float(flutter_data["latitude"]),
                user_longitude=float(flutter_data["longitude"]),
                limit=5
            )
            if not is_search_relevant(search_query, results):
                 return json.dumps({
                    "Status": "error", 
                    "Message": f"Service '{search_query}' is not currently available in Etown."
                }, indent=2)
            
            clean_results = []
            for r in results:
                clean_r = {k: str(v) if not isinstance(v, (int, float, bool, str, type(None))) else v for k,v in r.items()}
                clean_results.append(clean_r)

            response = {
                "Action": "ShowProviders",
                "Message": f"Found {len(clean_results)} results for '{search_query}'.",
                "Status": "in_progress",
                "Providers": clean_results
            }

    elif "invite" in scenario:
        
        common_required = ["contact_id", "user_profile_id", "mobilenumber_accesstoken"]
        missing_common = validate_required_fields(flutter_data, common_required)
        
        if missing_common:
             return json.dumps({
                "Status": "error", 
                "Message": f"Missing authentication fields for Invite: {', '.join(missing_common)}"
            }, indent=2)

        email = flutter_data.get("email")
        mobile = flutter_data.get("mobile_number")
        invite_role = flutter_data.get("invite_role", "Member")
        oauth_token = flutter_data.get("oauth_token")
        
        if email:
            email_error = validate_email(email)
            if email_error: return json.dumps(email_error, indent=2)

            if not flutter_data.get("receiver_name"):
                return json.dumps({"Status": "error", "Message": "Missing 'receiver_name' for email invite."}, indent=2)
                
            msg, data = await handle_external_email_invite(
                contact_id=flutter_data["contact_id"],
                user_profile_id=flutter_data["user_profile_id"],
                receiver_name=flutter_data["receiver_name"],
                inviter_type=2, 
                invite_role=invite_role,
                invitecontact_email=email,
                mobilenumber_accesstoken=flutter_data["mobilenumber_accesstoken"]
            )
            response = {"Action": "InviteSent", "Message": msg, "Status": "complete", "Data": data}
            
        elif mobile:
            mobile_error = validate_indian_mobile(mobile)
            if mobile_error: return json.dumps(mobile_error, indent=2)

            if not flutter_data.get("receiver_name"):
                return json.dumps({"Status": "error", "Message": "Missing 'receiver_name' for mobile invite."}, indent=2)

            msg, data = await handle_external_mobile_invite(
                contact_id=flutter_data["contact_id"],
                user_profile_id=flutter_data["user_profile_id"],
                receiver_name=flutter_data["receiver_name"],
                inviter_type=2, 
                invite_role=invite_role,
                invitecontact_mobilenumber=str(mobile),
                mobilenumber_accesstoken=flutter_data["mobilenumber_accesstoken"]
            )
            response = {"Action": "InviteSent", "Message": msg, "Status": "complete", "Data": data}
            
        else:
            search_name = flutter_data.get("query")
            if not search_name:
                 return json.dumps({"Status": "error", "Message": "Provide 'query' (name), 'email', or 'mobile_number'."}, indent=2)
            
            if not flutter_data.get("user_name"):
                 return json.dumps({"Status": "error", "Message": "Missing 'user_name' for internal invite."}, indent=2)

            msg, output = await handle_internal_invite(
                search_term=search_name,
                role=invite_role,
                action="send",
                contact_id=flutter_data["contact_id"],
                user_profile_id=flutter_data["user_profile_id"],
                user_name=flutter_data["user_name"],
                mobilenumber_accesstoken=flutter_data["mobilenumber_accesstoken"],
                oauth_token=oauth_token,
                language="en",
                user_role=2
            )
            response = {"Action": "InternalInviteResult", "Message": msg, "Status": "complete", "Data": output}

    else:
        response = {
            "Status": "unknown",
            "Message": f"Scenario '{test_scenario}' not recognized.",
        }

    return json.dumps(response, indent=2)