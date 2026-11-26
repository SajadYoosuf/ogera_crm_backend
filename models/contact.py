"""
Contact model — the central table in the CRM.

WHAT IS THIS FILE?
------------------
This file defines the `Contact` database model using SQLAlchemy.
A "model" is a Python class that maps directly to a MySQL table. Each attribute
of the class corresponds to a column in the table.

WHY DO WE NEED IT?
------------------
Every lead (potential customer or partner) in the CRM is a Contact.
When we run `db.add(contact)` and `db.commit()`, SQLAlchemy converts the
Python object into an INSERT statement and writes it to MySQL.

HOW SQLALCHEMY MODELS WORK (simple version):
    class Contact(Base)   →   CREATE TABLE contacts (...)
    contact.id            →   contacts.id  (a column)
    contact.lead_stages   →   all LeadStage rows where contact_id = contact.id (a relationship)

RELATIONSHIPS:
    - One Contact has many LeadStages         (pipeline history)
    - One Contact has many FollowupSchedules  (call-back reminders)
    - One Contact belongs to one Employee     (assigned_to)
    - One Contact may have ONE of:
        • ConsumerProfile  (house stage, tank info)
        • PartnerProfile   (profession, work area)
    - A Contact can refer other Contacts      (self-referential referral tree)
"""

import datetime
from sqlalchemy import BigInteger, String, ForeignKey, Text, DateTime, func, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Contact(Base):
    """
    Represents one lead/contact in the CRM.

    A contact can be one of two types:
        • consumer  — individual customer (e.g. homeowner wanting a water tank)
        • partner   — a tradesperson who refers customers (e.g. plumber, interior designer)

    Each type can have its own extra details table (ConsumerProfile, PartnerProfile),
    linked via a one-to-one relationship.

    SOFT DELETE:
        We never truly delete contacts. Instead we set `deleted_at` to the current time.
        Queries filter with `Contact.deleted_at.is_(None)` to show only live contacts.
        This preserves history (lead stages, assignments) even after removal.
    """

    __tablename__ = "contacts"

    # --- Primary key ---
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # --- Core identity fields ---
    # String(10) instead of SAEnum — Pydantic ContactType validates the value at API level.
    # Using SAEnum with a name would create a named PostgreSQL type that is hard to modify.
    contact_type: Mapped[str] = mapped_column(String(10), default="consumer")
    name: Mapped[str | None] = mapped_column(String(150))
    # The phone number as entered (may include country code, spaces, dashes)
    phone: Mapped[str] = mapped_column(String(20))
    # Last 10 digits of phone. Uniqueness enforced via a partial unique index in SQL
    # (WHERE deleted_at IS NULL), not unique=True here, so soft-deleted contacts
    # don't block re-registration with the same number.
    phone_key: Mapped[str] = mapped_column(String(10), default="")
    phone_alt: Mapped[str | None] = mapped_column(String(20))

    # --- Location fields ---
    # Optional at creation; required for the contact to become verified
    district: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(255))

    # --- Ownership & attribution ---
    # NULL = not yet assigned; manager assigns after reviewing the new lead
    assigned_to: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=True)
    # The employee who created this contact row
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id"))

    # --- Referral ---
    # If another contact referred this person, store their id here.
    # This creates a self-referential tree: a contact can refer many others.
    referred_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("contacts.id"))

    # --- Branch ---
    # FK to the branches table (replaces the old VARCHAR enum column).
    # Branches are tenant-specific and admin-managed, so they live in a table, not code.
    branch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("branches.id"), nullable=True)

    # --- Lead metadata ---
    # Set when this contact was created from a lead conversion
    lead_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("leads.id"), nullable=True)
    lead_source_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("lead_sources.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    # --- Audit timestamps ---
    # server_default=func.now() → MySQL sets this automatically on INSERT (no Python needed)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    # onupdate=func.now() → MySQL updates this automatically on every UPDATE
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    # Soft-delete timestamp. NULL = contact is live. Non-NULL = contact is deleted.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime)

    # -------------------------------------------------------------------------
    # RELATIONSHIPS
    # SQLAlchemy uses these to automatically JOIN and load related rows.
    # They do NOT add columns to the table — they are Python-only helpers.
    # -------------------------------------------------------------------------

    # The employee currently responsible for following up with this contact
    assigned_employee: Mapped["Employee | None"] = relationship(
        "Employee", back_populates="assigned_contacts", foreign_keys=[assigned_to]
    )
    # Employee who entered the lead
    creator: Mapped["Employee | None"] = relationship("Employee", foreign_keys=[created_by], back_populates="created_contacts")

    # Self-referential referral relationships:
    #   referred_by  → the ONE contact that referred this one (parent)
    #   referrals    → all contacts this contact has referred (children)
    # `remote_side=[id]` tells SQLAlchemy that `id` is the "parent" side of the join.
    referred_by: Mapped["Contact | None"] = relationship(
        "Contact", foreign_keys=[referred_by_id], remote_side=[id], back_populates="referrals"
    )
    referrals: Mapped[list["Contact"]] = relationship(
        "Contact", foreign_keys=[referred_by_id], back_populates="referred_by"
    )

    # Branch relationship
    branch: Mapped["Branch | None"] = relationship("Branch", foreign_keys=[branch_id])

    # The marketing channel / source that brought in this lead (e.g. "Facebook", "Offline")
    lead_source: Mapped["LeadSource | None"] = relationship("LeadSource", back_populates="contacts")

    # Full pipeline history for this contact (append-only; newest = current stage)
    lead_stages: Mapped[list["LeadStage"]] = relationship("LeadStage", back_populates="contact")

    # All scheduled follow-up calls for this contact
    followup_schedules: Mapped[list["FollowupSchedule"]] = relationship("FollowupSchedule", back_populates="contact")

    # Type-specific detail tables. uselist=False → one-to-one (returns an object, not a list).
    # Only ONE of these will ever have data for a given contact.
    consumer_profile: Mapped["ConsumerProfile | None"] = relationship(
        "ConsumerProfile", back_populates="contact", uselist=False
    )
    partner_profile: Mapped["PartnerProfile | None"] = relationship(
        "PartnerProfile", back_populates="contact", uselist=False
    )
    dealer_profile: Mapped["DealerProfile | None"] = relationship(
        "DealerProfile", back_populates="contact", uselist=False
    )
    # -------------------------------------------------------------------------
    # INDEXES
    # An index speeds up filtering/searching on a column.
    # Without an index, MySQL scans every row (slow for large tables).
    # With an index, MySQL uses a B-tree lookup (fast even for millions of rows).
    # -------------------------------------------------------------------------
    __table_args__ = (
        Index("idx_contacts_type", "contact_type"),     # filter by consumer/partner
        Index("idx_contacts_assigned", "assigned_to"),  # "show my leads" query
        Index("idx_contacts_district", "district"),     # filter by district
        Index("idx_contacts_created_by", "created_by"), # filter by creator
        Index("idx_contacts_branch", "branch_id"),      # filter by branch
        Index("idx_contacts_phone_key", "phone_key"),   # dedup lookups
    )
