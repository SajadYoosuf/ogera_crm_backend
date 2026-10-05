from models import Contact


def missing_fields(contact: Contact) -> list[str]:
    """Names of the details still pending on a contact. Empty means verified."""
    missing = [
        field
        for field in ("name", "phone", "district", "address")
        if not (getattr(contact, field) or "").strip()
    ]
    if contact.lead_source_id is None:
        missing.append("lead_source")

    # Field-visit leads need the GPS pin and who entered them
    if contact.lead_source is not None and contact.lead_source.slug == "offline":
        if contact.latitude is None or contact.longitude is None:
            missing.append("location")
        if contact.created_by is None:
            missing.append("created_by")

    if contact.contact_type == "consumer":
        if contact.consumer_profile is None or not contact.consumer_profile.house_stage:
            missing.append("house_stage")
    elif contact.contact_type == "partner":
        if contact.partner_profile is None or not contact.partner_profile.work_area:
            missing.append("work_area")
    return missing
