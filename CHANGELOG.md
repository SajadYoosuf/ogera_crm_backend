# Changelog

## 2026-10-05 — `48b3df0` (branch `phase1`)

**Add verified / unverified filter to contacts**

Author: sajad yoosuf
Previous commit on this side: `d39dc1b` Add verified / unverified status to contacts (2026-10-05)

### Summary
`GET /contacts` accepts `verified=true|false`, filtered in SQL so totals and paging stay correct. Notes: [docs/changes/2026-10-05-contact-verification.md](docs/changes/2026-10-05-contact-verification.md).

### Files
| Status | File | Change |
| --- | --- | --- |
| Modified | `verification.py` | Adds `verified_clause()`, the SQL twin of `missing_fields()` |
| Modified | `routes/contacts.py` | New `verified` query parameter on the list route |
| Modified | `docs/changes/2026-10-05-contact-verification.md` | Notes updated |

### Notes
No migration. `verified_clause()` and `missing_fields()` must be changed together. Checked on an in-memory SQLite database (216 contacts, same result as the Python rule); not run against MySQL. The same commit also touched web and `API.md`.

## 2026-10-05 — `d39dc1b` (branch `phase1`)

**Add verified / unverified status to contacts**

Author: sajad yoosuf
Previous commit on this side: `c93ee92` docs: update changelog for 2ce70d2 (2026-10-03)

### Summary
Contacts now report `is_verified` and `missing_fields`, computed on read. Notes: [docs/changes/2026-10-05-contact-verification.md](docs/changes/2026-10-05-contact-verification.md).

### Files
| Status | File | Change |
| --- | --- | --- |
| Added | `verification.py` | `missing_fields(contact)` holds the rules: name, phone, district, address, lead source; offline leads also GPS and creator; consumers need house stage, partners need work area |
| Modified | `schemas/contact.py` | `is_verified` and `missing_fields` added to `ContactListItem` and `ContactResponse` |
| Modified | `routes/contacts.py` | Fills the two fields in `GET /contacts` and `GET /contacts/{id}`; the list also eager-loads `consumer_profile` |
| Added | `docs/changes/2026-10-05-contact-verification.md` | Change notes |

### Notes
No migration or new column. `API.md` at the repo root documents the fields. The same commit also touched web. The route itself was not run against a live database; the rules were checked with sample contacts.

## 2026-10-02 — `2ce70d2` (branch `phase1`)

**Restructure repo into backend / web / mobile folders**

Author: sajad yoosuf
Previous commit on this side: none (first commit on this side)

### Summary

The FastAPI backend is tracked in the shared repo for the first time, under `ogera_crm_backend/`. Before this it sat untracked beside the web app, with an empty repo of its own that has been removed.

### Files

| Status | File | Change |
| --- | --- | --- |
| Added | `main.py`, `auth.py`, `config.py`, `database.py`, `middleware.py`, `mailer.py`, `requirements.txt` | App entry point, JWT auth, settings, MySQL engine, request logging, employee-credential email. |
| Added | `models/` (10 files) | SQLAlchemy models: contacts, partner and consumer profiles, employees, roles, lead sources and stages, staff locations. |
| Added | `routes/` (7 files) | Auth, contacts, employees, lead sources, roles and staff-location routes. |
| Added | `schemas/` (9 files) | Pydantic request and response models. |
| Added | `scripts/` (13 files) | `create_tables.sql`, SQL migrations, seed, and data import scripts (contacts, Excel leads, professionals). |
| Added | `docs/changes/` | Change notes for the employee and contacts work. |
| Added | `.gitignore` | Ignores `.env*`, keys, `.venv`, `__pycache__` and the `contacts_*` / `professionals_*` JSON exports. |

### Notes

- Customer data files in this folder (`*.xlsx`, `*.pdf`, `buyers_register.*`, `contacts_*.json`, `professionals_*.json`) and `.env` are deliberately not in git; the root `.gitignore` covers them.
- There are no migration tracking tables; run the SQL in `scripts/` by hand, and check data migrations before re-running them.
- Pairs with the web entry for `2ce70d2`.
