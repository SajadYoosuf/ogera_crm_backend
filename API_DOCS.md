# Ogera CRM — Auth API Reference

Base URL: `https://<your-vercel-domain>/`

All protected endpoints require a `Bearer` token in the `Authorization` header.

---

## Headers

| Header | Required on | Value |
|---|---|---|
| `Content-Type` | POST/PATCH requests | `application/json` |
| `Authorization` | All protected endpoints | `Bearer <access_token>` |

---

## Endpoints

### 1. Login

**`POST /auth/login`**

Sign in with email and password. Returns a JWT and the employee's profile.

**Request body**
```json
{
  "email": "admin@example.com",
  "password": "Passw0rd!"
}
```

**Success response — `200 OK`**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "company": {
    "id": 1,
    "name": "Ventino Homes",
    "logo_url": "https://example.com/logo.png",
    "status": "active"
  },
  "employee": {
    "id": 1,
    "name": "Ada Admin",
    "email": "admin@example.com",
    "phone": "9000000001",
    "role_id": 1,
    "role_slug": "admin",
    "is_active": 1,
    "created_at": "2024-01-01T10:00:00",
    "updated_at": "2024-01-01T10:00:00",
    "deleted_at": null
  }
}
```

**Error responses**

| Status | When |
|---|---|
| `401` | Email not found, or wrong password |
| `403` | Account is inactive or deleted |
| `503` | Database unavailable |

```json
{ "detail": "Invalid email or password" }
{ "detail": "Employee account is inactive" }
{ "detail": "Server unavailable, please retry in a moment" }
```

> The 401 message is the same whether the email doesn't exist or the password is wrong.
> This is intentional — it prevents attackers from discovering which emails are registered.

---

### 2. Get My Profile

**`GET /auth/me`**

Returns the profile of the currently signed-in employee.
Use this on app start to restore the session from a saved token.

**Headers**
```
Authorization: Bearer <access_token>
```

**Success response — `200 OK`**
```json
{
  "company": {
    "id": 1,
    "name": "Ventino Homes",
    "logo_url": "https://example.com/logo.png",
    "status": "active"
  },
  "employee": {
    "id": 1,
    "name": "Ada Admin",
    "email": "admin@example.com",
    "phone": "9000000001",
    "role_id": 1,
    "role_slug": "admin",
    "is_active": 1,
    "created_at": "2024-01-01T10:00:00",
    "updated_at": "2024-01-01T10:00:00",
    "deleted_at": null
  }
}
```

**Error responses**

| Status | When |
|---|---|
| `401` | Token missing, expired, invalid, or password changed since token was issued |
| `403` | Account has been deactivated |
| `503` | Database unavailable |

```json
{ "detail": "Authentication required" }
{ "detail": "Invalid token" }
{ "detail": "Your login has expired. Please log in again to continue." }
{ "detail": "Your account has been deactivated. Please contact your administrator." }
```

---

### 3. Change Password

**`POST /auth/change-password`**

Change the signed-in employee's password. Returns a fresh token so the employee
stays signed in after the change.

**Headers**
```
Authorization: Bearer <access_token>
Content-Type: application/json
```

**Request body**
```json
{
  "current_password": "Passw0rd!",
  "new_password": "NewPass1"
}
```

Password rules for `new_password`:
- Minimum 6 characters
- At least one letter and one digit

**Success response — `200 OK`**
```json
{
  "message": "Password changed",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

> Save the new `access_token` and discard the old one. The old token is now invalid.

**Error responses**

| Status | When |
|---|---|
| `400` | Wrong current password, or new password same as current, or weak password |
| `401` | Token invalid or expired |
| `503` | Database unavailable |

```json
{ "detail": "The password you entered doesn't match your current one." }
{ "detail": "New password must be different from the current one" }
{ "detail": "must contain at least one letter and one digit" }
```

---

### 4. Forgot Password — Step 1: Request a code

**`POST /auth/password-reset/request`**

Emails a 6-digit OTP to the employee's registered address.  
Any previously active code for that account is cancelled.  
No auth token required.

**Request body**
```json
{ "email": "employee@example.com" }
```

**Success response — `200 OK`**
```json
{
  "email_masked": "e***e@example.com",
  "expires_in_seconds": 600
}
```

**Error responses**

| Status | `detail` | When |
|---|---|---|
| `422` | Validation message | Missing or malformed email |
| `404` | `"No active account uses this email."` | Unknown email, or account inactive/deleted |
| `503` | `"Could not send reset code. Please try again."` | Mail server unreachable |

---

### 5. Forgot Password — Step 2: Verify the code

**`POST /auth/password-reset/verify`**

Checks the 6-digit code from the email. Returns a short-lived reset token on success.  
The code is single-use and expires after 10 minutes.  
No auth token required.

**Request body**
```json
{
  "email": "employee@example.com",
  "code": "483921"
}
```

`code` must be exactly 6 digits (`0–9`), no letters or spaces.

**Success response — `200 OK`**
```json
{
  "reset_token": "eyJhbGci...",
  "expires_in_seconds": 600
}
```

**Error responses**

| Status | `detail` | When |
|---|---|---|
| `422` | Validation message | Bad email, code wrong length, or code has non-digits |
| `404` | `"No active account uses this email."` | Account not found or deactivated |
| `400` | `"No active code. Request a new code."` | No code requested yet, or already used |
| `400` | `"This code has expired. Request a new code."` | Code older than 10 minutes |
| `400` | `"Incorrect code. Please check your email and try again."` | Wrong code |

---

### 6. Forgot Password — Step 3: Set new password

**`POST /auth/password-reset/reset`**

Sets a new password using the `reset_token` from Step 2.  
The token is single-use — the password fingerprint inside it becomes stale after the change.  
No auth token required.

**Request body**
```json
{
  "reset_token": "eyJhbGci...",
  "new_password": "NewPass1"
}
```

Password rules: 6–15 characters, at least one letter and one digit.

**Success response — `200 OK`**
```json
{ "message": "Password updated. You can now log in with your new password." }
```

Navigate to the login screen after success. No token is returned.

**Error responses**

| Status | `detail` | When |
|---|---|---|
| `422` | Validation message | Missing token, weak password |
| `400` | `"This reset link has expired or was already used. Start again."` | Token expired, used, or tampered |
| `400` | `"Your new password must be different from your current password."` | Same as current password |
| `503` | `"Could not save your new password. Please try again in a moment."` | DB error |

---

## Token

The token is a **JWT (JSON Web Token)**. It contains:

| Claim | Meaning |
|---|---|
| `sub` | Employee ID |
| `tenant` | PostgreSQL schema name (e.g. `"ventino"`) |
| `phf` | Password fingerprint — invalidated if password changes |
| `exp` | Expiry timestamp |

**Default expiry: 5 days.**

The token is rejected (`401`) if:
- It is expired
- The employee changed their password after the token was issued
- The employee was deleted

---

## Employee object fields

| Field | Type | Notes |
|---|---|---|
| `id` | int | Employee ID |
| `name` | string | Full name |
| `email` | string | Login email |
| `phone` | string \| null | 10-digit normalised phone |
| `role_id` | int | FK to roles table |
| `role_slug` | string \| null | e.g. `"admin"`, `"manager"`, `"sales_executive"` |
| `is_active` | int | `1` = active, `0` = deactivated |
| `created_at` | datetime | ISO 8601 |
| `updated_at` | datetime | ISO 8601 |
| `deleted_at` | datetime \| null | null = not deleted |

---

## Common role slugs

| Slug | Access level |
|---|---|
| `admin` | Full access — manage employees, roles, settings |
| `manager` | Same as admin |
| `sales_executive` | Own leads and contacts only |
