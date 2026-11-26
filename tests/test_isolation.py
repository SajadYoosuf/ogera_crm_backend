"""The suite must not be able to reach a live database or messaging service."""
import socket

import httpx
import pytest

import config
import database
import isolation
from routes import lead_whatsapp
from services import whatsapp_service


def test_live_settings_are_replaced_even_when_the_shell_provides_them():
    env = {name: "live-value" for name in isolation.CREDENTIALS}
    env.update(DATABASE_URL="postgresql+psycopg2://user:pw@db.example.com/crm", SECRET_KEY="live-value")

    isolation.isolate_environment(env)

    assert env["DATABASE_URL"] == "sqlite://"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert "SECRET_KEY" not in env
    assert {env[name] for name in isolation.CREDENTIALS} == {""}


def test_the_app_was_imported_without_credentials():
    assert database.engine.url.get_backend_name() == "sqlite"
    assert (config.SMTP_HOST, config.SMTP_USER, config.SMTP_PASSWORD) == ("", "", "")
    assert config.FIREBASE_SERVICE_ACCOUNT_JSON == ""
    for module in (lead_whatsapp, whatsapp_service):
        assert (module.WHATSAPP_ACCESS_TOKEN, module.WHATSAPP_PHONE_NUMBER_ID) == ("", "")


def test_outside_connections_are_refused():
    before = len(isolation.blocked_hosts)

    with pytest.raises(httpx.ConnectError, match="Tests may not connect"):
        httpx.post("https://graph.facebook.com/v18.0/0/messages", json={}, trust_env=False)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock, pytest.raises(isolation.LiveNetworkBlocked):
        sock.connect(("203.0.113.1", 25))  # a bare address, with no name lookup

    assert isolation.blocked_hosts[before:] == ["graph.facebook.com", "203.0.113.1"]
    del isolation.blocked_hosts[before:]  # deliberate attempts do not belong in the run summary


def test_loopback_connections_still_work():
    with socket.create_server(("127.0.0.1", 0)) as server:
        with socket.create_connection(("127.0.0.1", server.getsockname()[1]), timeout=5) as client:
            assert client.getpeername()[0] == "127.0.0.1"
