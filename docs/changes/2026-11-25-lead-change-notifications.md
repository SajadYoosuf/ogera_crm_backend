# In-app notifications for lead changes

## What changed
- New `services/lead_notifications.py`: `notify_lead_change()` sends one in-app notification to every active admin/manager (any see-all head) and the lead's current assignee, except the person who made the change and any explicit exclusions.
- Changes that now notify:
  - `PUT /leads/{id}` edits (lists the changed fields). When the assignee changes, the new assignee is excluded, since they already get the "lead assigned" notification.
  - `DELETE /leads/{id}` (lead soft-deleted).
  - `POST /leads/{id}/convert` (lead converted to a contact).
  - `POST /leads/{id}/interactions/log` (activity logged). Replaces the earlier heads-only notification, so the assignee is now included too.
  - Follow-up tasks: scheduled, status changed or completed, rescheduled, reassigned, and deleted. The new assignee of a reassigned task is excluded (they already get "follow-up assigned").
- Notification type `lead_changed`, title "Lead updated by <name>" (or "deleted by" / "converted by"), linked to the lead. The activity log keeps type `activity_logged`.

## Access / data impact
- Recipients are admins, managers and the assignee only. No permission checks change.
- A head's edit now notifies the assignee and the other heads; a staff edit notifies the heads and not the staff member themselves.

## Verification
- `pytest tests/test_leads_mvp.py`: 42 of 43 pass. The one failure, `test_whatsapp_reply_validation`, is the WhatsApp config test that was already failing before this change.
- New tests: `test_staff_lead_edit_notifies_admin_not_the_actor`, `test_admin_lead_edit_notifies_assignee`.
- Not run: the web notification bell with a real login.

## Known issues or not done yet
- Not covered: bulk assign, lead import, WhatsApp messages sent from a lead, and inbound leads created by the website, Sheets or WhatsApp (creation already notifies, by a separate path).
- Every edit sends a notification; there is no batching.
