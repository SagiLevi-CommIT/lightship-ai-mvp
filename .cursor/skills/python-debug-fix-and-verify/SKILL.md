---
name: python-debug-fix-and-verify
description: Debug a failing behavior: reproduce, isolate, write regression test, minimal fix, run verification, update status.
---

# Python Debug / Fix / Verify

## Use when
- There is a bug, exception, failing test, or wrong output.
- You want a strict "repro → test → fix → verify" loop.

## Workflow
1. **Reproduce**
   - Run the failing command in Terminal within `.venv`.
   - Capture the full traceback/logs.

2. **Isolate**
   - Identify the smallest module/function responsible.
   - Avoid refactors unless required.

3. **Add a regression test**
   - Prefer pytest.
   - If you must create a standalone repro script, add:
     - `sys.path.append("src")` at the top (repo convention).

4. **Fix**
   - Minimal change that makes the test pass.
   - Keep behavior stable for unaffected paths.

5. **Verify**
   - Run:
     - `python -m pytest -q` (or the project's test command)
   - If no tests exist, run a deterministic verification script/command.

6. **Update docs**
   - Update `IMPLEMENTATION_STATUS.md` (include the exact test command + pass/fail).
   - Update README / summaries if behavior changed.

## Output expectations
- A clear "What broke / Why / What changed / How verified" note (short).
