from .auth import router as auth_router
from .password_reset import router as password_reset_router
from .recent_activity import router as recent_activity_router
from .contacts import router as contacts_router
from .dashboard import router as dashboard_router
from .employees import router as employees_router
from .lead_activities import router as lead_activities_router
from .followups import router as followups_router
from .history import router as history_router
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

__all__ = ["auth_router", "password_reset_router", "recent_activity_router", "contacts_router", "dashboard_router", "employees_router", "lead_activities_router", "followups_router", "history_router", "lead_sources_router", "notifications_router", "pipeline_router", "products_router", "roles_router", "sales_router", "settings_router", "tasks_router", "attendance_router", "staff_locations_router", "expenses_router", "diagnostics_router"]
