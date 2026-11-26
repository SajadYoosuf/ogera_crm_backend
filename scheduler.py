"""
Background scheduler — runs the notification jobs on a timer.

WHAT THIS FILE DOES:
    Starts two background jobs when the FastAPI server boots:

    1. Inactivity check  — runs every 60 minutes.
       Queries contacts with no recent logged interaction and fires inactivity reminders.

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
import config

logger = logging.getLogger(__name__)

# Using BackgroundScheduler (thread-based) because FastAPI may run with a sync
# ASGI server (uvicorn in sync mode). BackgroundScheduler runs in its own thread
# and does not interfere with the async event loop.
_scheduler = BackgroundScheduler(timezone="Asia/Kolkata", daemon=True)


def _run_inactivity_check() -> None:
    """Hourly — contacts with no recent activity, for every active tenant."""
    from services.followup_reminders import run_for_all_tenants
    from services.notification_service import run_48h_rule
    run_for_all_tenants(run_48h_rule)


def _run_daily_digest() -> None:
    """Morning summary of tasks due today, for every active tenant."""
    from services.followup_reminders import run_for_all_tenants
    from services.notification_service import run_daily_digest
    run_for_all_tenants(run_daily_digest)


def _run_followups_due() -> None:
    """09:00 IST — lead follow-ups due today, for every active tenant."""
    from services.followup_reminders import run_for_all_tenants, send_due_today
    run_for_all_tenants(send_due_today)


def _run_followups_overdue() -> None:
    """Hourly — lead follow-ups more than 24 h overdue, for every active tenant."""
    from services.followup_reminders import run_for_all_tenants, send_overdue
    run_for_all_tenants(send_overdue)


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
    # Lead follow-up reminders (tenant-aware; see services/followup_reminders.py)
    _scheduler.add_job(
        _run_followups_due,
        CronTrigger(hour=9, minute=0, timezone="Asia/Kolkata"),
        id="lead_followups_due",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_followups_overdue,
        "interval",
        hours=1,
        id="lead_followups_overdue",
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
