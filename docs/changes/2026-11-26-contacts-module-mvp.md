# Contacts Module MVP — Backend

## What changed

**Migration** (`migrations/001_contacts_module_mvp.sql`)
- Created `ventino.pipeline_stages` (25 stages seeded: 10 consumer, 9 partner, 6 dealer)
- Created `ventino.lead_stage_log` with FK → contacts, pipeline_stages, employees
- Created `ventino.contact_assignments`
- Created `ventino.dealer_profiles` (business_name, gstin, town_city, pin_code, dealer_type, assigned_rep, credit_limit, outstanding_balance, data JSONB)
- Added `show_in_list BOOLEAN DEFAULT FALSE` to `contact_field_definitions`

**Models**
- `models/contact_field_definition.py`: added `show_in_list` column
- `models/dealer_profile.py`: new model for dealer contact type
- `models/__init__.py`: exports `DealerProfile`

**Schemas**
- `schemas/enums.py`: `ContactType` gains `dealer = "dealer"`
- `schemas/contact.py`: added `ContactStageResponse`, `ContactStageSet`
- `schemas/__init__.py`: exports the two new stage schemas

**Services**
- `services/lead_access.py`:
  - Added `open_task_contact_ids_for()`, `_has_open_task_on_contact()`
  - Replaced stub `ensure_can_work_contact` with full RBAC (assigned + created + open task)
  - Added `ensure_can_edit_contact`, `ensure_can_log_on_contact`, `ensure_can_assign_task_on_contact`
  - Old `ensure_can_log_activity`, `ensure_can_work_followup`, `ensure_can_assign_followup` kept as aliases

**Routes**
- `routes/pipeline.py`: fixed `get_db` → `get_tenant_db` (bug: was querying wrong schema)
- `routes/contacts.py`:
  - Added imports: `LeadStage`, `PipelineStage`, `RecordLog`, `add_log`, stage schemas
  - New `GET /contacts/{id}/stage` — returns current stage
  - New `POST /contacts/{id}/stage` — moves contact to a stage, writes `lead_stage_log` + `record_logs`
  - New `GET /contacts/{id}/timeline` — newest-first audit log for a contact
- `routes/contact_interactions.py` (new):
  - `GET /contacts/{id}/interactions` (?kind=log|task)
  - `POST /contacts/{id}/interactions/log`
  - `POST /contacts/{id}/interactions/task`
  - `PATCH /contact-interactions/{iid}`
  - `PATCH /contacts/{id}/interactions/{iid}/reschedule`
  - `PATCH /contacts/{id}/interactions/{iid}/reassign` (head only)
  - `DELETE /contacts/{id}/interactions/{iid}` (head only)
- `routes/__init__.py` + `main.py`: registered `contact_interactions_router`, re-enabled `recent_activity_router`

## Access / data impact

- No existing data modified. All DB changes are additive.
- `pipeline_stages` and `lead_stage_log` tables now exist — `GET /pipeline/stages` will return results for the first time.
- `pipeline.py` fix: previously `GET /pipeline/stages` would have silently failed against the wrong schema.

## Verification

- `uv run python -c "import main; print('OK')"` — imports clean, no errors
- DB migration confirmed via psql: 25 tables now in ventino, `show_in_list` column present, 25 pipeline stages seeded
- Endpoints not yet tested against a running server (no test suite for contacts yet)

## Known issues / not done yet

- `dealer_profiles` CRUD endpoints in `routes/contacts.py` not built yet (create/update/read dealer profile)
- `contact_import.py` does not yet create a first task in `interactions` for imported contacts
- `POST /contacts` does not yet insert a stage row or first follow-up task (journeys 1 and 2 from plan)
- Frontend: pipeline page, contact detail modal, pipeline bar, contact timeline — not started
- Tests: none yet for stage, timeline, interactions, reschedule, reassign, open-task visibility
