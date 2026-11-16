# Authentication Blueprint — Complete Guide

> **Who this is for:** A beginner backend developer joining the team.
> This guide walks through every piece of the auth system — the database tables,
> the schemas, the utility functions, and the routes — in the order a request
> travels through the code.

---

## Big Picture — What Authentication Does

Authentication answers one question: **"Who are you?"**

When an employee opens the mobile app or the web dashboard, they type their
email and password. The backend checks them, then hands back a **JWT token**
(a signed string). From that moment on, every request the app makes includes
that token in the `Authorization` header. The backend reads the token, trusts
it, and knows who is making the request — without touching the database on
every call.

```
Client (app / web)                    Backend (FastAPI)
─────────────────                     ─────────────────
POST /auth/login                  →   check email + password in DB
  { email, password }             ←   { access_token, employee }

GET /contacts  (every later call)
  Authorization: Bearer <token>   →   decode token → get employee
                                  ←   { contacts data }
```

---

## Piece 1 — The Database Tables

### `employees` table  →  `models/employee.py`

This is the core user table. One row = one employee.

```
Column          Type            What it stores
──────────────  ──────────────  ───────────────────────────────────────────
id              INT PK          Auto-generated number, used as the user ID
name            VARCHAR(120)    Display name
email           VARCHAR(180)    Login identifier, must be unique
password_hash   VARCHAR(255)    Argon2 hash of the password — NEVER the raw password
role            VARCHAR(40)     FK → roles.slug  e.g. "admin", "sales_executive"
phone           VARCHAR(20)     Optional
branch          VARCHAR(60)     Office branch: malappuram / perinthalmanna / kottakkal
fcm_token       VARCHAR(512)    Legacy push-notification token (mobile)
is_active       SMALLINT        1 = active, 0 = deactivated (can't log in)
created_at      DATETIME        Set by MySQL automatically on INSERT
updated_at      DATETIME        Set by MySQL automatically on every UPDATE
deleted_at      DATETIME        NULL = live account. Timestamp = soft-deleted (can't log in)
```

**Key rule:** An employee can log in only when `is_active = 1` AND `deleted_at IS NULL`.

**Key rule:** `password_hash` is never sent to the client in any response.

### `roles` table  →  `models/role.py`

Stores the list of roles. Built-in roles are seeded at startup.

```
Column      What it stores
──────────  ───────────────────────────────────────────
id          Auto-generated PK
slug        Machine-readable name: "admin", "manager", "sales_executive"
name        Human-readable: "Admin", "Manager", "Sales Executive"
is_system   1 = can't be deleted, 0 = custom role
is_active   1 = active
```

`employees.role` stores a `slug` (not an `id`). Think of it like a label.

### `password_reset_codes` table  →  `models/password_reset.py`

Stores the one-time codes emailed for "forgot password". One row per code sent.

```
Column        What it stores
────────────  ──────────────────────────────────────────────────────────────
id            Auto-generated PK
employee_id   FK → employees.id (who requested the reset)
code_hash     HMAC-SHA256 of the 6-digit code. The actual code is NEVER stored.
attempts      How many times a wrong code was entered (locked at 5)
expires_at    When the code stops working (10 min after creation)
created_at    When the code was created
consumed_at   NULL = still usable. Timestamp = already used or invalidated.
```

---

## Piece 2 — The Config (`config.py`)

Auth-related settings. All come from environment variables (`.env` file).
Never hard-code these values.

```
Variable                    Default    Meaning
──────────────────────────  ─────────  ──────────────────────────────────────────────
SECRET_KEY                  (set it!)  The secret used to sign all JWT tokens.
                                       If leaked, anyone can forge tokens.
ALGORITHM                   HS256      The signing algorithm (HMAC-SHA256).
ACCESS_TOKEN_EXPIRE_MINUTES 7200       Token lives 5 days (5 × 24 × 60 = 7200 min).
PASSWORD_MIN_LENGTH         6          Passwords must be at least 6 characters.
PASSWORD_MAX_LENGTH         15         Passwords must be at most 15 characters.
RESET_CODE_TTL_MINUTES      10         Email code expires in 10 minutes.
RESET_CODE_RESEND_SECONDS   60         Must wait 60 s before requesting another code.
RESET_CODE_MAX_ATTEMPTS     5          5 wrong attempts locks the code.
RESET_TOKEN_TTL_MINUTES     10         The post-verify reset token lives 10 minutes.
```

---

## Piece 3 — The Auth Utility Functions (`auth.py`)

This file is the **engine room**. All password and token logic lives here.

### 3a. Password hashing

```python
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
```

**Argon2** is the recommended password-hashing algorithm (safer than bcrypt or
MD5). It intentionally takes a fraction of a second to compute, which makes
brute-force attacks impractical.

```python
def get_password_hash(password: str) -> str:
    return pwd_context.hash(password[:72])
```

Converts `"mypassword1"` → `"$argon2id$v=19$m=65536,t=3,p=4$..."`.
The result is a long random-looking string. Even if someone steals the database,
they can't reverse it to get the original password.

```python
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)
```

At login: takes the password the user typed and the stored hash, returns `True`
if they match.

### 3b. Creating a JWT token

```python
def create_access_token(employee_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": str(employee_id), "exp": expire}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
```

A JWT is three base64-encoded parts joined by dots:
`HEADER.PAYLOAD.SIGNATURE`

The **payload** here contains:
- `sub` — the employee's numeric id (as a string). "sub" is short for "subject".
- `exp` — the expiry Unix timestamp. The library refuses to decode an expired token.

The **signature** is created by signing the payload with `SECRET_KEY`. Nobody
can change the payload without breaking the signature.

The client stores this token and sends it back on every request.

### 3c. Verifying a JWT token on each request

```python
security = HTTPBearer()   # reads "Authorization: Bearer <token>" from the header

def get_current_user(credentials=Depends(security), db=Depends(get_db)) -> Employee:
    token = credentials.credentials          # the raw token string
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])  # decode + verify
    employee_id = int(payload["sub"])
    employee = db.scalar(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.deleted_at.is_(None),
            Employee.is_active == 1,
        )
    )
    return employee  # returned to the route as `current_user`
```

`get_current_user` is a **FastAPI Dependency** — you put `Depends(get_current_user)`
in a route's parameters and FastAPI calls it automatically before running your
route code. If the token is invalid or the employee is deactivated, it raises a
`401 Unauthorized` error and the route never runs.

### 3d. Role-based access control

```python
HEAD_OFFICE_ROLES = ("admin", "manager")

def require_roles(*roles):
    def checker(current_user: Employee = Depends(get_current_user)) -> Employee:
        if current_user.role not in {*roles}:
            raise HTTPException(status_code=403, detail="You do not have permission")
        return current_user
    return checker

require_admin_or_manager = require_roles(*HEAD_OFFICE_ROLES)
```

`require_roles` returns a dependency you can drop into any route. The route is
403 Forbidden unless the logged-in employee's role is in the allowed list.

Usage in a route:
```python
@router.get("/employees", ...)
def list_employees(viewer = Depends(require_admin_or_manager)):
    ...  # only admin and manager reach this line
```

---

## Piece 4 — The Pydantic Schemas

Schemas define what the API **accepts** (request body) and **returns** (response).
They live in `schemas/employee.py`. They are separate from the DB model.

### Request schemas (data coming IN from the client)

```
LoginRequest         { email: str, password: str }
                     Used by POST /auth/login

ProfileUpdate        { name?, email?, current_password? }
                     Used by PATCH /auth/me

ChangePasswordRequest  { current_password: str, new_password: str }
                       Used by POST /auth/change-password
```

### Response schemas (data going OUT to the client)

```
EmployeeResponse     id, name, email, role, phone, branch, is_active,
                     created_at, updated_at, deleted_at
                     NOTE: password_hash is intentionally excluded

LoginResponse        access_token, token_type="bearer", employee: EmployeeResponse
```

### Validation helpers in `schemas/employee.py`

```
_validate_phone()      strips spaces, enforces 7-15 digits, keeps last 10
_validate_password()   enforces min/max length and letter+digit requirement
```

These run inside Pydantic `@field_validator` methods, so bad input is rejected
before it ever reaches a route handler.

---

## Piece 5 — The Auth Routes (`routes/auth.py`)

### POST /auth/login

**What it does:** Checks email + password, returns a token.

```
Step 1: Find employee by email in DB
Step 2: verify_password(body.password, employee.password_hash)  → 401 if wrong
Step 3: Check is_active and deleted_at  → 403 if inactive/deleted
Step 4: Check platform header  → 403 if a sales exec tries to use the web
Step 5: create_access_token(employee.id)  → the JWT string
Step 6: Return { access_token, token_type, employee }
```

Why is the 401 message `"Invalid email or password"` for both wrong email AND
wrong password? Because telling the attacker *which* one is wrong gives them
extra information. A generic message is safer.

### GET /auth/me

**What it does:** Returns the logged-in employee's profile.

```
Depends(get_current_user) → finds the employee from the token → return their data
```

Apps call this on startup to restore the session without requiring a re-login.

### PATCH /auth/me

**What it does:** Lets an employee update their own name or email.

- Name change: no extra check required.
- Email change: must supply `current_password` to confirm identity.
- Checks for email uniqueness before saving.

### POST /auth/change-password

**What it does:** Changes the logged-in employee's password.

```
Step 1: Verify current_password against stored hash → 400 if wrong
Step 2: Reject if new_password == current_password
Step 3: Hash new_password and save
Step 4: Send email notification (background task — does not block the response)
```

---

## Piece 6 — Forgot Password Flow (`routes/password_reset.py`)

This is a three-step flow. No token is required — the employee is locked out.

```
Step 1 — POST /auth/password-reset/request
         Client sends: { email }
         Backend: generates a 6-digit code, stores its HMAC (not the code itself),
                  emails the code to the employee. Returns: masked email, TTLs.

Step 2 — POST /auth/password-reset/verify
         Client sends: { email, code }
         Backend: checks HMAC matches, checks not expired, checks attempt count.
         On success: marks code as consumed, returns a short-lived reset_token.
         This reset_token is a JWT with "purpose": "password_reset" + a fingerprint
         of the current password hash (so the token stops working if the password
         was already changed).

Step 3 — POST /auth/password-reset/reset
         Client sends: { reset_token, new_password }
         Backend: decodes the reset_token, checks purpose + password fingerprint,
                  validates new password, saves new hash, emails confirmation.
```

**Security details:**
- The 6-digit code itself is **never stored** — only its HMAC keyed with `SECRET_KEY`.
  A stolen database cannot be used to find valid codes.
- The `attempts` counter on the code row is incremented for each wrong try.
  5 wrong tries consume the code immediately.
- A new code invalidates all previous active codes for the same employee.
- The reset token embeds a fingerprint of the current `password_hash`. If the
  password changes (e.g. the employee remembered it and logged in), the token
  becomes invalid even if it has not expired.

---

## Piece 7 — The Platform Guard

```python
# request_context.py
def client_platform(x_client, x_client_type) -> str:
    return "mobile" if sent == "mobile" else "web"
```

The mobile app sends the header `x-client-type: mobile`. Anything else is
treated as "web" (the Next.js dashboard).

At login:
```python
if platform == "web" and employee.role not in HEAD_OFFICE_ROLES:
    raise HTTPException(403, "Please sign in on the Ogera mobile app")
```

Sales executives use the mobile app. Admins and managers use the web dashboard.
This is enforced at login only (not on individual API calls) because a client
can fake the header — the real protection is role-based: even if a sales exec
somehow got a web token, restricted routes would still return 403.

---

## Full Request Lifecycle — Login to Protected Route

```
1.  Client: POST /auth/login { email: "ali@ogera.in", password: "pass1" }
2.  Route:  find employee row WHERE email = "ali@ogera.in"
3.  auth:   verify_password("pass1", employee.password_hash)  → True
4.  Route:  checks is_active=1 and deleted_at=None and platform OK
5.  auth:   create_access_token(employee.id)
            → "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI3IiwiZXhwIjoxNzQ...
6.  Route:  returns { access_token: "eyJ...", employee: { id:7, name:"Ali", ... } }

--- (5 days pass) ---

7.  Client: GET /contacts
            Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
8.  security = HTTPBearer() extracts the token string
9.  get_current_user() decodes it → sub="7", exp=valid
10. DB query: SELECT * FROM employees WHERE id=7 AND deleted_at IS NULL AND is_active=1
11. Returns Employee object to the route as `current_user`
12. Route runs, returns contacts belonging to employee 7
```

---

## Common Errors and Their Causes

| HTTP Code | Message                            | Cause                                                        |
|-----------|------------------------------------|--------------------------------------------------------------|
| 401       | Invalid email or password          | Email not found OR password wrong                            |
| 401       | Invalid token                      | Token is malformed, tampered, or expired                     |
| 401       | Employee not found                 | Token was valid but employee deleted/deactivated since issue |
| 403       | Employee account is inactive       | `is_active=0` or `deleted_at` is set                         |
| 403       | Please sign in on the Ogera app    | Sales executive tried to use the web                         |
| 403       | You do not have permission         | Role is not in the allowed list for that route               |
| 400       | Current password is incorrect      | Wrong password on change-password or profile-update          |
| 409       | An employee with this email exists | Duplicate email on update                                    |
| 429       | Please wait N seconds              | Password reset code requested too soon                       |

---

## Files Involved in Auth (Quick Reference)

```
auth.py                      Core: hashing, token create/verify, get_current_user, require_roles
config.py                    JWT secret, token TTL, password limits, reset code config
request_context.py           Reads x-client-type header → "mobile" or "web"
models/employee.py           employees table (SQLModel)
models/role.py               roles table (DeclarativeBase)
models/password_reset.py     password_reset_codes table (DeclarativeBase)
schemas/employee.py          EmployeeCreate, EmployeeUpdate, EmployeeResponse, validators
routes/auth.py               /auth/login, /auth/me, PATCH /auth/me, /auth/change-password
routes/password_reset.py     /auth/password-reset/request, /verify, /reset
```
