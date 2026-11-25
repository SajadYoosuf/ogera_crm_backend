# Lead-created notifications cannot block lead creation

## What changed
- `services/lead_service.py` `_notify_lead_created()`: the head (admin/manager) lookup now filters active employees with `Employee.is_active == 1`, matching the integer column used elsewhere.
- New `_notify_lead_created_safely()`: runs the notification step inside a savepoint (`db.begin_nested()`). A database error while building notifications is logged and skipped, and the lead is still saved.
- `create_lead()` calls the safe wrapper instead of `_notify_lead_created()` directly.

## Access / data impact
- No change to who can see or assign leads.
- Notifications for a new lead can be dropped if the notification query fails. The lead itself is no longer lost because of it.

## Verification
- Staged file compiles (`py_compile`).
- Not run: the API end to end, and no pytest suite for notifications exists yet.

## Known issues or not done yet
- A dropped notification is only visible in the server log (`lead %s saved but notifications were skipped`). Nothing retries it.
