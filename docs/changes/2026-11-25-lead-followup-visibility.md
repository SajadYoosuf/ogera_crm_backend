# Lead follow-up reassignment keeps the lead visible to the new assignee

Date: 2026-11-25
Web counterpart: none (the leads list and detail are server-scoped; no frontend change needed).

## What changed

- `services/lead_access.py`
  - New `open_task_lead_ids_for(employee_id)`: subquery of lead ids that have a follow-up
    (`interactions.kind = 'task'`, `entity_type = 'lead'`) assigned to the employee whose status is not `completed`.
    A NULL status counts as open (column default is `todo`).
  - New `can_view_lead(employee, lead)`: heads see everything; staff see the lead if they are the assignee
    or hold an open follow-up on it.
  - `ensure_can_work_lead` and `ensure_can_log_on_lead` now use `can_view_lead`, so the new assignee can open
    the lead and log on it while the follow-up is open.
- `services/lead_service.py` `list_leads`: staff scope is now
  `assigned_to = me OR id IN (open follow-up lead ids for me)`.
- `tests/test_reassigned_followup_visibility.py` (new, 5 tests): reassigned follow-up shows the lead to the new
  assignee; access ends when the follow-up is completed; original owner keeps the lead; a completed follow-up grants
  nothing; an open follow-up on a lead the employee does not own grants access; unrelated staff still see nothing.
- WhatsApp inbox wiring carried in this same commit (already tested on the live system by the owner):
  `config.py` (`PUBLIC_API_URL`), `routes/__init__.py` and `main.py` register the `whatsapp_inbox`,
  `whatsapp_config` and `whatsapp_templates` routers. The router files themselves were already tracked.
- `scripts/migrations/005_fcm_devices.sql` (untracked before this commit): creates `fcm_devices`, run once per tenant schema.

## Access / data impact

- Role access widens for staff only: a staff member can now list, open, and log on a lead that is assigned to someone
  else when they hold an open follow-up on it. Access ends when that follow-up is completed.
- Heads (admin, manager) are unchanged.
- No schema change. Reads only from the existing `interactions` table.

## Verification

- `uv lock --check`: resolved 62 packages, exit 0 (lockfile in sync). `uv sync` itself was not re-run.
- `python -c "import main"`: OK.
- `py_compile` on routes, services, models, schemas, main.py, config.py: OK.
- Conflict-marker scan: none.
- `tests/test_reassigned_followup_visibility.py`: 5 passed.
- Full suite (`pytest`, run on the working tree including the WhatsApp wiring): **210 failed, 14 errors, 317 passed, 2 xfailed** in 572s.
  The failures are in files outside this change's scope (see Known issues). Not compared to a baseline.
- Not run: the live database was not touched by this session. Live testing was done by the owner before this commit.

## Known issues or not done yet

- Out of scope for this change; failing in the full run and not fixed here:
  - Lead test files: `test_lead_access.py` (13), `test_lead_tasks.py` (13), `test_leads_mvp.py` (1), `test_task_status.py` (3),
    `test_lead_pipeline_edge_cases.py` (14), `test_followup_reschedule.py` (2). The first four fail the same way without this change.
    They use contact-scoped follow-up/activity routes or older field names.
  - WhatsApp: `test_whatsapp_templates.py` (18), `test_whatsapp_config.py` (14), `test_whatsapp_inbox.py` (14).
  - Other: `test_contact_verification_flow.py` (20), `test_dashboard.py` (16), `test_contact_import.py` (10),
    `test_contacts_module.py` (11), `test_mobile_api.py` (11), `test_team_access.py` (8), `test_employees.py` (6),
    `test_partner_types.py` (6), `test_contact_types.py` (6), `test_partner_stages.py` (5), `test_employee_security_fixes.py` (4),
    `test_no_role_rules.py` (4), `test_recent_activity.py` (4), `test_settings.py` (4), `test_fcm.py` (2), `test_products.py` (1).
  - Plus 14 test errors in the same run.
- `tests/test_leads_mvp.py::test_whatsapp_reply_validation` expects a 503 when Meta credentials are missing. It fails
  when a local `.env` supplies them; it passes in a clean environment.
- The lead-list role scope does not yet expose the reason (assigned vs. follow-up) to the UI.
