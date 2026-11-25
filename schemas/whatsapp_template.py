import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["MARKETING", "UTILITY", "AUTHENTICATION"]
HeaderType = Literal["text", "image", "video", "document"]
TemplateStatus = Literal["DRAFT", "PENDING", "APPROVED", "REJECTED", "PAUSED", "DISABLED", "IN_APPEAL", "PENDING_DELETION"]


class TemplateButton(BaseModel):
    type: Literal["QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE"]
    text: str = Field(default="", max_length=100)
    url: str | None = None
    phone_number: str | None = None
    example: str | None = None


class TemplateSampleValues(BaseModel):
    body: list[str] = Field(default_factory=list)
    header: list[str] = Field(default_factory=list)


class WhatsAppTemplateInput(BaseModel):
    """What the form sends. Field-level rules (limits, variables, buttons) are checked by
    services/whatsapp_template_rules.py so the user gets one specific sentence."""
    name: str = Field(max_length=512)
    language: str = Field(default="en_US", max_length=15)
    category: Category = "MARKETING"
    header_type: HeaderType | None = None
    header_content: str | None = None
    header_media_url: str | None = None
    header_handle: str | None = None
    body_text: str = ""
    footer_text: str | None = None
    buttons: list[TemplateButton] | None = None
    sample_values: TemplateSampleValues | None = None

    @field_validator("name", "language", mode="before")
    @classmethod
    def strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("header_content", "header_media_url", "header_handle", "footer_text", mode="after")
    @classmethod
    def blank_to_none(cls, v: str | None) -> str | None:
        return v if v and v.strip() else None


class WhatsAppTemplateResponse(BaseModel):
    id: int
    name: str
    language: str
    category: Category
    header_type: HeaderType | None = None
    header_content: str | None = None
    header_media_url: str | None = None
    header_handle: str | None = None
    body_text: str
    footer_text: str | None = None
    buttons: list[TemplateButton] | None = None
    sample_values: TemplateSampleValues | None = None
    status: TemplateStatus
    meta_template_id: str | None = None
    rejection_reason: str | None = None
    quality_score: str | None = None
    submission_error: str | None = None
    last_submitted_at: datetime.datetime | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime | None = None
    # Filled in by the route: how many values a send needs.
    body_variable_count: int = 0
    header_variable_count: int = 0
    can_edit: bool = False
    can_submit: bool = False

    model_config = {"from_attributes": True}


class WhatsAppTemplateList(BaseModel):
    items: list[WhatsAppTemplateResponse]
    total: int


class TemplateSyncResult(BaseModel):
    fetched: int
    created: int
    updated: int
