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


# Role-specific access bullet points shown in the welcome email.
# Each value is a list of plain-text bullet strings; the HTML version wraps them in <ul><li>.
_ROLE_ACCESS_BULLETS: dict[str, list[str]] = {
    "admin": [
        "Full system configuration and settings control",
        "User account creation, roles, and permission management",
        "Data management, imports/exports, and system integrity oversight",
        "Visibility across all teams, pipelines, and reports",
    ],
    "manager": [
        "Track and manage leads and customer interactions in one place",
        "Monitor deal progress and sales pipeline in real time",
        "Generate reports and insights to guide planning and strategy",
        "Collaborate more effectively with the sales and support teams",
    ],
    "sales_executive": [
        "Your assigned leads and customer contacts",
        "Tools to log calls, meetings, and follow-ups",
        "Your personal sales pipeline and deal tracking",
        "Reports on your own targets and performance",
    ],
}
_DEFAULT_ACCESS_BULLETS = [
    "Access to the features relevant to your role",
    "Tools to manage customer interactions and track progress",
    "Reports and insights to support your work",
]


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
    bullets = _ROLE_ACCESS_BULLETS.get(role, _DEFAULT_ACCESS_BULLETS)
    bullets_text = "\n".join(f"- {b}" for b in bullets)
    bullets_html = "\n".join(f"    <li>{escape(b)}</li>" for b in bullets)

    text = (
        f"Dear {name},\n\n"
        "Welcome to the team! We're glad to have you on board and looking forward to the impact you'll bring to this role.\n\n"
        "As part of your onboarding, we'd like to introduce you to our CRM system, which will be central to how we manage customer relationships, track leads, and drive sales performance going forward.\n\n"
        "Why we built this CRM:\n"
        "Our goal is to give every team member a single, reliable view of our customers — from first inquiry to final sale — so nothing falls through the cracks and decisions are backed by real data rather than guesswork.\n\n"
        f"Your role: {role_label}\n"
        f"As a {role_label}, you'll have access to:\n"
        f"{bullets_text}\n\n"
        f"Your login credentials:\n"
        f"URL:                {APP_LOGIN_URL}\n"
        f"Username / Email:   {email}\n"
        f"Temporary Password: {password}\n\n"
        "For security, please log in and change your password at your earliest convenience. "
        "A short walkthrough session will also be scheduled this week to help you get familiar with the system.\n\n"
        "Feel free to reach out with any questions — we want to make sure you're fully equipped to hit the ground running.\n\n"
        "Once again, welcome to the team. We're excited to have you with us.\n\n"
        f"Best regards,\n{SMTP_FROM_NAME}\n"
    )
    html = f"""
<div style="font-family:Arial,sans-serif;font-size:15px;color:#222;max-width:600px;margin:0 auto">
  <p>Dear <strong>{escape(name)}</strong>,</p>

  <p>Welcome to the team! We're glad to have you on board and looking forward to the impact you'll bring to this role.</p>

  <p>As part of your onboarding, we'd like to introduce you to our CRM system, which will be central to how we manage customer relationships, track leads, and drive sales performance going forward.</p>

  <p><strong>Why we built this CRM:</strong><br>
  Our goal is to give every team member a single, reliable view of our customers — from first inquiry to final sale — so nothing falls through the cracks and decisions are backed by real data rather than guesswork.</p>

  <p><strong>Your role: {escape(role_label)}</strong><br>
  As a {escape(role_label)}, you'll have access to:</p>
  <ul>
{bullets_html}
  </ul>

  <p><strong>Your login credentials:</strong></p>
  <table style="border-collapse:collapse;margin-bottom:12px">
    <tr><td style="padding:4px 12px 4px 0;color:#555">URL</td><td><a href="{escape(APP_LOGIN_URL)}">{escape(APP_LOGIN_URL)}</a></td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#555">Username / Email</td><td>{escape(email)}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#555">Temporary Password</td><td><code style="background:#f4f4f4;padding:2px 6px;border-radius:3px">{escape(password)}</code></td></tr>
  </table>

  <p>For security, please log in and change your password at your earliest convenience. A short walkthrough session will also be scheduled this week to help you get familiar with the system.</p>

  <p>Feel free to reach out with any questions — we want to make sure you're fully equipped to hit the ground running.</p>

  <p>Once again, welcome to the team. We're excited to have you with us.</p>

  <p>Best regards,<br><strong>{escape(SMTP_FROM_NAME)}</strong></p>
</div>
"""

    message = EmailMessage()
    message["Subject"] = f"Welcome Aboard — Your CRM Login Credentials Inside"
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


def send_password_reset_code(name: str, email: str, code: str, expires_minutes: int) -> dict:
    """Email a password-reset code. Never raises. Returns {"sent": bool, "message": str}."""
    if not SMTP_HOST or not SMTP_FROM:
        print(f"[mail] SMTP not configured; reset code to {email} was NOT sent")
        return {"sent": False, "message": "Email sending is not configured on the server"}

    try:
        validate_email(email, check_deliverability=True)
    except EmailNotValidError as exc:
        print(f"[mail] NOT sending reset code to {email}: {exc}")
        return {"sent": False, "message": f"Reset code not sent: {exc}"}

    text = (
        f"Hi {name},\n\n"
        f"Your {SMTP_FROM_NAME} password reset code is: {code}\n\n"
        f"It expires in {expires_minutes} minutes. If you did not ask to reset your password, ignore this email.\n"
    )
    html = (
        f"<p>Hi {escape(name)},</p>"
        f"<p>Your {escape(SMTP_FROM_NAME)} password reset code is:</p>"
        f"<p style=\"font-size:24px;font-weight:bold;letter-spacing:4px\">{escape(code)}</p>"
        f"<p>It expires in {expires_minutes} minutes. If you did not ask to reset your password, ignore this email.</p>"
    )

    message = EmailMessage()
    message["Subject"] = f"Your {SMTP_FROM_NAME} password reset code"
    message["From"] = formataddr((SMTP_FROM_NAME, SMTP_FROM))
    message["To"] = email
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        _send(message)
    except smtplib.SMTPRecipientsRefused:
        print(f"[mail] recipient refused reset code: {email}")
        return {"sent": False, "message": "The mail server refused this email address"}
    except smtplib.SMTPAuthenticationError:
        print("[mail] SMTP login failed - check SMTP_USER / SMTP_PASSWORD")
        return {"sent": False, "message": "Reset code not sent: mail server login failed"}
    except Exception as exc:  # noqa: BLE001 - must never break the request
        print(f"[mail] FAILED to send reset code to {email}: {type(exc).__name__}: {exc}")
        return {"sent": False, "message": "Reset code not sent: could not reach the mail server"}

    print(f"[mail] reset code handed to mail server for {email}")
    return {"sent": True, "message": "Reset code sent"}


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
