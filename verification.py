from sqlalchemy import and_, exists, func, or_, select
from models import ConsumerProfile, Contact, DealerProfile, LeadSource


def missing_fields(contact: Contact) -> list[str]:
    """Names of the details still pending on a contact. Empty means verified."""
    missing = [
        field
        for field in ("phone", "district", "address")
        if not (getattr(contact, field) or "").strip()
    ]
    if contact.lead_source_id is None:
        missing.append("lead_source")

    # Field-visit consumer leads need who entered them. Partners only need the basics above. The GPS pin
    # is only asked for when the contact is created, so imported offline contacts without one are still verified.
    if contact.contact_type != "partner" and contact.lead_source is not None and contact.lead_source.slug == "offline":
        if contact.created_by is None:
            missing.append("created_by")

    if contact.contact_type == "consumer":
        if contact.consumer_profile is None or not contact.consumer_profile.house_stage:
            missing.append("house_stage")
    if contact.contact_type == "dealer":
        if contact.dealer_profile is None or not (contact.dealer_profile.owner_name or "").strip():
            missing.append("owner_name")
    return missing


def _filled(column):
    return func.length(func.trim(func.coalesce(column, ""))) > 0


def verified_clause():
    """SQL twin of `missing_fields() == []`, so the list can filter and paginate in the database.

    Keep the two in sync: any rule changed in one must change in the other.
    """
    offline_ids = select(LeadSource.id).where(LeadSource.slug == "offline")
    # IS NULL keeps the whole expression non-NULL, so ~verified_clause() still matches these rows
    offline_ok = or_(
        Contact.contact_type == "partner",
        Contact.lead_source_id.is_(None),
        Contact.lead_source_id.not_in(offline_ids),
        Contact.created_by.is_not(None),
    )
    consumer_ok = or_(
        Contact.contact_type != "consumer",
        exists().where(
            ConsumerProfile.contact_id == Contact.id, _filled(ConsumerProfile.house_stage)
        ),
    )
    dealer_ok = or_(
        Contact.contact_type != "dealer",
        exists().where(DealerProfile.contact_id == Contact.id, _filled(DealerProfile.owner_name)),
    )
    return and_(
        _filled(Contact.phone),
        _filled(Contact.district),
        _filled(Contact.address),
        Contact.lead_source_id.is_not(None),
        offline_ok,
        consumer_ok,
        dealer_ok,
    )
