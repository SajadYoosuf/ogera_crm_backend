import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

# Browser origins allowed to call the API (comma separated). Add the deployed web app's address here.
CORS_ORIGINS = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(5 * 24 * 60)))  # 5 days

# Passwords (employee passwords and reset passwords share these limits)
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "6"))
PASSWORD_MAX_LENGTH = int(os.getenv("PASSWORD_MAX_LENGTH", "15"))

# Forgot password: the emailed code and the short-lived token that follows it
RESET_CODE_TTL_MINUTES = int(os.getenv("RESET_CODE_TTL_MINUTES", "10"))      # how long a code stays valid
RESET_CODE_RESEND_SECONDS = int(os.getenv("RESET_CODE_RESEND_SECONDS", "60"))  # wait before another code
RESET_CODE_MAX_ATTEMPTS = int(os.getenv("RESET_CODE_MAX_ATTEMPTS", "5"))    # wrong tries before the code locks
RESET_TOKEN_TTL_MINUTES = int(os.getenv("RESET_TOKEN_TTL_MINUTES", "10"))    # time to set the new password after verifying

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
