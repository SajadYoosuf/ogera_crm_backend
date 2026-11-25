from .leads import router as leads_router
from .lead_interactions import router as lead_interactions_router
from .lead_statuses import router as lead_statuses_router
from .lead_whatsapp import router as lead_whatsapp_router
from .lead_tasks import router as lead_tasks_router
from .branches import router as branches_router
from .contact_fields import router as contact_fields_router
from .lead_inbound import router as lead_inbound_router
from .webhooks import router as webhooks_router
from .api_keys import router as api_keys_router
from .auth import router as auth_router
from .password_reset import router as password_reset_router
# from .recent_activity import router as recent_activity_router  # disabled — contact_assignments not in ventino yet
from .contacts import router as contacts_router
from .dashboard import router as dashboard_router
from .employees import router as employees_router
# --- disabled: tables not yet migrated to ventino PostgreSQL schema ---
# from .lead_activities import router as lead_activities_router
# from .followups import router as followups_router
# from .history import router as history_router
from .lead_sources import router as lead_sources_router
from .notifications import router as notifications_router
from .pipeline import router as pipeline_router
from .products import router as products_router
from .roles import router as roles_router
from .sales import router as sales_router
from .settings import router as settings_router
from .tasks import router as tasks_router
from .attendance import router as attendance_router
from .staff_locations import router as staff_locations_router
from .expenses import router as expenses_router
from .diagnostics import router as diagnostics_router
from .followup_settings import router as followup_settings_router
from .whatsapp_inbox import router as whatsapp_inbox_router
from .whatsapp_config import router as whatsapp_config_router
from .whatsapp_templates import router as whatsapp_templates_router

__all__ = ["leads_router", "lead_inbound_router", "webhooks_router", "api_keys_router", "auth_router", "password_reset_router", "contacts_router", "dashboard_router", "employees_router", "lead_sources_router", "notifications_router", "pipeline_router", "products_router", "roles_router", "sales_router", "settings_router", "tasks_router", "attendance_router", "staff_locations_router", "expenses_router", "diagnostics_router", "branches_router", "contact_fields_router", "lead_interactions_router", "lead_statuses_router", "lead_whatsapp_router", "lead_tasks_router", "followup_settings_router", "whatsapp_inbox_router", "whatsapp_config_router", "whatsapp_templates_router"]
