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
    "timezone": "Asia/Kolkata"
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
    "timezone": "Asia/Kolkata"
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
