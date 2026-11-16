# Password Reset API — Frontend Integration Guide

**Base URL:** `https://your-api-domain.com`  
**All requests:** `Content-Type: application/json`  
**All responses:** `Content-Type: application/json`

Every error — validation or business logic — returns the same shape:

```json
{ "detail": "Human-readable message" }
```

---

## Flow Overview

```
Step 1  POST /auth/password-reset/request   →  emails a 6-digit code
Step 2  POST /auth/password-reset/verify    →  validates code, returns reset_token
Step 3  POST /auth/password-reset/reset     →  sets new password using reset_token
```

No auth token is required for any of these three steps.

---

## Step 1 — Request a reset code

### `POST /auth/password-reset/request`

Emails a 6-digit OTP to the employee's registered email address.  
Any previously active code for that account is voided.  
A 60-second cooldown is enforced between requests.

### Request body

| Field   | Type   | Required | Validation                    |
|---------|--------|----------|-------------------------------|
| `email` | string | ✅        | Must be a valid email address |

```json
{
  "email": "employee@example.com"
}
```

### Success response — `200 OK`

| Field                | Type    | Description                                         |
|----------------------|---------|-----------------------------------------------------|
| `email_masked`       | string  | Partially hidden address, e.g. `s***d@gmail.com`    |
| `expires_in_seconds` | integer | How long the code is valid (default: 600 = 10 min)  |
| `resend_in_seconds`  | integer | Cooldown before another code can be requested (default: 60) |

```json
{
  "email_masked": "s***d@gmail.com",
  "expires_in_seconds": 600,
  "resend_in_seconds": 60
}
```

### Error responses

| Status | `detail` message | When it happens |
|--------|------------------|-----------------|
| `422`  | `"Email is required"` | No email field sent |
| `422`  | `"Email must be a valid email address"` | Malformed email (missing @, etc.) |
| `404`  | `"No active account uses this email."` | Email not found, or account is deactivated/deleted |
| `429`  | `"Please wait {N} seconds before requesting another code."` | Requested again before cooldown expired |
| `503`  | `"Could not send reset code"` | Mail server unreachable — no code row saved |

### Usage notes

- Show `email_masked` as confirmation: _"Code sent to s\*\*\*d@gmail.com"_
- Use `resend_in_seconds` to drive a countdown timer on the "Resend" button
- Use `expires_in_seconds` to show how long the user has to enter the code
- On `404`: show a neutral message like _"If this email is registered, a code has been sent"_ — do not confirm whether the account exists
- On `429`: start a countdown using the number in `detail`

---

## Step 2 — Verify the code

### `POST /auth/password-reset/verify`

Checks the 6-digit code the user received by email.  
Returns a short-lived `reset_token` on success.  
After 5 wrong attempts the code is locked — the user must request a new one.

### Request body

| Field   | Type   | Required | Validation                                       |
|---------|--------|----------|--------------------------------------------------|
| `email` | string | ✅        | Must be a valid email address                     |
| `code`  | string | ✅        | Exactly 6 digits (`0–9`), no letters or spaces   |

```json
{
  "email": "employee@example.com",
  "code": "483921"
}
```

### Success response — `200 OK`

| Field                | Type    | Description                                             |
|----------------------|---------|---------------------------------------------------------|
| `reset_token`        | string  | JWT to use in Step 3. Single-use, tied to this account. |
| `expires_in_seconds` | integer | Token validity window (default: 600 = 10 min)           |

```json
{
  "reset_token": "eyJhbGci...",
  "expires_in_seconds": 600
}
```

### Error responses

| Status | `detail` message | When it happens |
|--------|------------------|-----------------|
| `422`  | `"Email must be a valid email address"` | Malformed email |
| `422`  | `"Code must be at least 6 characters long"` | Fewer than 6 characters sent |
| `422`  | `"Code must be 6 characters or fewer"` | More than 6 characters sent |
| `422`  | `"Code must be exactly 6 digits"` | Contains letters, spaces, or symbols |
| `404`  | `"No active account uses this email."` | Account not found or deactivated |
| `400`  | `"No active code. Request a new code."` | No code was ever requested, or it was already used |
| `400`  | `"This code has expired. Request a new code."` | Code older than 10 minutes |
| `400`  | `"Too many wrong attempts. Request a new code."` | 5th wrong attempt — code is now locked |
| `400`  | `"Incorrect code. {N} attempts left."` | Wrong code, still has attempts remaining |

### Usage notes

- Store `reset_token` in memory only — not in `localStorage` (expires in 10 min)
- Display remaining attempts from the `detail` message: _"Incorrect code. 3 attempts left."_
- On `"Too many wrong attempts"` and `"This code has expired"`: redirect to Step 1
- On `"No active code"`: the user probably already completed the flow — redirect to login
- Strip leading/trailing spaces from the code input before sending — the API rejects anything that isn't exactly 6 digits

---

## Step 3 — Set a new password

### `POST /auth/password-reset/reset`

Sets a new password using the `reset_token` from Step 2.  
The token is single-use — once the password changes, the token stops working.  
Any existing login sessions for this account are automatically invalidated.  
The employee is emailed a confirmation notice.

### Request body

| Field          | Type   | Required | Validation |
|----------------|--------|----------|------------|
| `reset_token`  | string | ✅        | 1–512 characters |
| `new_password` | string | ✅        | 6–15 characters, must contain at least one letter and one digit |

```json
{
  "reset_token": "eyJhbGci...",
  "new_password": "NewPass1"
}
```

### Success response — `200 OK`

```json
{
  "message": "Password updated. You can now log in with your new password."
}
```

> **No token is returned.** After success, navigate the user to the login screen.  
> Any old token they had is now dead.

### Error responses

| Status | `detail` message | When it happens |
|--------|------------------|-----------------|
| `422`  | `"Reset token is required"` | Blank or missing token |
| `422`  | `"New password is required"` | Blank or missing password |
| `422`  | `"New password must be at least 6 characters"` | Password too short |
| `422`  | `"New password must be at most 15 characters"` | Password too long |
| `422`  | `"New password must contain at least one letter and one digit"` | Only letters or only digits |
| `400`  | `"This reset link has expired or was already used. Start again."` | Token expired, already used, or tampered |
| `400`  | `"Your new password must be different from your current password."` | Same as current password |
| `503`  | `"Could not save your new password. Please try again in a moment."` | DB unavailable at save time |

### Usage notes

- After `200`: clear `reset_token` from memory and redirect to login
- On any `400` from this step: redirect to Step 1 — the token cannot be recovered
- On `503`: show a retry button — the password was **not** changed

---

## Password Rules (applies to all endpoints)

| Rule | Value |
|------|-------|
| Minimum length | 6 characters |
| Maximum length | 15 characters |
| Must contain | At least one letter AND at least one digit |
| Symbols allowed | Yes (`@`, `!`, `#`, `_`, etc.) |
| Spaces allowed | Yes |

**Show these rules in the UI** before or alongside the password input field.

**Examples of rejected passwords:**

| Password | Reason |
|----------|--------|
| `abc` | Too short |
| `abcdefg` | No digit |
| `1234567` | No letter |
| `abcdefghijklmnop` | Too long (16 chars) |

**Examples of accepted passwords:**

| Password | Why it passes |
|----------|---------------|
| `abc123` | 6 chars, has letter and digit |
| `Pass12` | 6 chars, has letter and digit |
| `Kerala2026` | 10 chars, has letter and digit |
| `p@ss!w0rd` | Symbols allowed |

---

## Complete flow — example sequence

```http
POST /auth/password-reset/request
{ "email": "sajad@ogera.in" }

→ 200
{
  "email_masked": "s***d@ogera.in",
  "expires_in_seconds": 600,
  "resend_in_seconds": 60
}
```

```http
POST /auth/password-reset/verify
{ "email": "sajad@ogera.in", "code": "391047" }

→ 200
{
  "reset_token": "eyJhbGci...",
  "expires_in_seconds": 600
}
```

```http
POST /auth/password-reset/reset
{ "reset_token": "eyJhbGci...", "new_password": "NewPass1" }

→ 200
{ "message": "Password updated. You can now log in with your new password." }
```

---

## Authenticated password change

For a **logged-in** employee who knows their current password.

### `POST /auth/change-password`

**Requires:** `Authorization: Bearer <token>` header.  
Returns a fresh token — **replace the stored token immediately** or the next request will fail with 401.

### Request body

| Field              | Type   | Required | Validation |
|--------------------|--------|----------|------------|
| `current_password` | string | ✅        | 6–15 characters |
| `new_password`     | string | ✅        | 6–15 characters, must contain at least one letter and one digit |

```json
{
  "current_password": "OldPass1",
  "new_password": "NewPass2"
}
```

### Success response — `200 OK`

| Field          | Type   | Description                               |
|----------------|--------|-------------------------------------------|
| `message`      | string | Confirmation text                         |
| `access_token` | string | Fresh JWT — replace the stored token now  |
| `token_type`   | string | Always `"bearer"`                         |

```json
{
  "message": "Password changed",
  "access_token": "eyJhbGci...",
  "token_type": "bearer"
}
```

### Error responses

| Status | `detail` message | When it happens |
|--------|------------------|-----------------|
| `401`  | `"Authentication required"` | No Bearer token sent |
| `401`  | `"Session expired, please log in again"` | Token valid but password was already changed elsewhere |
| `422`  | `"Current password must be at least 6 characters long"` | Too short |
| `422`  | `"New password must contain at least one letter and one digit"` | Complexity rule failed |
| `400`  | `"The password you entered doesn't match your current one. Please try again."` | Wrong current password |
| `400`  | `"New password must be different from the current one"` | Same as existing |
| `503`  | `"Could not save your new password. Please try again in a moment."` | DB unavailable |

### Usage notes

- After `200`: replace `access_token` in storage with the new one — the old token is now dead
- If the user is on two devices and changes password on one, the other device gets `401 "Session expired"` on its next request — redirect to login
