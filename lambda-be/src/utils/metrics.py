"""CloudWatch EMF + lightweight metrics helper.

Emits CloudWatch Embedded Metric Format lines so CloudWatch can extract
metrics at ingest time without ``PutMetricData`` API calls. Safe to call
whether or not the code is running on Lambda — if ``EMIT_METRICS`` is
false or the logger is mis-configured the helpers degrade to best-effort
JSON log lines that will still be captured by CloudWatch Logs but won't
be surfaced as metrics.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from typing import Dict, Iterable, Mapping, Optional

logger = logging.getLogger("lightship.metrics")


def _emit_enabled() -> bool:
    return os.environ.get("EMIT_METRICS", "true").lower() == "true"


def _namespace() -> str:
    return os.environ.get("METRICS_NAMESPACE", "Lightship/Backend")


def _emf(metrics: Mapping[str, float], dimensions: Optional[Mapping[str, str]] = None,
         unit: str = "Count") -> Dict:
    """Build a CloudWatch Embedded Metric Format payload."""
    dims = dict(dimensions or {})
    dim_keys = list(dims.keys())
    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": _namespace(),
                    "Dimensions": [dim_keys] if dim_keys else [[]],
                    "Metrics": [
                        {"Name": name, "Unit": unit} for name in metrics.keys()
                    ],
                }
            ],
        },
    }
    emf.update({k: float(v) for k, v in metrics.items()})
    emf.update(dims)
    return emf


def put_metric(name: str, value: float = 1.0, unit: str = "Count",
               dimensions: Optional[Mapping[str, str]] = None) -> None:
    if not _emit_enabled():
        return
    try:
        line = _emf({name: value}, dimensions=dimensions, unit=unit)
        logger.info(json.dumps(line))
    except Exception:
        # metrics must never break the pipeline
        pass


def put_metrics(metrics: Mapping[str, float], unit: str = "Count",
                dimensions: Optional[Mapping[str, str]] = None) -> None:
    if not _emit_enabled() or not metrics:
        return
    try:
        line = _emf(metrics, dimensions=dimensions, unit=unit)
        logger.info(json.dumps(line))
    except Exception:
        pass


def count(name: str, amount: float = 1.0,
          dimensions: Optional[Mapping[str, str]] = None) -> None:
    put_metric(name, amount, unit="Count", dimensions=dimensions)


def duration_ms(name: str, value_ms: float,
                dimensions: Optional[Mapping[str, str]] = None) -> None:
    put_metric(name, value_ms, unit="Milliseconds", dimensions=dimensions)


@contextmanager
def stage_timer(stage_name: str, dimensions: Optional[Mapping[str, str]] = None):
    """Context manager: emits ``<stage>Ms`` + ``<stage>Failures`` on exception."""
    started = time.monotonic()
    failed = False
    try:
        yield
    except Exception:
        failed = True
        raise
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000.0
        dims = dict(dimensions or {})
        dims.setdefault("Stage", stage_name)
        try:
            duration_ms(f"{stage_name}Ms", elapsed_ms, dimensions=dims)
            if failed:
                count(f"{stage_name}Failures", 1.0, dimensions=dims)
        except Exception:
            pass
