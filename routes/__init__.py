from .auth import router as auth_router
from .password_reset import router as password_reset_router
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

__all__ = ["auth_router", "password_reset_router", "contacts_router", "dashboard_router", "employees_router", "lead_activities_router", "followups_router", "history_router", "lead_sources_router", "notifications_router", "pipeline_router", "products_router", "roles_router", "sales_router", "settings_router", "tasks_router"]
