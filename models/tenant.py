import datetime
import os
from typing import Optional

from sqlalchemy import Column, DateTime, func
from sqlmodel import SQLModel, Field

# SQLite (used by tests) does not support named schemas.
_PUBLIC_SCHEMA = {} if os.getenv("DATABASE_URL", "").startswith("sqlite") else {"schema": "public"}


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"
    __table_args__ = _PUBLIC_SCHEMA

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=120)
    schema_name: str = Field(max_length=63, unique=True)
    logo_url: Optional[str] = Field(default=None)
    status: str = Field(default="active")
    created_at: datetime.datetime = Field(
        sa_column=Column("created_at", DateTime, nullable=False, server_default=func.now())
    )
    updated_at: datetime.datetime = Field(
        sa_column=Column(
            "updated_at", DateTime, nullable=False,
            server_default=func.now(), onupdate=func.now(),
        )
    )
