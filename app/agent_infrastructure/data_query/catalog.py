"""Data Query Catalog - the curated chaatie_ro views query_my_data may read, with column meanings and examples.

Agent-layer constants module describing each view created by scripts/2026_11_chaatie_data_query.sql, its member or provider scope, its columns and worked examples; it is rendered into the SQL-generation prompt and doubles as the validator's allow-list.

Features:
    - One ViewSpec per chaatie_ro view, kept column-for-column in step with the migration
    - Role scoping: member views, provider views and shared platform-wide aggregates
    - Business rules stated per view, so the model never re-derives statuses or time zones
    - Worked question-to-SQL examples per view for few-shot generation
    - Pure constants: imports no app code, so the validator and tests load it standalone

Classes:
    ViewSpec: One curated view - name, scope, description, columns, notes and examples

Functions:
    views_for_scope(): The views a member or provider may query
    allowed_columns(): Column allow-list per view name for a scope
    render_catalog(): Prompt text describing the views for a scope

Dependencies:
    - dataclasses: frozen ViewSpec records

Updates:
    27-Oct-2026: Initial catalogue of seven chaatie_ro views for query_my_data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

SCHEMA = "chaatie_ro"

BOOKING_STATUSES = ("requested", "accepted", "declined", "accept_declined", "completed", "cancelled")


@dataclass(frozen=True)
class ViewSpec:
    """One curated chaatie_ro view.

    Attributes:
        name: View name inside the chaatie_ro schema.
        scope: 'member', 'provider' or 'both'.
        description: What one row means and who it covers.
        columns: (column name, meaning) pairs, in view order.
        notes: Business rules the model must respect when querying the view.
        examples: (question, SQL) pairs used as few-shot examples.
    """

    name: str
    scope: str
    description: str
    columns: Tuple[Tuple[str, str], ...]
    notes: Tuple[str, ...] = ()
    examples: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)


_STATUS_NOTE = (
    "status is lower-case and one of: " + ", ".join(BOOKING_STATUSES)
    + "; 'declined' and 'accept_declined' both mean the provider turned the booking down."
)
_TIME_NOTE = "All *_at columns are IST timestamps; compare them with CURRENT_DATE / now() directly."

VIEWS: Tuple[ViewSpec, ...] = (
    ViewSpec(
        name="member_bookings",
        scope="member",
        description="One row per booking the calling member placed. Already limited to the caller.",
        columns=(
            ("booking_id", "Internal booking id"),
            ("tracking_id", "Booking tracking id shown to the user, e.g. ETRQ12345"),
            ("status", "Booking status"),
            ("service_names", "Comma-separated names of the items or services booked"),
            ("item_count", "Number of items in the booking"),
            ("provider_business_name", "Business the booking was placed with"),
            ("provider_category", "Normalized category of that business, e.g. plumber, bakery"),
            ("listed_price_total", "Sum of the listed prices of the items; NULL when no item had a numeric price"),
            ("priced_item_count", "How many items had a numeric listed price"),
            ("requested_service_time", "Service date and time the member asked for, as free text"),
            ("created_at", "When the booking was placed"),
            ("accepted_at", "When the provider accepted it"),
            ("completed_at", "When it was completed"),
            ("cancelled_at", "When it was cancelled"),
            ("cancel_reason", "Reason given for a cancellation"),
            ("rating_given", "Star rating (1-5) the member gave, NULL if not rated"),
        ),
        notes=(
            _STATUS_NOTE,
            _TIME_NOTE,
            "listed_price_total is the catalogue price, not an invoice or a payment; say so when reporting spend.",
        ),
        examples=(
            ("How many bookings did I make last month?",
             "SELECT count(*) AS bookings FROM chaatie_ro.member_bookings "
             "WHERE created_at >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month' "
             "AND created_at < date_trunc('month', CURRENT_DATE)"),
            ("Which business have I booked most often?",
             "SELECT provider_business_name, count(*) AS bookings FROM chaatie_ro.member_bookings "
             "GROUP BY provider_business_name ORDER BY bookings DESC LIMIT 5"),
        ),
    ),
    ViewSpec(
        name="member_booking_items",
        scope="member",
        description="One row per item inside the calling member's bookings. Use for item-level questions.",
        columns=(
            ("booking_id", "Internal booking id"),
            ("tracking_id", "Booking tracking id"),
            ("status", "Status of the booking the item belongs to"),
            ("created_at", "When the booking was placed"),
            ("item_name", "Name of the item or service"),
            ("quantity", "Quantity booked, NULL when not numeric"),
            ("listed_price", "Listed price of the item, NULL when not numeric"),
            ("provider_business_name", "Business that provides the item"),
            ("provider_category", "Normalized category of that business"),
        ),
        notes=(_STATUS_NOTE, _TIME_NOTE),
        examples=(
            ("What have I ordered from bakeries?",
             "SELECT item_name, count(*) AS times_ordered FROM chaatie_ro.member_booking_items "
             "WHERE provider_category ILIKE '%bakery%' GROUP BY item_name ORDER BY times_ordered DESC LIMIT 20"),
        ),
    ),
    ViewSpec(
        name="provider_received_bookings",
        scope="provider",
        description="One row per booking the calling provider received. Already limited to the caller.",
        columns=(
            ("booking_id", "Internal booking id"),
            ("tracking_id", "Booking tracking id"),
            ("status", "Booking status"),
            ("customer_name", "Name of the member who booked"),
            ("service_names", "Comma-separated items or services booked from this provider"),
            ("item_count", "Number of this provider's items in the booking"),
            ("listed_price_total", "Sum of the listed prices of those items; NULL when none was numeric"),
            ("requested_service_time", "Service date and time the customer asked for, as free text"),
            ("created_at", "When the booking was received"),
            ("accepted_at", "When the provider accepted it"),
            ("completed_at", "When it was completed"),
            ("cancelled_at", "When it was cancelled"),
            ("cancel_reason", "Reason given for a cancellation"),
            ("rating_received", "Star rating (1-5) the customer gave, NULL if not rated"),
        ),
        notes=(
            _STATUS_NOTE,
            _TIME_NOTE,
            "For rate questions, return counts per status so the reply can show both the count and the share.",
        ),
        examples=(
            ("How many requests did I get this week?",
             "SELECT count(*) AS requests FROM chaatie_ro.provider_received_bookings "
             "WHERE created_at >= date_trunc('week', CURRENT_DATE)"),
            ("How many of my bookings were completed versus cancelled this year?",
             "SELECT status, count(*) AS bookings FROM chaatie_ro.provider_received_bookings "
             "WHERE created_at >= date_trunc('year', CURRENT_DATE) GROUP BY status ORDER BY bookings DESC"),
        ),
    ),
    ViewSpec(
        name="provider_offer_stats",
        scope="provider",
        description="One row per offer the calling provider posted, with how many distinct members viewed it.",
        columns=(
            ("offer_id", "Offer id"),
            ("title", "Offer title"),
            ("description", "First 200 characters of the offer text"),
            ("validity", "Valid, Pending or Expired"),
            ("is_active", "False when the provider switched the offer off"),
            ("created_at", "When the offer was posted"),
            ("starts_at", "Scheduled start"),
            ("ends_at", "Scheduled end"),
            ("viewer_count", "Distinct members who viewed the offer"),
        ),
        notes=(_TIME_NOTE,),
        examples=(
            ("Which of my offers got the most views?",
             "SELECT title, viewer_count, validity FROM chaatie_ro.provider_offer_stats "
             "ORDER BY viewer_count DESC LIMIT 5"),
        ),
    ),
    ViewSpec(
        name="provider_profile_views_daily",
        scope="provider",
        description="First-time viewers of the calling provider's profile, per IST day and source.",
        columns=(
            ("view_date", "IST date of the first view"),
            ("source", "Where the view came from, e.g. chaatie_search"),
            ("new_viewer_count", "Members who viewed the profile for the first time that day"),
        ),
        notes=(
            "Each member is counted once, on the day they first viewed; repeat views are not recorded.",
        ),
        examples=(
            ("How many people viewed my profile in the last 30 days?",
             "SELECT COALESCE(sum(new_viewer_count), 0) AS viewers FROM chaatie_ro.provider_profile_views_daily "
             "WHERE view_date >= CURRENT_DATE - 30"),
        ),
    ),
    ViewSpec(
        name="provider_rating_summary",
        scope="provider",
        description="The calling provider's aggregate rating (usually one row).",
        columns=(
            ("business_name", "Business name"),
            ("overall_rating", "Average star rating"),
            ("rating_count", "Number of ratings"),
            ("member_booking_count", "Bookings counted towards the rating"),
        ),
        examples=(
            ("What is my rating?",
             "SELECT business_name, overall_rating, rating_count FROM chaatie_ro.provider_rating_summary"),
        ),
    ),
    ViewSpec(
        name="provider_directory_stats",
        scope="both",
        description="Platform-wide counts of active listings by category and city. Never lists individual businesses.",
        columns=(
            ("category", "Normalized category, e.g. plumber, bakery, electrician"),
            ("city", "City name, title-cased"),
            ("is_verified", "True for verified providers"),
            ("listing_count", "Active service listings"),
            ("provider_count", "Distinct providers"),
        ),
        notes=(
            "Use only for counts. To find or recommend actual businesses, the search tools are the right choice.",
            "Match categories and cities with ILIKE, since spellings vary.",
        ),
        examples=(
            ("How many plumbers are there in Madurai?",
             "SELECT COALESCE(sum(provider_count), 0) AS plumbers FROM chaatie_ro.provider_directory_stats "
             "WHERE category ILIKE '%plumb%' AND city ILIKE 'madurai'"),
        ),
    ),
)


def views_for_scope(scope: str) -> List[ViewSpec]:
    """Return the views whose scope is 'both' or equals the given member or provider scope."""
    return [view for view in VIEWS if view.scope in ("both", scope)]


def allowed_columns(scope: str) -> Dict[str, frozenset]:
    """Return a mapping of view name to its column-name set for the views the scope may read."""
    return {view.name: frozenset(name for name, _ in view.columns) for view in views_for_scope(scope)}


def render_catalog(scope: str) -> str:
    """Render the scope's views, columns, rules and examples as prompt text for SQL generation."""
    blocks: List[str] = []
    for view in views_for_scope(scope):
        lines = [f"### {SCHEMA}.{view.name}", view.description, "Columns:"]
        lines.extend(f"- {name}: {meaning}" for name, meaning in view.columns)
        if view.notes:
            lines.append("Rules:")
            lines.extend(f"- {note}" for note in view.notes)
        if view.examples:
            lines.append("Examples:")
            for question, sql in view.examples:
                lines.append(f"Q: {question}")
                lines.append(f"SQL: {sql}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
