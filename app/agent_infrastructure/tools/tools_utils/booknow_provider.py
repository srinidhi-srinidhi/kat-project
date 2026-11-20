"""Book Now Provider Tool - finds a named business and prepares it for booking.

Member-agent tool behind booknow_provider: looks up a business by name (exact, substring, then trigram suggestions), attaches its services and distance, and returns provider data plus directives telling the agent whether to confirm the name, pick a service or book immediately.

Features:
    - Name Matching: Exact, then substring, then trigram "did you mean" suggestions
    - Name-confirmation Gate: Non-exact hits carry name_confirmation_required=True and a confirm directive
    - Pre-supplied Details Fast Path: additional_details triggers a book-immediately directive
    - Distance Handling: Flags providers beyond 25 km; treats >1000 km as bad location data
    - Multi-service Disambiguation: Lists services with providerservicesid for multi-item booking
    - Regional Language Support: Matches business names typed in Tamil, Hindi, or English
    - Sub-service Enrichment: Attaches each business's service list via _enrich_results_with_subservices
    - Visibility Filters: Excludes disabled and review-pending providers from both lookup paths
    - Plan Badges: Items carry Etown_providertype from the provider's plan badge
    - Role Validation: Rejects provider-role callers, since only members can start this flow

Classes:
    No classes defined (module uses function-based booking utilities)

Functions:
    _find_similar_businesses(): Trigram-similarity "did you mean" suggestions when no name matches
    handle_booknow_provider(): Main business search and booking preparation handler

Dependencies:
    - geopy.distance: geodesic distance between the member and each candidate provider
    - app.db.client: Database access for the business-name and similarity queries
    - app.core.logging: Centralized logging for search operations
    - app.agent_infrastructure.tools.tools_utils.search_provider: _enrich_results_with_subservices
    - app.etown.provider.provider_review_utils: review_pending_exclusion SQL filter
    - app.etown.plans.plans_utils: premium_display and resolve_provider_badges

Updates:
    11-Mar-2026: Updated module docstring to project standards.
    10-Aug-2026: Dropped additional_details; books immediately when details are complete.
    20-Aug-2026: Verified markers report the paid tier's badge label; items expose Etown_providertype.
    26-Aug-2026: Logged previously-silent exception handlers; rewrote vague log messages.
    02-Sep-2026: Disabled providers excluded from exact-name lookup and suggestions.
    21-Sep-2026: Providers with an unapproved etown_provider_review row excluded from both paths.
"""

from typing import Dict, Any, Optional, Tuple, List
from geopy.distance import geodesic
from app.db.client import Database
from app.core.logging import logger
from app.etown.provider.provider_review_utils import review_pending_exclusion
from app.etown.plans.plans_utils import premium_display, resolve_provider_badges
from app.agent_infrastructure.tools.tools_utils.search_provider import _enrich_results_with_subservices

_SUGGESTION_SIMILARITY_THRESHOLD = 0.3
_MAX_SUGGESTIONS = 5

_SERVICE_RANGE_KM = 25
_IMPLAUSIBLE_DISTANCE_KM = 1000.0
_IMPLAUSIBLE_DISTANCE_MESSAGE = (
    "[IMPLAUSIBLE DISTANCE — the member's location and this provider's are too far apart to be "
    "a real journey, so it is a location-data problem. Do NOT state the distance and do NOT "
    "treat it as a service-range issue. Ask for the address ONCE. When the member answers with "
    "an address (or says to use their profile address), call request_provider immediately with "
    "that address AND distance_confirmed=true — the stored coordinates will still look wrong, "
    "so without that flag the booking can never complete. Do NOT ask about the address or the "
    "distance a second time.]\n\n"
    "I could not work out where this service should be delivered — the location on your profile "
    "is a long way from this provider. Which address should I book this for?"
)

_NAME_UNCONFIRMED_DIRECTIVE = (
    "[UNCONFIRMED PROVIDER NAME — the name the user typed did not exactly match this "
    "business, so it may be the wrong provider. You MUST ask the user to confirm this is "
    "the business they meant and WAIT for their reply. Do NOT greet with \"what do you "
    "need today\", do NOT list services/products, do NOT collect booking details, and do "
    "NOT book until the user explicitly confirms this business is correct.\n"
    "ALREADY CONFIRMED? If the user has said yes to this business anywhere earlier in the "
    "conversation, do NOT ask again — asking twice is the failure this note exists to "
    "prevent. Call booknow_provider once more passing the EXACT business name shown above "
    "(not the user's original spelling) together with name_confirmed=true, then go straight "
    "to request_provider with the details they already gave.]"
)

_DETAILS_SUPPLIED_DIRECTIVE = (
    "[BOOKING DETAILS ALREADY SUPPLIED BY THE USER: \"{details}\". Do NOT ask the user to "
    "repeat anything stated there and do NOT open with a generic \"what do you need today\". "
    "If those details already give you the service/item wanted AND when it is wanted, call "
    "request_provider NOW and confirm the completed booking — do not ask the user to confirm "
    "again. Default the service address to the member's profile address unless they named a "
    "different one, and pass their wording through as detailed_request so the provider sees "
    "the full request. Ask ONLY for a detail that is genuinely missing and that you cannot "
    "default, and ask for all such details together in one message.]"
)


async def _find_similar_businesses(
    search_term: str,
    user_latitude: Optional[float] = None,
    user_longitude: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return distinct businesses whose names are trigram-similar to the search term, best first, as bookable items."""
    try:
        query = """
            SELECT
                epc.providerservicesid,
                epc.businessname,
                epc.services,
                epc.servicedescription,
                epc.businesscategory,
                epc.servicecategory,
                epc.providertype,
                epc.pricerange,
                epc.yearsinbusiness,
                COALESCE(ep.trusted, false) AS trusted,
                epc.userprofileid,
                epc.providerid,
                epc.address,
                epc.city,
                epc.latitude,
                epc.longitude,
                epc.mobilenumber,
                epc.email,
                epc.contactid,
                epc.websiteurl,
                GREATEST(
                    similarity(epc.businessname, $1),
                    word_similarity($1, epc.businessname)
                ) AS name_similarity
            FROM etown_providerservices_copy epc
            LEFT JOIN etown_provider ep ON ep.providerid = epc.providerid
            WHERE GREATEST(
                    similarity(epc.businessname, $1),
                    word_similarity($1, epc.businessname)
                  ) >= $2
              AND ep.disabled IS NOT TRUE
              """ + "AND " + review_pending_exclusion("epc.providerid") + """
            ORDER BY name_similarity DESC
            LIMIT 25
        """
        rows = await Database.fetch(query, search_term, _SUGGESTION_SIMILARITY_THRESHOLD)
        if not rows:
            return []

        badge_map = await resolve_provider_badges(row['userprofileid'] for row in rows)
        suggestions: List[Dict[str, Any]] = []
        seen_names: set = set()
        for row in rows:
            logger.debug(
                "booknow candidate | business=%r name_similarity=%s",
                row["businessname"], row["name_similarity"],
            )
            bname = (row['businessname'] or '').strip()
            name_key = bname.lower()
            if not bname or name_key in seen_names:
                continue
            seen_names.add(name_key)

            item = {
                "providerservicesid": row['providerservicesid'],
                "businessname": row['businessname'],
                "services": row['services'],
                "servicedescription": row['servicedescription'],
                "businesscategory": row['businesscategory'],
                "servicecategory": row['servicecategory'],
                "Etown_providertype": premium_display(
                    row['providertype'],
                    (badge_map.get(row['userprofileid']) or {}).get("premium_label"),
                ),
                "pricerange": row['pricerange'],
                "yearsinbusiness": row['yearsinbusiness'],
                "trusted": row['trusted'],
                "userprofileid": row['userprofileid'],
                "providerid": row['providerid'],
                "address": row['address'],
                "city": row['city'],
                "latitude": row['latitude'],
                "longitude": row['longitude'],
                "mobilenumber": row['mobilenumber'],
                "email": row['email'],
                "contactid": row['contactid'],
                "websiteurl": row['websiteurl'],
                "source": "database",
                "category": str(row['businesscategory']).lower() if row['businesscategory'] and str(row['businesscategory']).lower() in ('service', 'product') else None,
                "name_similarity": round(float(row['name_similarity']), 3),
            }
            if user_latitude and user_longitude and row['latitude'] and row['longitude']:
                try:
                    dist = geodesic(
                        (user_latitude, user_longitude),
                        (float(row['latitude']), float(row['longitude']))
                    ).km
                    item['distance_km'] = round(dist, 2)
                    item['distance_meters'] = int(dist * 1000)
                except (ValueError, TypeError):
                    logger.debug("booknow: could not compute distance for a candidate row; leaving it unset")
            suggestions.append(item)
            logger.debug(
                "booknow suggestion kept | business=%r name_similarity=%s distance_km=%s",
                row["businessname"], row["name_similarity"], item.get("distance_km"),
            )
            if len(suggestions) >= _MAX_SUGGESTIONS:
                break

        return suggestions
    except Exception as e:
        logger.error(f"Error finding similar businesses for '{search_term}': {e}", exc_info=True)
        return []


async def handle_booknow_provider(
    search_term: str,
    user_role: int = 2,
    additional_details: str = "",
    user_latitude: Optional[float] = None,
    user_longitude: Optional[float] = None,
    name_confirmed: bool = False,
) -> Tuple[str, Optional[List[Dict[str, Any]]]]:
    """Find a business by name for booking, falling back to similar-name suggestions, and build the LLM-facing reply.

    Args:
        search_term: Business name the user typed.
        user_role: 1 for provider (refused), 2 for member.
        additional_details: Booking details the user already supplied, echoed as a directive.
        user_latitude: User latitude for distance checks, or None.
        user_longitude: User longitude for distance checks, or None.
        name_confirmed: True when the user already confirmed the business; books the top match.

    Returns:
        Tuple of markdown reply and provider items, or None items when nothing matched or on error.
    """
    try:
        if user_role == 1:
            return "Please switch to your member account to proceed with booking.", None

        normalized_search = ' '.join(search_term.strip().split())
        logger.info(f"BookNow Provider search for: {normalized_search}")

        details_directive = (
            _DETAILS_SUPPLIED_DIRECTIVE.format(details=additional_details.strip()) + "\n\n"
            if additional_details and additional_details.strip()
            else ""
        )
        
        query = """
            SELECT 
                epc.providerservicesid, 
                epc.businessname, 
                epc.services, 
                epc.servicedescription,
                epc.businesscategory,
                epc.servicecategory,
                epc.providertype,
                epc.pricerange,
                epc.yearsinbusiness,
                COALESCE(ep.trusted, false) AS trusted,
                epc.userprofileid,
                epc.providerid,
                epc.address,
                epc.city,
                epc.latitude,
                epc.longitude,
                epc.mobilenumber,
                epc.email,
                epc.contactid,
                epc.websiteurl
            FROM etown_providerservices_copy epc
            LEFT JOIN etown_provider ep ON ep.providerid = epc.providerid
            WHERE TRIM(epc.businessname) ILIKE $1
              AND ep.disabled IS NOT TRUE
              """ + "AND " + review_pending_exclusion("epc.providerid") + """
            ORDER BY
            CASE
            WHEN LOWER(epc.businessname) LIKE LOWER($2 || '%') THEN 0
            ELSE 1
            END,
            LENGTH(epc.businessname),epc.businessname
        """
        
        rows = await Database.fetch(query, normalized_search, normalized_search)
        if not rows:
            rows = await Database.fetch(query,f"%{normalized_search}%",normalized_search)
        if not rows:
            trimmed_search = normalized_search.replace(' ', '%')
            rows = await Database.fetch(query,f"%{trimmed_search}%",normalized_search)
        
        if not rows:
            suggestions = await _find_similar_businesses(
                normalized_search, user_latitude, user_longitude
            )
            if suggestions:
                suggestions = await _enrich_results_with_subservices(suggestions)
                if name_confirmed:
                    confirmed = suggestions[0]
                    confirmed['name_confirmation_required'] = False
                    logger.info(
                        "BookNow: '%s' already confirmed by the user -> booking '%s'",
                        normalized_search, confirmed.get('businessname'),
                    )
                    return (
                        details_directive
                        + f"Proceeding with **{confirmed['businessname']}** as confirmed. "
                        f"Book it now with the details already supplied.",
                        [confirmed],
                    )

                for s in suggestions:
                    s['name_confirmation_required'] = True
                if len(suggestions) == 1:
                    only = suggestions[0]
                    dist_note = (
                        f" (approximately {only['distance_km']:.0f} km away)"
                        if only.get('distance_km') is not None
                        and only['distance_km'] <= _IMPLAUSIBLE_DISTANCE_KM else ""
                    )
                    markdown_content = (
                        f"{_NAME_UNCONFIRMED_DIRECTIVE}\n\n"
                        f"Are you talking about **{only['businessname']}**{dist_note}? "
                        f"If yes, let me know what all you need."
                    )
                else:
                    suggestion_lines = "\n".join(
                        f"- **{s['businessname']}**"
                        + (f" ({s['distance_km']} km away)"
                           if s.get('distance_km') is not None
                           and s['distance_km'] <= _IMPLAUSIBLE_DISTANCE_KM else "")
                        for s in suggestions
                    )
                    markdown_content = (
                        f"{_NAME_UNCONFIRMED_DIRECTIVE}\n\n"
                        f"Did you mean one of these businesses?\n\n"
                        f"{suggestion_lines}\n\n"
                        f"If not, please share the correct spelling."
                    )
                logger.info(
                    f"BookNow: no exact match for '{search_term}', returning "
                    f"{len(suggestions)} similar-name suggestion(s)."
                )
                return details_directive + markdown_content, suggestions
            return (
                f"Sorry, we couldn't find the business '{search_term}' in our directory. "
                "Please check the spelling or try searching for the type of service they provide.",
                None
            )
        business_map = {}
        for row in rows:
            bname = (row['businessname'] or '').strip()
            if bname not in business_map:
                business_map[bname] = []
            business_map[bname].append(row)
        selected_bname = None
        for bname in business_map:
            if bname.lower() == normalized_search.lower():
                selected_bname = bname
                break
        if selected_bname is None:
            contained = [
                bname for bname in business_map
                if normalized_search.lower() in bname.lower()
                or bname.lower() in normalized_search.lower()
            ]
            if len(contained) == 1:
                selected_bname = contained[0]

        if name_confirmed and selected_bname is None and business_map:
            selected_bname = list(business_map.keys())[0]
            logger.info(
                "BookNow: '%s' accepted as already-confirmed by the user -> '%s'",
                normalized_search, selected_bname,
            )

        is_exact_match = selected_bname is not None
        if not selected_bname:
            selected_bname = list(business_map.keys())[0]
        selected_rows = business_map[selected_bname]
        badge_map = await resolve_provider_badges(row['userprofileid'] for row in selected_rows)
        items = []
        for row in selected_rows:
            item = {
                "providerservicesid": row['providerservicesid'],
                "businessname": row['businessname'],
                "services": row['services'],
                "servicedescription": row['servicedescription'],
                "businesscategory": row['businesscategory'],
                "servicecategory": row['servicecategory'],
                "Etown_providertype": premium_display(
                    row['providertype'],
                    (badge_map.get(row['userprofileid']) or {}).get("premium_label"),
                ),
                "pricerange": row['pricerange'],
                "yearsinbusiness": row['yearsinbusiness'],
                "trusted": row['trusted'],
                "userprofileid": row['userprofileid'],
                "providerid": row['providerid'],
                "address": row['address'],
                "city": row['city'],
                "latitude": row['latitude'],
                "longitude": row['longitude'],
                "mobilenumber": row['mobilenumber'],
                "email": row['email'],
                "contactid": row['contactid'],
                "websiteurl": row['websiteurl'],
                "source": "database",
                "category": str(row['businesscategory']).lower() if row['businesscategory'] and str(row['businesscategory']).lower() in ('service', 'product') else None,
                "name_confirmation_required": False,
            }
            if user_latitude and user_longitude and row['latitude'] and row['longitude']:
                dist = geodesic(
                    (user_latitude, user_longitude),
                    (float(row['latitude']), float(row['longitude']))
                ).km
                item['distance_km'] = round(dist, 2)
                item['distance_meters'] = int(dist * 1000)
            items.append(item)
        items = await _enrich_results_with_subservices(items)
        business_names = list(business_map.keys())
        if not is_exact_match and len(business_names) > 1:
            markdown_content = (f"I found multiple businesses matching **'{search_term}'**:\n\n")
            for name in business_names:
                markdown_content += f"- **{name}**\n"
            markdown_content += ("\nPlease tell me which business you want to book.")
            return details_directive + markdown_content, items
        seen_providers = {}
        for item in items:
            pid = item.get('providerid') or item.get('contactid') or item.get('providerservicesid')
            if pid not in seen_providers:
                seen_providers[pid] = item
        items = list(seen_providers.values())

        provider_distance_km = None
        for it in items:
            d = it.get('distance_km')
            if d is not None:
                provider_distance_km = d
                break
        if provider_distance_km is not None and provider_distance_km > _IMPLAUSIBLE_DISTANCE_KM:
            logger.error(
                "Implausible distance for '%s': %.1f km (user=%s,%s). Asking for the service "
                "address instead of quoting it.",
                selected_bname, provider_distance_km, user_latitude, user_longitude,
            )
            return details_directive + _IMPLAUSIBLE_DISTANCE_MESSAGE, items

        is_far = provider_distance_km is not None and provider_distance_km > _SERVICE_RANGE_KM

        if not is_exact_match:
            for it in items:
                it['name_confirmation_required'] = True
            if is_far:
                markdown_content = (
                    f"{_NAME_UNCONFIRMED_DIRECTIVE}\n\n"
                    f"Are you talking about **{selected_bname}**? It's approximately "
                    f"{provider_distance_km:.0f} km away from your location, which is beyond our "
                    f"standard {_SERVICE_RANGE_KM} km range. If yes, would you like to proceed "
                    f"despite the distance, or explore closer options?"
                )
            else:
                markdown_content = (
                    f"{_NAME_UNCONFIRMED_DIRECTIVE}\n\n"
                    f"Are you talking about **{selected_bname}**? "
                    f"If yes, let me know what all you need."
                )
            return details_directive + markdown_content, items

        far_question = (
            "Would you still like to proceed with booking despite the distance, "
            "or would you prefer to explore closer options?"
        )
        distance_prefix = ""
        if is_far:
            distance_prefix = (
                f"**{selected_bname}** is approximately {provider_distance_km:.0f} km away from "
                f"your location, which is beyond our standard {_SERVICE_RANGE_KM} km service range.\n\n"
            )

        if len(items) == 1:
            provider = items[0]
            services_list = provider.get('subservices') or []
            if services_list:
                services_text = "\n".join(
                    f"- **{s['name']}** (providerservicesid: {s['id']})" for s in services_list
                )
                closing = far_question if is_far else (
                    "Please let me know which service(s) you would like to book.\n"
                    "To book **multiple items**, provide all desired UUIDs in `provider_item_ids` "
                    "and their quantities in `quantities` when calling `request_provider`."
                )
                markdown_content = (
                    f"{distance_prefix}"
                    f"Here are the available services from **{selected_bname}**:\n\n"
                    f"{services_text}\n\n"
                    f"{closing}"
                )
            else:
                closing = far_question if is_far else "Would you like to proceed with booking this item?"
                markdown_content = (
                    f"{distance_prefix}"
                    f"The available item from **{selected_bname}** is:\n\n"
                    f"- **{provider['services']}** (providerservicesid: {provider['providerservicesid']})\n\n"
                    f"{closing}"
                )
        else:
            markdown_content = distance_prefix + f"Here are the available providers for **{selected_bname}**:\n\n"
            for item in items:
                markdown_content += f"- **{item['services']}** (providerservicesid: {item['providerservicesid']})\n"
            if is_far:
                markdown_content += "\n" + far_question
            else:
                markdown_content += "\nPlease select the item(s) you would like to book. To book multiple items at once, provide all their UUIDs in `provider_item_ids` and matching quantities in `quantities`."
        return details_directive + markdown_content, items

    except Exception as e:
        logger.error(f"Error in handle_booknow_provider: {str(e)}", exc_info=True)
        return f"An error occurred while searching for the provider: {str(e)}", None


if __name__ == "__main__":
    import asyncio
    
    async def test_handle_booknow_provider():
        await Database.init()
        
        try:
            print("=== Test 1: Successful Business Search ===")
            result_text, result_data = await handle_booknow_provider(
                search_term="Abhilash medicals",
                user_role=2,
                additional_details="Looking for emergency plumbing service"
            )
            print(f"Response: {result_text}")
            print(f"Number of items: {len(result_data) if result_data else 0}")
            if result_data:
                for item in result_data[:3]:
                    print(f"  - {item.get('name')} (ID: {item.get('id')})")
            print()
            
            print("=== Test 2: Business Not Found ===")
            result_text, result_data = await handle_booknow_provider(
                search_term="NonExistentBusiness12345",
                user_role=2
            )
            print(f"Response: {result_text}")
            print(f"Data: {result_data}")
            print()
            
            print("=== Test 3: Provider Role Validation ===")
            result_text, result_data = await handle_booknow_provider(
                search_term="ABC Plumbing Services",
                user_role=1
            )
            print(f"Response: {result_text}")
            print(f"Data: {result_data}")
        except Exception as e:
            logger.error("booknow self-test failed", exc_info=True)
            print(f"Test failed with error: {e}")
        finally:
            await Database.close()
    
    asyncio.run(test_handle_booknow_provider())