"""Forgot password: emailed code -> verify -> new password, for any active employee. The mailer is stubbed; no email is sent."""
import datetime

import pytest

from database import SessionLocal
from models import Employee, PasswordResetCode
from routes import password_reset
from seed_data import PASSWORD, email_of


@pytest.fixture()
def reset(client, monkeypatch):
    """Captures emailed codes and lets a test move the clock forward."""
    # Start at the real UTC time: the reset token expiry is checked against the real clock by jose.
    clock = {"now": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)}
    sent: list[dict] = []

    def fake_send(name, email, code, expires_minutes):
        sent.append({"name": name, "email": email, "code": code, "expires_minutes": expires_minutes})
        return {"sent": True, "message": "Reset code sent"}

    notices: list[dict] = []

    def fake_notice(name, email):
        notices.append({"name": name, "email": email})
        return {"sent": True, "message": "Notice sent"}

    monkeypatch.setattr(password_reset, "_now", lambda: clock["now"])
    monkeypatch.setattr(password_reset, "send_password_reset_code", fake_send)
    monkeypatch.setattr(password_reset, "send_password_changed_notice", fake_notice)

    class Flow:
        def __init__(self):
            self.client = client
            self.sent = sent
            self.clock = clock

        def advance(self, **delta):
            self.clock["now"] += datetime.timedelta(**delta)

        def request(self, key="anu", email=None):
            return client.post("/auth/password-reset/request", json={"email": email or email_of(key)})

        def verify(self, code, key="anu", email=None):
            return client.post("/auth/password-reset/verify", json={"email": email or email_of(key), "code": code})

        def reset_password(self, token, new_password):
            return client.post("/auth/password-reset/reset", json={"reset_token": token, "new_password": new_password})

        def login(self, password, key="anu"):
            return client.post("/auth/login", json={"email": email_of(key), "password": password})

        def last_code(self):
            return self.sent[-1]["code"]

    flow = Flow()
    flow.notices = notices
    return flow


def test_request_emails_the_code_to_the_employees_own_address_and_masks_it(reset):
    response = reset.request("anu")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email_masked"].endswith("@example.com") and "***" in body["email_masked"]
    assert body["expires_in_seconds"] == 600 and body["resend_in_seconds"] == 60
    assert len(reset.sent) == 1
    assert reset.sent[0]["email"] == email_of("anu")
    assert len(reset.last_code()) == 6 and reset.last_code().isdigit()


def test_admins_can_still_reset_their_password(reset):
    assert reset.request("admin").status_code == 200
    assert reset.sent[0]["email"] == email_of("admin")


def test_email_is_matched_ignoring_case_and_spaces(reset):
    response = reset.request(email="  ANU@Example.COM ")

    assert response.status_code == 200, response.text
    assert reset.sent[0]["email"] == email_of("anu")


def test_unknown_email_returns_404_and_sends_nothing(reset):
    response = reset.request(email="nobody@example.com")

    assert response.status_code == 404
    assert reset.sent == []


def test_deactivated_account_cannot_request_a_code(reset):
    with SessionLocal() as db:
        db.query(Employee).filter(Employee.email == email_of("anu")).update({Employee.is_active: 0})
        db.commit()

    assert reset.request("anu").status_code == 404
    assert reset.sent == []


def test_code_is_stored_only_as_a_hash(reset):
    reset.request("anu")

    with SessionLocal() as db:
        row = db.query(PasswordResetCode).one()
        assert reset.last_code() not in row.code_hash
        assert len(row.code_hash) == 64


def test_resend_is_blocked_for_60_seconds(reset):
    reset.request("anu")

    early = reset.request("anu")
    assert early.status_code == 429
    assert "seconds" in early.json()["detail"]

    reset.advance(seconds=61)
    assert reset.request("anu").status_code == 200


def test_cooldown_is_per_employee(reset):
    assert reset.request("anu").status_code == 200
    assert reset.request("raj").status_code == 200  # a different employee is not held back


def test_a_new_code_makes_the_previous_one_useless(reset):
    reset.request("anu")
    first = reset.last_code()
    reset.advance(seconds=61)
    reset.request("anu")
    second = reset.last_code()
    assert first != second

    assert reset.verify(first).status_code == 400
    assert reset.verify(second).status_code == 200


def test_a_code_sent_to_one_employee_cannot_reset_another(reset):
    reset.request("anu")

    response = reset.verify(reset.last_code(), key="raj")

    assert response.status_code == 400
    assert response.json()["detail"] == "No active code. Request a new code."


def test_mail_failure_returns_503_and_leaves_no_usable_code(reset, monkeypatch):
    monkeypatch.setattr(
        password_reset, "send_password_reset_code",
        lambda *a, **k: {"sent": False, "message": "Reset code not sent: could not reach the mail server"},
    )

    response = reset.request("anu")

    assert response.status_code == 503
    assert "could not reach the mail server" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.query(PasswordResetCode).count() == 0


def test_correct_code_gives_a_reset_token_that_works_once(reset):
    reset.request("anu")

    verified = reset.verify(reset.last_code())
    assert verified.status_code == 200, verified.text
    token = verified.json()["reset_token"]

    assert reset.verify(reset.last_code()).status_code == 400  # the code is single-use
    assert reset.reset_password(token, "NewPass123").status_code == 200
    assert reset.reset_password(token, "AnotherPass1").status_code == 400  # the token is single-use


def test_wrong_code_reports_attempts_left_then_locks_after_five(reset):
    reset.request("anu")
    correct = reset.last_code()
    wrong = "000000" if correct != "000000" else "111111"

    for left in (4, 3, 2, 1):
        response = reset.verify(wrong)
        assert response.status_code == 400
        assert response.json()["detail"] == f"Incorrect code. {left} attempts left."

    last = reset.verify(wrong)
    assert last.json()["detail"] == "Too many wrong attempts. Request a new code."
    # Even the right code is refused once the code is locked.
    assert reset.verify(correct).status_code == 400


def test_a_new_code_resets_the_attempt_counter(reset):
    reset.request("anu")
    correct = reset.last_code()
    wrong = "000000" if correct != "000000" else "111111"
    for _ in range(3):
        reset.verify(wrong)

    reset.advance(seconds=61)
    reset.request("anu")
    assert reset.verify(reset.last_code()).status_code == 200


def test_expired_code_is_rejected(reset):
    reset.request("anu")
    correct = reset.last_code()

    reset.advance(minutes=10, seconds=1)

    response = reset.verify(correct)
    assert response.status_code == 400
    assert response.json()["detail"] == "This code has expired. Request a new code."


def test_verify_without_a_request_is_rejected(reset):
    response = reset.verify("123456")
    assert response.status_code == 400
    assert response.json()["detail"] == "No active code. Request a new code."


def test_reset_changes_the_password_and_the_old_one_stops_working(reset):
    reset.request("anu")
    token = reset.verify(reset.last_code()).json()["reset_token"]

    assert reset.reset_password(token, "NewPass123").status_code == 200

    assert reset.login("NewPass123").status_code == 200
    assert reset.login(PASSWORD).status_code == 401


def test_a_successful_reset_emails_the_employee(reset):
    reset.request("anu")
    token = reset.verify(reset.last_code()).json()["reset_token"]

    assert reset.reset_password(token, "NewPass123").status_code == 200

    assert reset.notices == [{"name": "Anu Sales", "email": email_of("anu")}]


def test_a_failed_notice_does_not_undo_the_reset(reset, monkeypatch):
    monkeypatch.setattr(password_reset, "send_password_changed_notice",
                        lambda *a, **k: {"sent": False, "message": "Notice not sent"})
    reset.request("anu")
    token = reset.verify(reset.last_code()).json()["reset_token"]

    assert reset.reset_password(token, "NewPass123").status_code == 200
    assert reset.login("NewPass123").status_code == 200


def test_new_password_must_differ_from_the_current_one(reset):
    reset.request("anu")
    token = reset.verify(reset.last_code()).json()["reset_token"]

    response = reset.reset_password(token, PASSWORD)

    assert response.status_code == 400
    assert response.json()["detail"] == "Your new password must be different from your current password."
    # The token is still valid after a rejected attempt, so the user can pick another password.
    assert reset.reset_password(token, "Different1").status_code == 200


@pytest.mark.parametrize("password, message", [
    ("short", "Use at least 6 characters."),
    ("x" * 16, "Use at most 15 characters."),
])
def test_password_length_is_validated(reset, password, message):
    reset.request("anu")
    token = reset.verify(reset.last_code()).json()["reset_token"]

    response = reset.reset_password(token, password)

    assert response.status_code == 400
    assert response.json()["detail"] == message


def test_reset_token_expires_after_ten_minutes(reset, monkeypatch):
    # Issue a token that is already past its expiry, since jose checks exp against the real clock.
    monkeypatch.setattr(password_reset, "RESET_TOKEN_TTL", datetime.timedelta(seconds=-1))
    reset.request("anu")
    token = reset.verify(reset.last_code()).json()["reset_token"]

    response = reset.reset_password(token, "NewPass123")
    assert response.status_code == 400
    assert response.json()["detail"] == "This reset link has expired or was already used. Start again."


def test_a_login_token_cannot_be_used_as_a_reset_token(reset):
    login = reset.login(PASSWORD).json()["access_token"]

    response = reset.reset_password(login, "NewPass123")

    assert response.status_code == 400
    assert reset.login(PASSWORD).status_code == 200


def test_reset_token_from_before_a_password_change_is_rejected(reset):
    reset.request("anu")
    token = reset.verify(reset.last_code()).json()["reset_token"]
    assert reset.reset_password(token, "NewPass123").status_code == 200

    # Another code and token pair, but the old token must still fail.
    reset.advance(seconds=61)
    reset.request("anu")
    assert reset.reset_password(token, "ThirdPass123").status_code == 400
