# Backend modules

The FastAPI backend, module by module. Each guide lists its files, routes, rules and tests. The request and response contract for every route is in the root `API.md`.

## Current modules

| Module | Guide | Main files |
| --- | --- | --- |
| Authentication and password reset | [authentication.md](authentication.md) | `routes/auth.py`, `routes/password_reset.py`, `auth.py`, `mailer.py` |
| Employees and roles | [employee-management.md](employee-management.md) | `routes/employees.py`, `routes/roles.py`, `models/role.py` |
| Contacts (consumer, partner, dealer) | [contacts.md](contacts.md) | `routes/contacts.py`, `contact_access.py`, `contact_import.py`, `contact_export.py` |
| Lead pipeline: follow-ups, activity log, history | [lead-pipeline.md](lead-pipeline.md) | `routes/followups.py`, `routes/lead_activities.py`, `routes/history.py` |
| Tasks screen (cross-lead task list) | [tasks.md](tasks.md) | `routes/tasks.py` |
| Settings: dropdown lists, pipeline stages, company profile | (in `API.md`, Settings) | `routes/settings.py`, `settings_catalog.py` |
| Lead sources | (in `API.md`) | `routes/lead_sources.py` |

Shared plumbing: `database.py` (engine and sessions), `config.py` (environment settings), `main.py` (app, CORS, routers, table creation), `request_context.py` (`X-Client` header), `phone_utils.py`.

## Removed modules

Rule 9 in `rule.md` limits the product to the modules above. These were removed from the code. Their tables and migration scripts remain for a later clean-up.

| Removed module | Former files |
| --- | --- |
| Performance dashboard | `routes/performance.py` |
| Sales ledger | `routes/sales.py`, `models/sale.py`, `schemas/sale.py` |
| Products catalogue | `routes/products.py`, `models/product.py`, `schemas/product.py` |
| Field map and staff location tracking | `routes/staff_locations.py`, `models/staff_location.py` |

## Tests

See [testing.md](testing.md) for how to run the suite. Test files are in `tests/`, one per module or theme.
