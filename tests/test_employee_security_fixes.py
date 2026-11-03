"""
Tests for three security/integrity fixes applied 2026-11-03:

  Issue 3 — Phone uniqueness: phones are normalized to last 10 digits on input
            and a unique DB constraint now enforces no two active employees share
            a number.

  Issue 5 — IDOR on GET /employees/{id}: a sales executive must not be able to
            read another employee's full profile (name, email, phone, branch, role).
            Self-lookup must still work.

  Issue 6 — FCM devices left live after soft delete: deleting an employee must
            remove all their fcm_devices rows so they stop receiving push
            notifications.

Each section is clearly labeled so you can find what broke if a test fails.
"""

import pytest
from seed_data import MOBILE_APP
from database import SessionLocal
from models import FcmDevice


@pytest.fixture()
def no_mail(monkeypatch):
    """Stub out the mailer so tests that call POST /employees don't need SMTP configured."""
    from routes import employees as emp_routes
    monkeypatch.setattr(emp_routes, "send_employee_credentials", lambda *a, **k: None)
    monkeypatch.setattr(emp_routes, "send_password_changed_notice", lambda *a, **k: None)


# ===========================================================================
# Issue 3 — Phone normalization and DB uniqueness
# ===========================================================================

class TestPhoneNormalization:
    """Phones submitted with +91 country code, spaces, or dashes must be stored
    as exactly 10 digits. The response should reflect the normalized value."""

    def test_phone_with_country_code_is_stored_normalized(self, client, login, no_mail):
        """POST with '+919800000091' → stored and returned as '9800000091'."""
        headers, _ = login("admin")
        resp = client.post("/employees", json={
            "name": "Normalize Test",
            "email": "normalize.test@example.com",
            "password": "Passw0rd!",
            "role": "sales_executive",
            "phone": "+919800000091",
        }, headers=headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["phone"] == "9800000091"

    def test_phone_with_spaces_and_dashes_is_normalized(self, client, login, no_mail):
        """'+91 980-000-0092' → '9800000092'."""
        headers, _ = login("admin")
        resp = client.post("/employees", json={
            "name": "Format Test",
            "email": "format.test@example.com",
            "password": "Passw0rd!",
            "role": "sales_executive",
            "phone": "+91 980-000-0092",
        }, headers=headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["phone"] == "9800000092"

    def test_country_code_variant_is_rejected_as_duplicate(self, client, login, no_mail):
        """'+919000000003' has the same last 10 digits as seed employee 'anu' (9000000003).
        It must be rejected with 409, not silently stored as a different number."""
        headers, _ = login("admin")
        resp = client.post("/employees", json={
            "name": "Country Code Dup",
            "email": "cc.dup@example.com",
            "password": "Passw0rd!",
            "role": "sales_executive",
            "phone": "+919000000003",
        }, headers=headers)
        assert resp.status_code == 409
        assert "phone" in resp.json()["detail"].lower()

    def test_update_with_country_code_variant_is_rejected_as_duplicate(self, client, login):
        """Updating raj's phone to '+91 900 000 0003' (same last 10 as anu) must be 409."""
        admin_headers, _ = login("admin")
        _, raj = login("raj")
        resp = client.put(f"/employees/{raj['id']}", json={"phone": "+91 900 000 0003"}, headers=admin_headers)
        assert resp.status_code == 409
        assert "phone" in resp.json()["detail"].lower()

    def test_update_keeping_own_phone_with_country_code_is_allowed(self, client, login):
        """Sending your own phone with +91 prefix during an update must not self-collide."""
        admin_headers, _ = login("admin")
        _, raj = login("raj")
        # raj's stored phone is 9000000004 — sending +919000000004 should be accepted
        resp = client.put(f"/employees/{raj['id']}", json={"phone": "+919000000004", "name": "Raj Renamed"}, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["phone"] == "9000000004"


# ===========================================================================
# Issue 5 — IDOR: GET /employees/{id} access control
# ===========================================================================

class TestGetEmployeeIDAR:
    """Any authenticated employee could previously read any other employee's
    full profile. Now only admin/manager may do so; others may only read their
    own record."""

    def test_sales_exec_cannot_fetch_another_employees_profile(self, client, login):
        """anu (sales_executive) fetching raj's profile must return 403."""
        admin_headers, _ = login("admin")
        _, raj = login("raj")

        anu_headers, _ = login("anu")
        resp = client.get(f"/employees/{raj['id']}", headers=anu_headers)
        assert resp.status_code == 403, resp.text

    def test_sales_exec_can_fetch_own_profile(self, client, login):
        """anu must be able to read their own record — self-lookup is always allowed."""
        anu_headers, anu = login("anu")
        resp = client.get(f"/employees/{anu['id']}", headers={**anu_headers, **MOBILE_APP})
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == anu["id"]

    def test_manager_can_fetch_another_employees_profile(self, client, login):
        """A manager must be able to look up any visible employee by id."""
        manager_headers, _ = login("manager")
        _, raj = login("raj")
        resp = client.get(f"/employees/{raj['id']}", headers=manager_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == raj["id"]

    def test_admin_can_fetch_another_employees_profile(self, client, login):
        admin_headers, _ = login("admin")
        _, raj = login("raj")
        resp = client.get(f"/employees/{raj['id']}", headers=admin_headers)
        assert resp.status_code == 200, resp.text

    def test_unauthenticated_request_is_rejected(self, client, login):
        _, raj = login("raj")
        assert client.get(f"/employees/{raj['id']}").status_code == 401


# ===========================================================================
# Issue 6 — FCM devices cleared on soft delete
# ===========================================================================

class TestSoftDeleteClearsFcmDevices:
    """When an employee is soft-deleted their fcm_devices rows must be removed
    so push notifications are no longer delivered to their devices."""

    def _register_token(self, client, headers, token: str):
        resp = client.post("/employees/me/fcm-token", json={"token": token, "platform": "android"}, headers=headers)
        assert resp.status_code == 200, resp.text

    def _device_count(self, employee_id: int) -> int:
        with SessionLocal() as db:
            return db.query(FcmDevice).filter(FcmDevice.employee_id == employee_id).count()

    def test_fcm_devices_removed_on_soft_delete(self, client, login):
        """Register a device for raj, soft-delete raj, then verify the row is gone."""
        admin_headers, _ = login("admin")
        raj_headers, raj = login("raj")

        # Register a push token for raj
        self._register_token(client, {**raj_headers, **MOBILE_APP}, "fake-fcm-token-raj-001")
        assert self._device_count(raj["id"]) == 1

        # Admin soft-deletes raj
        resp = client.delete(f"/employees/{raj['id']}", headers=admin_headers)
        assert resp.status_code == 204

        # FCM device must be gone
        assert self._device_count(raj["id"]) == 0

    def test_multiple_fcm_devices_all_removed_on_soft_delete(self, client, login):
        """Raj registers from two devices — both must be cleared on delete."""
        admin_headers, _ = login("admin")
        raj_headers, raj = login("raj")

        self._register_token(client, {**raj_headers, **MOBILE_APP}, "fake-fcm-token-phone")
        self._register_token(client, {**raj_headers, **MOBILE_APP}, "fake-fcm-token-tablet")
        assert self._device_count(raj["id"]) == 2

        client.delete(f"/employees/{raj['id']}", headers=admin_headers)

        assert self._device_count(raj["id"]) == 0

    def test_deleted_employee_jwt_is_rejected(self, client, login):
        """JWT session is effectively invalidated because get_current_user checks
        deleted_at IS NULL and is_active = 1 on every request."""
        admin_headers, _ = login("admin")
        raj_headers, raj = login("raj")

        client.delete(f"/employees/{raj['id']}", headers=admin_headers)

        # Using raj's still-valid JWT must now return 401
        resp = client.get(f"/employees/{raj['id']}", headers={**raj_headers, **MOBILE_APP})
        assert resp.status_code == 401, resp.text
