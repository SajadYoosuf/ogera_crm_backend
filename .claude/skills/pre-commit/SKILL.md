---
name: pre-commit
description: Gate every backend commit — run pytest, verify imports and uv lock integrity. Use this BEFORE every git commit for any feature, fix, schema change, endpoint addition, or multi-file refactor. Triggered by "commit", "push", "ready to commit", "pre-commit", "run tests", "is it working", "done", or when about to call git commit. Do not skip for small changes — the gate is fast.
---

# Pre-commit gate (backend)

A commit is not ready when the code looks right; it is ready when this gate passes. Run every step in order. Stop at the first blocking failure and fix it before continuing.

## Priority levels

**P0 — blocking:** The commit cannot go out until these pass.
**P1 — high:** Fix before committing unless a pre-existing failure is documented.
**P2 — advisory:** Note in the commit message or change notes; may ship.

---

## Step 1 — Dependency integrity (P0)

```bash
cd ogera_crm_backend-leads
uv sync --frozen
```

Expected: resolves with no errors. A conflict or missing package here means the lockfile is stale.
If it fails: run `uv lock` to regenerate, then re-sync. Commit `uv.lock` with the feature.

**Also check:** if you added a new package, confirm it appears in `uv.lock` before continuing.

---

## Step 2 — Import smoke test (P0)

```bash
cd ogera_crm_backend-leads
python -c "import main" 2>&1
```

Expected: silent (no output). Any `ImportError`, `ModuleNotFoundError`, or traceback is a blocking failure.

This catches missing dependencies, circular imports, and broken `__init__` wiring before Vercel does.

---

## Step 3 — Run the test suite (P0 for existing tests; P1 to add new ones)

```bash
cd ogera_crm_backend-leads
pytest -x -v --tb=short 2>&1
```

Flags: `-x` stops at first failure (fast feedback); `-v` names each test; `--tb=short` gives the useful lines without noise.

**Passage threshold:**
- All existing tests must pass (P0).
- Any new feature/endpoint/service you wrote must have at least one test (P1). If no test exists yet, write it now — see `docs/modules/` for examples. Use `sqlite:///:memory:` as `DATABASE_URL` in the test environment.
- If the suite is empty (`no tests ran`), that is not a pass: add a smoke test for the main route.

**Pre-existing failures:** If a test was failing before your change (verify with `git stash && pytest; git stash pop`), document it in `docs/changes/YYYY-MM-DD-<slug>.md` under **Known issues** and proceed. Do not mask it with `pytest.mark.skip` without a comment.

---

## Step 4 — Type / lint check (P1)

```bash
cd ogera_crm_backend-leads
python -m py_compile routes/*.py services/*.py models/*.py schemas/*.py main.py 2>&1
```

Expected: silent. A `SyntaxError` is blocking. Add `ruff check .` if ruff is installed (it is not required by default).

---

## Step 5 — Conflict check (P0)

```bash
cd ogera_crm_backend-leads
git status
git diff --stat HEAD
```

Confirm:
- No merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`) in any staged file. Run `grep -rn "<<<<<<" . --include="*.py"` to be sure.
- No unintended files in the diff (`.env`, `*.pyc`, `__pycache__/`). Stage only the files for this change.
- `uv.lock` is included if dependencies changed.

---

## Step 6 — Change note (P0 for major changes; P2 for small fixes)

Major changes (new endpoint, schema change, role-access change, multi-file refactor) require a change note at `docs/changes/YYYY-MM-DD-<slug>.md` with:

- **What changed** — files and behaviour.
- **Access / data impact** — any role, tenant or data effect.
- **Verification** — paste the pytest summary line (`N passed in X.Xs`) and any step that was not run.
- **Known issues** — pre-existing failures, skipped tests, or items not done.

Small fixes (one-line, typo, style) need no note but the commit message must say what changed and why.

---

## Gate summary

| Step | Priority | Pass condition |
|------|----------|----------------|
| `uv sync --frozen` | P0 | No errors |
| `python -c "import main"` | P0 | Silent |
| `pytest -x -v` | P0 | All tests pass; new features have tests |
| `py_compile` on changed files | P1 | No syntax errors |
| Conflict markers absent | P0 | `grep` finds nothing |
| Change note written | P0 (major) / P2 (fix) | File exists before commit |

**Do not call `git commit` until all P0 steps pass.**
When all P0 steps pass and ≥ 80% of P1 steps pass, the commit is ready.
Report the result in a single table (step, status, notes) before committing.
