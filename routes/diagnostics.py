import socket
import time
import urllib.request
from fastapi import APIRouter
from sqlalchemy import text
# Import your existing engine and Base from your database config file
from database import engine, Base

router = APIRouter(prefix="/diagnostics", tags=["Diagnostics"])


@router.get("/render-network-test")
def render_network_test():
    """
    Tests raw socket reachability from Render to Hostinger.
    Bypasses PyMySQL/SQLAlchemy entirely to test the bare network.
    """
    target_ip = "187.127.150.170"
    target_port = 3306

    # 1. Fetch Render container's real outbound egress IP
    try:
        req = urllib.request.Request(
            "https://api.ipify.org", 
            headers={"User-Agent": "Mozilla/5.0"}
        )
        render_egress_ip = urllib.request.urlopen(req, timeout=5).read().decode("utf8")
    except Exception as e:
        render_egress_ip = f"Detection failed: {e}"

    # 2. Test raw TCP handshake with 5s timeout
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    
    start_time = time.time()
    result_code = sock.connect_ex((target_ip, target_port))
    elapsed_ms = round((time.time() - start_time) * 1000, 2)
    sock.close()

    is_connected = (result_code == 0)

    return {
        "render_outbound_ip": render_egress_ip,
        "target": f"{target_ip}:{target_port}",
        "raw_tcp_connected": is_connected,
        "socket_result_code": result_code,
        "socket_time_ms": f"{elapsed_ms} ms",
        "diagnosis": (
            "TCP Network Open: Network reachability is working. Issue is strictly DB credentials, password encoding, or SSL handshake."
            if is_connected
            else f"TCP Packets Blocked: Packets from Render IP ({render_egress_ip}) cannot reach your VPS port 3306."
        )
    }


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