"""
Firebase Admin SDK initialisation.

HOW TO SET UP (two options — pick one):

  Option A — file path (local dev):
    Set FIREBASE_SERVICE_ACCOUNT_JSON to the absolute path of the downloaded
    service-account JSON file, e.g.:
      FIREBASE_SERVICE_ACCOUNT_JSON=C:/secrets/ogera-crm-serviceAccount.json

  Option B — inline JSON string (Render / any cloud env var):
    Set FIREBASE_SERVICE_ACCOUNT_JSON to the full JSON contents as a single
    string (the value starts with '{').  Render → Environment → add the var,
    paste the whole JSON as the value.

GRACEFUL DEGRADATION:
    If the var is not set, Firebase init is skipped. In-app notifications still
    work; FCM push is silently disabled.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)
_firebase_ready = False


def init_firebase() -> None:
    """Initialise the Firebase Admin SDK once at server startup. Never raises."""
    global _firebase_ready
    raw = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_JSON not set — FCM push notifications disabled")
        return
    try:
        import firebase_admin
        from firebase_admin import credentials
        if firebase_admin._apps:
            _firebase_ready = True
            return
        # Option B: value is a JSON object string
        if raw.startswith("{"):
            cred = credentials.Certificate(json.loads(raw))
        else:
            # Option A: value is a file path
            if not os.path.exists(raw):
                logger.warning(
                    "FIREBASE_SERVICE_ACCOUNT_JSON looks like a file path but the file does not "
                    "exist on this host: %s — FCM push notifications disabled. "
                    "On cloud deployments, set the variable to the JSON contents directly (Option B).",
                    raw,
                )
                return
            cred = credentials.Certificate(raw)
        firebase_admin.initialize_app(cred)
        _firebase_ready = True
        logger.info("Firebase Admin SDK initialised (project: ogera-crm)")
    except Exception as exc:
        logger.error("Firebase Admin SDK init failed: %s", exc)


def is_ready() -> bool:
    """True if Firebase was initialised successfully."""
    return _firebase_ready
