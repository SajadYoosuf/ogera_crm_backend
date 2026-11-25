import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

Digits = Annotated[str, Field(min_length=5, max_length=50, pattern=r"^\d+$")]


class WhatsAppConfigUpdate(BaseModel):
    """Secrets are optional: leaving one out (or blank) keeps the saved value."""
    phone_number_id: Digits
    waba_id: Digits
    app_id: Annotated[str, Field(max_length=50, pattern=r"^\d*$")] | None = None
    business_id: Annotated[str, Field(max_length=50, pattern=r"^\d*$")] | None = None
    api_version: Annotated[str, Field(pattern=r"^v\d{1,2}\.\d{1,2}$")] = "v21.0"
    access_token: Annotated[str, Field(max_length=1000)] | None = None
    app_secret: Annotated[str, Field(max_length=200)] | None = None

    @field_validator("access_token", "app_secret", "app_id", "business_id", mode="after")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


class WhatsAppConfigResponse(BaseModel):
    """What the browser sees. Secrets are never included, only whether they are saved."""
    configured: bool
    phone_number_id: str | None = None
    waba_id: str | None = None
    app_id: str | None = None
    business_id: str | None = None
    api_version: str = "v21.0"
    has_access_token: bool = False
    access_token_hint: str | None = None
    has_app_secret: bool = False
    verify_token: str | None = None
    webhook_url: str | None = None
    display_phone_number: str | None = None
    verified_name: str | None = None
    quality_rating: str | None = None
    status: str = "disconnected"
    connected_at: datetime.datetime | None = None
    last_checked_at: datetime.datetime | None = None
    last_error: str | None = None


class WhatsAppTestResult(BaseModel):
    ok: bool
    status: str
    detail: str
    config: WhatsAppConfigResponse
