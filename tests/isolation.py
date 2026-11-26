"""Keeps the pytest suite and the end-to-end server away from live services.

Both must call `isolate()` before importing any app module: several modules read their credentials once,
at import time. Without it, `.env` is loaded and a test can send a real WhatsApp message, email or push,
or point `Base.metadata.drop_all` at a real database.
"""
import ipaddress
import os
import socket
from collections.abc import MutableMapping

# Set to "" rather than removed, so that nothing (such as load_dotenv) fills them in again.
CREDENTIALS = (
    "DB_HOST", "DB_PASSWORD", "POSTGRES_HOST", "POSTGRES_PASSWORD",
    "FIREBASE_SERVICE_ACCOUNT_JSON", "GOOGLE_APPLICATION_CREDENTIALS",
    "SMTP_FROM", "SMTP_HOST", "SMTP_PASSWORD", "SMTP_USER",
    "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_ID", "WHATSAPP_APP_SECRET", "WHATSAPP_DEFAULT_SCHEMA",
    "WHATSAPP_ENCRYPTION_KEY", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_TEMPLATE_SENDING_ENABLED",
    "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_WELCOME_HEADER_IMAGE",
)

# Every refused connection, as "host" strings, so a test run can report what tried to get out.
blocked_hosts: list[str] = []


class LiveNetworkBlocked(OSError):
    """An OSError, so the app handles it like any other network failure."""


def isolate_environment(env: MutableMapping[str, str] = os.environ) -> None:
    """Ignore `.env`, blank every credential and use a throwaway database, whatever the shell has set."""
    env["PYTHON_DOTENV_DISABLED"] = "1"
    for name in CREDENTIALS:
        env[name] = ""
    env.pop("SECRET_KEY", None)  # the app's built-in development key
    env["DATABASE_URL"] = "sqlite://"  # never inherited: the tests drop every table
    env["SQL_ECHO"] = "0"


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if host in ("", "localhost"):
        return True
    try:
        address = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _check(host) -> None:
    if not _is_local(host):
        blocked_hosts.append(str(host))
        raise LiveNetworkBlocked(f"Tests may not connect to {host!r}. Mock the call instead.")


def block_network() -> None:
    """Refuse every connection that leaves this machine. Loopback stays open. Safe to call twice."""
    if getattr(socket, "_ogera_network_blocked", False):
        return
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        _check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def _check_address(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            _check(address[0])

    def connect(sock, address):
        _check_address(sock, address)
        return real_connect(sock, address)

    def connect_ex(sock, address):
        _check_address(sock, address)
        return real_connect_ex(sock, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket._ogera_network_blocked = True


def isolate() -> None:
    isolate_environment()
    block_network()
