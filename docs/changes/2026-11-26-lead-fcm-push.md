# Lead and in-app notifications also send FCM push

Web counterpart: `ogera_crm_web` (Firebase client, token registration after login). Its changes are not part of this commit.

## What changed

- `services/notification_service.py`
  - `create_notification` now queues the push on the SQLAlchemy session when the caller does not pass `background_tasks`. Callers that do pass it keep the previous behaviour.
  - A session `after_commit` hook sends the queued pushes in a background thread. A session `after_rollback` hook drops them, so a rolled-back notification sends nothing.
  - `notify_roles`, `notify_task_completed` and `run_48h_rule` filter staff by role slug through the `roles` table (`Employee.role_id IN (SELECT roles.id WHERE roles.slug IN (...))`). `Employee` has no `role` column, so these queries previously raised and were logged, and admin/manager notifications were never created.
  - The inactivity and daily-digest jobs no longer dispatch pushes manually, because the hook covers them.
- `routes/employees.py`: `POST /employees/me/fcm-token` and `DELETE /employees/me/fcm-token` now update `employees.fcm_token` through the request's tenant session. Previously they set the field on the `current_user` object, which belongs to a closed session, so only `fcm_devices` was written.

Covered by the hook without caller changes: lead created and lead assigned (manual, webhook, WhatsApp-created), lead interactions, follow-ups, tasks, contact interactions, expenses, and the scheduled jobs.

## Access / data impact

- Admin and manager notifications for new leads, staff activity and completed tasks are now created for employees whose role slug is `admin` or `manager`. No other roles were added.
- `employees.fcm_token` is written on every FCM token registration. This column must exist in each tenant schema: `ALTER TABLE employees ADD COLUMN IF NOT EXISTS fcm_token VARCHAR(512);`
- `fcm_devices` must exist in each tenant schema (`scripts/migrations/005_fcm_devices.sql`).
- No change to roles, permissions or stored notification rows.

## Verification

- Import check of `services.notification_service` and `routes.employees`: passes under `-W error::SyntaxWarning`.
- Ad-hoc SQLite check (not committed): push is not sent before commit, is sent after commit, and is not sent after rollback. Passed.
- Full pytest suite was not re-run for this change. The last full run before it had 169 failed and 14 errors. The causes seen were `schema="public"` on SQLite (`models/employee_lookup.py` and others) and a `WhatsAppMessage.is_read` test mismatch. Neither file is changed here.
- Not yet verified end to end on the live database: a lead created by a sales executive produces a push on an admin's Windows device.

## Known issues or not done yet

- Savepoint edge case: a notification created inside a `begin_nested()` savepoint that later rolls back, while the outer transaction commits, still sends its push. Only the lead-created notification step uses a savepoint, and it only rolls back if it raises.
- Pushes are sent from a daemon thread per commit, not a queue. Acceptable at current volume, not a durable delivery guarantee.
- `tele_caller` and other non-admin roles do not receive admin/manager notifications. This needs a product decision.
- `routes/whatsapp_inbox.py` `unread_count` / `mark_read` changes are not part of this commit.
