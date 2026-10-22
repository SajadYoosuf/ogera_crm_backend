"""
Join table: contacts ↔ products (many-to-many).

WHAT IS THIS FILE?
------------------
A contact can be interested in many products, and one product can be of interest
to many contacts. SQLAlchemy represents this with a "secondary" join table — a
plain two-column table with no extra data beyond the two foreign keys.

HOW IT WORKS:
    contacts ──< contact_interested_products >── products
       id (PK)       contact_id  product_id         id (PK)

WHY A PLAIN Table AND NOT A MAPPED CLASS?
    We store no extra columns on this join (no date, no notes). SQLAlchemy's
    recommended pattern for a simple M2M is a plain `Table` object passed as
    `secondary` on the relationship. A full mapped class is only needed if the
    join itself carries extra data (e.g. a "date of interest" column).
"""
from sqlalchemy import BigInteger, Column, ForeignKey, Integer, Table
from database import Base

contact_interested_products = Table(
    "contact_interested_products",
    Base.metadata,
    # contacts.id is BIGINT → contact_id must also be BIGINT.
    # products.id is plain INT (no BigInteger) → product_id must be INT to match.
    Column("contact_id", BigInteger, ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("product_id", Integer, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
)
