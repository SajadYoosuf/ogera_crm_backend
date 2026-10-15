# Module: Authentication

Covers how an employee proves who they are, how the app keeps them signed in, how a forgotten password is reset, and how routes decide who may call them.

Status: login, `/auth/me`, token checks and the password-change notice are committed. The forgot-password flow (`routes/password_reset.py`, `models/password_reset.py`) is in the working tree and not committed yet.

## Files

| File | What it does |
| --- | --- |
| `auth.py` | Password hashing (argon2), JWT create and verify, `get_current_user`, `require_roles` |
| `routes/auth.py` | `POST /auth/login`, `GET /auth/me` |
| `routes/password_reset.py` | `POST /auth/password-reset/request`, `/verify`, `/reset` |
| `models/password_reset.py` | `PasswordResetCode` table |
| `models/employee.py` | `Employee` table: `password_hash`, `is_active`, `deleted_at`, `role` |
| `mailer.py` | `send_password_reset_code`, `send_password_changed_notice` |
| `config.py` | Secret key, token lifetime, password limits, reset timings |
| `middleware.py` | Request log; redacts `password`, `password_hash`, `access_token`, `token`, `authorization` |

## Routes

| Method and path | Who may call | What it does |
| --- | --- | --- |
| `POST /auth/login` | Anyone | Checks email and password, returns a bearer token and the employee |
| `GET /auth/me` | Any signed-in employee | Returns the caller's profile |
| `POST /auth/password-reset/request` | Anyone | Emails a 6-digit code to the employee's address |
| `POST /auth/password-reset/verify` | Anyone | Checks the code, returns a 10-minute reset token |
| `POST /auth/password-reset/reset` | Holder of a reset token | Sets the new password |

Every other route in the backend declares its own rule. Sign-in-only routes use `get_current_user`. Role-limited routes use `require_roles(...)`. Some routes check the role inside the handler.

## Business logic

### Sign-in

1. Look up the employee by the email sent. Matching uses the database collation, and emails are stored in lowercase, so send them in lowercase.
2. Check the password against `password_hash` with argon2. A wrong email and a wrong password return the same `401`.
3. Refuse with `403` if `is_active` is 0 or `deleted_at` is set.
4. Issue an HS256 JWT signed with `SECRET_KEY`. Claims: `sub` (employee id) and `exp`. Lifetime: `ACCESS_TOKEN_EXPIRE_MINUTES`, 7200 minutes (5 days) by default.

### Each request

1. `HTTPBearer` reads `Authorization: Bearer <token>`. A missing header is refused.
2. The token signature and expiry are checked. A bad or expired token gives `401 Invalid token`.
3. The employee is loaded by `sub`. A missing or soft-deleted employee gives `401 Employee not found`.
4. `require_roles` (used by role-limited routes) also refuses with `403` when `is_active` is 0 or the role is not in the allowed list.

Sign-in-only routes stop after step 3. They do not check `is_active`, so see the gap below.

### Forgot password

1. **Request.** The email is matched ignoring case and surrounding spaces, and must belong to an active, non-deleted employee. Otherwise the answer is `404` and nothing is sent. A new request is refused for `RESET_CODE_RESEND_SECONDS` after the previous one (`429`). Older unused codes for that employee are marked used. The code is random, stored only as an HMAC keyed with `SECRET_KEY`, and emailed to the employee. If the mail cannot be sent, the code is rolled back and the answer is `503`.
2. **Verify.** The code must be the newest unused code for that employee, not expired, and not locked. A wrong code adds one attempt and reports how many are left. After `RESET_CODE_MAX_ATTEMPTS` wrong tries the code locks. A correct code is marked used and returns a JWT with `purpose=password_reset`, a `pw` fingerprint of the current password hash, and an expiry of `RESET_TOKEN_TTL_MINUTES`.
3. **Reset.** The token must be unexpired, have the reset purpose, belong to an active employee, and its `pw` fingerprint must match the current password hash. A token that has already been used no longer matches, so it cannot be used twice. The new password must be `PASSWORD_MIN_LENGTH` to `PASSWORD_MAX_LENGTH` characters and different from the current one. It is hashed and saved, then the employee is emailed a password-changed notice. A failed notice does not undo the change.

A login token is never accepted as a reset token, because the reset check requires `purpose=password_reset`.

## Database

```
employees                       roles
---------                       -----
id          PK                  id          PK
name        VARCHAR(120)        slug        UNIQUE  <-- employees.role
email       VARCHAR(180) UNIQUE name        UNIQUE
password_hash VARCHAR(255)      is_active   SMALLINT
role        FK roles.slug       ...
phone       VARCHAR(20)
is_active   SMALLINT default 1
deleted_at  DATETIME NULL
created_at, updated_at

password_reset_codes
--------------------
id          PK
employee_id FK employees.id (BIGINT)
code_hash   VARCHAR(64)     HMAC of the code, never the code
attempts    SMALLINT        wrong tries so far
expires_at  DATETIME
created_at  DATETIME
consumed_at DATETIME NULL   set when used, replaced, locked or expired
```

No table stores a session. A token is valid until it expires, so there is nothing to delete at sign-out.

## End-to-end flow

```
Sign in
  app ──POST /auth/login {email,password}──▶ routes/auth.py
        ▶ employees (email lookup) ▶ argon2 verify ▶ is_active check
        ◀── {access_token, employee}  (JWT, 5 days)

Protected call
  app ──Authorization: Bearer <jwt>──▶ route
        ▶ auth.get_current_user: verify signature+expiry ▶ load employees row
        ▶ require_roles (role-limited routes only): role + is_active
        ▶ route handler

Forgot password
  app ──POST /request {email}──▶ routes/password_reset.py
        ▶ employees (active, by email) ▶ password_reset_codes (insert, expire old)
        ▶ mailer.send_password_reset_code ──▶ employee's inbox
  app ──POST /verify {email,code}──▶ compare HMAC ▶ mark code used ──▶ reset_token
  app ──POST /reset {reset_token,new_password}──▶ check token + pw fingerprint
        ▶ hash new password ▶ employees.password_hash
        ▶ mailer.send_password_changed_notice ──▶ employee's inbox
```

## Settings

All in `config.py`, each overridable in `.env`:

| Name | Default | Used for |
| --- | --- | --- |
| `SECRET_KEY` | placeholder | Signs JWTs and keys the reset-code HMAC. Must be set in production. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 7200 | Login lifetime |
| `PASSWORD_MIN_LENGTH` | 6 | Shortest password |
| `PASSWORD_MAX_LENGTH` | 15 | Longest password |
| `RESET_CODE_TTL_MINUTES` | 10 | Reset code validity |
| `RESET_CODE_RESEND_SECONDS` | 60 | Wait between codes for one employee |
| `RESET_CODE_MAX_ATTEMPTS` | 5 | Wrong tries before the code locks |
| `RESET_TOKEN_TTL_MINUTES` | 10 | Window to set the new password |

## Known gaps

1. **Deactivated employees on sign-in-only routes.** Routes that only need a valid token do not check `is_active`. A deactivated employee's token works there until it expires. Login and `require_roles` routes already refuse them. Fix: add the `is_active` check to `get_current_user`.
2. **No token revocation.** Tokens cannot be cancelled early, so a password change does not end existing sessions.
3. **Default secret.** `SECRET_KEY` falls back to a placeholder when unset.
4. **Login email case** depends on the database collation, not the code.
5. **Enumeration.** The forgot-password request returns `404` for an unknown email, which reveals whether an address has an account. This is a deliberate trade-off for simplicity.

## Tests

`tests/test_password_reset.py` (forgot-password flow, lockout, expiry, token reuse), `tests/test_mobile_api.py` (login, `/auth/me`, token errors), `tests/test_employee_passwords.py` (password length and the change notice).
