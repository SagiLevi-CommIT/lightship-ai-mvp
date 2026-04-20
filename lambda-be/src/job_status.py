"""Job status / progress persistence for the Lightship MVP backend.

Single source of truth for progress writes. Every mutation updates both:

1. ``processing_status`` — a module-level dict that acts as a warm cache
   on whichever Lambda container processes the request. Readers on the
   same container get instant updates without a Dynamo read.
2. The ``lightship_jobs`` DynamoDB row (when ``set_table`` has been
   called with a live table) — so cross-invocation reads (and the
   ``/jobs`` / ``/history`` views) always see consistent values.

All writes alias every attribute via ``ExpressionAttributeNames`` so
reserved words (``status``, ``message``, ``progress``, ``name``) never
cause a silent ``ValidationException``. The function signatures are the
public contract relied on by ``api_server.py`` and ``lambda_function.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Warm cache + table handle (module-level so callers can mutate without an
# import cycle; ``api_server`` re-exports ``processing_status``).
# ---------------------------------------------------------------------------
processing_status: Dict[str, Dict[str, Any]] = {}
_jobs_table = None


def set_table(table) -> None:
    """Bind a boto3 DynamoDB table resource for all writes.

    Safe to call with ``None`` to operate in warm-cache-only mode (unit
    tests use this to avoid touching real AWS).
    """
    global _jobs_table
    _jobs_table = table


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alias_update(attrs: Dict[str, Any]) -> Dict[str, Any]:
    """Build a ``update_item`` kwarg bundle with fully aliased names.

    Every attribute name is aliased (``#a0``, ``#a1`` …) and every value
    is bound (``:v0`` …), so reserved words never leak into the
    expression. ``None`` values are filtered out so callers can pass
    ``completed_at=None`` unconditionally without zeroing the row.
    """
    filtered = {k: v for k, v in attrs.items() if v is not None}
    if not filtered:
        return {}

    set_parts = []
    names: Dict[str, str] = {}
    values: Dict[str, Any] = {}
    for i, (key, val) in enumerate(sorted(filtered.items())):
        name_alias = f"#a{i}"
        value_alias = f":v{i}"
        names[name_alias] = key
        values[value_alias] = val
        set_parts.append(f"{name_alias} = {value_alias}")
    return {
        "UpdateExpression": "SET " + ", ".join(set_parts),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": values,
    }


# ---------------------------------------------------------------------------
# Public API — kept stable so api_server.py / lambda_function.py don't break.
# ---------------------------------------------------------------------------
def put_job(job_id: str, **attrs: Any) -> None:
    """Create (or overwrite) the initial job row. ``None`` values are
    dropped so callers can pass every optional attribute up front.
    """
    now = _now_iso()
    item: Dict[str, Any] = {"job_id": job_id, "created_at": now, "updated_at": now}
    for k, v in attrs.items():
        if v is not None:
            item[k] = v

    # Keep warm-cache in sync so subsequent reads don't race the Dynamo
    # commit on the same container.
    processing_status[job_id] = {
        "status": item.get("status", "QUEUED"),
        "progress": float(item.get("progress", 0.0)),
        "message": item.get("message", "Queued"),
        "current_step": item.get("current_step", "queued"),
    }

    if _jobs_table is None:
        return
    try:
        _jobs_table.put_item(Item=item)
    except Exception as e:  # noqa: BLE001
        logger.warning("DynamoDB put_item failed for %s: %s", job_id, e)


def update_status(job_id: str, status: str, **extra: Any) -> None:
    """Move a job to ``status`` and set any extra attributes.

    Uses aliased UpdateExpression so reserved words (``status``) work.
    """
    now = _now_iso()
    payload = {"status": status, "updated_at": now, **extra}
    if status.upper() == "COMPLETED" and "completed_at" not in payload:
        payload["completed_at"] = now

    cached = processing_status.setdefault(job_id, {})
    for k, v in payload.items():
        if v is not None:
            cached[k] = v

    if _jobs_table is None:
        return
    kwargs = _alias_update(payload)
    if not kwargs:
        return
    try:
        _jobs_table.update_item(Key={"job_id": job_id}, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("DynamoDB update_status failed for %s: %s", job_id, e)


def write_progress(
    job_id: str,
    *,
    status: str,
    progress: float,
    message: str,
    current_step: str,
    **extra: Any,
) -> None:
    """Write a granular progress beacon.

    Updates both the warm cache and DynamoDB so the UI sees monotonic
    progress regardless of which Lambda container handles the next
    ``/status`` GET. ``progress`` is coerced to a float in [0.0, 1.0].
    """
    p = max(0.0, min(1.0, float(progress)))
    payload = {
        "status": status,
        "progress": p,
        "message": message,
        "current_step": current_step,
        "updated_at": _now_iso(),
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    if status.upper() == "COMPLETED":
        payload.setdefault("completed_at", _now_iso())

    processing_status[job_id] = {**processing_status.get(job_id, {}), **payload}

    if _jobs_table is None:
        return
    kwargs = _alias_update(payload)
    if not kwargs:
        return
    try:
        _jobs_table.update_item(Key={"job_id": job_id}, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("DynamoDB write_progress failed for %s: %s", job_id, e)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Read the full Dynamo row, or ``None`` if missing."""
    if _jobs_table is None:
        return None
    try:
        resp = _jobs_table.get_item(Key={"job_id": job_id})
        return resp.get("Item")
    except Exception as e:  # noqa: BLE001
        logger.warning("DynamoDB get_item failed for %s: %s", job_id, e)
        return None


def read_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Normalised status row that the ``/status`` endpoint returns.

    Warm-cache first, then Dynamo fallback. Always returns the same
    keys so the UI's type signatures line up: ``status``, ``progress``,
    ``message``, ``current_step``.
    """
    cached = processing_status.get(job_id)
    if cached:
        return {
            "status": cached.get("status", "UNKNOWN"),
            "progress": float(cached.get("progress", 0.0)),
            "message": cached.get("message", ""),
            "current_step": cached.get("current_step"),
        }

    row = get_job(job_id)
    if not row:
        return None

    try:
        progress = float(row.get("progress") or 0.0)
    except (TypeError, ValueError):
        progress = 0.0

    normalised = {
        "status": row.get("status", "UNKNOWN"),
        "progress": progress,
        "message": row.get("message", ""),
        "current_step": row.get("current_step"),
    }
    # keep the warm cache populated so the next hit is free
    processing_status[job_id] = {**row, **normalised}
    return normalised
