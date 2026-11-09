import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ORIGINS, UPLOAD_DIR
from routes import auth_router, password_reset_router, recent_activity_router, contacts_router, dashboard_router, employees_router, lead_activities_router, followups_router, history_router, lead_sources_router, notifications_router, pipeline_router, roles_router, sales_router, settings_router, tasks_router, products_router, attendance_router, staff_locations_router, expenses_router, diagnostics_router
from middleware import RequestLoggingMiddleware
from firebase_init import init_firebase
from scheduler import start_scheduler, stop_scheduler

# Configure Python's logging so that logger.info() / logger.warning() calls
# actually appear in the PyCharm console (and any terminal).
# Without this, Python silently drops all log calls because there is no handler.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%H:%M:%S",
)

# Quieten noisy third-party loggers so your own messages stand out.
logging.getLogger("passlib").setLevel(logging.WARNING)
logging.getLogger("jose").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize runtime services without performing database DDL or seeding."""
    logger.info("FastAPI is starting up...")

    init_firebase()
    start_scheduler()

    yield

    stop_scheduler()
    logger.info("FastAPI is shutting down...")


app = FastAPI(title="Ogera CRM API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Normalize Pydantic 422 validation errors into the same shape as all other errors:
      {"detail": "string"}

    Without this, FastAPI returns {"detail": [{"type":..., "loc":..., "msg":...}]},
    which is a list. Every other error in the app returns {"detail": "string"}.
    Mixing the two forces the frontend to handle two different formats.

    This handler picks the first validation error, turns it into:
      "Password: String should have at most 15 characters"
    so the frontend always reads response.data.detail as a plain string.
    """
    errors = exc.errors()
    if not errors:
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    first = errors[0]
    # loc is e.g. ("body", "password") — skip wrapper keys like "body"/"query"/"path"
    field_parts = [p for p in first["loc"] if p not in ("body", "query", "path")]
    field = str(field_parts[-1]).replace("_", " ").capitalize() if field_parts else ""
    msg = first["msg"]
    detail = f"{field}: {msg}" if field else msg

    return JSONResponse(status_code=422, content={"detail": detail})


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
app.include_router(diagnostics_router)

Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/", summary="API status", description="Confirms the API is running. No sign-in needed.")
def root():
    return {"message": "Ogera CRM API is running"}


@app.get("/health", summary="Health check", description="Returns healthy when the server is up. Used by uptime checks. No sign-in needed.")
def health_check():
    return {"status": "healthy"}
