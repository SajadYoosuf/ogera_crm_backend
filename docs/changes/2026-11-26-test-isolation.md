# Tests no longer use `.env` or reach live services

## What changed

- `tests/isolation.py` (new), called by `tests/conftest.py` and `tests/e2e_server.py` before any app import:
  - stops `.env` from loading and blanks the database, Firebase, SMTP and WhatsApp credentials, including ones
    set in the shell;
  - forces `DATABASE_URL=sqlite://`. Before, a `DATABASE_URL` already set in the shell was used as it was, and
    the test fixtures drop every table;
  - refuses connections that leave the machine. A pytest run lists any host that was refused.
- `tests/test_isolation.py` (new) covers those three points and that loopback still works.

## Access / data impact

- Test code only. The app reads `.env` as before.
- The mobile end-to-end server no longer sends WhatsApp messages, email or push.
- A test that needs a setting must set it itself, for example with `monkeypatch`.

## Verification

- `tests/test_isolation.py`: 4 passed, also with a fake PostgreSQL URL and fake credentials set in the shell.
- `test_whatsapp_reply_validation` passes with `.env` present.
- Full suite without `tests/test_contacts_module.py`, on a clean copy of commit `023832d` plus these files:
  140 failed, 357 passed, 2 xfailed. The same commit without them and without `.env`: 138 failed, 355 passed,
  2 xfailed. The four tests that differ between the two runs each passed in most or all repeated runs alone.
- Full suite in the working tree with `.env` present: no outside connection was attempted.
- Not run: the mobile end-to-end test.

## Known issues or not done yet

- Some tests fail at random on both versions, with "Could not save. Please try again." on lead creation. A likely
  cause, not proven, is the push dispatch thread in `services/notification_service.py` sharing the in-memory
  SQLite connection.
- The connection block covers Python sockets and name lookups. An asyncio connection to a bare IP address on
  Windows would not pass through it.
- Scripts and migrations outside `tests/` still read `.env`.
