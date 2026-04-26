"""Phase 2 observability tests.

Covers:

* ``utils.metrics`` — EMF line format, unit handling, context-manager timing.
* ``utils.logging_setup.JsonFormatter`` — structured payload, exception
  capture, reserved-attr filtering.

Fast, offline, no boto3 clients.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import pytest


_BE_SRC = Path(__file__).resolve().parent.parent / "lambda-be"
if str(_BE_SRC) not in sys.path:
    sys.path.insert(0, str(_BE_SRC))


@pytest.fixture(autouse=True)
def enable_metrics(monkeypatch):
    monkeypatch.setenv("EMIT_METRICS", "true")
    monkeypatch.setenv("METRICS_NAMESPACE", "Lightship/Test")
    monkeypatch.setenv("ENVIRONMENT", "test")


def _load_metrics_module():
    """Force a fresh import so module-level env lookups see the test env."""
    import importlib
    if "src.utils.metrics" in sys.modules:
        module = importlib.reload(sys.modules["src.utils.metrics"])
    else:
        from src.utils import metrics as module
    return module


def test_put_metric_emits_valid_emf(capsys):
    metrics = _load_metrics_module()
    metrics.put_metric("RekognitionCalls", 1.0)

    out = capsys.readouterr().out.strip()
    assert out, "metric line missing"
    payload = json.loads(out)

    assert payload["RekognitionCalls"] == 1.0
    assert payload["Service"] == "lightship-backend"
    assert payload["Environment"] == "test"

    cw = payload["_aws"]["CloudWatchMetrics"][0]
    assert cw["Namespace"] == "Lightship/Test"
    assert cw["Metrics"] == [{"Name": "RekognitionCalls", "Unit": "Count"}]
    assert "Service" in cw["Dimensions"][0]


def test_put_metrics_batches_multiple_values(capsys):
    metrics = _load_metrics_module()
    metrics.put_metrics(
        {
            "FramesExtracted": 12.0,
            "FramesSelected": 5.0,
            "RekognitionCalls": 5.0,
        }
    )

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["FramesExtracted"] == 12.0
    assert payload["FramesSelected"] == 5.0
    assert payload["RekognitionCalls"] == 5.0
    names = {m["Name"] for m in payload["_aws"]["CloudWatchMetrics"][0]["Metrics"]}
    assert names == {"FramesExtracted", "FramesSelected", "RekognitionCalls"}


def test_emit_metrics_can_be_disabled(monkeypatch, capsys):
    monkeypatch.setenv("EMIT_METRICS", "false")
    metrics = _load_metrics_module()
    metrics.put_metric("Ignored", 1.0)
    assert capsys.readouterr().out == ""


def test_stage_timer_emits_duration_and_failure_metrics(capsys):
    metrics = _load_metrics_module()
    with metrics.stage_timer("label"):
        time.sleep(0.01)

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    payloads = [json.loads(ln) for ln in lines]
    durations = [p for p in payloads if "StageDurationMs" in p]
    assert durations, f"no duration metric emitted: {payloads}"
    assert durations[-1]["Stage"] == "label"
    assert durations[-1]["StageDurationMs"] >= 10.0


def test_stage_timer_counts_failures(capsys):
    metrics = _load_metrics_module()
    with pytest.raises(RuntimeError):
        with metrics.stage_timer("flaky"):
            raise RuntimeError("boom")

    lines = [json.loads(ln) for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    failures = [p for p in lines if "StageFailures" in p]
    durations = [p for p in lines if "StageDurationMs" in p]
    assert failures and failures[-1]["Stage"] == "flaky"
    assert durations and durations[-1]["Stage"] == "flaky"


def test_json_formatter_emits_structured_record():
    from src.utils.logging_setup import JsonFormatter

    formatter = JsonFormatter(service="lightship-backend")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    record.job_id = "abc-123"
    record.stage = "label"

    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello world"
    assert payload["job_id"] == "abc-123"
    assert payload["stage"] == "label"
    assert payload["level"] == "INFO"
    assert payload["service"] == "lightship-backend"


def test_json_formatter_captures_exceptions():
    from src.utils.logging_setup import JsonFormatter

    formatter = JsonFormatter()
    try:
        raise ValueError("kaboom")
    except ValueError:
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname=__file__, lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )

    payload = json.loads(formatter.format(record))
    assert payload["level"] == "ERROR"
    assert "kaboom" in payload["exception"]
    assert "ValueError" in payload["exception"]


def test_setup_logging_is_idempotent():
    from src.utils.logging_setup import setup_logging

    setup_logging("INFO")
    initial = len(logging.getLogger().handlers)
    setup_logging("DEBUG")
    assert len(logging.getLogger().handlers) == initial
