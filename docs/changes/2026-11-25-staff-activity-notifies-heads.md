# Staff-logged activity notifies admins and managers

## What changed
- `routes/lead_interactions.py` `log_activity()`: when a non-head employee logs an activity on a lead, every active head (admin, manager, or any role with see-all records) except the actor gets an in-app notification of type `activity_logged`: title "Activity logged by <staff name>" — message "<staff name> logged <activity> on <lead name>.", linked to the lead.
- Heads logging their own activity do not notify other heads (no notification for head-to-head logs).
- `tests/test_leads_mvp.py`: autouse fixture `_heads_see_all` marks the admin and manager roles see-all, as production does. The test seed leaves that flag off, which made most leads tests fail with "Only admin or manager…" 403s. Two new tests cover the staff-logged case and the head-logged case.

## Access / data impact
- Admins and managers now receive notifications about staff activity on leads. Notifications are visible only to the recipient, as with other notifications.
- No permission check changes.

## Verification
- `pytest tests/test_leads_mvp.py`: 40 of 41 pass. The one failure, `test_whatsapp_reply_validation`, expects a 503 when WhatsApp is not configured. It gets 201 because the WhatsApp config work in this repo is uncommitted. Not caused by this change.
- Not run: the web notification bell with a real staff login.

## Known issues or not done yet
- Notifications are sent for every staff activity log. No batching or digest.
- Only in-app; no push.
