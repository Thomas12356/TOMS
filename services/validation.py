"""Shared validation for Starling IDs, timezone-aware timestamps and money."""

from datetime import datetime, timezone
from uuid import UUID


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Expected a timestamp.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if "T" not in value or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone.")
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:
        raise ValueError("Timestamp is outside the supported UTC range.") from None


def uid(value):
    if not isinstance(value, str):
        raise ValueError("Expected a UUID string.")
    return str(UUID(value))


def money(value):
    if not isinstance(value, dict):
        raise ValueError("Expected money object.")
    amount, currency = value.get("minorUnits"), value.get("currency")
    if isinstance(amount, bool) or not isinstance(amount, int) or not 0 <= amount <= 2**63 - 1:
        raise ValueError("Invalid minor units.")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise ValueError("Invalid currency.")
    return amount, currency.upper()


def optional_text(value, *, field, maximum):
    """Validate optional user text before PostgreSQL receives it."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"{field} must be a string of at most {maximum} characters or null.")
    if "\x00" in value:
        raise ValueError(f"{field} must not contain null characters.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must contain valid Unicode text.") from None
    return value.strip() or None
