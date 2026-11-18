---
name: endpoint-review
description: MVP checklist for reviewing or writing a FastAPI route endpoint. Covers DB-model sync, request/response schemas, exception handling, status codes, and frontend-readable errors. Run this before saying an endpoint is done.
---

# Endpoint Review — MVP Checklist

This is an MVP project. Only handle edge cases that **will actually happen** in production.
Skip theoretical edge cases. Skip over-engineering. Fix what matters.

---

## 1. DB ↔ Model Sync

Before touching a route, confirm the SQLAlchemy model matches the live table.

**Check:**
- Every column in the table exists as a field in the model (name, type, nullable)
- Every `relationship()` or FK references the correct table and column
- `__tablename__` and schema are correct
- No column the route reads/writes is missing from the model

**How to check fast:**
```bash
# Inspect live table columns
SELECT column_name, data_type, is_nullable FROM information_schema.columns
WHERE table_name = '<your_table>' ORDER BY ordinal_position;
```
Compare output against the model file. Fix any mismatch before writing the route logic.

---

## 2. Request Schema (Pydantic)

**Must have:**
- A dedicated `class XxxRequest(BaseModel)` — never read raw `dict` from the body
- `Field(min_length=..., max_length=...)` on every string the user types
- `EmailStr` for email fields
- `field_validator` for any custom rule (password strength, phone format)
- Strip + lowercase emails before DB queries: `email.strip().lower()`

**Must NOT have:**
- Optional fields that should be required
- No max_length on a string that gets stored in the DB (SQL injection surface + DB error)
- Trusting the client for IDs they shouldn't control (use `current_user.id` from the token)

---

## 3. Response Schema (Pydantic)

**Rule: never return a raw SQLAlchemy object directly.**

Always return a `class XxxResponse(BaseModel)` with `model_config = {"from_attributes": True}`.

**Check:**
- Route return type annotated with `response_model=XxxResponse`
- All fields in the response schema exist on the model (or are set via `object.__setattr__`)
- Sensitive fields excluded: `password_hash`, internal FKs the frontend doesn't need
- Datetimes serialise correctly (use `datetime` type, not `str`)

---

## 4. Exception Handling — Only What Matters (MVP)

Handle these. Everything else falls to the global handler in `main.py`.

| Situation | Status | Detail message |
|-----------|--------|----------------|
| Wrong credentials / not found (auth) | 401 | "Invalid email or password" — never say which is wrong |
| Account inactive / deactivated | 403 | "Your account has been deactivated. Contact your administrator." |
| Resource not found (by ID) | 404 | "X not found" |
| Bad user input (business rule) | 400 | Plain English: "New password must be different from current one" |
| Pydantic validation fail | 422 | Handled globally by `main.py` — do not duplicate |
| DB unavailable (`OperationalError`) | 503 | "Server unavailable, please retry in a moment." |
| DB commit failed (`SQLAlchemyError`) | 503 | "Could not save. Please try again." + `db.rollback()` |
| Permission denied | 403 | "You do not have permission to perform this action" |

**Pattern for every DB session block:**
```python
try:
    with get_session_for_schema(schema) as db:
        # ... query and business logic ...
        try:
            db.commit()
        except SQLAlchemyError as exc:
            db.rollback()
            logger.error("ENDPOINT NAME — commit failed: %s", exc)
            raise HTTPException(503, detail="Could not save. Please try again.")
except OperationalError as exc:
    logger.error("ENDPOINT NAME — DB error: %s", exc)
    raise HTTPException(503, detail="Server unavailable, please retry in a moment.")
```

**Do NOT:**
- Catch bare `Exception` — it hides real bugs
- Return stack traces or SQLAlchemy error strings to the client
- Raise 500 manually — a 500 means you missed something
- Add try/except around code that cannot throw the caught exception

---

## 5. HTTP Status Codes — Quick Reference

| What happened | Code |
|---------------|------|
| Created a resource | 201 |
| Read / updated / action done | 200 |
| Bad input from user | 400 |
| Missing or invalid token | 401 |
| Valid token, wrong permissions | 403 |
| Resource not found | 404 |
| Pydantic schema mismatch | 422 |
| DB down / email failed / external service | 503 |

---

## 6. Frontend-Readable Error Messages

The frontend reads `response.data.detail` on every error. Write for that.

**Rules:**
- One sentence. Plain English. No jargon.
- Say what the user can do next: "Request a new code." / "Please log in again."
- Never expose: table names, column names, SQL errors, stack traces, internal IDs
- Same message for wrong email vs wrong password (avoids account enumeration)
- For 503: always say "please retry" so the user knows it is temporary

**Bad:** `"unique constraint failed: employees.email"`
**Good:** `"An account with this email already exists."`

---

## 7. Authorization Check

RBAC (role-based permissions) is not implemented yet — only basic auth is in place.

For every protected endpoint, ask:
- Is `Depends(get_current_user)` on the route? Every non-public endpoint needs it.
- Does the route use `current_user.id` (from the token) for ownership queries — never a client-supplied ID?
- Can user A reach user B's data by changing an ID in the URL? (IDOR)

```python
# Correct — ownership enforced via token, not client input
contact = db.scalar(select(Contact).where(
    Contact.id == contact_id,
    Contact.employee_id == current_user.id,   # <-- prevents IDOR
))
if contact is None:
    raise HTTPException(404, detail="Contact not found")
```

> When RBAC is implemented, update this section to cover role checks.

---

## 8. Logging

Log at the route level for any failure that hits the database or sends an email.

```python
logger = logging.getLogger(__name__)

logger.info("ACTION: employee_id=%d", current_user.id)          # on success
logger.warning("ACTION FAILED: reason=%s", reason)              # on expected failure
logger.error("ACTION — error: %s", exc)                         # on unexpected error
```

Never log passwords, tokens, or full email addresses (mask them: `a***@domain.com`).

---

## 9. Quick Pre-Ship Checklist

Before marking an endpoint done, run through this list:

- [ ] SQLAlchemy model matches live DB table
- [ ] Request body uses a Pydantic schema with field limits
- [ ] Response uses a Pydantic schema — no raw ORM objects returned
- [ ] `OperationalError` caught on every `get_session_for_schema` block
- [ ] `SQLAlchemyError` caught on every `db.commit()`
- [ ] All `db.rollback()` calls present before re-raising on commit failure
- [ ] Correct HTTP status codes (201 for create, 404 for not found, etc.)
- [ ] Error `detail` messages are plain English, safe for the frontend to display
- [ ] `Depends(get_current_user)` on every non-public route
- [ ] No IDOR: ownership checked with `current_user.id`, not a client-supplied value
- [ ] `logger.error(...)` on every caught exception
