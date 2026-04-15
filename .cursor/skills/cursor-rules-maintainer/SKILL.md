---
name: cursor-rules-maintainer
description: Create/update Cursor project rules (.mdc): correct frontmatter, globs scoping, token-efficient content.
---

# Cursor Rules Maintainer

## Use when
- You need to add/change **Cursor project rules** in `.cursor/rules/`.
- You want rules that are **token-efficient** and scoped with globs.

## Rule file format (recommended)
- Use `.mdc` files in `.cursor/rules/`.
- Add YAML frontmatter (common fields):
  - `description`: short, specific trigger text
  - `globs`: comma-separated glob patterns (ex: `*.py,tests/**/*.py`)
  - `alwaysApply`: true/false

## Best practices
- Keep `alwaysApply: true` rules **small** (core guardrails only).
- Prefer scoped rules via `globs` to avoid token waste.
- Make descriptions specific so the agent can choose the right rule.
- If a rule becomes long, split it by responsibility (env / testing / style / framework).

## Troubleshooting checklist
- If rules don't appear/apply:
  - Ensure the file extension is `.mdc`
  - Reload the window / restart Cursor
  - Try creating one rule via Settings → Rules UI (some versions differ)
