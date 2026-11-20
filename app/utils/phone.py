"""Phone Helpers - Indian mobile number validation and normalization.

Utility module that reduces raw phone strings to the 10-digit national mobile number, rejects landlines and malformed input, and detects the +91 country code.

Features:
    - Validation: reject landlines and malformed input, accept only Indian mobiles
    - Normalization: reduce common formats (+91 / 91 / leading 0) to the national number

Classes:
    No classes defined (module exposes validation/normalization functions only)

Functions:
    normalize_indian_mobile(): Return the 10-digit national mobile number, or None
    is_valid_indian_mobile(): True when the input is a valid Indian mobile number
    has_india_country_code(): True when a number carries the +91 country code

Dependencies:
    - re: digit extraction and mobile-series matching

Notes:
    Indian mobile numbers are 10 digits and begin with 6, 7, 8, or 9. Landlines
    (area-code based, other leading digits) are intentionally rejected. The 91
    country code and a leading trunk 0 are stripped before validation.

Updates:
    10-Jul-2026: Initial creation for /notify_unverified phone-number identification
    14-Aug-2026: Added has_india_country_code() so ingest paths keep only +91 places.
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\D+")
_INDIA_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
INDIA_COUNTRY_CODE = "91"


def normalize_indian_mobile(mobile_number: str | None) -> str | None:
    """Return the 10-digit Indian mobile number from a raw phone string, or None if it is not one."""
    if not mobile_number:
        return None
    digits = _DIGITS_RE.sub("", mobile_number)
    if digits.startswith("0091"):
        digits = digits[4:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if _INDIA_MOBILE_RE.match(digits) else None


def is_valid_indian_mobile(mobile_number: str | None) -> bool:
    """Return True when the string normalizes to a valid 10-digit Indian mobile number."""
    return normalize_indian_mobile(mobile_number) is not None


def has_india_country_code(phone_number: str | None) -> bool:
    """Return True when an international-format phone number carries India's 91 country code."""
    if not phone_number:
        return False
    digits = _DIGITS_RE.sub("", phone_number)
    if digits.startswith("00"):
        digits = digits[2:]
    return digits.startswith(INDIA_COUNTRY_CODE) and len(digits) >= 11
