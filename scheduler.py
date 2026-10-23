"""
Background scheduler — runs the notification jobs on a timer.

WHAT THIS FILE DOES:
    Starts two background jobs when the FastAPI server boots:

    1. Inactivity check  — runs every 60 minutes.
       Queries contacts with no recent ActivityLog and fires inactivity reminders.

    2. Daily digest      — runs once per day at NOTIF_DIGEST_HOUR (IST, default 8 AM).
       Sends each employee a summary of their tasks due today.

WHY APScheduler IN-PROCESS?
    For MVP, running the scheduler inside the same FastAPI process is the simplest option —
    no Redis, no Celery, no separate worker. The two jobs are lightweight DB queries.
    If the server has multiple workers, each worker runs its own scheduler instance.
    Because notifications are deduplicated by (recipient, type, entity, 24h window),
    duplicate firings from multiple workers are harmless.

HOW TO USE:
    In main.py:
        from scheduler import start_scheduler, stop_scheduler
        @app.on_event("startup")
        def startup(): start_scheduler()
        @app.on_event("shutdown")
        def shutdown(): stop_scheduler()
"""
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from database import SessionLocal
import config

logger = logging.getLogger(__name__)

# Using BackgroundScheduler (thread-based) because FastAPI may run with a sync
# ASGI server (uvicorn in sync mode). BackgroundScheduler runs in its own thread
# and does not interfere with the async event loop.
_scheduler = BackgroundScheduler(timezone="Asia/Kolkata")


def _run_inactivity_check() -> None:
    """Job wrapper — opens its own DB session and calls the service function."""
    from services.notification_service import run_48h_rule
    with SessionLocal() as db:
        run_48h_rule(db)


def _run_daily_digest() -> None:
    """Job wrapper — opens its own DB session and calls the service function."""
    from services.notification_service import run_daily_digest
    with SessionLocal() as db:
        run_daily_digest(db)


def start_scheduler() -> None:
    """Register jobs and start the scheduler. Called once at server startup."""
    _scheduler.add_job(
        _run_inactivity_check,
        "interval",
        hours=1,
        id="inactivity_check",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_daily_digest,
        CronTrigger(hour=config.NOTIF_DIGEST_HOUR, minute=0, timezone="Asia/Kolkata"),
        id="daily_digest",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Notification scheduler started — inactivity check every 1h, digest at %02d:00 IST",
        config.NOTIF_DIGEST_HOUR,
    )


def stop_scheduler() -> None:
    """Gracefully stop the scheduler. Called at server shutdown."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Notification scheduler stopped")
