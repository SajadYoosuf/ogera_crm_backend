"""Phone number cleaning and the duplicate key, shared by contacts and employees."""
import re

_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


def clean_phone(raw: str) -> str:
    """Remove spaces, dashes, brackets and dots. Raises ValueError unless it is 7-15 digits with an optional +."""
    cleaned = re.sub(r"[\s\-().]", "", raw or "")
    if not _PHONE_RE.match(cleaned):
        raise ValueError("Phone must be 7-15 digits, optionally starting with +")
    return cleaned


def phone_key(phone: str) -> str:
    """Last 10 digits, so '+91 90000 00003' and '9000000003' are the same number."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-10:]
