import enum


class RoleEnum(str, enum.Enum):
    admin = "admin"                      # full system access
    manager = "manager"                  # manages staff, all leads, reports
    sales = "sales_executive"                      # sales executive (closes deals)


class ContactType(str, enum.Enum):
    consumer = "consumer"
    partner = "partner"


def profession_key(raw: str) -> str:
    """Stored profession text as an option value: "Interior Designer" -> "interior_designer". Partner types live in Settings."""
    return raw.strip().lower().replace(" ", "_")


class BranchEnum(str, enum.Enum):
    """
    The three physical branch offices.
    Used on Contact and Sale so every record can be tied to the branch it came from.
    """
    malappuram = "malappuram"
    kottakkal = "kottakkal"
    perinthalmanna = "perinthalmanna"


class LossReasonEnum(str, enum.Enum):
    price = "price"
    competitor = "competitor"
    timing = "timing"
    not_interested = "not_interested"
    other = "other"

