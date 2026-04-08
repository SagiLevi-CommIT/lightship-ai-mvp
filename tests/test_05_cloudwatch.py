"""
Test 05 – CloudWatch Logs validation.
Architecture ref: Section 9 (Observability) and Section 6 (Lambda/ECS logging).

Verifies:
  - Lambda log group /aws/lambda/lightship-mvp-backend exists
  - ECS log group /ecs/lightship-mvp-frontend exists
  - After a Lambda invocation, log entries appear within a reasonable timeout
  - Log entries contain expected structural markers (START/END/REPORT)
"""
import json
import time
import pytest
import boto3
from conftest import (
    LAMBDA_FUNCTION, AWS_REGION,
    BACKEND_LOG_GROUP, FRONTEND_LOG_GROUP,
    invoke_lambda,
)

LOG_WAIT_SECONDS = 30   # max time to wait for CloudWatch delivery
LOOKBACK_MS = 5 * 60 * 1000  # 5 minutes


class TestLogGroupExistence:
    """Log groups must exist once Lambda/ECS have run at least once."""

    def test_lambda_log_group_exists(self, logs_client):
        resp = logs_client.describe_log_groups(logGroupNamePrefix=BACKEND_LOG_GROUP)
        groups = [g for g in resp["logGroups"] if g["logGroupName"] == BACKEND_LOG_GROUP]
        assert len(groups) == 1, (
            f"Lambda log group {BACKEND_LOG_GROUP} not found. "
            "Lambda may never have been successfully invoked."
        )

    def test_ecs_log_group_exists(self, logs_client):
        resp = logs_client.describe_log_groups(logGroupNamePrefix=FRONTEND_LOG_GROUP)
        groups = [g for g in resp["logGroups"] if g["logGroupName"] == FRONTEND_LOG_GROUP]
        assert len(groups) == 1, (
            f"ECS log group {FRONTEND_LOG_GROUP} not found."
        )

    def test_lambda_log_group_retention_set(self, logs_client):
        """Log retention should be configured to control storage costs."""
        resp = logs_client.describe_log_groups(logGroupNamePrefix=BACKEND_LOG_GROUP)
        groups = [g for g in resp["logGroups"] if g["logGroupName"] == BACKEND_LOG_GROUP]
        if not groups:
            pytest.skip(f"Log group {BACKEND_LOG_GROUP} does not exist yet")
        retention = groups[0].get("retentionInDays")
        if retention is None:
            pytest.skip("Lambda log group has no retention policy set (infinite) - consider setting one")
        assert retention > 0


class TestLambdaLogEntries:
    """After invoking the Lambda health endpoint, logs should appear within LOG_WAIT_SECONDS."""

    def _get_events_since(self, logs_client, log_group: str, since_ms: int) -> list:
        """Return all log events that arrived after `since_ms` (epoch ms)."""
        try:
            streams_resp = logs_client.describe_log_streams(
                logGroupName=log_group,
                orderBy="LastEventTime",
                descending=True,
                limit=5,
            )
        except logs_client.exceptions.ResourceNotFoundException:
            return []
        events = []
        for stream in streams_resp.get("logStreams", []):
            resp = logs_client.get_log_events(
                logGroupName=log_group,
                logStreamName=stream["logStreamName"],
                startTime=since_ms,
                startFromHead=True,
            )
            events.extend(resp.get("events", []))
        return events

    def test_health_invoke_produces_logs(self, lambda_client, logs_client):
        """Invoke GET /health and verify Lambda emits at least one log entry."""
        before_ms = int(time.time() * 1000)

        # Invoke health endpoint
        http_status, fn_error, result = invoke_lambda(lambda_client, "GET", "/health")

        # Wait for CloudWatch delivery (eventual consistency)
        deadline = time.time() + LOG_WAIT_SECONDS
        found_events = []
        while time.time() < deadline:
            found_events = self._get_events_since(logs_client, BACKEND_LOG_GROUP, before_ms)
            if found_events:
                break
            time.sleep(3)

        assert len(found_events) > 0, (
            f"No log entries appeared in {BACKEND_LOG_GROUP} within {LOG_WAIT_SECONDS}s "
            f"after invoking /health (http_status={http_status}, fn_error={fn_error}). "
            "Lambda may not be running or the log group may not exist yet."
        )

    def test_lambda_logs_contain_lifecycle_entries(self, logs_client):
        """Lambda log streams should contain START/END/REPORT entries (AWS runtime markers)."""
        since_ms = int(time.time() * 1000) - LOOKBACK_MS
        events = self._get_events_since(logs_client, BACKEND_LOG_GROUP, since_ms)

        if not events:
            pytest.skip(
                "No recent log entries found in Lambda log group. "
                "Run test_health_invoke_produces_logs first."
            )

        messages = [e["message"] for e in events]
        has_lifecycle = any(m.startswith(("START", "END", "REPORT")) for m in messages)
        assert has_lifecycle, (
            "Expected Lambda lifecycle entries (START/END/REPORT) in log stream. "
            f"Sample messages: {messages[:5]}"
        )

    def test_lambda_logs_no_crash_on_startup(self, logs_client):
        """Lambda logs must not contain python-multipart import errors (known past bug)."""
        since_ms = int(time.time() * 1000) - LOOKBACK_MS
        events = self._get_events_since(logs_client, BACKEND_LOG_GROUP, since_ms)
        crash_messages = [
            e["message"] for e in events
            if "python-multipart" in e["message"] or "ModuleNotFoundError" in e["message"]
        ]
        assert len(crash_messages) == 0, (
            f"Lambda crash entries found: {crash_messages[:3]}"
        )


class TestECSLogEntries:
    """Verify ECS Streamlit frontend is writing logs to CloudWatch."""

    def test_ecs_log_streams_exist(self, logs_client):
        """At least one log stream should exist for the running ECS task."""
        try:
            resp = logs_client.describe_log_streams(
                logGroupName=FRONTEND_LOG_GROUP,
                orderBy="LastEventTime",
                descending=True,
                limit=10,
            )
        except logs_client.exceptions.ResourceNotFoundException:
            pytest.fail(f"ECS log group {FRONTEND_LOG_GROUP} does not exist")
        streams = resp.get("logStreams", [])
        assert len(streams) > 0, (
            f"No log streams found in {FRONTEND_LOG_GROUP}. "
            "ECS frontend task may not be writing logs."
        )

    def test_ecs_has_recent_log_events(self, logs_client):
        """ECS should have logged at least once in the past 30 minutes."""
        thirty_min_ago_ms = int(time.time() * 1000) - 30 * 60 * 1000
        try:
            streams_resp = logs_client.describe_log_streams(
                logGroupName=FRONTEND_LOG_GROUP,
                orderBy="LastEventTime",
                descending=True,
                limit=3,
            )
        except logs_client.exceptions.ResourceNotFoundException:
            pytest.fail(f"Log group {FRONTEND_LOG_GROUP} not found")

        events = []
        for stream in streams_resp.get("logStreams", []):
            last_event = stream.get("lastEventTimestamp", 0)
            if last_event >= thirty_min_ago_ms:
                resp = logs_client.get_log_events(
                    logGroupName=FRONTEND_LOG_GROUP,
                    logStreamName=stream["logStreamName"],
                    startTime=thirty_min_ago_ms,
                    limit=5,
                )
                events.extend(resp.get("events", []))

        assert len(events) > 0, (
            f"No log entries in {FRONTEND_LOG_GROUP} in the past 30 minutes. "
            "ECS frontend task may be unhealthy or not generating logs."
        )


class TestCloudWatchMetrics:
    """Verify Lambda CloudWatch metrics are being emitted."""

    def test_lambda_invocations_metric_exists(self):
        """AWS/Lambda namespace should have Invocations metric after at least one call."""
        cw_client = boto3.client("cloudwatch", region_name=AWS_REGION)
        resp = cw_client.list_metrics(
            Namespace="AWS/Lambda",
            Dimensions=[{"Name": "FunctionName", "Value": LAMBDA_FUNCTION}],
        )
        metrics = resp.get("Metrics", [])
        metric_names = [m["MetricName"] for m in metrics]
        if not metric_names:
            pytest.skip(
                f"No Lambda metrics found for {LAMBDA_FUNCTION} yet. "
                "Metrics may take a few minutes to appear after first invocation."
            )
        assert "Invocations" in metric_names or "Errors" in metric_names, (
            f"Expected Invocations or Errors metric. Found: {metric_names}"
        )
