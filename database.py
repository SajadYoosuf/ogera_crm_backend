
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv
import os

load_dotenv()

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "ogera_crm")

# DATABASE_URL overrides the MySQL settings above (the automated tests point it at SQLite).
# Disable SSL explicitly for the configured MySQL endpoint.
DATABASE_URL = os.getenv("DATABASE_URL") or f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?ssl_disabled=True"
# Raw SQL printing is off by default; set SQL_ECHO=1 in .env to see every query while debugging.
SQL_ECHO = os.getenv("SQL_ECHO", "0") == "1"

if DATABASE_URL.startswith("sqlite"):
    if DATABASE_URL in ("sqlite://", "sqlite:///:memory:"):
        from sqlalchemy.pool import StaticPool

        # An in-memory database exists only on one connection, so every session shares it.
        # Fine for the pytest suite, which sends one request at a time.
        engine = create_engine(
            DATABASE_URL, echo=SQL_ECHO, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        # A file database gets a connection per session: sharing one across the server's worker
        # threads crashes the process. Writers wait up to 30 s for the lock instead of failing.
        engine = create_engine(
            DATABASE_URL, echo=SQL_ECHO, connect_args={"check_same_thread": False, "timeout": 30}
        )
else:
    # Vercel functions should not keep a process-wide connection pool between
    # invocations. Open and close a fresh MySQL connection per request.
    engine = create_engine(
        DATABASE_URL,
        echo=SQL_ECHO,
        poolclass=NullPool,
        connect_args={"connect_timeout": 10, "ssl_disabled": True},
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
