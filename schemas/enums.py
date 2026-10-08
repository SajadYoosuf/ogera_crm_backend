import enum


class RoleEnum(str, enum.Enum):
    admin = "admin"                      # full system access
    manager = "manager"                  # manages staff, all leads, reports
    team_lead = "team_lead"              # supervises a team of callers / sales
    sales = "sales_executive"                      # sales executive (closes deals)
    tele_caller = "tele_caller"          # makes outbound/inbound calls, qualifies leads
    field_executive = "field_executive"  # on-site visits, measurements, installs
    support = "support"                  # after-sales / customer support


class ContactType(str, enum.Enum):
    consumer = "consumer"
    partner = "partner"
    dealer = "dealer"


class ProfessionType(str, enum.Enum):
    plumber = "plumber"
    engineer = "engineer"
    interior_designer = "interior_designer"
    architect = "architect"
    contractor = "contractor"
    electrician = "electrician"
    other = "other"                      # legacy imports; clients do not offer it for new partners

    @classmethod
    def normalise(cls, raw: str) -> "ProfessionType":
        """Map legacy free text ("Interior Designer") to a member; unknown text becomes other."""
        key = raw.strip().lower().replace(" ", "_")
        return cls(key) if key in cls._value2member_map_ else cls.other


class StageEnum(str, enum.Enum):
    new_lead = "new_lead"
    first_call = "first_call"
    connected = "connected"
    aware = "aware"
    interested = "interested"
    quoted = "quoted"
    negotiating = "negotiating"
    won = "won"
    lost = "lost"
    referral = "referral"
    repeat_customer = "repeat_customer"


class LossReasonEnum(str, enum.Enum):
    price = "price"
    competitor = "competitor"
    timing = "timing"
    not_interested = "not_interested"
    other = "other"


class HouseStageEnum(str, enum.Enum):
    ready = "ready"
    renovation = "renovation"
    construction = "construction"


class TankTypeEnum(str, enum.Enum):
    concrete = "concrete"
    plastic = "plastic"
    none = "none"
    other = "other"
