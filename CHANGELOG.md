# Changelog

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
