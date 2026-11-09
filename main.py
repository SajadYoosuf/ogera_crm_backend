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


def _friendly_validation_message(error: dict) -> str:
    """
    Convert a single Pydantic validation error into a plain-English sentence.

    Pydantic error types use technical language ("String should have at least 1
    character"). This function maps those to messages a user can act on
    ("Name is required" or "Name must be at least 1 character long").

    How it works:
      - `loc`  is a tuple like ("body", "email") — we extract the field name.
      - `type` is a dotted code like "string_too_short" or "value_error" — used
               to pick the right template.
      - `ctx`  holds constraint values like {"min_length": 1, "max_length": 120}.
    """
    # Extract the user-facing field name from the loc tuple.
    # loc is e.g. ("body", "current_password") — skip wrapper keys.
    loc = error.get("loc", ())
    field_parts = [p for p in loc if p not in ("body", "query", "path", "header")]
    field = str(field_parts[-1]).replace("_", " ").capitalize() if field_parts else ""

    error_type = error.get("type", "")
    ctx = error.get("ctx", {})

    # Map technical Pydantic error types to human messages.
    if error_type in ("missing", "value_error.missing"):
        msg = "is required"
    elif error_type == "string_too_short":
        min_len = ctx.get("min_length", 1)
        msg = "is required" if min_len <= 1 else f"must be at least {min_len} characters long"
    elif error_type == "string_too_long":
        max_len = ctx.get("max_length", "")
        msg = f"must be {max_len} characters or fewer" if max_len else "is too long"
    elif error_type in ("value_error.email", "string_type") and "email" in field.lower():
        msg = "must be a valid email address"
    elif error_type == "int_parsing":
        msg = "must be a whole number"
    elif error_type == "float_parsing":
        msg = "must be a number"
    elif error_type == "bool_parsing":
        msg = "must be true or false"
    elif error_type == "enum":
        allowed = ctx.get("expected", "")
        msg = f"must be one of: {allowed}" if allowed else "is not a valid option"
    elif error_type == "string_pattern_mismatch":
        # Pattern errors are always from fields with a specific expected format.
        # Give a plain-English hint based on the field name rather than exposing the regex.
        if "code" in field.lower():
            msg = "must be exactly 6 digits"
        elif "phone" in field.lower():
            msg = "must be 7–15 digits (e.g. 9876543210)"
        else:
            msg = "is not in the expected format"
    elif error_type == "greater_than":
        msg = f"must be greater than {ctx.get('gt', '')}"
    elif error_type == "greater_than_equal":
        msg = f"must be {ctx.get('ge', '')} or more"
    elif error_type == "less_than":
        msg = f"must be less than {ctx.get('lt', '')}"
    elif error_type == "less_than_equal":
        msg = f"must be {ctx.get('le', '')} or less"
    elif error_type == "value_error":
        raw = error.get("msg", "is invalid").removeprefix("Value error, ")
        # EmailStr (and email_validator) raises value_error with a long technical
        # message like "value is not a valid email address: An email address must
        # have an @-sign." — replace the whole thing with one clean sentence.
        if "not a valid email" in raw or "email address" in raw.lower():
            msg = "must be a valid email address"
        else:
            # Other value_error messages come from our own field_validator functions
            # and are already written in plain English — pass them through as-is.
            msg = raw
            return msg if not field else f"{field} {msg}"
    else:
        # Fallback: clean up the raw Pydantic message slightly.
        msg = error.get("msg", "is invalid")

    return f"{field} {msg}".strip() if field else msg


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Normalize Pydantic 422 validation errors into the same {"detail": "string"} shape
    used by every other error in the app.

    Without this, FastAPI returns {"detail": [{"type":..., "loc":..., "msg":...}]},
    a list that the frontend would have to parse differently from all other errors.

    Reports only the first validation error — showing one clear problem at a time
    is more actionable for a mobile or web form than a list of every failure.
    """
    errors = exc.errors()
    if not errors:
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    detail = _friendly_validation_message(errors[0])
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
