from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base, SessionLocal
import models  # noqa: F401 — registers all models with Base before create_all
from routes import auth_router, contacts_router, employees_router, lead_sources_router, roles_router, staff_locations_router
from middleware import RequestLoggingMiddleware

Base.metadata.create_all(bind=engine)

with SessionLocal() as _db:
    models.seed_system_roles(_db)  # built-in roles must exist before employees can reference them

app = FastAPI(title="Ogera CRM API", version="1.0.0")

app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth_router)
app.include_router(contacts_router)
app.include_router(employees_router)
app.include_router(lead_sources_router)
app.include_router(roles_router)
app.include_router(staff_locations_router)


@app.get("/")
def root():
    return {"message": "Ogera CRM API is running"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}
