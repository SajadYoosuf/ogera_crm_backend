# Configurable CORS origins and the live API

## Why
The web app is moving to the live API (https://ogera-crm.onrender.com). The backend only allowed `http://localhost:3000`, so a deployed web app would be blocked by the browser.

## What changes
- `config.py`: `CORS_ORIGINS` from the environment variable of the same name (comma separated); default `http://localhost:3000`.
- `main.py`: CORS middleware uses it.
- On Render, set `CORS_ORIGINS` to every web address that calls the API, for example `http://localhost:3000,https://<your-web-app>`.
- Web side: set `NEXT_PUBLIC_API_BASE_URL=https://ogera-crm.onrender.com` (build-time variable on the web host, `.env.local` for local development). The example env files are git-ignored, so this is documented here.

## Out of scope
Deployment settings themselves (Render start command and environment variables are edited in the Render dashboard).

## Verification
Import the app with and without `CORS_ORIGINS`; call the live API from the web app and confirm no CORS error in the browser console.
