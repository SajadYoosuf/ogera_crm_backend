"""Facts about the request: which app sent it.

The mobile app sends `x-client-type: mobile` (older builds sent `X-Client: mobile`). Anything else counts as the
website. A client can fake this header, so it only decides which form fields are required and which app a role
signs in to; the admin/manager guard in auth.py is what protects employee management and settings.
"""
from fastapi import Header


def client_platform(
    x_client: str | None = Header(default=None),
    x_client_type: str | None = Header(default=None),
) -> str:
    """'mobile' when either header says mobile, otherwise 'web'."""
    sent = (x_client_type or x_client or "").strip().lower()
    return "mobile" if sent == "mobile" else "web"
