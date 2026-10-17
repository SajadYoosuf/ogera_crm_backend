"""Admin-editable dropdown options behind Settings -> Enums.

Four categories live in `enum_values`. Lead sources and lead stages keep their existing tables
(contacts point at them), so each category gets a small adapter and the API treats all six alike.
"""
import re
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import CompanySetting, Contact, EnumValue, LeadActivity, LeadSource, LeadStage, PartnerProfile, StageType

LEAD_SOURCE = "lead_source"
LEAD_STAGE = "lead_stage"
ACTIVITY_TYPE = "activity_type"
LOSS_REASON = "loss_reason"
DISTRICT = "district"

CATEGORY_LABELS = {
    LEAD_SOURCE: "Lead sources",
    LEAD_STAGE: "Lead stages",
    ACTIVITY_TYPE: "Activity types",
    LOSS_REASON: "Loss reasons",
    DISTRICT: "Districts",
}
CATEGORIES = tuple(CATEGORY_LABELS)

# Values the code branches on. Their label and position can change, but they can't be turned off or removed.
LOCKED = {
    LEAD_SOURCE: {"offline"},  # offline leads must carry a location
    LEAD_STAGE: {"new_lead", "won", "lost"},  # pipeline entry/exit stages
    ACTIVITY_TYPE: {"field_visit", "internal_note"},  # GPS capture / cannot be scheduled
}
# The pipeline is fixed in code (stage rules, follow-up clocks), so stages can be renamed and reordered but not added.
NO_NEW_VALUES = {LEAD_STAGE}

# Seeded from what the app already stores, so existing records keep matching. (value, label)
DEFAULT_ENUMS: dict[str, list[tuple[str, str]]] = {
    ACTIVITY_TYPE: [
        ("phone_call", "Phone call"), ("field_visit", "Field visit"), ("whatsapp", "WhatsApp"),
        ("demo", "Demo"), ("internal_note", "Internal note"),
        ("other", "Other"),
    ],
    LOSS_REASON: [
        ("price", "Price too high"), ("competitor", "Went to a competitor"), ("timing", "Timing"),
        ("not_interested", "Not interested"), ("other", "Other"),
    ],
    DISTRICT: [
        ("thiruvananthapuram", "Thiruvananthapuram"), ("kollam", "Kollam"), ("pathanamthitta", "Pathanamthitta"),
        ("alappuzha", "Alappuzha"), ("kottayam", "Kottayam"), ("idukki", "Idukki"), ("ernakulam", "Ernakulam"),
        ("thrissur", "Thrissur"), ("palakkad", "Palakkad"), ("malappuram", "Malappuram"),
        ("kozhikode", "Kozhikode"), ("wayanad", "Wayanad"), ("kannur", "Kannur"), ("kasaragod", "Kasaragod"),
    ],
}

# Pipeline stages per contact type. (slug, name, description, contact_type, display_order)
DEFAULT_STAGES: list[tuple[str, str, str, str, int]] = [
    # Consumer pipeline
    ("new_lead",             "New Lead",               "Just entered. Not yet contacted.",                    "consumer", 1),
    ("first_call",           "First Call",              "First call attempted. Waiting to reach them.",        "consumer", 2),
    ("connected",            "Connected",               "Spoke to them. Qualify and decide next step.",        "consumer", 3),
    ("aware",                "Aware",                   "Needs education before hearing a price.",             "consumer", 4),
    ("interested",           "Interested",              "Ready for demo or detailed info.",                    "consumer", 5),
    ("quotation",            "Quotation",               "Quote sent. 3-day follow-up clock starts.",           "consumer", 6),
    ("negotiation",          "Negotiation",             "Price and terms under discussion.",                   "consumer", 7),
    ("won",                  "Won",                     "Deal closed.",                                        "consumer", 8),
    ("lost",                 "Lost",                    "Did not buy. Reason captured.",                       "consumer", 9),
    ("after_sales_referring","After Sales / Referring", "Post-sale support and referral tracking.",            "consumer", 10),
    # Partner pipeline
    ("new_lead",             "New Lead",               "Just entered.",                                        "partner", 1),
    ("first_call",           "First Call",              "First contact made.",                                 "partner", 2),
    ("connected",            "Connected",               "Qualified partner prospect.",                         "partner", 3),
    ("demo_introduction",    "Demo / Introduction",     "Product demo scheduled or delivered.",                "partner", 4),
    ("aware",                "Aware",                   "Partner understands the product.",                    "partner", 5),
    ("interested",           "Interested",              "Partner ready to commit.",                            "partner", 6),
    ("active",               "Active",                  "Active partner.",                                     "partner", 7),
    ("referral",             "Referral",                "Partner sending referrals.",                          "partner", 8),
    ("inactive",             "Inactive",                "Partner no longer active.",                           "partner", 9),
    # Dealer pipeline
    ("new_lead",             "New Lead",               "Just entered.",                                        "dealer", 1),
    ("connected",            "Connected",               "Initial contact made.",                               "dealer", 2),
    ("meeting_done",         "Meeting Done",            "In-person or video meeting completed.",               "dealer", 3),
    ("aware",                "Aware",                   "Dealer understands the product range.",               "dealer", 4),
    ("terms_agreed",         "Terms Agreed",            "Commercial terms discussed and agreed.",              "dealer", 5),
    ("sample_sent",          "Sample Sent",             "Product sample dispatched.",                          "dealer", 6),
    ("first_order",          "First Order",             "First order placed.",                                 "dealer", 7),
    ("placed",               "Placed",                  "Order confirmed and in fulfilment.",                  "dealer", 8),
    ("active_dealer",        "Active Dealer",           "Recurring active dealer.",                            "dealer", 9),
    ("inactive_dealer",      "Inactive Dealer",         "Dealer no longer ordering.",                          "dealer", 10),
]

DEFAULT_COMPANY = {"company_name": "Ogera", "company_logo_url": None}


@dataclass
class Item:
    id: int
    value: str
    label: str
    sort_order: int
    is_active: bool
    locked: bool = False
    usage_count: int = 0


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:max_len].strip("_")
    if not slug:
        raise HTTPException(status_code=422, detail="Use letters or digits in the name")
    return slug


def seed_defaults(db: Session) -> None:
    """Insert any missing default options and company settings (idempotent; never overwrites edits)."""
    have = {(c, v) for c, v in db.query(EnumValue.category, EnumValue.value).all()}
    for category, rows in DEFAULT_ENUMS.items():
        for order, (value, label) in enumerate(rows, start=1):
            if (category, value) not in have:
                db.add(EnumValue(category=category, value=value, label=label, sort_order=order))
    existing = {(s, ct) for s, ct in db.query(StageType.slug, StageType.contact_type).all()}
    for slug, name, description, contact_type, order in DEFAULT_STAGES:
        if (slug, contact_type) not in existing:
            db.add(StageType(
                slug=slug, name=name, description=description,
                contact_type=contact_type, order=order,
            ))
    keys = {k for (k,) in db.query(CompanySetting.setting_key).all()}
    for key, value in DEFAULT_COMPANY.items():
        if key not in keys:
            db.add(CompanySetting(setting_key=key, value=value))
    db.commit()


def _enum_items(db: Session, category: str) -> list[Item]:
    rows = (
        db.query(EnumValue).filter(EnumValue.category == category)
        .order_by(EnumValue.sort_order, EnumValue.id).all()
    )
    return [Item(r.id, r.value, r.label, r.sort_order, bool(r.is_active), r.value in LOCKED.get(category, ())) for r in rows]


def _source_items(db: Session, category: str) -> list[Item]:
    rows = db.query(LeadSource).order_by(LeadSource.name).all()
    return [Item(r.id, r.slug, r.name, 0, bool(r.is_active), r.slug in LOCKED[category]) for r in rows]


def _stage_items(db: Session, category: str) -> list[Item]:
    # Only the consumer pipeline is managed via Settings; partner and dealer stages are fixed in code.
    rows = db.query(StageType).filter(StageType.contact_type == "consumer").order_by(StageType.order, StageType.id).all()
    return [Item(r.id, r.slug, r.name, r.order, bool(r.is_active), r.slug in LOCKED[category]) for r in rows]


def list_items(db: Session, category: str, with_usage: bool = False) -> list[Item]:
    items = {LEAD_SOURCE: _source_items, LEAD_STAGE: _stage_items}.get(category, _enum_items)(db, category)
    if with_usage:
        counts = usage_counts(db, category)
        for item in items:
            item.usage_count = counts.get(item.value.lower(), 0)
    return items


def usage_counts(db: Session, category: str) -> dict[str, int]:
    """How many records use each value, keyed by lower-cased value. One grouped query per category."""
    if category == LEAD_SOURCE:
        rows = (
            db.query(LeadSource.slug, func.count(Contact.id))
            .join(Contact, Contact.lead_source_id == LeadSource.id)
            .filter(Contact.deleted_at.is_(None)).group_by(LeadSource.slug).all()
        )
    elif category == LEAD_STAGE:
        rows = (
            db.query(StageType.slug, func.count(func.distinct(LeadStage.contact_id)))
            .join(LeadStage, LeadStage.stage_id == StageType.id)
            .group_by(StageType.slug).all()
        )
    elif category == ACTIVITY_TYPE:
        rows = db.query(LeadActivity.type, func.count(LeadActivity.id)).group_by(LeadActivity.type).all()
    elif category == LOSS_REASON:
        rows = (
            db.query(LeadStage.loss_reason, func.count(LeadStage.id))
            .filter(LeadStage.loss_reason.isnot(None)).group_by(LeadStage.loss_reason).all()
        )
    else:  # district is free text on the contact
        rows = (
            db.query(Contact.district, func.count(Contact.id))
            .filter(Contact.deleted_at.is_(None)).group_by(Contact.district).all()
        )
    out: dict[str, int] = {}
    for key, count in rows:
        norm = str(key).strip().lower()
        if category == DISTRICT:
            norm = norm.replace(" ", "_")  # "Interior Designer" and interior_designer are the same option
        out[norm] = out.get(norm, 0) + count
    return out


def active_values(db: Session, category: str) -> set[str]:
    return {i.value for i in list_items(db, category) if i.is_active}


def ensure_allowed(db: Session, category: str, value: str, current: str | None = None) -> None:
    """422 unless `value` is an active option. Re-saving a record's existing value stays allowed after it is turned off."""
    items = list_items(db, category)
    if not items or value == current or any(i.value == value and i.is_active for i in items):
        return  # nothing configured yet, an unchanged value, or an active option
    raise HTTPException(
        status_code=422, detail=f"'{value}' is not an available option for {CATEGORY_LABELS[category].lower()}"
    )


def labels(db: Session, category: str) -> dict[str, str]:
    return {i.value: i.label for i in list_items(db, category)}


def get_company(db: Session) -> dict[str, str | None]:
    rows = {r.setting_key: r.value for r in db.query(CompanySetting).all()}
    return {k: rows.get(k, DEFAULT_COMPANY[k]) for k in DEFAULT_COMPANY}


def set_company(db: Session, values: dict[str, str | None]) -> None:
    rows = {r.setting_key: r for r in db.query(CompanySetting).all()}
    for key, value in values.items():
        if key in rows:
            rows[key].value = value
        else:
            db.add(CompanySetting(setting_key=key, value=value))
    db.commit()
