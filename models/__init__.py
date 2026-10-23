from .role import Role, SYSTEM_ROLES, seed_system_roles
from .employee import Employee
from .lead_source import LeadSource
from .pipeline_stage import PipelineStage
from .enum_value import EnumValue
from .company_setting import CompanySetting
from .contact import Contact
from .lead_stage import LeadStage
from .followup_schedule import FollowupSchedule
from .followup_reschedule import FollowupReschedule
from .followup_edit_log import FollowupEditLog
from .activity_log import ActivityLog
from .contact_assignment import ContactAssignment
from .consumer_profile import ConsumerProfile
from .partner_profile import PartnerProfile
from .password_reset import PasswordResetCode
from .dealer_profile import DealerProfile
from .sale import Sale
from .product import Product, ProductOption
from .contact_interested_product import contact_interested_products  # registers the join table with Base.metadata
from .notification import Notification

__all__ = [
    "Role",
    "SYSTEM_ROLES",
    "seed_system_roles",
    "Employee",
    "LeadSource",
    "PipelineStage",
    "EnumValue",
    "CompanySetting",
    "Contact",
    "LeadStage",
    "FollowupSchedule",
    "FollowupReschedule",
    "FollowupEditLog",
    "ActivityLog",
    "ContactAssignment",
    "ConsumerProfile",
    "PartnerProfile",
    "PasswordResetCode",
    "DealerProfile",
    "Sale",
    "Product",
    "ProductOption",
    "contact_interested_products",
    "Notification",
]
