---
name: python-new-project-bootstrap
description: Bootstrap a new Python repo on Windows PowerShell: .venv, src/tests, Black+Ruff+pytest, VS Code configs.
---

# Python New Project Bootstrap (Windows / PowerShell)

## Use when
- Starting a **new Python project** from scratch.
- Creating the **initial repo structure**, tooling, and debug/test setup.
- You want repeatable scaffolding: `.venv`, `src/`, `tests/`, `.vscode/`, `.logs/`, and baseline docs.

## Workflow (do this in order)
1. **Inspect constraints**
   - Read existing `workspace_requirements.txt` / README if present.
   - Confirm Python version target if stated; otherwise assume the system default (python.org installer or winget-managed).

2. **Create structure**
   - `src/`, `tests/`, `.vscode/`, `.logs/`
   - Do **not** "create .venv as a folder"; create the venv via PowerShell command.

3. **Create the virtual environment**
   - PowerShell:
     - `python -m venv .venv`
     - `.venv\Scripts\Activate.ps1`
     - If execution policy blocks activation: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

4. **Dependencies**
   - Add initial `requirements.txt`.
   - If the project uses Black/Ruff/PyTest/Pyright, ensure they are included (or document how they're installed).

5. **Editor tooling**
   - Add `.vscode/settings.json` (format-on-save, ruff/black integration)
   - Add `.vscode/launch.json`:
     - Run current file
     - Debug pytest (purpose: debug-test)
   - Add `.vscode/tasks.json`:
     - lint, format, test commands

6. **Logging**
   - Ensure `.logs/` exists.
   - Standard: console + timed rotating file `./.logs/app.log` (nightly rotation, ~7 backups).

7. **Tests**
   - Add at least one "smoke test" demonstrating import + basic behavior.

8. **Docs**
   - Create/Update:
     - `README.md` (setup + run + test commands for Windows PowerShell)
     - `IMPLEMENTATION_STATUS.md` (what exists, what's next)

## Definition of Done
- Repo runs in **Windows PowerShell** using `.venv`
- `pytest -q` works
- Formatting/linting instructions are present
- Debug configs exist in `.vscode/`
- `README.md` includes exact commands for Windows PowerShell
