"""Pure-Python tests for the ``lambda-be/src/job_status`` module.

These tests do not touch AWS. They use a stub DynamoDB table fixture so we
can verify that every mutation uses aliased attribute names (reserved-word
safe) and keeps the warm cache + table in sync.
"""
from __future__ import annotations

import os
import sys
import types
import pytest

# Add lambda-be to path so ``src.job_status`` imports cleanly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))


@pytest.fixture()
def job_status_fresh():
    """Fresh ``job_status`` module per test so in-memory state doesn't leak."""
    if "src.job_status" in sys.modules:
        del sys.modules["src.job_status"]
    from src import job_status as js
    js.processing_status.clear()
    js.set_table(None)
    return js


class _StubTable:
    """Minimal stand-in for a boto3 DynamoDB Table."""

    def __init__(self):
        self.items: dict = {}
        self.update_calls: list = []

    def put_item(self, Item: dict):
        self.items[Item["job_id"]] = dict(Item)

    def update_item(self, Key: dict, **kwargs):
        self.update_calls.append({"Key": Key, **kwargs})
        row = self.items.setdefault(Key["job_id"], {"job_id": Key["job_id"]})
        # Apply aliased SET expression: UpdateExpression="SET #a0 = :v0, ..."
        names = kwargs["ExpressionAttributeNames"]
        values = kwargs["ExpressionAttributeValues"]
        for key_alias, value_alias in _parse_set_expression(kwargs["UpdateExpression"]):
            row[names[key_alias]] = values[value_alias]

    def get_item(self, Key: dict):
        item = self.items.get(Key["job_id"])
        return {"Item": item} if item else {}


def _parse_set_expression(expr: str):
    assert expr.startswith("SET ")
    parts = [p.strip() for p in expr[4:].split(",")]
    for p in parts:
        left, _, right = p.partition(" = ")
        yield left, right


def test_put_job_sets_warm_cache_and_filters_none(job_status_fresh):
    js = job_status_fresh
    table = _StubTable()
    js.set_table(table)

    js.put_job(
        "job-a",
        status="QUEUED",
        filename="hello.mp4",
        snapshot_strategy=None,  # must be dropped
    )

    assert "snapshot_strategy" not in table.items["job-a"]
    assert table.items["job-a"]["filename"] == "hello.mp4"
    assert js.processing_status["job-a"]["status"] == "QUEUED"


def test_write_progress_uses_aliased_reserved_words(job_status_fresh):
    from decimal import Decimal

    js = job_status_fresh
    table = _StubTable()
    js.set_table(table)
    js.put_job("job-b", status="QUEUED")

    js.write_progress(
        "job-b",
        status="PROCESSING",
        progress=0.42,
        message="Halfway there",
        current_step="refining",
    )

    call = table.update_calls[-1]
    assert "#a" in call["UpdateExpression"]
    for key in ("status", "message", "progress"):
        assert key in call["ExpressionAttributeNames"].values()
    # Warm cache keeps the Python float so the UI sees standard JSON.
    assert js.processing_status["job-b"]["progress"] == pytest.approx(0.42)
    # Dynamo writes must be Decimal because boto3 rejects floats.
    assert isinstance(table.items["job-b"]["progress"], Decimal)
    assert float(table.items["job-b"]["progress"]) == pytest.approx(0.42)
    assert table.items["job-b"]["status"] == "PROCESSING"


def test_write_progress_clamps_to_unit_interval(job_status_fresh):
    js = job_status_fresh
    js.write_progress(
        "job-c",
        status="PROCESSING",
        progress=42.0,  # deliberately out of range
        message="...",
        current_step="step",
    )
    assert js.processing_status["job-c"]["progress"] == 1.0

    js.write_progress(
        "job-c",
        status="PROCESSING",
        progress=-5.0,
        message="...",
        current_step="step",
    )
    assert js.processing_status["job-c"]["progress"] == 0.0


def test_read_status_prefers_warm_cache(job_status_fresh):
    js = job_status_fresh
    js.processing_status["job-d"] = {
        "status": "PROCESSING",
        "progress": 0.7,
        "message": "warm",
        "current_step": "foo",
    }
    row = js.read_status("job-d")
    assert row == {
        "status": "PROCESSING",
        "progress": 0.7,
        "message": "warm",
        "current_step": "foo",
    }


def test_read_status_falls_back_to_dynamo_when_cold(job_status_fresh):
    js = job_status_fresh
    table = _StubTable()
    table.put_item({
        "job_id": "job-e",
        "status": "COMPLETED",
        "progress": 1.0,
        "message": "done",
        "current_step": "completed",
    })
    js.set_table(table)

    row = js.read_status("job-e")
    assert row["status"] == "COMPLETED"
    assert row["progress"] == 1.0
    # Subsequent read should be served from warm cache.
    assert "job-e" in js.processing_status


def test_read_status_missing_returns_none(job_status_fresh):
    js = job_status_fresh
    table = _StubTable()
    js.set_table(table)
    assert js.read_status("nope") is None


def test_dynamo_safe_converts_floats_to_decimal(job_status_fresh):
    """boto3's DynamoDB resource rejects Python floats with
    'Float types are not supported. Use Decimal types instead.'."""
    from decimal import Decimal
    js = job_status_fresh

    result = js._dynamo_safe({
        "progress": 0.42,
        "nested": [1.5, 2.5],
        "meta": {"ratio": 0.9},
        "status": "PROCESSING",  # strings stay put
    })
    assert isinstance(result["progress"], Decimal)
    assert all(isinstance(v, Decimal) for v in result["nested"])
    assert isinstance(result["meta"]["ratio"], Decimal)
    assert result["status"] == "PROCESSING"


def test_update_status_with_error_message_reserved_word(job_status_fresh):
    js = job_status_fresh
    table = _StubTable()
    js.set_table(table)
    js.put_job("job-f", status="PROCESSING")

    js.update_status("job-f", "FAILED", error_message="oops")

    call = table.update_calls[-1]
    assert "error_message" in call["ExpressionAttributeNames"].values()
    assert table.items["job-f"]["status"] == "FAILED"
    assert table.items["job-f"]["error_message"] == "oops"
