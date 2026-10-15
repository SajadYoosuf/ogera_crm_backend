import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from email_validator import EmailNotValidError, validate_email
from config import (
    APP_LOGIN_URL,
    SMTP_FROM,
    SMTP_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_SECURITY,
    SMTP_USER,
)


def _send(message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if SMTP_SECURITY == "ssl":
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15, context=context)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
    with server:
        if SMTP_SECURITY == "starttls":
            server.starttls(context=context)
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)


def send_employee_credentials(name: str, email: str, password: str, role: str) -> dict:
    """Email a new employee their login details and report what happened.

    Never raises. Returns {"sent": bool, "message": str}. `sent=True` means the mail server
    accepted the message: Gmail accepts any recipient and bounces unknown addresses later,
    so only the recipient *domain* can be verified up front.
    """
    if not SMTP_HOST or not SMTP_FROM:
        print(f"[mail] SMTP not configured; credentials email to {email} was NOT sent")
        return {"sent": False, "message": "Email sending is not configured on the server"}

    try:
        validate_email(email, check_deliverability=True)
    except EmailNotValidError as exc:
        print(f"[mail] NOT sending to {email}: {exc}")
        return {"sent": False, "message": f"Credentials email not sent: {exc}"}

    role_label = role.replace("_", " ").title()
    text = (
        f"Hi {name},\n\n"
        f"A {SMTP_FROM_NAME} account has been created for you ({role_label}).\n\n"
        f"Login URL: {APP_LOGIN_URL}\n"
        f"Email:     {email}\n"
        f"Password:  {password}\n\n"
        "Please sign in and keep these details private.\n"
    )
    html = (
        f"<p>Hi {escape(name)},</p>"
        f"<p>A {escape(SMTP_FROM_NAME)} account has been created for you (<b>{escape(role_label)}</b>).</p>"
        f"<p><b>Login URL:</b> <a href=\"{escape(APP_LOGIN_URL)}\">{escape(APP_LOGIN_URL)}</a><br>"
        f"<b>Email:</b> {escape(email)}<br>"
        f"<b>Password:</b> <code>{escape(password)}</code></p>"
        "<p>Please sign in and keep these details private.</p>"
    )

    message = EmailMessage()
    message["Subject"] = f"Your {SMTP_FROM_NAME} login details"
    message["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
    message["To"] = email
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        _send(message)
    except smtplib.SMTPRecipientsRefused:
        print(f"[mail] recipient refused: {email}")
        return {"sent": False, "message": "The mail server refused this email address"}
    except smtplib.SMTPAuthenticationError:
        print("[mail] SMTP login failed - check SMTP_USER / SMTP_PASSWORD")
        return {"sent": False, "message": "Credentials email not sent: mail server login failed"}
    except Exception as exc:  # noqa: BLE001 - must never break the request
        print(f"[mail] FAILED to send credentials email to {email}: {type(exc).__name__}: {exc}")
        return {"sent": False, "message": "Credentials email not sent: could not reach the mail server"}

    print(f"[mail] credentials email handed to mail server for {email}")
    return {
        "sent": True,
        "message": f"Credentials email sent to {email}. If the address has no mailbox, "
        "the mail provider bounces it to the sender inbox.",
    }


def send_password_changed_notice(name: str, email: str) -> dict:
    """Tell an employee that their password was just changed. Never raises. Returns {"sent": bool, "message": str}.

    The change itself is not undone when this fails, so the caller only logs the result.
    """
    if not SMTP_HOST or not SMTP_FROM:
        print(f"[mail] SMTP not configured; password change notice to {email} was NOT sent")
        return {"sent": False, "message": "Email sending is not configured on the server"}

    try:
        validate_email(email, check_deliverability=True)
    except EmailNotValidError as exc:
        print(f"[mail] NOT sending password change notice to {email}: {exc}")
        return {"sent": False, "message": f"Notice not sent: {exc}"}

    text = (
        f"Hi {name},\n\n"
        f"The password for your {SMTP_FROM_NAME} account was just changed.\n\n"
        "If you did not change it, contact your admin right away.\n"
    )
    html = (
        f"<p>Hi {escape(name)},</p>"
        f"<p>The password for your {escape(SMTP_FROM_NAME)} account was just changed.</p>"
        "<p>If you did not change it, contact your admin right away.</p>"
    )

    message = EmailMessage()
    message["Subject"] = f"Your {SMTP_FROM_NAME} password was changed"
    message["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
    message["To"] = email
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        _send(message)
    except Exception as exc:  # noqa: BLE001 - must never break the password change
        print(f"[mail] FAILED to send password change notice to {email}: {type(exc).__name__}: {exc}")
        return {"sent": False, "message": "Notice not sent: could not reach the mail server"}

    print(f"[mail] password change notice handed to mail server for {email}")
    return {"sent": True, "message": "Notice sent"}
