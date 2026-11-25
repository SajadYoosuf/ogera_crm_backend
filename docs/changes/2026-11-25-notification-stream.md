# Live notification stream (server-sent events)

## What changed
- `routes/notifications.py`: new `GET /notifications/stream`. It returns `text/event-stream`, keeps the connection open, and sends one `event: notification` (the same JSON as the list endpoint) for each notification created for the caller after the connection opened. Notifications that already existed when it connected are not re-sent. The server checks the database every 3 s, looks back 2 min so late commits are not missed, sends a `: ping` every 15 s, and closes after 10 min so the client reconnects.
- `tests/test_notification_stream.py`: a new notification is pushed once; an older one is not.

## Access / data impact
- Each user receives only their own notifications (filtered by recipient id, same as the list endpoint). Same JWT in the Authorization header.
- One lightweight query every 3 s per open stream. Works with any number of uvicorn workers, because the database is the source of truth.

## Verification
- `pytest tests/test_notification_stream.py`: 2 pass.
- Not run: a live browser session against the deployed API.

## Known issues or not done yet
- The stream ends after 10 min; clients reconnect with backoff.
- Pushes arrive within about 3 s of the row being committed.
