"""Pending Ratings Tool - finds a user's most recent unrated completed booking.

Agent-layer lookup used by the classifier bridge and dispatcher: given a user profile id, one etown_allrequests query returns the latest completed booking with no rating, with provider name and image, so Chaatie can ask for a rating first.

Features:
    - Pending Rating Lookup: One query finds the latest completed, unrated booking with provider name/image
    - Guardrail Support: Powers the "rate your last booking" enforcement prompt
    - Graceful Fallbacks: String for a missing profile id; status dicts for no-pending and errors

Classes:
    No classes defined (module uses function-based rating utilities)

Functions:
    check_pending_ratings(): Main pending rating status checker for completed bookings

Dependencies:
    - logging: Module logger for query errors
    - app.db.client: Database operations with retry mechanisms

Updates:
    11-Mar-2026: Updated module docstring to follow project standards with comprehensive documentation
"""

import logging
from app.db.client import Database

logger = logging.getLogger(__name__)

async def check_pending_ratings(user_profile_id: str):
    """Return the user's most recent completed but unrated booking as a status dict, or a plain message without a profile id."""
    if not user_profile_id:
        return "No user profile ID provided."

    query = """
        SELECT
            ar.requestid,
            ar.createdon,
            rsl.servicename AS business_name,
            rsl.serviceimageurl AS profile_image
        FROM etown_allrequests AS ar
        LEFT JOIN LATERAL (
            SELECT servicename, serviceimageurl, serviceprovideruserprofileid
            FROM etown_requestservicelists
            WHERE bookingid = ar.allrequestid
            ORDER BY createdon DESC
            LIMIT 1
        ) rsl ON TRUE
        WHERE ar.requestoruserprofileid = $1
          AND LOWER(TRIM(ar.requeststatus)) = 'completed'
          AND ar.bookingcompletedtime IS NOT NULL
          AND ar.ratingvalue IS NULL
          AND (ar."isRating" IS NULL OR LOWER(ar."isRating"::text) = 'false')
          AND rsl.serviceprovideruserprofileid IS NOT NULL
        ORDER BY ar.bookingcompletedtime DESC
        LIMIT 1;
    """
    
    try:
        rows = await Database.fetch(query, user_profile_id)

        if rows and len(rows) > 0:
            booking = rows[0]
            return {
                "status": "pending_rating",
                "request_id": booking.get("requestid"),
                "business_name": booking.get("business_name"),
                "profile_image": booking.get("profile_image"),
                "date": booking.get("createdon").isoformat() if booking.get("createdon") else None,
                "message": "User has a pending rating. Please ask them to rate this service."
            }
        else:
            return {
                "status": "no_pending_ratings",
                "message": "No pending ratings found."
            }

    except Exception as e:
        logger.error(f"Error checking pending ratings: {e}", exc_info=True)
        return {"status": "error", "message": f"Error checking ratings: {str(e)}"}