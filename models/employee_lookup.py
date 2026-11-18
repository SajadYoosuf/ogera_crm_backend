"""
EmployeeLookup — lives in the public schema (table: employee_tenant_map).

This table is the first stop at login: given an email address it tells us
which PostgreSQL schema holds that employee's data (e.g. "ventino").

Why a separate table in public?
  Every tenant's employees table lives in its own schema. We cannot query all
  of them without knowing which one to look in first. This global index solves
  the chicken-and-egg problem: look up the schema here, then switch to it.

Columns:
  email       — indexed; must match the email in the tenant's employees table
  tenant_id   — numeric ID for future tenant management (billing, admin)
  schema_name — PostgreSQL schema name used in SET search_path (e.g. "ventino")
"""
from sqlmodel import SQLModel, Field


class EmployeeLookup(SQLModel, table=True):
    __tablename__ = "employee_tenant_map"
    __table_args__ = {"schema": "public"}  # always in public, never in a tenant schema

    email: str = Field(max_length=180, primary_key=True)
    tenant_id: int
    schema_name: str = Field(max_length=63)  # routing key: e.g. "ventino"
