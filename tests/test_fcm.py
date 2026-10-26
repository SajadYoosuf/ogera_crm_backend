from database import SessionLocal
from models import FcmDevice
from services import notification_service


def test_register_and_unregister_fcm_device(client, login):
    headers, employee = login("anu")
    token = "test-token-for-anu"

    registered = client.post(
        "/employees/me/fcm-token",
        headers=headers,
        json={"token": token, "platform": "android", "device_id": "phone-1"},
    )
    assert registered.status_code == 200, registered.text

    with SessionLocal() as db:
        device = db.query(FcmDevice).filter(FcmDevice.token == token).one()
        assert device.employee_id == employee["id"]
        assert device.platform == "android"

    removed = client.request(
        "DELETE", "/employees/me/fcm-token", headers=headers, json={"token": token}
    )
    assert removed.status_code == 200, removed.text
    with SessionLocal() as db:
        assert db.query(FcmDevice).filter(FcmDevice.token == token).first() is None


def test_notification_sends_to_all_registered_devices(client, login, monkeypatch):
    headers, employee = login("anu")
    for token in ("web-token", "phone-token"):
        response = client.post("/employees/me/fcm-token", headers=headers, json={"token": token})
        assert response.status_code == 200

    sent = []
    monkeypatch.setattr(
        notification_service,
        "send_fcm",
        lambda token, *args, **kwargs: sent.append(token) or True,
    )
    with SessionLocal() as db:
        notification_service.create_notification(db, employee["id"], "test", "Test", "Hello")
    assert set(sent) == {"web-token", "phone-token"}
