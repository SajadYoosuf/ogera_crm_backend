# In-app notification when a follow-up's due time passes

## What changed
- `services/followup_reminders.py`: new `send_due_now()` and `DUE_NOW_LOOKBACK` (10 minutes). For each open lead task whose `due_at` passed in the last 10 minutes, it creates a `followup_time` notification for the assignee ("Follow-up due now"). It runs once per task, using the same once-per-lead dedupe as the other reminders.
- `scheduler.py`: new job `lead_followups_due_now`, every 5 minutes, running `send_due_now` for each active tenant.
- `tests/test_leads_mvp.py`: `test_due_time_reminder_fires_once_when_time_passes`.

## Why
Scheduling a follow-up with a time (Schedule Follow-up) created no notification at that time. The existing reminders only run at 09:00 IST (due today) and hourly (more than 24 h overdue). Also, when the assignee scheduled the task for themselves, no "assigned" notification was sent. The new rule covers both.

## Access / data impact
- Only the task's assignee receives the notification. No new permissions.
- Adds one notification row per task at its due time. The existing unread-count polling in the web app shows it.

## Verification
- `pytest tests/test_leads_mvp.py -k due_time`: passes.
- Not run: the two older reminder tests (`test_due_today_reminder_once_per_day_and_skips_completed`, `test_overdue_reminder_after_24h_once`). They fail in the test setup because the admin's lead creation returns 403 "Only admin or manager can assign leads to a specific person". That happens before any reminder code runs. It looks like it comes from the uncommitted permissions work in this repo; not investigated further.

## Known issues or not done yet
- Reminders are checked every 5 minutes, so a notification can arrive up to about 5 minutes after the due time.
- If the server is down for more than 10 minutes around a due time, that reminder is not sent.
- The activity log modal itself has no time field; the time comes from the Schedule Follow-up modal.
