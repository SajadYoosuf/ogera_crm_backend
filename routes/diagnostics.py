import time

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from database import Base, engine


router = APIRouter(prefix="/diagnostics", tags=["Diagnostics"])


@router.get("/db-check")
def diagnose_database_connection():
    """Check database connectivity, server details, and existing tables."""
    start_time = time.time()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            ping_time = round((time.time() - start_time) * 1000, 2)

            server_info = connection.execute(
                text("SELECT CURRENT_USER(), DATABASE(), VERSION()")
            ).fetchone()

            tables_result = connection.execute(text("SHOW TABLES")).fetchall()
            existing_tables = [row[0] for row in tables_result]

        return {
            "status": "HEALTHY",
            "latency_ms": f"{ping_time} ms",
            "connected_user": server_info[0],
            "database_name": server_info[1],
            "mysql_version": server_info[2],
            "tables_found_in_db": existing_tables,
            "registered_models_in_code": list(Base.metadata.tables.keys()),
        }

    except Exception as exc:
        elapsed = round((time.time() - start_time) * 1000, 2)
        return {
            "status": "FAILED",
            "elapsed_before_failure_ms": f"{elapsed} ms",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


@router.post("/run-table-creation")
def trigger_table_creation():
    """Create any missing registered tables and report the resulting tables."""
    start_time = time.time()
    try:
        Base.metadata.create_all(bind=engine)
        elapsed = round((time.time() - start_time) * 1000, 2)

        with engine.connect() as connection:
            tables_result = connection.execute(text("SHOW TABLES")).fetchall()
            current_tables = [row[0] for row in tables_result]

        return {
            "status": "SUCCESS",
            "execution_time_ms": f"{elapsed} ms",
            "models_synced": list(Base.metadata.tables.keys()),
            "tables_in_mysql": current_tables,
        }

    except Exception as exc:
        elapsed = round((time.time() - start_time) * 1000, 2)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "DDL_CREATION_FAILED",
                "elapsed_time_ms": f"{elapsed} ms",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        ) from exc
