# Contacts sort

## Why
The Contacts design has a "Sort by" control, and the list must be sorted on the server so paging stays correct.

## What changes
- `GET /contacts` accepts `sort`: `newest` (default, created date descending), `oldest`, `name_asc`, `name_desc` (case-insensitive name). Each order ends with the id so pages do not repeat rows.
- Unknown `sort` values return HTTP 422.

## Out of scope
- No new endpoint and no schema change. Other backend work in the tree is not part of this change.

## Verification
- Module parses; the staged file was checked on its own. No automated test covers the sort yet.
