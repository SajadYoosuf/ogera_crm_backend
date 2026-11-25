"""WhatsApp connection settings: /whatsapp/config.

Meta is never called for real: the HTTP client in the service is replaced with a fake.
Rules checked here:
  - secrets are stored encrypted and never appear in any response
  - leaving a secret blank on update keeps the saved one
  - changing the account resets the connection status
  - roles without "integrations" update permission cannot read-modify the settings
  - a WABA id cannot be claimed by two tenants
"""
import base64
import os

import pytest
from sqlalchemy import select

import services.whatsapp_config_service as svc
from database import SessionLocal
from models.whatsapp_config import WhatsAppConfig, WhatsAppTenantMap
from services.crypto import CryptoError, decrypt_secret, encrypt_secret, mask_secret

TOKEN = "EAAGsecrettokenvalue1234567890x7Qz"
BODY = {
    "phone_number_id": "1234567890123",
    "waba_id": "9876543210987",
    "app_id": "1112223334445",
    "access_token": TOKEN,
    "app_secret": "appsecretvalue",
}


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def fake_meta(monkeypatch, phone=None, waba=None):
    """Replace httpx.Client in the service. Returns the list of (url, auth header) it was called with."""
    calls = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            calls.append((url, (headers or {}).get("Authorization")))
            if url.endswith(BODY["phone_number_id"]):
                return phone or FakeResponse(200, {
                    "display_phone_number": "+91 90000 00001", "verified_name": "Ventino", "quality_rating": "GREEN"})
            return waba or FakeResponse(200, {"id": BODY["waba_id"]})

        def post(self, url, headers=None):
            calls.append((url, (headers or {}).get("Authorization")))
            return FakeResponse(200, {"success": True})

    monkeypatch.setattr(svc.httpx, "Client", FakeClient)
    return calls


def test_crypto_round_trip_and_tamper_detection():
    blob = encrypt_secret("hello")
    assert blob != "hello" and decrypt_secret(blob) == "hello"
    assert encrypt_secret("hello") != blob, "each value must get its own nonce"
    with pytest.raises(CryptoError):
        decrypt_secret(blob[:-4] + "AAAA")


def test_crypto_needs_a_valid_key(monkeypatch):
    monkeypatch.delenv("WHATSAPP_ENCRYPTION_KEY")
    with pytest.raises(CryptoError):
        encrypt_secret("x")
    monkeypatch.setenv("WHATSAPP_ENCRYPTION_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(CryptoError):
        encrypt_secret("x")


def test_mask_hides_short_values_and_shows_ends_of_long_ones():
    assert mask_secret("short") == "••••••••"
    assert mask_secret(TOKEN) == "EAAG…x7Qz"


def test_unconfigured_tenant_gets_an_empty_config_with_a_webhook_url(client, login):
    headers, _ = login("admin")
    body = client.get("/whatsapp/config", headers=headers).json()
    assert body["configured"] is False
    assert body["webhook_url"].endswith("/webhooks/whatsapp")


def test_save_stores_secrets_encrypted_and_never_returns_them(client, login):
    headers, _ = login("admin")
    response = client.put("/whatsapp/config", headers=headers, json=BODY)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["configured"] and body["has_access_token"] and body["has_app_secret"]
    assert body["access_token_hint"] == "EAAG…x7Qz"
    assert body["verify_token"], "a verify token is generated on first save"
    assert body["status"] == "disconnected"
    assert TOKEN not in response.text and "appsecretvalue" not in response.text

    with SessionLocal() as db:
        row = db.scalar(select(WhatsAppConfig))
        assert TOKEN not in row.access_token_enc
        assert decrypt_secret(row.access_token_enc) == TOKEN
        assert decrypt_secret(row.app_secret_enc) == "appsecretvalue"
        mapping = db.get(WhatsAppTenantMap, BODY["waba_id"])
        assert mapping.schema_name == "test"


def test_first_save_requires_the_access_token(client, login):
    headers, _ = login("admin")
    without = {k: v for k, v in BODY.items() if k != "access_token"}
    assert client.put("/whatsapp/config", headers=headers, json=without).status_code == 422


def test_blank_secret_on_update_keeps_the_saved_one(client, login):
    headers, _ = login("admin")
    client.put("/whatsapp/config", headers=headers, json=BODY)
    update = {**BODY, "access_token": "", "app_secret": None, "api_version": "v22.0"}
    body = client.put("/whatsapp/config", headers=headers, json=update).json()
    assert body["api_version"] == "v22.0" and body["has_access_token"] and body["has_app_secret"]
    with SessionLocal() as db:
        assert decrypt_secret(db.scalar(select(WhatsAppConfig)).access_token_enc) == TOKEN


def test_invalid_ids_are_rejected(client, login):
    headers, _ = login("admin")
    assert client.put("/whatsapp/config", headers=headers, json={**BODY, "waba_id": "abc"}).status_code == 422
    assert client.put("/whatsapp/config", headers=headers, json={**BODY, "api_version": "21"}).status_code == 422


def test_test_connection_marks_connected_and_stores_what_meta_says(client, login, monkeypatch):
    headers, _ = login("admin")
    client.put("/whatsapp/config", headers=headers, json=BODY)
    calls = fake_meta(monkeypatch)

    result = client.post("/whatsapp/config/test", headers=headers).json()
    assert result["ok"] is True and result["status"] == "connected"
    assert result["config"]["display_phone_number"] == "+91 90000 00001"
    assert result["config"]["quality_rating"] == "GREEN"
    assert all(auth == f"Bearer {TOKEN}" for _, auth in calls)


def test_test_connection_failure_is_recorded_without_leaking_the_token(client, login, monkeypatch):
    headers, _ = login("admin")
    client.put("/whatsapp/config", headers=headers, json=BODY)
    fake_meta(monkeypatch, phone=FakeResponse(401, {"error": {"message": "Invalid OAuth access token"}}))

    result = client.post("/whatsapp/config/test", headers=headers).json()
    assert result["ok"] is False and result["status"] == "error"
    assert "Invalid OAuth access token" in result["config"]["last_error"]
    assert TOKEN not in str(result)


def test_changing_the_account_resets_a_connected_status(client, login, monkeypatch):
    headers, _ = login("admin")
    client.put("/whatsapp/config", headers=headers, json=BODY)
    fake_meta(monkeypatch)
    client.post("/whatsapp/config/test", headers=headers)

    body = client.put("/whatsapp/config", headers=headers, json={**BODY, "phone_number_id": "5550001112223", "access_token": ""}).json()
    assert body["status"] == "disconnected" and body["display_phone_number"] is None


def test_changing_the_waba_moves_the_tenant_map_entry(client, login):
    headers, _ = login("admin")
    client.put("/whatsapp/config", headers=headers, json=BODY)
    client.put("/whatsapp/config", headers=headers, json={**BODY, "waba_id": "5550009998887"})
    with SessionLocal() as db:
        ids = [m.waba_id for m in db.scalars(select(WhatsAppTenantMap))]
    assert ids == ["5550009998887"]


def test_a_waba_claimed_by_another_tenant_is_refused(client, login):
    with SessionLocal() as db:
        db.add(WhatsAppTenantMap(waba_id=BODY["waba_id"], phone_number_id="0000000000001", schema_name="other"))
        db.commit()
    headers, _ = login("admin")
    response = client.put("/whatsapp/config", headers=headers, json=BODY)
    assert response.status_code == 409, response.text


def test_rotate_verify_token_changes_it(client, login):
    headers, _ = login("admin")
    first = client.put("/whatsapp/config", headers=headers, json=BODY).json()["verify_token"]
    second = client.post("/whatsapp/config/rotate-verify-token", headers=headers).json()["verify_token"]
    assert first != second


def test_subscribe_calls_meta_with_the_waba_id(client, login, monkeypatch):
    headers, _ = login("admin")
    client.put("/whatsapp/config", headers=headers, json=BODY)
    calls = fake_meta(monkeypatch)
    assert client.post("/whatsapp/config/subscribe", headers=headers).json()["ok"] is True
    assert calls[-1][0].endswith(f"/{BODY['waba_id']}/subscribed_apps")


def test_delete_removes_credentials_and_the_tenant_map(client, login):
    headers, _ = login("admin")
    client.put("/whatsapp/config", headers=headers, json=BODY)
    assert client.delete("/whatsapp/config", headers=headers).status_code == 204
    assert client.get("/whatsapp/config", headers=headers).json()["configured"] is False
    with SessionLocal() as db:
        assert db.scalar(select(WhatsAppTenantMap)) is None


def test_permissions_manager_can_view_and_update_but_sales_cannot_touch_it(client, login):
    manager, _ = login("manager")
    assert client.get("/whatsapp/config", headers=manager).status_code == 200
    assert client.put("/whatsapp/config", headers=manager, json=BODY).status_code == 200

    sales, _ = login("anu")
    assert client.get("/whatsapp/config", headers=sales).status_code == 403
    assert client.put("/whatsapp/config", headers=sales, json=BODY).status_code == 403
    assert client.delete("/whatsapp/config", headers=sales).status_code == 403


def test_requires_login(client):
    assert client.get("/whatsapp/config").status_code in (401, 403)
