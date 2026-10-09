# Contact import and export

## Why
Contacts could only be loaded through one-off CLI scripts with hardcoded layouts, and could not be exported at all. Staff need to import messy sheets safely and export filtered lists as CSV, Excel or PDF.

## What changes
- `contact_import.py` (new): reads CSV/XLSX, finds the header row (title rows above it are skipped), maps headers by alias, normalizes phones, validates each row and classifies it as `ready`, `warning`, `duplicate` or `error`. `analyze` never writes; `run_import` writes once.
- `contact_export.py` (new): builds CSV (UTF-8 BOM), XLSX (openpyxl, phones kept as text, formula-looking cells escaped) and PDF (reportlab, landscape, repeated header).
- `routes/contacts.py`: list filters moved into `contact_filters` / `contact_query` so list and export share them; new filters `created_from` / `created_to`; new routes `GET /contacts/export`, `GET /contacts/import/template`, `POST /contacts/import/analyze`, `POST /contacts/import/commit`.
- New dependencies: `openpyxl`, `reportlab`, `python-multipart` (added to `requirements.txt`).
- Endpoint docs in the root `API.md`.

## Out of scope
- Mobile screens, Google Sheets / JSON input, background jobs for very large files, and replacing the legacy `scripts/import_*.py`.

## Verification
- Messy sheet (title rows, odd headers, bad phones, in-file and CRM duplicates, bad date, unknown type/source) classified correctly; XLSX float phones read as text.
- Against the dev database: export returns 200 for csv, xlsx and pdf; a sales-role user can export (own contacts only) but gets 403 on import; wrong file type gets 400; bad format gets 422.
- `run_import` tested in a transaction that was rolled back (creates consumer and partner with profile and stage, fills blanks on a duplicate).

## Deviations / notes
- Import is admin/manager only; other roles export only contacts assigned to them.
- PDF only shows Malayalam text when the server has a Malayalam font (`assets/fonts/NotoSansMalayalam-Regular.ttf` or a system one); otherwise it falls back to Helvetica.
