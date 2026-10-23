"""
Firebase Admin SDK initialisation.

WHY FIREBASE ADMIN?
    The Admin SDK lets our backend send push notifications to web browsers and mobile
    devices via FCM (Firebase Cloud Messaging). It is initialised once at server startup
    and reused for every notification.

HOW TO SET UP:
    1. Firebase Console → Project Settings → Service Accounts → Generate new private key.
    2. Save the downloaded JSON file to a path that is NOT inside the repo
       (e.g. /secrets/ogera-crm-serviceAccount.json).
    3. Add FIREBASE_SERVICE_ACCOUNT_JSON=<that path> to the backend .env file.

GRACEFUL DEGRADATION:
    If FIREBASE_SERVICE_ACCOUNT_JSON is not set (e.g. local dev), Firebase init is skipped.
    In-app notifications still work; FCM push is silently disabled.
"""
import logging
import os

logger = logging.getLogger(__name__)
_firebase_ready = False


def init_firebase() -> None:
    """Initialise the Firebase Admin SDK once at server startup. Never raises."""
    global _firebase_ready
    key_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not key_path:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_JSON not set — FCM push notifications disabled")
        return
    try:
        import firebase_admin
        from firebase_admin import credentials
        if not firebase_admin._apps:
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
        _firebase_ready = True
        logger.info("Firebase Admin SDK initialised (project: ogera-crm)")
    except Exception as exc:
        logger.error("Firebase Admin SDK init failed: %s", exc)


def is_ready() -> bool:
    """True if Firebase was initialised successfully."""
    return _firebase_ready
