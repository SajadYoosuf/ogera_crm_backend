# 2026-11-25 — Staff own the leads they create; import is head-only (backend)

Staff-created leads were auto-assigned to whichever active employee had the fewest open leads, so a sales
executive could create a lead and never see it. Imports defaulted to "unassigned", and staff could run them.

## What changed
- `routes/leads.py` `POST /leads` — when a non-head creates a lead without `assigned_to`, the lead is assigned to
  the creator. Heads keep the existing auto-assign behaviour, and they can still pick an assignee.
- `routes/leads.py` `POST /leads/import/analyze` and `POST /leads/import/commit` — now admin/manager only
  (`ensure_is_head`, 403 for staff). Imports are no longer a staff action.

## Access / data impact
- Staff can create leads and see them (their own leads are assigned to them). Visibility rules are unchanged:
  staff still see only leads assigned to them.
- Staff can no longer import. Existing leads are not changed.

## Verification
- `tests/test_leads_mvp.py::test_staff_created_lead_is_theirs_and_staff_cannot_import` — passes: a staff create
  is assigned to the creator, and staff get 403 on import analyze and commit.
- The rest of `tests/test_leads_mvp.py` is unchanged: 25 of 37 tests fail with the same error as before this
  change (the fixture gets 403 when creating a lead as admin with `assigned_to`). Not investigated here.

## Known issues or not done yet
- The RBAC doc (R2) says staff should also see leads they created. Not done here: with the creator now as the
  default assignee, the lists are consistent, but a lead a head later reassigns away from the creator is no
  longer visible to the creator.
- Web change in the same work: the Import / Export menu is hidden for staff (`ogera_crm_web`, see its change note).
