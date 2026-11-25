# Fix Vercel build failure + add pre-commit gate skill

## What changed

**pyproject.toml** — added `[tool.setuptools] packages = []` and removed the unused `[project.scripts]` entry.

Vercel passes `--no-editable` to `uv sync`, which triggers a setuptools wheel build even though `tool.uv.package = false`. Setuptools' flat-layout auto-discovery then finds `models/`, `routes/`, `schemas/`, `services/` and aborts with *"Multiple top-level packages discovered"*. Explicitly declaring `packages = []` tells setuptools there is nothing to build, so the build backend completes cleanly.

**uv.lock** — regenerated after pyproject.toml change. No dependency versions changed.

**.claude/skills/pre-commit/SKILL.md** — new skill that gates every backend commit. Steps: `uv sync --frozen`, `python -c "import main"`, `pytest -x -v`, `py_compile` on changed files, conflict-marker check, change-note check. Commit blocked until all P0 steps pass.

## Access / data impact

None. Build configuration only; no runtime behaviour changed.

## Verification

- `uv lock`: resolved 62 packages in 1 ms — no errors.
- `uv run python -c "import main"`: silent (OK).
- `uv run pytest -x -v --tb=short`: 24 passed, 1 pre-existing failure (`test_analyze_invalid_house_stage_warns` — House stage custom field not seeded in test DB; existed before this change, confirmed by running the test on the unpatched HEAD via `git stash`).

## Known issues

`tests/test_contact_import.py::test_analyze_invalid_house_stage_warns` — pre-existing. The test expects a stage-validation warning on the `House stage` column, but the in-memory test database does not seed custom field definitions, so the column is treated as unmapped. No regression introduced here.
