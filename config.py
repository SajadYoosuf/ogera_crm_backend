import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(5 * 24 * 60)))  # 5 days

# Outgoing email (SMTP). Email sending is skipped, with a log line, when SMTP_HOST is unset.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Ogera CRM")
# "starttls" (port 587), "ssl" (port 465) or "none"
SMTP_SECURITY = os.getenv("SMTP_SECURITY", "starttls").lower()
APP_LOGIN_URL = os.getenv("APP_LOGIN_URL", "http://localhost:3000")
