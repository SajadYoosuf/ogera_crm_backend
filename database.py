"""
Database session setup — SQLModel + SQLAlchemy, PostgreSQL on Vercel.

Three session helpers:
  get_public_db()            FastAPI Depends — login only (queries public.employee_tenant_map)
  get_session_for_schema()   context manager — opens a session for a specific tenant schema
  get_db()                   legacy FastAPI Depends — still used by non-auth routes (TODO: replace)

SQLite: used by the test suite only (DATABASE_URL=sqlite:///:memory:).
"""
import os
import re
from contextlib import contextmanager

from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.main import default_registry
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from dotenv import load_dotenv

load_dotenv()

# ── PostgreSQL connection settings ────────────────────────────────────────────
# Reads POSTGRES_* vars; DATABASE_URL env var overrides everything (used by tests).
POSTGRES_USER     = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_HOST     = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT     = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB       = os.getenv("POSTGRES_DB", "ogera_crm")

DATABASE_URL = os.getenv("DATABASE_URL") or (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Set SQL_ECHO=1 in .env to print every SQL query — useful when debugging.
SQL_ECHO = os.getenv("SQL_ECHO", "0") == "1"


# ── Engine creation ───────────────────────────────────────────────────────────
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
    if os.getenv("VERCEL"):
        # Vercel serverless: each function invocation is stateless — no persistent process
        # to keep a pool in, so NullPool (open-and-close per request) is the only safe option.
        engine = create_engine(
            DATABASE_URL,
            echo=SQL_ECHO,
            poolclass=NullPool,
            connect_args={"connect_timeout": 10},
        )
    else:
        # Persistent server (local dev, Docker, cloud VM):
        # reuse connections across requests so each request does not pay the TCP
        # handshake + DB auth cost on every call.
        # pool_pre_ping=True tests each connection before use — prevents stale-connection
        # errors after the DB server restarted or dropped an idle connection.
        engine = create_engine(
            DATABASE_URL,
            echo=SQL_ECHO,
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10},
        )


# ── Schema name validation ────────────────────────────────────────────────────
# PostgreSQL schema names are used in SET search_path TO {schema}.
# Because that is a raw SQL string (SQLAlchemy text() does not parameterize identifiers),
# we validate the name before use to prevent SQL injection.
# Valid: "tenant_ogera", "tenant_abc123"
# Rejected (→ ValueError): "public; DROP TABLE employees--", "TENANT", "tenant ogera"
_SCHEMA_RE = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


def _validate_schema_name(schema: str) -> str:
    """
    SQL injection guard for schema names used in SET search_path.

    Why this matters: SET search_path TO {schema} is not a parameterized query —
    PostgreSQL does not support binding an identifier as a parameter. We build the
    string ourselves, so we must enforce the format before it touches SQL.

    Allowed: lowercase letters, digits, underscores; starts with a letter; max 63 chars.
    Raises ValueError if the format is wrong (caller converts this to 401).
    """
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"Invalid schema name: {schema!r}")
    return schema


# ── Session factories ─────────────────────────────────────────────────────────

# Kept for the scheduler, scripts, and all non-auth routes during the incremental
# migration. New code should use get_public_db() or get_session_for_schema().
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Base class for models not yet migrated to SQLModel.
# Shares SQLModel's registry so old-style Base models and SQLModel models can
# reference each other's relationships without "mapper not found" errors.
class Base(DeclarativeBase):
    registry = default_registry


def get_db():
    """
    Legacy FastAPI dependency — one Session per request, auto-closed when done.

    Still used by the scheduler and all non-auth routes while the full multi-tenant
    migration is in progress. Points at the default PostgreSQL schema.

    How to use in a route:
        db: Session = Depends(get_db)
    """
    with Session(engine, expire_on_commit=False) as db:
        yield db


def get_public_db():
    """
    FastAPI dependency — session for the public schema.

    Used by the login endpoint to query public.employee_lookup.
    The EmployeeLookup model already hard-codes schema="public" in __table_args__,
    so no SET search_path is needed here — the table is always fully qualified.

    How to use in a route:
        db_public: Session = Depends(get_public_db)
    """
    with Session(engine, expire_on_commit=False) as db:
        yield db


@contextmanager
def get_public_db_ctx():
    """
    Context manager version of get_public_db — for use outside of FastAPI Depends.

    Use this when you need a public-schema session in regular code (not a route),
    for example in the lead_inbound router's auth helper which runs before the
    tenant schema is known.

    How to use:
        with get_public_db_ctx() as db:
            tenant = db.scalar(select(Tenant).where(...))
    """
    with Session(engine, expire_on_commit=False) as db:
        yield db


@contextmanager
def get_session_for_schema(schema: str):
    """
    Context manager — yields a Session scoped to the given tenant schema.

    How it works:
      1. Validates the schema name (raises ValueError → caller returns 401).
      2. Opens a raw connection and runs SET search_path TO {schema}.
         After this, any unqualified table name like "employees" resolves to
         {schema}.employees instead of public.employees.
      3. Wraps the connection in a SQLModel Session and yields it.
      4. The connection is returned to the pool when the with-block exits.

    Why not Depends()?
      The schema is only known after decoding the JWT or querying employee_lookup,
      which happens inside the route/dependency — too late for FastAPI's startup-time
      injection. So we use a plain context manager instead.

    Usage:
        with get_session_for_schema("tenant_ogera") as db:
            employee = db.scalar(select(Employee).where(...))
    """
    _validate_schema_name(schema)   # raises ValueError if name is unsafe
    with engine.connect() as conn:
        # SET search_path is PostgreSQL-only; SQLite (used by the test suite) has no schemas.
        if not DATABASE_URL.startswith("sqlite"):
            conn.execute(text(f"SET search_path TO {schema}"))
            # Ends the SET's transaction, so the session below owns its own: db.commit() then
            # reaches the database before the response, not after it.
            conn.commit()
        with Session(bind=conn, expire_on_commit=False) as db:
            yield db
        # db.commit() inside the route flushes changes to the connection but does NOT
        # commit the connection's own transaction. conn.commit() here does that.
        # On any exception the with engine.connect() block rolls back automatically.
        conn.commit()


