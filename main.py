import os
import socket
import urllib.request
import logging
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ORIGINS, UPLOAD_DIR
from database import DB_HOST, DB_PORT, engine, Base, SessionLocal
import models  # noqa: F401 — registers all models with Base before create_all
from routes import auth_router, password_reset_router, recent_activity_router, contacts_router, dashboard_router, employees_router, lead_activities_router, followups_router, history_router, lead_sources_router, notifications_router, pipeline_router, roles_router, sales_router, settings_router, tasks_router, products_router, attendance_router, staff_locations_router, expenses_router
import settings_catalog
from middleware import RequestLoggingMiddleware
from firebase_init import init_firebase
from scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)

app = FastAPI(title="Ogera CRM API", version="1.0.0")


@app.on_event("startup")
def startup_event() -> None:
    # Do not connect to the database while importing this module. If the remote
    # database is unavailable, importing main.py must still allow Uvicorn to
    # start and Render to detect the listening port.
    try:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as _db:
            models.seed_system_roles(_db)  # built-in roles must exist before employees can reference them
            settings_catalog.seed_defaults(_db)  # default dropdown options and company name
        logger.info("Database initialization completed")
    except Exception:
        logger.exception(
            "Database initialization failed; the API will remain available, "
            "but database-backed requests may fail until the database is reachable"
        )

    init_firebase()
    start_scheduler()


@app.on_event("shutdown")
def shutdown_event() -> None:
    stop_scheduler()

app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router)
app.include_router(password_reset_router)
app.include_router(recent_activity_router)  # before contacts_router: /contacts/recent-activity must win over /contacts/{contact_id}
app.include_router(contacts_router)
app.include_router(dashboard_router)
app.include_router(employees_router)
app.include_router(lead_activities_router)
app.include_router(followups_router)
app.include_router(history_router)
app.include_router(lead_sources_router)
app.include_router(pipeline_router)
app.include_router(products_router)
app.include_router(roles_router)
app.include_router(sales_router)
app.include_router(settings_router)
app.include_router(notifications_router)
app.include_router(tasks_router)
app.include_router(attendance_router)
app.include_router(staff_locations_router)
app.include_router(expenses_router)

Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/", summary="API status", description="Confirms the API is running. No sign-in needed.")
def root():
    return {"message": "Ogera CRM API is running"}


@app.get("/health", summary="Health check", description="Returns healthy when the server is up. Used by uptime checks. No sign-in needed.")
def health_check():
    return {"status": "healthy"}


@app.get("/debug-db-network", include_in_schema=False)
def debug_db_network(x_debug_token: str | None = Header(default=None)):
    """Temporarily diagnose Render egress and TCP access to the database.

    Disabled unless ENABLE_DB_NETWORK_DEBUG=1 is set. If
    DB_NETWORK_DEBUG_TOKEN is configured, callers must provide it in the
    X-Debug-Token header. Remove the environment variable after testing.
    """
    if os.getenv("ENABLE_DB_NETWORK_DEBUG", "0") != "1":
        raise HTTPException(status_code=404, detail="Not found")

    expected_token = os.getenv("DB_NETWORK_DEBUG_TOKEN")
    if expected_token and x_debug_token != expected_token:
        raise HTTPException(status_code=403, detail="Invalid debug token")

    try:
        egress_ip = urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode("utf-8")
    except Exception as exc:
        egress_ip = f"Error finding egress IP: {exc}"

    db_port = int(DB_PORT)
    result = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            result = sock.connect_ex((DB_HOST, db_port))
        can_connect = result == 0
        status_message = (
            f"Port {db_port} is REACHABLE from Render"
            if can_connect
            else f"Connection failed with code: {result}"
        )
    except Exception as exc:
        can_connect = False
        status_message = f"Socket error: {exc}"

    return {
        "render_outbound_ip": egress_ip,
        "database_host": DB_HOST,
        "database_port": db_port,
        "mysql_reachable": can_connect,
        "detail": status_message,
    }
