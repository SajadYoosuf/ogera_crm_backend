# Lead activity log and tasks

## Why
Sales people need to log what they did on a lead (field visit, phone call, WhatsApp, quote, demo, internal note) and schedule the next task, with Pending / Due today / Overdue / Done states.

## What changes
- New table `lead_activities` (created by `create_all` on startup): contact, kind, subtype, notes, due_at, status (`pending`/`done`), done_at, optional GPS (field visits), created_by.
- New `models/lead_activity.py`, `schemas/lead_activity.py`, `routes/lead_activities.py` (registered in `main.py`).
- `GET /contacts/{id}/activities`, `POST /contacts/{id}/activities`, `PATCH /contacts/{id}/activities/{activity_id}` (mark done). Documented in `API.md`.
- Kinds and subtypes: field_visit (consumer_visit, dealer_meeting, partner_intro), phone_call (check_interest, quote_followup, payment_chase), whatsapp (send_quote, payment_reminder, festival_offer), quote (new_quote, revised_price), demo (showroom_demo, sample_given), internal_note.
- `task_state` is computed on read: `done`, `overdue` (due before today), `due_today`, `pending`.

## Out of scope
Escalation alerts, notifications, quote builder, board-level overdue counts from tasks.

## Verification
POST a logged call, a scheduled visit and a note; PATCH one to done; check `task_state` for past, today and future due dates.
