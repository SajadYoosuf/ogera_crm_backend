import datetime
from typing import Optional

from sqlalchemy import Column, SmallInteger, DateTime, func
from sqlmodel import SQLModel, Field


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"
    __table_args__ = {"schema": "public"}

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=120)
    schema_name: str = Field(max_length=63, unique=True)
    logo_url: Optional[str] = Field(default=None)
    timezone: str = Field(max_length=50, default="Asia/Kolkata")
    is_active: int = Field(
        default=1,
        sa_column=Column("is_active", SmallInteger, nullable=False, server_default="1"),
    )
    created_at: datetime.datetime = Field(
        sa_column=Column("created_at", DateTime, nullable=False, server_default=func.now())
    )
    updated_at: datetime.datetime = Field(
        sa_column=Column(
            "updated_at", DateTime, nullable=False,
            server_default=func.now(), onupdate=func.now(),
        )
    )
