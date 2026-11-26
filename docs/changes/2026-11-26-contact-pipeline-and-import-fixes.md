# Contacts: pipeline stage on list rows, and import fixes

Web change note: [../../../ogera_crm_web/docs/changes/2026-11-26-pipeline-board-visible.md](../../../ogera_crm_web/docs/changes/2026-11-26-pipeline-board-visible.md)

## What changed

- `GET /contacts` rows now carry `lead_stage` (`stage_id`, `stage_slug`, `stage_name`, `contact_type`,
  `changed_at`), or `null` when the contact has no stage. The newest `lead_stage_log` row wins. One extra query
  per page (`_current_stages` in `routes/contacts.py`). The pipeline board uses it to place cards in columns.
- `POST /contacts/import/commit` without `assigned_to` no longer returns 500. It filtered on `Employee.role`,
  which does not exist. It now joins `roles` and picks active, not deleted `sales_executive` employees.
- The same endpoint accepts `assigned_to` as one id (`"5"`, which the web sends) or a JSON array (`"[3,5]"`).
- `routes/pipeline.py` stays. It had been deleted in the working tree, which made `GET /pipeline/stages` return
  404 and left the pipeline board empty. It was restored unchanged, so this commit has no diff for it.

## Access / data impact

- No migration and no data change.
- No role change. Stages are returned only for contacts the caller can already list.
- Import now succeeds where it failed before, so it writes contacts, a `new_lead` stage and a follow-up task.

## Verification

- `tests/test_contact_list_stage.py` and `tests/test_contact_import_assignee.py`: 13 passed (SQLite).
- Full suite without `tests/test_contacts_module.py`: 139 failed, 354 passed, 2 xfailed. At commit `22460e8`:
  160 failed, 350 passed, 2 xfailed, 12 errors.
- `tests/test_contacts_module.py`: 33 failed, 8 passed, both at `22460e8` and with these changes.
- Four tests failed in the full run but not at `22460e8`:
  - two call `/tasks`, which is removed (see the tableless-models note);
  - `test_task_with_unknown_assignee_is_rejected` passed when run alone;
  - `test_whatsapp_reply_validation` fails only when `.env` has WhatsApp credentials, and passed 4 of 4 runs with
    them blanked.
- Read-only checks on the `ventino` schema: stages exist for consumer, partner and dealer; the new stage query
  runs; 4 active sales executives. It had no live contacts.
- `uv lock --check` and `import main` pass.
- Not checked: a real import against PostgreSQL, or the signed-in pages in a browser.

## Known issues or not done yet

- **Tests can call live services.** `test_whatsapp_reply_validation` reads `.env` and posts a real request to
  Meta when WhatsApp credentials are present. Blank `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` before
  running the suite, until the test is isolated.
- A contact added by hand gets no stage, so the board shows it under "No Stage".
- Import with the phone column unmapped returns 200 and creates nothing, instead of an error.
- `settings_catalog.DEFAULT_STAGES` has no dealer stages. A new tenant would start without them.
- An `assigned_to` id that matches no employee is not checked.
