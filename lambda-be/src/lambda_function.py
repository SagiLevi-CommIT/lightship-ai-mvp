"""Lambda entry-point — routes every event type the backend container handles.

Event types handled
-------------------

1. ALB HTTP request → Mangum → FastAPI.
2. SQS batch (``event["Records"][*]["eventSource"] == "aws:sqs"``) → dispatcher
   that starts a Step Functions execution per message.
3. Step Functions task invocation with ``action == "pipeline_stage"`` → runs
   ``process_video_worker`` inside the SFN execution.
4. Step Functions error-handler invocation with ``action == "mark_failed"`` →
   updates the job row to ``FAILED``.
5. Legacy ``action == "process_worker"`` (self-invoke) — still supported
   during Phase 3 migration so un-deployed stacks keep working. Removed in
   Phase 6 after SFN deploy is verified.

All routing decisions happen in ``lambda_handler`` so the function has a
single, readable control flow; the ALB path is the default fall-through.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, "/var/task/src")

from api_server import app, process_video_worker  # noqa: E402
from mangum import Mangum  # noqa: E402

from src import job_status  # noqa: E402
from src.utils.logging_setup import setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger(__name__)

_http_handler = Mangum(app, lifespan="off")

_PIPELINE_STATE_MACHINE_ARN = os.environ.get("PIPELINE_STATE_MACHINE_ARN", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_sfn_client = None
if _PIPELINE_STATE_MACHINE_ARN:
    try:
        import boto3  # noqa: WPS433
        _sfn_client = boto3.client("stepfunctions", region_name=_AWS_REGION)
        logger.info(
            "Step Functions dispatcher ready",
            extra={"state_machine_arn": _PIPELINE_STATE_MACHINE_ARN},
        )
    except Exception as e:
        logger.warning("Step Functions client init failed: %s", e)


def _dispatch_sqs_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Start one Step Functions execution per SQS record.

    Returns a batchItemFailures response so SQS can retry only the records
    that actually failed; successful records get deleted from the queue.
    """
    if _sfn_client is None or not _PIPELINE_STATE_MACHINE_ARN:
        raise RuntimeError(
            "Received SQS event but PIPELINE_STATE_MACHINE_ARN is not configured; "
            "either set the env var or stop routing SQS to this Lambda."
        )

    batch_item_failures = []
    for record in records:
        message_id = record.get("messageId", "?")
        try:
            payload = json.loads(record["body"])
            job_id = payload["job_id"]

            execution_name = f"job-{job_id}-{message_id}"[:80]
            logger.info(
                "Starting SFN execution",
                extra={
                    "job_id": job_id,
                    "execution_name": execution_name,
                    "message_id": message_id,
                },
            )
            _sfn_client.start_execution(
                stateMachineArn=_PIPELINE_STATE_MACHINE_ARN,
                name=execution_name,
                input=json.dumps({**payload, "action": "pipeline_stage"}),
            )
        except _sfn_client.exceptions.ExecutionAlreadyExists:
            logger.info(
                "SFN execution already exists (idempotent retry)",
                extra={"message_id": message_id},
            )
        except Exception as e:
            logger.exception(
                "Failed to dispatch SQS record to Step Functions",
                extra={"message_id": message_id, "error": str(e)},
            )
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}


def _handle_mark_failed(event: Dict[str, Any]) -> Dict[str, Any]:
    """Step Functions Catch target: move the job to ``FAILED`` in Dynamo."""
    job_id = event.get("job_id")
    error = event.get("error", "State machine Catch")

    if not job_id:
        logger.warning("mark_failed without job_id: %s", event)
        return {"ok": False}

    error_str = error if isinstance(error, str) else json.dumps(error)[:1000]
    job_status.update_status(
        job_id,
        "FAILED",
        error_message=error_str,
    )
    logger.error(
        "Pipeline execution failed",
        extra={"job_id": job_id, "error": error_str},
    )
    return {"ok": True, "job_id": job_id}


def lambda_handler(event: Any, context: Any) -> Any:
    """Route every Lambda event to the right handler.

    The order matters: SFN → SQS → HTTP. SFN payloads are small JSON with
    an explicit ``action`` key, SQS payloads have a ``Records`` list, and
    anything else is assumed to be an ALB HTTP event.
    """
    if isinstance(event, dict):
        action = event.get("action")
        if action == "pipeline_stage" or action == "process_worker":
            # ``filename`` is a reserved attribute on LogRecord (points at
            # the source file of the log call); using it in ``extra`` raises
            # ``KeyError: Attempt to overwrite 'filename' in LogRecord``.
            # Use ``video_filename`` instead so we keep the job context.
            logger.info(
                "Worker mode",
                extra={
                    "job_id": event.get("job_id"),
                    "video_filename": event.get("filename"),
                    "action": action,
                },
            )
            return process_video_worker(event)

        if action == "mark_failed":
            return _handle_mark_failed(event)

        records = event.get("Records")
        if isinstance(records, list) and records and records[0].get("eventSource") == "aws:sqs":
            return _dispatch_sqs_records(records)

    return _http_handler(event, context)
