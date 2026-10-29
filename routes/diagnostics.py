import time
from fastapi import APIRouter
from sqlalchemy import text
# Import your existing engine and Base from your database config file
from database import engine, Base

router = APIRouter(prefix="/diagnostics", tags=["Diagnostics"])


@router.get("/db-check")
def db_check():
    """
    Tests SQLAlchemy query execution and inspects tables.
    """
    start_time = time.time()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            ping_time = round((time.time() - start_time) * 1000, 2)

            server_info = connection.execute(
                text("SELECT CURRENT_USER(), DATABASE(), VERSION()")
            ).fetchone()

            tables_result = connection.execute(text("SHOW TABLES;")).fetchall()
            existing_tables = [row[0] for row in tables_result]

        return {
            "status": "HEALTHY",
            "latency_ms": f"{ping_time} ms",
            "connected_user": server_info[0],
            "database_name": server_info[1],
            "mysql_version": server_info[2],
            "tables_found_in_db": existing_tables,
            "registered_models_in_code": list(Base.metadata.tables.keys())
        }

    except Exception as e:
        elapsed = round((time.time() - start_time) * 1000, 2)
        return {
            "status": "FAILED",
            "elapsed_before_failure_ms": f"{elapsed} ms",
            "error_type": type(e).__name__,
            "error_message": str(e)
        }
