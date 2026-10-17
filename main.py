from fastapi import FastAPI
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from config import CORS_ORIGINS, UPLOAD_DIR
from database import engine, Base, SessionLocal
import models  # noqa: F401 — registers all models with Base before create_all
from routes import auth_router, password_reset_router, contacts_router, employees_router, lead_activities_router, followups_router, history_router, lead_sources_router, performance_router, products_router, roles_router, sales_router, settings_router, staff_locations_router, tasks_router
import settings_catalog
from middleware import RequestLoggingMiddleware

Base.metadata.create_all(bind=engine)

with SessionLocal() as _db:
    models.seed_system_roles(_db)  # built-in roles must exist before employees can reference them
    settings_catalog.seed_defaults(_db)  # default dropdown options and company name

app = FastAPI(title="Ogera CRM API", version="1.0.0")

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
app.include_router(contacts_router)
app.include_router(employees_router)
app.include_router(lead_activities_router)
app.include_router(followups_router)
app.include_router(history_router)
app.include_router(lead_sources_router)
app.include_router(products_router)
app.include_router(performance_router)
app.include_router(roles_router)
app.include_router(sales_router)
app.include_router(settings_router)
app.include_router(staff_locations_router)
app.include_router(tasks_router)

Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/", summary="API status", description="Confirms the API is running. No sign-in needed.")
def root():
    return {"message": "Ogera CRM API is running"}


@app.get("/health", summary="Health check", description="Returns healthy when the server is up. Used by uptime checks. No sign-in needed.")
def health_check():
    return {"status": "healthy"}
