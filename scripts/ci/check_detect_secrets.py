#!/usr/bin/env python3
"""Parse detect-secrets JSON output and exit non-zero if findings exist.

Usage:
  python3 scripts/ci/check_detect_secrets.py <path-to-detect-secrets.json> <fail-true-or-false>

The second argument should be the string \"true\" or \"false\" (CodeBuild
passes SECURITY_FAIL_BUILD).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: check_detect_secrets.py <json_path> <fail_true_or_false>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    fail = str(sys.argv[2]).strip().lower() in ("1", "true", "yes")
    if not path.is_file():
        print(f"detect-secrets report not found: {path}", file=sys.stderr)
        return 1 if fail else 0
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data.get("results") or {}
    count = sum(len(v) for v in results.values())
    if count:
        print(f"detect-secrets: {count} potential secret(s) found:")
        for fname, secrets in results.items():
            for entry in secrets:
                print(f"  {fname}: {entry}")
        return 1 if fail else 0
    print("detect-secrets PASSED - no secrets found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
