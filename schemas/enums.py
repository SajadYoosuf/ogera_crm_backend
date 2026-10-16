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
    other = "other"
    plumber = "plumber"
    engineer = "engineer"
    interior_designer = "interior_designer"
    architect = "architect"
    contractor = "contractor"
    electrician = "electrician"

    @classmethod
    def normalise(cls, raw: str) -> "ProfessionType":
        """Map legacy free text ("Interior Designer") to a member; unknown text becomes other."""
        key = raw.strip().lower().replace(" ", "_")
        return cls(key) if key in cls._value2member_map_ else cls.other


def profession_key(raw: str) -> str:
    """Stored profession text as an option value: "Interior Designer" -> "interior_designer"."""
    return raw.strip().lower().replace(" ", "_")


# Fixed lists in code (not in Settings). Changing them is a code change.
class HouseStage(str, enum.Enum):
    ready = "ready"
    under_construction = "under_construction"
    renovation = "renovation"


class TankType(str, enum.Enum):
    plastic = "plastic"
    concrete = "concrete"
    steel = "steel"
    other = "other"



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
