from .auth import router as auth_router
from .password_reset import router as password_reset_router
from .contacts import router as contacts_router
from .employees import router as employees_router
from .lead_activities import router as lead_activities_router
from .lead_sources import router as lead_sources_router
from .performance import router as performance_router
from .products import router as products_router
from .roles import router as roles_router
from .sales import router as sales_router
from .settings import router as settings_router
from .staff_locations import router as staff_locations_router
from .tasks import router as tasks_router

__all__ = ["auth_router", "password_reset_router", "contacts_router", "employees_router", "lead_activities_router", "lead_sources_router", "performance_router", "products_router", "roles_router", "sales_router", "settings_router", "staff_locations_router", "tasks_router"]
