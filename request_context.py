"""Facts about the request that the form rules need. The client header is advisory: it decides which fields
are required, it is not a security control."""
from fastapi import Header


def client_platform(x_client: str | None = Header(default=None)) -> str:
    """'mobile' when the app sends X-Client: mobile, otherwise 'web'."""
    return "mobile" if (x_client or "").strip().lower() == "mobile" else "web"
