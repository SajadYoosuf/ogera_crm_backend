"""
Contact verification — defines what "verified" means for each contact type.

HOW VERIFICATION WORKS:
    A contact starts as "unverified" (new_lead stage) when created with only
    phone + lead_source. As the team fills in more details, `missing_fields()`
    returns an empty list and `is_verified` becomes True.

    A verified contact can advance past the new_lead pipeline stage.

REQUIRED FIELDS BY TYPE:
    All types:    phone, name, district, address, lead_source
    Field visit:  + latitude, longitude (GPS captured by mobile at creation)
    Consumer:     + house_stage
    Partner:      + profession_type, work_area
    Dealer:       + owner_name

TWO IMPLEMENTATIONS:
    `missing_fields(contact)` — Python function, runs on an already-loaded ORM object.
                                Used in single-contact reads and stage transitions.
    `verified_clause()`       — SQLAlchemy expression, runs inside the DB query.
                                Used for the verified=true/false list filter and pagination.
    Keep both in sync: a rule changed in one must change in the other.
"""

from sqlalchemy import and_, exists, func, or_, select
from models import ConsumerProfile, Contact, DealerProfile, LeadSource, PartnerProfile


def missing_fields(contact: Contact) -> list[str]:
    """Names of the details still pending on a contact. Empty list means verified."""
    missing = [
        field
        for field in ("phone", "name", "district", "address")
        if not (getattr(contact, field) or "").strip()
    ]
    if contact.lead_source_id is None:
        missing.append("lead_source")

    # Field visit contacts need a GPS pin captured by the mobile app at creation.
    if contact.lead_source is not None and contact.lead_source.slug == "field_visit":
        if contact.latitude is None or contact.longitude is None:
            missing.append("location")

    if contact.contact_type == "consumer":
        if contact.consumer_profile is None or not contact.consumer_profile.house_stage:
            missing.append("house_stage")

    if contact.contact_type == "partner":
        if contact.partner_profile is None or not (contact.partner_profile.profession_type or "").strip():
            missing.append("profession_type")
        if contact.partner_profile is None or not (contact.partner_profile.work_area or "").strip():
            missing.append("work_area")

    if contact.contact_type == "dealer":
        if contact.dealer_profile is None or not (contact.dealer_profile.owner_name or "").strip():
            missing.append("owner_name")

    return missing


def _filled(column):
    """True in SQL when a column is not null, not empty, and not whitespace-only."""
    return func.length(func.trim(func.coalesce(column, ""))) > 0


def verified_clause():
    """SQL twin of `missing_fields() == []`, so the list can filter and paginate in the database.

    Keep in sync with `missing_fields()` above: any rule changed in one must change in the other.
    """
    field_visit_ids = select(LeadSource.id).where(LeadSource.slug == "field_visit")

    # Field visit contacts must have a GPS pin.
    # IS NULL in the outer expression keeps non-field-visit contacts always passing this check.
    location_ok = or_(
        Contact.lead_source_id.is_(None),
        Contact.lead_source_id.not_in(field_visit_ids),
        and_(Contact.latitude.is_not(None), Contact.longitude.is_not(None)),
    )

    consumer_ok = or_(
        Contact.contact_type != "consumer",
        exists().where(
            ConsumerProfile.contact_id == Contact.id, _filled(ConsumerProfile.house_stage)
        ),
    )

    partner_ok = or_(
        Contact.contact_type != "partner",
        exists().where(
            PartnerProfile.contact_id == Contact.id,
            _filled(PartnerProfile.profession_type),
            _filled(PartnerProfile.work_area),
        ),
    )

    dealer_ok = or_(
        Contact.contact_type != "dealer",
        exists().where(DealerProfile.contact_id == Contact.id, _filled(DealerProfile.owner_name)),
    )

    return and_(
        _filled(Contact.phone),
        _filled(Contact.name),
        _filled(Contact.district),
        _filled(Contact.address),
        Contact.lead_source_id.is_not(None),
        location_ok,
        consumer_ok,
        partner_ok,
        dealer_ok,
    )
