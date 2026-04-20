"""Logging configuration — JSON structured on Lambda, readable locally.

In AWS Lambda every stdout line goes straight to CloudWatch Logs with no
formatting of its own, so emitting JSON here means every log entry becomes
queryable via CloudWatch Logs Insights without any ingestion pipeline:

    fields @timestamp, job_id, stage, duration_ms
    | filter level = 'ERROR'
    | stats count() by stage

Locally we fall back to the human-readable text format so terminal tails
are still skimmable.
"""
from __future__ import annotations

import json
import logging
import logging.config
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Attributes the stdlib always attaches to a LogRecord. Anything NOT in this
# set is treated as caller-supplied context (e.g. ``logger.info(..., extra={
# "job_id": j})``) and merged into the JSON payload.
_STANDARD_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
}


class JsonFormatter(logging.Formatter):
    """Emit one-line JSON per record.

    Never raises: if the record can't be serialised (e.g. some ``extra``
    value isn't JSON-safe) we fall back to ``repr`` so a single bad log
    line doesn't kill the Lambda invocation.
    """

    def __init__(self, service: str = "lightship-backend") -> None:
        super().__init__()
        self._service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            ) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "service": self._service,
            "message": record.getMessage(),
        }

        aws_request_id = getattr(record, "aws_request_id", None) or os.environ.get(
            "AWS_LAMBDA_REQUEST_ID"
        )
        if aws_request_id:
            payload["aws_request_id"] = aws_request_id

        function_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
        if function_name:
            payload["function"] = function_name

        for attr_name, attr_value in record.__dict__.items():
            if attr_name in _STANDARD_ATTRS or attr_name.startswith("_"):
                continue
            if attr_name == "message":
                continue
            payload[attr_name] = attr_value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        try:
            return json.dumps(payload, default=str)
        except Exception:
            return json.dumps(
                {
                    "timestamp": payload.get("timestamp"),
                    "level": payload.get("level"),
                    "message": repr(record.getMessage()),
                    "service": self._service,
                    "serialization_error": True,
                }
            )


def setup_logging(log_level: str | None = None) -> None:
    """Configure the root logger.

    Called once at Lambda container bootstrap (and once on uvicorn startup).
    Idempotent — re-running swaps the handler rather than stacking another.
    """
    level = (log_level or os.environ.get("LOG_LEVEL", "INFO")).upper()

    use_json = os.environ.get("LOG_FORMAT", "").lower() == "json" or bool(
        os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    )

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler(stream=sys.stdout)
    stream.setLevel(level)
    if use_json:
        stream.setFormatter(JsonFormatter())
    else:
        stream.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
    root.addHandler(stream)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)

    if not use_json:
        try:
            log_dir = "/tmp/.logs" if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else ".logs"
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.TimedRotatingFileHandler(
                os.path.join(log_dir, "app.log"),
                when="midnight",
                interval=1,
                backupCount=7,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            root.addHandler(file_handler)
        except Exception:
            pass

    logging.getLogger(__name__).info(
        "Logging initialised",
        extra={"log_level": level, "format": "json" if use_json else "text"},
    )
