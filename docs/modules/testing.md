# Testing the backend

## Unit and API tests (pytest)

Run from `ogera_crm_backend`:

```bash
.venv\Scripts\python.exe -m pytest -q
```

- Each test gets a fresh in-memory SQLite database (`tests/conftest.py`). Nothing needs MySQL.
- Seed data is in `tests/seed_data.py`: admin, manager, two sales staff (`anu`, `raj` is a field executive), and four lead sources. The password is the same for all of them.
- The `login` fixture returns `(headers, employee)` for a seeded user.
- Last full run: 155 passing.

Known flaky test: `tests/test_contacts_module.py::test_type_counts_respect_visibility` failed in some full runs and passes alone. Its cause is not pinned down yet.

## Live end-to-end server

`tests/e2e_server.py` runs the real API on a throwaway SQLite file, seeded like the tests, so the web app can be tested against it:

```bash
.venv\Scripts\python.exe tests\e2e_server.py --port 8765
```

It allows the web origin `http://localhost:3100` and `http://127.0.0.1:3100` through `CORS_ORIGINS`. Set that variable when starting it from a different port:

```bash
set CORS_ORIGINS=http://localhost:3100,http://127.0.0.1:3100
```

The web live tests and their setup are in the web repo: `ogera_crm_web/docs/TESTING.md`.

## Where the tests are

| File | Covers |
| --- | --- |
| `test_contacts_module.py`, `test_contact_types.py`, `test_contact_import.py` | Contacts, types, import and export |
| `test_employee_passwords.py`, `test_employee_duplicates.py`, `test_password_reset.py` | Employees and passwords |
| `test_partner_stages.py` | Partner and consumer pipelines, task list counts |
| `test_mobile_api.py` | Mobile app contract |
| `test_followup_reschedule.py` | Reschedule history, completing with a note |
| `test_lead_pipeline_edge_cases.py` | Follow-up and activity rules, role and visibility limits |
