"""
Database session setup — SQLModel + SQLAlchemy 2.x.

What is SQLModel?
  SQLModel wraps SQLAlchemy (DB table mapping) and Pydantic (data validation) in one package.
  A model class with `table=True` becomes a real DB table.
  A model class without `table=True` is just a Pydantic schema (for API shapes).

What happens here:
  1. Reads DB credentials from environment variables (.env file).
  2. Creates one shared `engine` — the connection to MySQL.
  3. `get_db()` gives each FastAPI request its own Session, then closes it automatically.

SQLite note:
  The `if DATABASE_URL.startswith("sqlite")` block is ONLY for the test suite.
  Tests set DATABASE_URL=sqlite:///:memory: so they run on a fast in-memory DB instead
  of the real MySQL server. The production app always uses MySQL.

All models will be migrated to `SQLModel, table=True` — starting with employee.py.
While migration is in progress, older models still inherit from `Base` (DeclarativeBase).
Both styles work with the same engine and Session.
"""
from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.main import default_registry
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from dotenv import load_dotenv
import os

load_dotenv()

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "ogera_crm")

# DATABASE_URL overrides individual settings above.
# The test suite points it at SQLite; production uses MySQL.
DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?ssl_disabled=True"
)
# Set SQL_ECHO=1 in .env to print every SQL query — useful when debugging.
SQL_ECHO = os.getenv("SQL_ECHO", "0") == "1"


if DATABASE_URL.startswith("sqlite"):
    if DATABASE_URL in ("sqlite://", "sqlite:///:memory:"):
        # In-memory SQLite: one shared connection so all sessions see the same data.
        # Used by the pytest suite.
        engine = create_engine(
            DATABASE_URL, echo=SQL_ECHO,
            connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        # File-based SQLite: each session gets its own connection; writers wait up to 30 s.
        engine = create_engine(
            DATABASE_URL, echo=SQL_ECHO,
            connect_args={"check_same_thread": False, "timeout": 30}
        )
else:
    # MySQL on Vercel / serverless: NullPool means no persistent connections between requests.
    # Each request opens and closes its own connection.
    engine = create_engine(
        DATABASE_URL,
        echo=SQL_ECHO,
        poolclass=NullPool,
        connect_args={"connect_timeout": 10, "ssl_disabled": True},
    )


# Base class for models not yet migrated to SQLModel.
# Kept so existing `class Foo(Base):` models don't break while migration is in progress.
# Each model will be converted one at a time — employee.py is first.
class Base(DeclarativeBase):
    # Share SQLModel's registry. Two separate registries cannot resolve each other's
    # relationship names (e.g. Contact -> "Employee"), so the old and new models must
    # live in the same one.
    registry = default_registry


# Kept for the scheduler, services, scripts and tests that still call SessionLocal().
# Same settings as get_db() below. New code should use get_db() or `with Session(engine)`.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """
    FastAPI dependency — one Session per request, auto-closed when done.

    How to use in a route:
        db: Session = Depends(get_db)

    expire_on_commit=False:
        After db.commit(), loaded objects stay usable in Python (not re-fetched from DB).
        This matters when a route commits and then returns the same object.
    """
    with Session(engine, expire_on_commit=False) as db:
        yield db
