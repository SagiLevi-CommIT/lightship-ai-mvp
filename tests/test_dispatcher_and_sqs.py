"""Phase 3 tests: SQS enqueue + Step Functions dispatcher.

These validate the new async dispatch path without touching AWS:

* ``api_server._enqueue_job`` prefers SQS when configured, and falls back
  to the legacy Lambda self-invoke otherwise.
* ``lambda_function._dispatch_sqs_records`` translates each SQS record
  into a ``StartExecution`` call and reports only the records that
  actually failed (``batchItemFailures``).
* ``lambda_function._handle_mark_failed`` updates the Dynamo row to
  ``FAILED`` with a short error string (no matter how long the SFN error
  payload was).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


_BE_ROOT = Path(__file__).resolve().parent.parent / "lambda-be"
_BE_SRC = _BE_ROOT / "src"
for _p in (_BE_ROOT, _BE_SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _stub_heavy_modules() -> None:
    # Only ``src.pipeline`` pulls in YOLO / torch / transformers — the rest
    # of the real backend modules are lightweight and should run as-is so
    # they stay testable by their own suites (e.g. test_07_config_generator).
    if "src.pipeline" not in sys.modules:
        pipe = types.ModuleType("src.pipeline")

        class _Pipe:
            def __init__(self, *a: Any, **kw: Any) -> None:
                self.merger = MagicMock()
                self.merger.output_dir = "/tmp"

            def process_video(self, *a: Any, **kw: Any) -> None:
                return None

        pipe.Pipeline = _Pipe
        sys.modules["src.pipeline"] = pipe


@pytest.fixture
def api_module():
    _stub_heavy_modules()
    from src import api_server, job_status

    # Reset all dispatch clients to a known state per-test.
    job_status.clear()

    yield api_server, job_status

    api_server._sqs_client = None
    api_server._lambda_client = None
    api_server.PROCESSING_QUEUE_URL = ""
    api_server.LAMBDA_FUNCTION_NAME = ""
    job_status.clear()


def test_enqueue_prefers_sqs_when_configured(api_module):
    from src.processing_models import ProcessingConfig

    api_server, _ = api_module
    sent = []

    class _SQS:
        def send_message(self, **kwargs: Any) -> Dict[str, Any]:
            sent.append(kwargs)
            return {"MessageId": "m1"}

    api_server._sqs_client = _SQS()
    api_server.PROCESSING_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/0/processing"

    proc = ProcessingConfig(max_snapshots=5, snapshot_strategy="clustering")
    path = api_server._enqueue_job("job-1", "input/v/a.mp4", "a.mp4", proc)
    assert path == "sqs"
    assert len(sent) == 1
    body = json.loads(sent[0]["MessageBody"])
    cfg = proc.model_dump(mode="json")
    ecs_env = {
        "MAX_SNAPSHOTS": str(proc.max_snapshots),
        "SNAPSHOT_STRATEGY": proc.snapshot_strategy,
        "NATIVE_FPS": "" if proc.native_fps is None else str(float(proc.native_fps)),
        "DETECTOR_BACKEND": proc.detector_backend,
        "LANE_BACKEND": proc.lane_backend,
    }
    assert body == {
        "job_id": "job-1",
        "s3_key": "input/v/a.mp4",
        "filename": "a.mp4",
        "config": cfg,
        "ecs_env": ecs_env,
    }
    assert sent[0]["QueueUrl"].endswith("/processing")
    assert sent[0]["MessageAttributes"]["job_id"]["StringValue"] == "job-1"


def test_enqueue_falls_back_to_lambda_self_invoke(api_module):
    api_server, _ = api_module
    invoked = []

    class _Lambda:
        def invoke(self, **kwargs: Any) -> Dict[str, Any]:
            invoked.append(kwargs)
            return {"StatusCode": 202}

    api_server._sqs_client = None
    api_server.PROCESSING_QUEUE_URL = ""
    api_server._lambda_client = _Lambda()
    api_server.LAMBDA_FUNCTION_NAME = "lightship-mvp-backend"

    from src.processing_models import ProcessingConfig

    path = api_server._enqueue_job("job-2", "x.mp4", "x.mp4", ProcessingConfig())
    assert path == "lambda"
    assert invoked[0]["FunctionName"] == "lightship-mvp-backend"
    body = json.loads(invoked[0]["Payload"])
    assert body["action"] == "process_worker"
    assert body["job_id"] == "job-2"


def test_enqueue_background_when_nothing_configured(api_module):
    api_server, _ = api_module
    api_server._sqs_client = None
    api_server.PROCESSING_QUEUE_URL = ""
    api_server._lambda_client = None
    api_server.LAMBDA_FUNCTION_NAME = ""

    from src.processing_models import ProcessingConfig

    path = api_server._enqueue_job("job-3", "y.mp4", "y.mp4", ProcessingConfig())
    assert path == "background"


def test_enqueue_marks_failed_when_sqs_raises(api_module):
    api_server, job_status_mod = api_module

    class _StubTable:
        def __init__(self) -> None:
            self.items: Dict[str, Dict[str, Any]] = {}

        def put_item(self, Item: Dict[str, Any]) -> Dict[str, Any]:
            self.items[Item["job_id"]] = dict(Item)
            return {}

        def get_item(self, Key: Dict[str, Any]) -> Dict[str, Any]:
            return {"Item": self.items.get(Key["job_id"])}

        def update_item(self, **kwargs: Any) -> Dict[str, Any]:
            job_id = kwargs["Key"]["job_id"]
            current = self.items.setdefault(job_id, {"job_id": job_id})
            names = kwargs.get("ExpressionAttributeNames", {})
            values = kwargs.get("ExpressionAttributeValues", {})
            for placeholder, attr_name in names.items():
                placeholder_value = placeholder.replace("#", ":")
                if placeholder_value in values:
                    current[attr_name] = values[placeholder_value]
            return {}

    class _StubTableAlias(_StubTable):
        def update_item(self, **kwargs: Any) -> Dict[str, Any]:
            # Alias-aware: the real job_status builds expressions like
            # ``#k0 = :v0`` with independent placeholders; we resolve by
            # order-of-appearance so the stub never drops attributes.
            job_id = kwargs["Key"]["job_id"]
            current = self.items.setdefault(job_id, {"job_id": job_id})
            names = kwargs.get("ExpressionAttributeNames", {})
            values = kwargs.get("ExpressionAttributeValues", {})
            expr = kwargs.get("UpdateExpression", "")
            # Parse "SET #a = :b, #c = :d" into pairs.
            import re
            for match in re.finditer(r"(#\w+)\s*=\s*(:\w+)", expr):
                name_key, value_key = match.group(1), match.group(2)
                attr_name = names.get(name_key, name_key)
                if value_key in values:
                    current[attr_name] = values[value_key]
            return {}

    table = _StubTableAlias()
    job_status_mod.set_table(table)
    table.put_item(Item={"job_id": "job-err", "status": "QUEUED"})

    class _BrokenSQS:
        def send_message(self, **kwargs: Any) -> None:
            raise RuntimeError("network down")

    api_server._sqs_client = _BrokenSQS()
    api_server.PROCESSING_QUEUE_URL = "https://sqs/queue"

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        from src.processing_models import ProcessingConfig

        api_server._enqueue_job("job-err", "a.mp4", "a.mp4", ProcessingConfig())

    assert exc_info.value.status_code == 500
    assert "sqs" in exc_info.value.detail.lower()
    assert table.items["job-err"]["status"] == "FAILED"
    assert "sqs dispatch failed" in table.items["job-err"]["error_message"]


# ---------------------------------------------------------------------------
# lambda_function dispatcher tests — SQS → Step Functions StartExecution
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher_module(api_module, monkeypatch):
    _api_server, _ = api_module
    monkeypatch.setenv("PIPELINE_STATE_MACHINE_ARN",
                       "arn:aws:states:us-east-1:0:stateMachine:lightship-mvp-pipeline")

    import importlib
    if "lambda_function" in sys.modules:
        module = importlib.reload(sys.modules["lambda_function"])
    else:
        import lambda_function as module
    return module


def test_dispatcher_starts_one_execution_per_record(dispatcher_module):
    started: List[Dict[str, Any]] = []

    class _SFN:
        class exceptions:
            class ExecutionAlreadyExists(Exception):
                pass

        def start_execution(self, **kwargs: Any) -> Dict[str, Any]:
            started.append(kwargs)
            return {"executionArn": "arn:aws:states:::execution:x:y"}

    dispatcher_module._sfn_client = _SFN()
    dispatcher_module._PIPELINE_STATE_MACHINE_ARN = (
        "arn:aws:states:us-east-1:0:stateMachine:lightship-mvp-pipeline"
    )

    records = [
        {
            "messageId": "mid-1",
            "eventSource": "aws:sqs",
            "body": json.dumps({
                "job_id": "job-A",
                "s3_key": "input/videos/A/clip.mp4",
                "filename": "clip.mp4",
                "config": {"max_snapshots": 5},
            }),
        },
        {
            "messageId": "mid-2",
            "eventSource": "aws:sqs",
            "body": json.dumps({
                "job_id": "job-B",
                "s3_key": "input/videos/B/clip.mp4",
                "filename": "clip.mp4",
                "config": {"max_snapshots": 3},
            }),
        },
    ]

    result = dispatcher_module._dispatch_sqs_records(records)

    assert result == {"batchItemFailures": []}
    assert len(started) == 2
    for call in started:
        payload = json.loads(call["input"])
        assert payload["action"] == "pipeline_stage"
        assert payload["job_id"].startswith("job-")
        assert call["name"].startswith("job-")


def test_dispatcher_tolerates_already_exists(dispatcher_module):
    class _ExecutionAlreadyExists(Exception):
        pass

    class _SFN:
        class exceptions:
            ExecutionAlreadyExists = _ExecutionAlreadyExists

        def start_execution(self, **kwargs: Any) -> None:
            raise _ExecutionAlreadyExists("already")

    dispatcher_module._sfn_client = _SFN()
    dispatcher_module._PIPELINE_STATE_MACHINE_ARN = (
        "arn:aws:states:us-east-1:0:stateMachine:lightship-mvp-pipeline"
    )

    records = [{"messageId": "m", "eventSource": "aws:sqs",
                "body": json.dumps({"job_id": "j"})}]
    result = dispatcher_module._dispatch_sqs_records(records)
    assert result == {"batchItemFailures": []}


def test_dispatcher_reports_individual_failures(dispatcher_module):
    class _SFN:
        class exceptions:
            class ExecutionAlreadyExists(Exception):
                pass

        def start_execution(self, **kwargs: Any) -> None:
            if "crash" in kwargs["name"]:
                raise RuntimeError("boom")
            return {"executionArn": "x"}

    dispatcher_module._sfn_client = _SFN()
    dispatcher_module._PIPELINE_STATE_MACHINE_ARN = (
        "arn:aws:states:us-east-1:0:stateMachine:lightship-mvp-pipeline"
    )

    records = [
        {"messageId": "ok", "eventSource": "aws:sqs",
         "body": json.dumps({"job_id": "job-ok"})},
        {"messageId": "crash", "eventSource": "aws:sqs",
         "body": json.dumps({"job_id": "job-crash"})},
    ]
    result = dispatcher_module._dispatch_sqs_records(records)
    assert result == {"batchItemFailures": [{"itemIdentifier": "crash"}]}


def test_mark_failed_updates_dynamo(dispatcher_module):
    import re
    from src import job_status

    class _Table:
        def __init__(self) -> None:
            self.items: Dict[str, Dict[str, Any]] = {}

        def put_item(self, Item: Dict[str, Any]) -> Dict[str, Any]:
            self.items[Item["job_id"]] = dict(Item)
            return {}

        def get_item(self, Key: Dict[str, Any]) -> Dict[str, Any]:
            return {"Item": self.items.get(Key["job_id"])}

        def update_item(self, **kwargs: Any) -> Dict[str, Any]:
            job_id = kwargs["Key"]["job_id"]
            current = self.items.setdefault(job_id, {"job_id": job_id})
            names = kwargs.get("ExpressionAttributeNames", {})
            values = kwargs.get("ExpressionAttributeValues", {})
            expr = kwargs.get("UpdateExpression", "")
            for match in re.finditer(r"(#\w+)\s*=\s*(:\w+)", expr):
                name_key, value_key = match.group(1), match.group(2)
                attr_name = names.get(name_key, name_key)
                if value_key in values:
                    current[attr_name] = values[value_key]
            return {}

    t = _Table()
    job_status.set_table(t)
    t.put_item(Item={"job_id": "j99", "status": "PROCESSING"})

    out = dispatcher_module._handle_mark_failed({
        "job_id": "j99",
        "error": {"Error": "States.Timeout", "Cause": "task timed out"},
    })

    assert out == {"ok": True, "job_id": "j99"}
    assert t.items["j99"]["status"] == "FAILED"
    assert "States.Timeout" in t.items["j99"]["error_message"]
