# Ogera CRM — backend

FastAPI and SQLAlchemy service for the Ogera CRM. The web app (`../ogera_crm_web`) and the mobile app (`../ogera_crm_mobile`) both call it. The API is described in `../API.md`.

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows (use source .venv/bin/activate elsewhere)
pip install -r requirements.txt
uvicorn main:app --reload        # http://127.0.0.1:8000
```

Database and other settings come from the environment (`config.py`, `.env`). Do not commit `.env` or keys.

Tests: `pip install -r requirements-dev.txt`, then run `pytest` from this folder.

## Layout

- `main.py` — app setup and routers
- `routes/` — endpoints (`contacts.py`, `lead_activities.py`, `tasks.py`, `settings.py`, …)
- `models/`, `schemas/` — database models and request/response schemas
- `docs/changes/` — change notes for each feature
- `CHANGELOG.md` — what changed, commit by commit

Migrations that must run before a deploy are listed in each changelog entry (`scripts/`).
