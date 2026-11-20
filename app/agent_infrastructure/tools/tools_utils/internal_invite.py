"""Internal Invite Tool - searches existing Etown users and invites them into a circle.

Member- and provider-agent tool behind internal_invite: finds members or providers by exact email or mobile or fuzzy name, resolves the follow-up yes/no confirmation from chat history, blocks duplicate and self invites, and sends through new_invite_send in-process.

Features:
    - Fuzzy Name Search: RapidFuzz plus space-insensitive matching, with a whole-word rescue pass
    - Identifier Search: exact email or last-10-digit mobile takes precedence over fuzzy search
    - Role Auto-Detection: corrects the requested Member/Provider role to the match's real type
    - Multi-name Batch Resolution: one call searches/sends for several names, results combined
    - Pending Confirmation Resolution: restores last turn's matches and classifies a yes/no reply
    - Duplicate/Relationship Guards: blocks invites to existing connections or pending invitees
    - Native Delivery: invitation create, notify and Dataverse mirror via new_invite_send
    - Translation: non-English search terms are translated to English via GPT-4.1

Classes:
    No classes defined (module uses function-based invitation utilities)

Functions:
    classify_invite_confirmation(): Classifies a follow-up reply as confirm, decline or unclear
    _pending_candidates(): Flattens a pending invite context into its candidate rows
    _candidate_display_name(): Best available display name for a candidate row
    _term_matches_pending(): Checks whether a search term names a person awaiting confirmation
    _select_confirmed_candidates(): Resolves which pending candidates a "yes" confirms
    get_pending_invite_context(): Restores an awaiting-confirmation result from chat history
    translate_to_english(): Translates a non-English search term via GPT-4.1
    get_contact_firstname(): Fetches a contact's first name from etown_contact
    _normalize_phone_digits(): Returns the last 10 digits of a phone-like string
    _find_existing_phonebook_invite(): Finds an active invitation row for a phonebook search
    check_existing_invite(): Checks for an existing relationship or active invitation
    get_invitation_statuses_for_targets(): Batch-resolves invite/relationship status for targets
    send_invite_via_api(): Creates the invitation in-house via new_invite_send
    get_user_by_identifier(): Searches members/providers by exact email or mobile number
    _send_invite_to_user(): Sends an invite to one resolved candidate row
    _send_invite_to_pending_selection(): Delivers invites to the confirmed pending candidates
    _handle_single_invite(): Searches for or sends to a single name
    handle_internal_invite(): Public entry point for search, confirmation and send

Dependencies:
    - rapidfuzz: process and fuzz scorers for fuzzy name matching
    - langchain_core.messages: SystemMessage/HumanMessage for the translation call
    - app.db.client: Database for member/provider search and invitation lookups
    - app.agent_infrastructure.infrastructure.llm_clients: gpt41 for term translation
    - app.core.logging: Centralized logging for invitation operations
    - app.etown.invitations.invitation_utils: new_invite_send for native invitation create

Updates:
    11-Mar-2026: Standardized module docstring; added role auto-detection; space-insensitive fuzzy matching.
    12-Mar-2026: Wired phonebook_only search_scope through tool schema and wrapper.
    26-May-2026: Fixed fuzzy matching false positives.
    16-Jul-2026: Invite send uses native new_invite_send() instead of PowerApps.
    27-Jul-2026: Pending-invite yes/no moved here; already-invited notice; multi-match asks which.
    26-Aug-2026: Logged silent exception handlers with tracebacks.
    24-Sep-2026: LLM-calling functions are traced to LangSmith with @traceable.
    08-Oct-2026: Removed LangSmith tracing.
"""

import json
import logging
import os
import asyncio
import re
from typing import Any, Dict, Tuple, Optional, List, Union
from rapidfuzz import process, fuzz
from langchain_core.messages import SystemMessage, HumanMessage

from app.db.client import Database
from app.agent_infrastructure.infrastructure.llm_clients import gpt41
from app.core.logging import logger
from app.etown.invitations.invitation_utils import new_invite_send

MY_CIRCLE_ROLES = {
    "provider": 1,
    "member": 2
}

CONFIRMATION_TOKENS = {
    "yes", "yeah", "yep", "yup", "ya", "sure", "ok", "okay", "confirm",
    "confirmed", "proceed", "send", "correct", "right", "ahead",
}
DECLINE_TOKENS = {
    "no", "nope", "nah", "cancel", "stop", "dont", "don't", "never",
    "not", "skip", "later",
}
MAX_CONFIRMATION_TOKENS = 6


def classify_invite_confirmation(text: str) -> str:
    """Classify a short reply to a pending invite as "confirm", "decline" or "unclear"."""
    cleaned = re.sub(r"[^\w\s']", " ", (text or "").lower()).strip()
    tokens = [t for t in cleaned.split() if t]

    if not tokens or len(tokens) > MAX_CONFIRMATION_TOKENS:
        return "unclear"

    if any(t in DECLINE_TOKENS for t in tokens):
        return "decline"
    if any(t in CONFIRMATION_TOKENS for t in tokens):
        return "confirm"
    return "unclear"


def _pending_candidates(pending: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten a pending invite context into its member + provider candidate rows."""
    if not isinstance(pending, dict):
        return []
    members = pending.get("member") or []
    providers = pending.get("provider") or []
    return [c for c in list(members) + list(providers) if isinstance(c, dict)]


def _candidate_display_name(candidate: Dict[str, Any], fallback: str = "") -> str:
    """Return the best available display name for a candidate row, or the fallback."""
    return (
        candidate.get("contactidname")
        or candidate.get("businessname")
        or candidate.get("firstname")
        or fallback
    )


def _term_matches_pending(term: str, pending_names: List[str]) -> bool:
    """Return True when the term loosely matches a pending name, space-insensitive and substring either way."""
    normalized = re.sub(r"\s+", "", (term or "").lower())
    if not normalized:
        return False
    for name in pending_names:
        candidate = re.sub(r"\s+", "", (name or "").lower())
        if not candidate:
            continue
        if normalized == candidate or normalized in candidate or candidate in normalized:
            return True
    return False


def _select_confirmed_candidates(
    candidates: List[Dict[str, Any]],
    terms: List[str],
    user_prompt: Optional[str],
) -> List[Dict[str, Any]]:
    """Return the pending candidates a confirmation refers to: all, one uniquely named, or [] when ambiguous."""
    if len(candidates) <= 1:
        return candidates

    haystack = re.sub(r"[^\w\s]", " ", f"{user_prompt or ''} {' '.join(terms)}".lower())
    if set(haystack.split()) & {"all", "both", "everyone", "everybody"}:
        return candidates

    normalized_haystack = re.sub(r"\s+", "", haystack)

    def normalized(candidate: Dict[str, Any]) -> str:
        return re.sub(r"\s+", "", _candidate_display_name(candidate).lower())

    named = [c for c in candidates if normalized(c) and normalized(c) in normalized_haystack]
    if named:
        longest = max(len(normalized(c)) for c in named)
        best = [c for c in named if len(normalized(c)) == longest]
        if len(best) == 1:
            return best
    return []


async def get_pending_invite_context(conversation_id: str) -> Optional[Dict[str, Any]]:
    """Return the last assistant turn's pending invite search output with ambiguous cleared, or None."""
    if not conversation_id:
        return None

    try:
        last_assistant = await Database.fetchrow(
            """
            SELECT metadata
            FROM public.etownuserchaatiehistory
            WHERE conversation_id = $1 AND role = 'assistant'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            conversation_id,
        )
    except Exception as e:
        logger.error(f"Could not load history for pending invite check: {e}", exc_info=True)
        return None

    if not last_assistant:
        return None

    meta = last_assistant.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            logger.debug("internal_invite: pending-invite metadata is not valid JSON")
            return None
    if not isinstance(meta, dict) or meta.get("response_type") != "internal_invite":
        return None

    implicit = meta.get("implicit_output")
    if not isinstance(implicit, dict) or implicit.get("search_invite") is not True:
        return None
    if not _pending_candidates(implicit):
        return None

    pending = dict(implicit)
    pending["ambiguous"] = []
    logger.info(f"Restored pending invite context: {pending.get('search_term')}")
    return pending

async def translate_to_english(text: str, source_language: str) -> str:
    """Translate a non-English search term to English with gpt41, returning the input on failure."""
    if not source_language or source_language.lower() in ["en", "english"]:
        return text
    try:
        system_prompt = "You are a translator. Translate the following name or search term to English. Return ONLY the translated text, no explanations."
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=text)]
        response = await gpt41.ainvoke(messages)
        return response.content.strip()
    except Exception as e:
        logger.error(f"Translation failed: {e}", exc_info=True)
        return text

async def get_contact_firstname(contact_id: str) -> Optional[str]:
    """Return the etown_contact firstname for a contact ID, or None."""
    if not contact_id:
        return None
    try:
        query = "SELECT firstname FROM etown_contact WHERE contactid = $1"
        result = await Database.fetchrow(query, contact_id)
        if result and result.get('firstname'):
            return result['firstname']
    except Exception as e:
        logger.error(f"Error fetching firstname for contact {contact_id}: {e}", exc_info=True)
    return None


def _normalize_phone_digits(value: Optional[str]) -> str:
    """Return the last 10 digits from a phone-like string."""
    if not value:
        return ""
    digits = "".join(filter(str.isdigit, value))
    return digits[-10:]


async def _find_existing_phonebook_invite(
    inviter_contact_id: str,
    search_term: str,
) -> Optional[Dict[str, Any]]:
    """Return the inviter's newest active invitation matching the term by mobile or name, or None."""
    try:
        search_lower = search_term.lower()
        phone_digits = _normalize_phone_digits(search_term)
        if phone_digits:
            query = """
                SELECT invitecontactname, invite_rolename, invitationcode, invitecontactmobilenumber, receivername
                FROM etown_invitation
                WHERE invitercontact = $1
                  AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
                  AND RIGHT(regexp_replace(coalesce(invitecontactmobilenumber, ''), '\\D', '', 'g'), 10) = $2
                ORDER BY createdon DESC
                LIMIT 1
            """
            row = await Database.fetchrow(query, inviter_contact_id, phone_digits)
        else:
            query = """
                SELECT invitecontactname, receivername, invite_rolename, invitationcode, invitecontactmobilenumber
                FROM etown_invitation
                WHERE invitercontact = $1
                  AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
                  AND (
                      LOWER(invitecontactname) LIKE '%' || $2 || '%'
                      OR LOWER(receivername) LIKE '%' || $2 || '%'
                  )
                ORDER BY createdon DESC
                LIMIT 1
            """
            row = await Database.fetchrow(query, inviter_contact_id, search_lower)

        return row
    except Exception as e:
        logger.error(f"Error checking phonebook invite fallback: {e}", exc_info=True)
        return None


async def check_existing_invite(current_contact_id: str, target_contact_id: str) -> Optional[str]:
    """Return a user-facing message when the contacts are related or share an active invite, else None."""
    try:
        member_rel_query = """
            SELECT relationshiptype FROM etown_memberrelation 
            WHERE (from_contactid = $1 AND to_contactid = $2) 
               OR (from_contactid = $2 AND to_contactid = $1)
            LIMIT 1
        """
        member_rel_result = await Database.fetchrow(member_rel_query, current_contact_id, target_contact_id)
        
        if member_rel_result:
            relationship_type = member_rel_result.get('relationshiptype')
            role_map = {1: "Provider", 2: "Member"}
            role_name = role_map.get(relationship_type, 'Unknown')
            logger.info(f"Found existing member relationship: {role_name} between {current_contact_id} and {target_contact_id}")
            return f"You already have an existing {role_name} connection with this user. You cannot send multiple invitations to the same person with different roles."
        
        provider_rel_query = """
            SELECT relationshiptype, relationshiptypename FROM etown_providerrelation 
            WHERE (fromcontactid = $1 AND tocontactid = $2) 
               OR (fromcontactid = $2 AND tocontactid = $1)
            LIMIT 1
        """
        provider_rel_result = await Database.fetchrow(provider_rel_query, current_contact_id, target_contact_id)
        
        if provider_rel_result:
            relationship_type = provider_rel_result.get('relationshiptype')
            role_name = provider_rel_result.get('relationshiptypename') or {1: "Provider", 2: "Member"}.get(relationship_type, 'Unknown')
            logger.info(f"Found existing provider relationship: {role_name} between {current_contact_id} and {target_contact_id}")
            return f"You already have an existing {role_name} connection with this user. You cannot send multiple invitations to the same person with different roles."
        
        query = """
            SELECT invitationcode, invitercontact, invitecontact, invitecontactname, invite_rolename, 
                   isopponentadded, createdon
            FROM etown_invitation
            WHERE (
                (invitercontact = $1 AND invitecontact = $2) OR
                (invitercontact = $2 AND invitecontact = $1)
            )
            AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
            ORDER BY createdon DESC
            LIMIT 1
        """
        result = await Database.fetchrow(query, current_contact_id, target_contact_id)

        if result:
            status = result.get('invitationcode')
            invite_role = result.get('invite_rolename', 'Unknown')
            is_accepted = str(result.get('isopponentadded')).lower() == 'true'
            status_key = (status or "").strip().lower()

            if status_key == 'accepted' or is_accepted:
                return f"You already have an existing {invite_role} connection with this user. You cannot send multiple invitations to the same person with different roles."
            elif status_key in ('requested', 'pending'):
                if result.get('invitercontact') == current_contact_id:
                    return "You have already sent an invite to this user. Please wait for them to respond."
                else:
                    return "This user has already sent you an invite. Please check your notifications to accept or decline it."
            else:
                logger.warning(f"Found invitation with unusual status '{status}' between {current_contact_id} and {target_contact_id}")
                return f"An invitation already exists between you and this user (status: {status}). Please wait for it to be resolved."
                    
        return None
    except Exception as e:
        logger.error(f"Error checking existing invite/relationship: {e}", exc_info=True)
        return None

async def get_invitation_statuses_for_targets(
    inviter_contact_id: str,
    target_contact_ids: List[str],
) -> Dict[str, str]:
    """Map each target contact ID to Accepted, Requested or its raw invitation code, omitting unrelated targets."""
    ids = [cid for cid in {c for c in target_contact_ids if c}]
    if not inviter_contact_id or not ids:
        return {}

    statuses: Dict[str, str] = {}
    try:
        member_rel_rows = await Database.fetch(
            """
            SELECT CASE WHEN from_contactid = $1 THEN to_contactid ELSE from_contactid END AS other
            FROM etown_memberrelation
            WHERE (from_contactid = $1 AND to_contactid = ANY($2::text[]))
               OR (to_contactid = $1 AND from_contactid = ANY($2::text[]))
            """,
            inviter_contact_id,
            ids,
        )
        provider_rel_rows = await Database.fetch(
            """
            SELECT CASE WHEN fromcontactid = $1 THEN tocontactid ELSE fromcontactid END AS other
            FROM etown_providerrelation
            WHERE (fromcontactid = $1 AND tocontactid = ANY($2::text[]))
               OR (tocontactid = $1 AND fromcontactid = ANY($2::text[]))
            """,
            inviter_contact_id,
            ids,
        )
        for row in list(member_rel_rows) + list(provider_rel_rows):
            other = row.get("other")
            if other:
                statuses[other] = "Accepted"

        invite_rows = await Database.fetch(
            """
            SELECT
                CASE WHEN invitercontact = $1 THEN invitecontact ELSE invitercontact END AS other,
                invitationcode,
                isopponentadded
            FROM etown_invitation
            WHERE (
                (invitercontact = $1 AND invitecontact = ANY($2::text[]))
                OR (invitecontact = $1 AND invitercontact = ANY($2::text[]))
            )
            AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
            ORDER BY createdon DESC
            """,
            inviter_contact_id,
            ids,
        )
        for row in invite_rows:
            other = row.get("other")
            if not other or other in statuses:
                continue
            code = row.get("invitationcode")
            code_key = (code or "").strip().lower()
            if str(row.get("isopponentadded")).lower() == "true" or code_key == "accepted":
                statuses[other] = "Accepted"
            elif code_key in ("requested", "pending"):
                statuses[other] = "Requested"
            elif code:
                statuses[other] = code
    except Exception as e:
        logger.error(f"Error resolving per-target invitation statuses: {e}", exc_info=True)

    return statuses


async def send_invite_via_api(
    inviter_id: str,
    inviter_profile_id: str,
    inviter_name: str,
    target_contact_id: str,
    target_profile_id: str,
    target_name: str,
    inviter_type: int,
    invite_role: int,
) -> Dict[str, Any]:
    """Create a Requested invitation through new_invite_send and return its result, or {} on failure.

    Args:
        inviter_id: Inviter's contact ID.
        inviter_profile_id: Inviter's user profile ID.
        inviter_name: Inviter's first name for the notification.
        target_contact_id: Invitee's contact ID.
        target_profile_id: Invitee's user profile ID.
        target_name: Invitee's first name.
        inviter_type: Inviter type, 2 for member, 1 for provider.
        invite_role: Role invited to, 1 for Provider, 2 for Member.

    Returns:
        The new_invite_send result dict, or {} on failure.
    """
    try:
        result = await new_invite_send(
            inviter=inviter_id,
            inviter_firstname=inviter_name,
            invite_contact=target_contact_id,
            invite_contact_firstname=target_name,
            type=inviter_type,
            invite_role=invite_role,
            invitation_code="Requested",
            inviter_userprofile_id=inviter_profile_id,
            invite_contact_userprofile_id=target_profile_id,
        )
        return result or {}
    except Exception as e:
        logger.error(f"new_invite_send failed: {e}", exc_info=True)
        return {}

async def get_user_by_identifier(identifier: str, contact_id: str) -> List[Dict[str, Any]]:
    """Find members and providers by exact email or last-10-digit mobile, excluding the requesting contact."""
    results = []
    
    if "@" in identifier and "." in identifier:
        logger.info(f"Searching for user by email: {identifier}")
        try:
            member_query = """
                    SELECT em.contactid, em.userprofileid, em.contactidname, em.email, em.mobilenumber, ec.profileimage as "Etown_profileimage", 'member' as type
                    FROM etown_member em
                    LEFT JOIN etown_contact ec ON em.contactid = ec.contactid
                    WHERE em.email = $1 AND em.contactid != $2
                    AND ec.verified IS NOT FALSE
            """
            provider_query = """
                SELECT contactid, userprofileid, businessname, firstname, email, mobilenumber, serviceimageurl as "Etown_serviceimageurl", 'provider' as type
                FROM etown_provider
                WHERE email = $1 AND contactid != $2
            """

            members = await Database.fetch(member_query, identifier, contact_id)
            providers = await Database.fetch(provider_query, identifier, contact_id)
            
            results.extend(members)
            results.extend(providers)
            
        except Exception as e:
            logger.error(f"Error searching by email: {e}", exc_info=True)
            
    elif any(char.isdigit() for char in identifier):
        digits = ''.join(filter(str.isdigit, identifier))
        if len(digits) >= 10:
            last_10 = digits[-10:]
            logger.info(f"Searching for user by mobile (last 10): {last_10}")
            
            try:
                member_query = """
                    SELECT em.contactid, em.userprofileid, em.contactidname, em.email, em.mobilenumber, ec.profileimage as "Etown_profileimage", 'member' as type
                    FROM etown_member em
                    LEFT JOIN etown_contact ec ON em.contactid = ec.contactid 
                    WHERE RIGHT(em.mobilenumber, 10) = $1 AND em.contactid != $2
                    AND ec.verified IS NOT FALSE
                """
                provider_query = """
                    SELECT contactid, userprofileid, businessname, firstname, email, mobilenumber, serviceimageurl as "Etown_serviceimageurl", 'provider' as type
                    FROM etown_provider 
                    WHERE RIGHT(mobilenumber, 10) = $1 AND contactid != $2
                """
                
                members = await Database.fetch(member_query, last_10, contact_id)
                providers = await Database.fetch(provider_query, last_10, contact_id)
                
                results.extend(members)
                results.extend(providers)
                
            except Exception as e:
                logger.error(f"Error searching by mobile: {e}", exc_info=True)
        else:
             logger.info(f"Input '{identifier}' has digits but fewer than 10, skipping mobile search.")

    cleaned_results = []
    for r in results:
        obj = dict(r)
        if obj.get('type') == 'member':
            obj['firstname'] = obj.get('contactidname')
        else:
            obj['firstname'] = obj.get('businessname') or obj.get('firstname')
        cleaned_results.append(obj)
        
    return cleaned_results


async def _send_invite_to_user(
    target_user: Dict[str, Any],
    role: str,
    contact_id: str,
    user_profile_id: str,
    user_name: str = "User",
    user_role: int = 2,
    fallback_name: str = "",
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Invite one resolved candidate after the duplicate check, correcting the role to the candidate's type.

    Args:
        target_user: Candidate row carrying contactid and userprofileid.
        role: Requested role, overridden by the candidate's actual type.
        contact_id: Inviter's contact ID.
        user_profile_id: Inviter's user profile ID.
        user_name: Inviter's display name when the contact lookup returns nothing.
        user_role: Inviter type, 2 for member, 1 for provider.
        fallback_name: Name to show when the candidate row carries none.

    Returns:
        (message, implicit_output), with implicit_output None on every failure.
    """
    target_contact_id = target_user.get('contactid')
    target_profile_id = target_user.get('userprofileid')
    target_name = _candidate_display_name(target_user, fallback_name)

    if not target_contact_id or not target_profile_id:
        logger.error(f"Missing contact/profile ID for target: {target_name}. Data: {target_user}")
        return f"Error: Could not retrieve contact details for {target_name}.", None

    existing_invite_msg = await check_existing_invite(contact_id, target_contact_id)
    if existing_invite_msg:
        return existing_invite_msg, None

    real_inviter_name = await get_contact_firstname(contact_id) or user_name

    detected_type = str(target_user.get('type') or 'member').lower()
    actual_role = "Provider" if detected_type == 'provider' else "Member"

    if actual_role.lower() != (role or "").lower():
        logger.info(f"Role auto-detection override: '{target_name}' is actually a {actual_role}, not {role}. Using {actual_role} role for invite.")
        role = actual_role

    invite_role_int = MY_CIRCLE_ROLES.get(role.lower(), 2)
    api_resp = await send_invite_via_api(
        inviter_id=contact_id,
        inviter_profile_id=user_profile_id,
        inviter_name=real_inviter_name,
        target_contact_id=target_contact_id,
        target_profile_id=target_profile_id,
        target_name=target_name,
        inviter_type=user_role,
        invite_role=invite_role_int,
    )

    if api_resp and (
        api_resp.get("Invitationcode") == "Requested" or
        api_resp.get("invitation_code") == "Requested" or
        api_resp.get("status") == "success"
    ):
        invitation_code = api_resp.get("Invitationcode") or api_resp.get("invitation_code")
        return f"Invite successfully sent to {target_name} as {role}.", {
            "status": "sent",
            "target_user": target_name,
            "invitationcode": invitation_code,
        }

    response_text = str(api_resp).lower()
    if any(keyword in response_text for keyword in ["already", "duplicate", "constraint", "exists"]):
        return f"Invite to {target_name} has already been sent or a connection already exists.", None

    return f"Failed to send invite to {target_name}. Please try again later.", None


async def _send_invite_to_pending_selection(
    pending: Dict[str, Any],
    contact_id: str,
    user_profile_id: str,
    user_name: str = "User",
    user_role: int = 2,
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Invite the confirmed candidates from a pending search without re-searching, and summarise sent and failed."""
    if candidates is None:
        candidates = _pending_candidates(pending)
    role = pending.get("role") or "Member"

    sent: List[str] = []
    problems: List[str] = []
    invitation_code = None

    for candidate in candidates:
        message, output = await _send_invite_to_user(
            target_user=candidate,
            role=role,
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            user_name=user_name,
            user_role=user_role,
            fallback_name=candidate.get("matched_query") or "",
        )
        if output and output.get("status") == "sent":
            sent.append(output.get("target_user") or _candidate_display_name(candidate))
            invitation_code = invitation_code or output.get("invitationcode")
        else:
            name = _candidate_display_name(candidate)
            problems.append(f"{name}: {message}" if name and len(candidates) > 1 else message)

    if sent:
        result_text = f"Invite successfully sent to {', '.join(sent)} as {role.lower()}."
        if problems:
            result_text += " " + " ".join(problems)
    else:
        result_text = " ".join(problems) or "I couldn't send the invite. Please try again later."

    implicit_output = {
        "status": "sent" if sent else "failed",
        "target_user": ", ".join(sent) if sent else None,
        "invitationcode": invitation_code,
        "role": role,
        "search_term": pending.get("search_term"),
        "search_invite": False,
        "contact_invite": False,
    }
    return result_text, implicit_output


async def _handle_single_invite(
    search_term: str,
    role: str,
    contact_id: str,
    user_profile_id: str,
    user_name: str = "User",
    oauth_token: str = "",
    mobilenumber_accesstoken: Optional[str] = None,
    language: str = "en",
    search_scope: str = "all",
    action: str = "search",
    user_role: int = 2
) -> Tuple[str, Dict[str, Any]]:
    """Search for one name by identifier or fuzzy match, then return matches or send the invite when action is send."""
    logger.info(f"Internal Invite: term='{search_term}', role='{role}', action='{action}'")

    if search_scope == "phonebook_only":
        existing_invite = await _find_existing_phonebook_invite(contact_id, search_term)
        if existing_invite:
            status = (existing_invite.get("invitationcode") or "").strip().lower()
            role_name = existing_invite.get("invite_rolename") or "Member"
            target_name = existing_invite.get("invitecontactname") or existing_invite.get("invitecontactmobilenumber") or search_term
            if status in ("requested", "pending"):
                return f"An invite has already been sent to {target_name} as {role_name}.", {
                    "status": "already_requested"
                }
            if status == "accepted":
                return f"{target_name} is already in your circle as {role_name}.", {
                    "status": "already_accepted"
                }

        implicit_output = {
            "search_term": [search_term], "role": role, "contact_invite": True, "search_invite": False
        }
        return "Opening your phonebook to select a contact.", implicit_output

    search_results = []
    results_dict = {"member": [], "provider": []}
    
    identifier_matches = await get_user_by_identifier(search_term, contact_id)
    
    if identifier_matches:
        logger.info(f"Found {len(identifier_matches)} matches by unique identifier (mobile/email). Skipping fuzzy name search.")
        search_results = identifier_matches
        
        for m in identifier_matches:
            if m.get('type') == 'member':
                results_dict["member"].append(m)
            else:
                results_dict["provider"].append(m)
                
    else:
        final_search_name = await translate_to_english(search_term, language)
        
        try:
            member_query = """
                SELECT em.contactid, em.userprofileid, em.contactidname, em.email, em.mobilenumber, ec.profileimage as "Etown_profileimage", 'member' as type
                FROM etown_member em
                LEFT JOIN etown_contact ec ON em.contactid = ec.contactid 
                WHERE em.contactidname IS NOT NULL AND em.contactid != $1
                AND ec.verified IS NOT FALSE
            """
            members = await Database.fetch(member_query, contact_id)
            
            provider_query = """
                SELECT contactid, userprofileid, businessname, firstname, email, mobilenumber, serviceimageurl as "Etown_serviceimageurl", 'provider' as type
                FROM etown_provider 
                WHERE (businessname IS NOT NULL OR firstname IS NOT NULL) AND contactid != $1
            """
            providers = await Database.fetch(provider_query, contact_id)

            candidates = []
            for m in members:
                name = m.get('contactidname')
                if name: candidates.append((name, 'member', m))

            for p in providers:
                name = p.get('businessname') or p.get('firstname')
                if name: candidates.append((name, 'provider', p))

            all_names = [c[0] for c in candidates]
            if all_names:
                normalized_search = final_search_name.replace(" ", "").lower()
                normalized_candidates = []
                for i, name in enumerate(all_names):
                    normalized_name = name.replace(" ", "").lower()
                    normalized_candidates.append((normalized_name, i, name))
                exact_match = None
                for norm_name, idx, original_name in normalized_candidates:
                    if norm_name == normalized_search:
                        exact_match = original_name
                        logger.info(f"Exact normalized match found: '{final_search_name}' -> '{original_name}'")
                        break
                
                if exact_match:
                    final_search_name = exact_match
                else:
                    best_match = None
                    best_score = 0
                    match1 = process.extractOne(final_search_name, all_names, scorer=fuzz.token_sort_ratio)
                    if match1 and match1[1] > best_score:
                        best_match = match1
                        best_score = match1[1]
                    normalized_names_only = [nc[0] for nc in normalized_candidates]
                    match2 = process.extractOne(normalized_search, normalized_names_only, scorer=fuzz.ratio)
                    if match2 and match2[1] > best_score:
                        for norm_name, idx, original_name in normalized_candidates:
                            if norm_name == match2[0]:
                                best_match = (original_name, match2[1])
                                best_score = match2[1]
                                break
                    if best_match and best_score >= 85:
                        logger.info(f"Corrected search term from '{final_search_name}' to '{best_match[0]}' based on fuzzy matching (score: {best_score}).")
                        final_search_name = best_match[0]
            matches = []
            direct_matches = process.extract(
                final_search_name, all_names, scorer=fuzz.token_sort_ratio, limit=10, score_cutoff=75
            )
            matches.extend(direct_matches)
            normalized_search = final_search_name.replace(" ", "").lower()
            for i, (name, type_str, row_data) in enumerate(candidates):
                normalized_name = name.replace(" ", "").lower()
                min_len_threshold = len(normalized_search) * 0.6
                if normalized_search in normalized_name or (normalized_name in normalized_search and len(normalized_name) >= min_len_threshold):
                    score = fuzz.ratio(normalized_search, normalized_name)
                    if score >= 75:
                        already_added = any(match_data[2] == i for match_data in matches)
                        if not already_added:
                            matches.append((name, score, i))

            if not matches:
                search_tokens = set(re.findall(r"\w+", final_search_name.lower()))
                if search_tokens:
                    for i, (name, type_str, row_data) in enumerate(candidates):
                        if search_tokens.issubset(set(re.findall(r"\w+", name.lower()))):
                            matches.append((name, 100, i))
                    if matches:
                        logger.info(
                            f"Whole-word rescue matched {len(matches)} candidate(s) for "
                            f"'{final_search_name}' after fuzzy matching found none."
                        )

            for _, score, idx in matches:
                _, type_str, row_data = candidates[idx]
                obj = dict(row_data)
                
                if type_str == 'member':
                    obj['firstname'] = obj.get('contactidname')
                    results_dict["member"].append(obj)
                else:
                    obj['firstname'] = obj.get('businessname') or obj.get('firstname')
                    results_dict["provider"].append(obj)

            search_results = results_dict["member"] + results_dict["provider"]
            
        except Exception as e:
            logger.error(f"Database Search failed: {e}", exc_info=True)
            search_results = []

    candidate_ids = [
        c.get("contactid")
        for c in (results_dict.get("member", []) + results_dict.get("provider", []))
        if c.get("contactid")
    ]
    if candidate_ids:
        status_map = await get_invitation_statuses_for_targets(contact_id, candidate_ids)
        for c in results_dict.get("member", []) + results_dict.get("provider", []):
            c["invitation_status"] = status_map.get(c.get("contactid"))

    if action == "send":
        if not search_results:
            return f"I couldn't find any user matching '{search_term}' to send the invite to.", None
        
        if len(search_results) > 1:
            implicit_output = {
                "member": results_dict.get("member", []),
                "provider": results_dict.get("provider", []),
                "search_term": search_term, "role": role, "contact_invite": True, "search_invite": True
            }
            return f"I found multiple people matching '{search_term}'. Please select the specific person from the list.", implicit_output
        
        return await _send_invite_to_user(
            target_user=search_results[0],
            role=role,
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            user_name=user_name,
            user_role=user_role,
            fallback_name=search_term,
        )

    if not search_results:
        try:
            search_lower = search_term.lower()
            phone_digits = _normalize_phone_digits(search_term)
            if phone_digits:
                invitation_query = """
                    SELECT invitecontactname, invite_rolename, invitationcode, invitecontactmobilenumber
                    FROM etown_invitation
                    WHERE invitercontact = $1
                      AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
                      AND (
                          RIGHT(regexp_replace(invitecontactmobilenumber, '\\D', '', 'g'), 10) = $2
                          OR LOWER(invitecontactname) LIKE '%' || $3 || '%'
                      )
                    ORDER BY createdon DESC
                    LIMIT 1
                """
                invite_row = await Database.fetchrow(invitation_query, contact_id, phone_digits, search_lower)
            else:
                invitation_query = """
                    SELECT invitecontactname, invite_rolename, invitationcode
                    FROM etown_invitation
                    WHERE invitercontact = $1
                      AND LOWER(invitationcode) NOT IN ('declined', 'cancelled', 'error')
                      AND LOWER(invitecontactname) LIKE '%' || $2 || '%'
                    ORDER BY createdon DESC
                    LIMIT 1
                """
                invite_row = await Database.fetchrow(invitation_query, contact_id, search_lower)

            if invite_row:
                status = (invite_row.get("invitationcode") or "").strip().lower()
                inv_role = invite_row.get("invite_rolename") or role
                name_display = invite_row.get("invitecontactname") or invite_row.get("invitecontactmobilenumber") or search_term

                if status in ("requested", "pending"):
                    return f"An invite has already been sent to {name_display} as {inv_role}.", {"status": "already_requested"}
                elif status == "accepted":
                    return f"{name_display} is already in your circle as {inv_role}.", {"status": "already_accepted"}
        except Exception as e:
            logger.error(f"Invitation check error: {e}", exc_info=True)

    implicit_output = {
        "member": results_dict.get("member", []),
        "provider": results_dict.get("provider", []),
        "search_term": search_term,
        "role": role,
        "contact_invite": True,
        "search_invite": True
    }
    
    if search_results:
        total_members = len(results_dict.get("member", []))
        total_providers = len(results_dict.get("provider", []))
        
        original_role = role
        if total_providers > 0 and role.lower() == "member":
            role = "Provider"
            logger.info(f"Role auto-detection: Found {total_providers} provider(s) but role param was '{original_role}'. Corrected to '{role}' for search results.")
        elif total_members > 0 and role.lower() == "provider":
            role = "Member" 
            logger.info(f"Role auto-detection: Found {total_members} member(s) but role param was '{original_role}'. Corrected to '{role}' for search results.")
        
        implicit_output["role"] = role
        if len(search_results) == 1:
            user_type = "provider" if total_providers > 0 else "member"
            only_match = search_results[0]
            match_name = _candidate_display_name(only_match, search_term)
            existing_status = only_match.get("invitation_status")
            if existing_status == "Requested":
                return f"You've already sent an invite to {match_name}. Please wait for them to respond.", implicit_output
            if existing_status == "Accepted":
                return f"{match_name} is already in your circle.", implicit_output
            return f"I found one match for \"{search_term}\". Would you like to send an invite to this user as a {role.lower()}? Please confirm to proceed.", implicit_output
        else:
            return f"Found {len(search_results)} matches for '{search_term}'. Please select one to invite as a {role.lower()}.", implicit_output
    else:
        return f"No users found in Etown matching '{search_term}'. You can invite them from your phone contacts.", implicit_output


async def handle_internal_invite(
    search_term: Union[str, List[str]],
    role: str,
    contact_id: str,
    user_profile_id: str,
    user_name: str = "User",
    oauth_token: str = "",
    mobilenumber_accesstoken: Optional[str] = None,
    language: str = "en",
    search_scope: str = "all",
    action: str = "search",
    user_role: int = 2,
    conversation_id: Optional[str] = None,
    user_prompt: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    """Resolve a pending invite confirmation, or search and invite one or more names in a single combined result.

    Args:
        search_term: Name or list of names to resolve.
        role: Requested role, Member or Provider.
        contact_id: Inviter's contact ID.
        user_profile_id: Inviter's user profile ID.
        user_name: Inviter's display name.
        oauth_token: Unused by the native send path.
        mobilenumber_accesstoken: Passed through to the single-name handler; unused there.
        language: Language of search_term, translated to English before searching.
        search_scope: "all" for a DB search, "phonebook_only" for the device picker.
        action: "search" or "send".
        user_role: Inviter type, 2 for member, 1 for provider.
        conversation_id: Conversation checked for a pending confirmation.
        user_prompt: Raw user message classified as confirm or decline.

    Returns:
        (message, implicit_output).
    """
    terms = search_term if isinstance(search_term, list) else [search_term]
    terms = [t.strip() for t in terms if t and t.strip()]

    if conversation_id and user_prompt and search_scope != "phonebook_only":
        pending = await get_pending_invite_context(conversation_id)
        if pending:
            name_terms = [t for t in terms if classify_invite_confirmation(t) == "unclear"]

            pending_names = [_candidate_display_name(c) for c in _pending_candidates(pending)]
            names_are_new = any(not _term_matches_pending(t, pending_names) for t in name_terms)

            if names_are_new:
                logger.info(
                    f"Pending invite for {pending_names} superseded by a new request for {terms}."
                )
            else:
                decision = classify_invite_confirmation(user_prompt)
                candidates = _pending_candidates(pending)
                selected = _select_confirmed_candidates(candidates, name_terms, user_prompt)
                logger.info(
                    f"Pending invite confirmation classified as '{decision}' for {user_prompt!r}; "
                    f"{len(selected)} of {len(candidates)} candidate(s) selected."
                )

                if decision == "decline":
                    return "No problem, I've cancelled that invite. Let me know if you'd like to invite someone else.", {
                        "status": "cancelled",
                        "search_term": pending.get("search_term"),
                        "role": pending.get("role"),
                        "search_invite": False,
                        "contact_invite": False,
                    }

                if decision == "confirm" or (len(candidates) > 1 and len(selected) == 1):
                    if not selected:
                        names = ", ".join(
                            _candidate_display_name(c) for c in candidates if _candidate_display_name(c)
                        )
                        return (
                            f"There are {len(candidates)} people matching that name ({names}). "
                            "Which one should I invite?",
                            pending,
                        )
                    return await _send_invite_to_pending_selection(
                        pending=pending,
                        contact_id=contact_id,
                        user_profile_id=user_profile_id,
                        user_name=user_name,
                        user_role=user_role,
                        candidates=selected,
                    )

                if action != "send":
                    return "I just need your confirmation. Reply 'Yes' to send the invite or 'No' to cancel.", pending

    if not terms:
        return "Please provide the name of the person you'd like to invite.", None

    if len(terms) == 1:
        return await _handle_single_invite(
            search_term=terms[0],
            role=role,
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            user_name=user_name,
            oauth_token=oauth_token,
            mobilenumber_accesstoken=mobilenumber_accesstoken,
            language=language,
            search_scope=search_scope,
            action=action,
            user_role=user_role
        )
    responses = []
    aggregated_members: List[Dict[str, Any]] = []
    aggregated_providers: List[Dict[str, Any]] = []
    sent, ambiguous, not_found = [], [], []
    sent_invitation_code = None

    for term in terms:
        response, output = await _handle_single_invite(
            search_term=term,
            role=role,
            contact_id=contact_id,
            user_profile_id=user_profile_id,
            user_name=user_name,
            oauth_token=oauth_token,
            mobilenumber_accesstoken=mobilenumber_accesstoken,
            language=language,
            search_scope=search_scope,
            action=action,
            user_role=user_role
        )
        logger.info("INVITE RESPONSE for %s: %s", term, response)
        responses.append(response)
        members = (output or {}).get("member") or []
        providers = (output or {}).get("provider") or []
        for m in members:
            m["matched_query"] = term
        for p in providers:
            p["matched_query"] = term
        aggregated_members.extend(members)
        aggregated_providers.extend(providers)

        if output and output.get("status") == "sent":
            sent.append(output.get("target_user", term))
            sent_invitation_code = sent_invitation_code or output.get("invitationcode")
        elif output and output.get("contact_invite") and not output.get("search_invite"):
            continue
        elif members or providers:
            ambiguous.append(term)
        else:
            not_found.append(term)
    if search_scope == "phonebook_only":
        valid_responses = [response for response in responses if response]
        return " ".join(valid_responses), {
            "search_term": terms,
            "role": role,
            "contact_invite": True,
            "search_invite": False
        }      
    if action == "send":
        parts = []
        if sent:
            parts.append(f"Invite sent to {', '.join(sent)}.")
        if ambiguous:
            parts.append(f"Multiple matches found for {', '.join(ambiguous)} - please confirm which one to invite.")
        if not_found:
            parts.append(f"No Etown user found for {', '.join(not_found)}.")
        result_text = " ".join(parts) if parts else f"No users found matching {', '.join(terms)}."
    else:
        found = [t for t in terms if t not in not_found]
        parts = []
        if found:
            parts.append(f"Found matches for {', '.join(found)}. Add them to your circle by tapping Invite.")
        if not_found:
            parts.append(f"No Etown user found for {', '.join(not_found)}.")
        result_text = " ".join(parts) if parts else f"No users found matching {', '.join(terms)}."

    implicit_output = {
        "member": aggregated_members,
        "provider": aggregated_providers,
        "search_term": terms,
        "role": role,
        "contact_invite": True,
        "search_invite": True,
        "not_found": not_found,
        "ambiguous": ambiguous,
    }
    if sent:
        implicit_output["status"] = "sent"
        implicit_output["invitationcode"] = sent_invitation_code or "Requested"
    return result_text, implicit_output

if __name__ == "__main__":
    import asyncio
    from app.db.client import Database 
    async def test():
        """Run a sample two-name internal invite send against the configured database."""
        await Database.init()
        response, output = await handle_internal_invite(
            search_term=["Dilli", "Praveen"],
            role="member",
            contact_id=os.environ.get("TEST_CONTACT_ID", ""),
            user_profile_id=os.environ.get("TEST_USER_PROFILE_ID", ""),
            user_name="Anubhav Lal",
            oauth_token=os.environ.get("TEST_OAUTH_TOKEN", ""),
            mobilenumber_accesstoken=os.environ.get("TEST_MOBILE_ACCESSTOKEN", ""),
            language="en",
            search_scope="all",
            action="send",
            user_role=2
        )
        print("Response:", response)
        print("Output:", output)
        await Database.close()

    asyncio.run(test())