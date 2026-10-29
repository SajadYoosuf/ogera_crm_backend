"""Compatibility entry point for the database diagnostics router.

The implementation lives with the other backend routers in
``routes.diagnostics``; this module supports importing it as ``db_diagnostics``.
"""

from routes.diagnostics import router

__all__ = ["router"]
