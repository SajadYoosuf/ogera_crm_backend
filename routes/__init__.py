from .auth import router as auth_router
from .contacts import router as contacts_router
from .employees import router as employees_router
from .lead_sources import router as lead_sources_router
from .roles import router as roles_router
from .staff_locations import router as staff_locations_router

__all__ = ["auth_router", "contacts_router", "employees_router", "lead_sources_router", "roles_router", "staff_locations_router"]
