"""
Test 06 – Full End-to-End Pipeline test.
Architecture ref: Section 4 (processing flow), Section 5 (DynamoDB tracking).

Flow: POST /process-video → job_id → poll GET /status/{job_id}
      → verify DynamoDB row exists → verify S3 results → verify CloudWatch logs
"""
import json
import os
import time
import pytest
from pathlib import Path
from conftest import (
    invoke_lambda, PROCESSING_BUCKET, DYNAMODB_TABLE,
    BACKEND_LOG_GROUP, AWS_REGION,
)

# Path to test video included in tests/ directory
TESTS_DIR = Path(__file__).parent
TEST_VIDEO_PATH = TESTS_DIR / "RED LIGHT CAR CRASH CAUGHT ON CAMERA.mp4"

# Max time in seconds to wait for the pipeline to finish
PIPELINE_TIMEOUT_SECONDS = 300
POLL_INTERVAL_SECONDS = 10


def _make_multipart_form_body(video_path: Path) -> tuple[bytes, str]:
    """Build a minimal multipart/form-data body with the video file."""
    boundary = "----LightshipE2EBoundary7f3a9c21"
    video_bytes = video_path.read_bytes()
    filename = video_path.name

    body_parts = []
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="video"; filename="{filename}"\r\n'.encode()
    )
    body_parts.append(b"Content-Type: video/mp4\r\n\r\n")
    body_parts.append(video_bytes)
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())

    body = b"".join(body_parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


class TestE2EPipeline:
    """Submit a real video, poll for completion, verify all downstream state."""

    @pytest.fixture(scope="class")
    def submitted_job(self, lambda_client):
        """Submit the test video and return (job_id, status_code, fn_error)."""
        if not TEST_VIDEO_PATH.exists():
            pytest.skip(
                f"Test video not found at {TEST_VIDEO_PATH}. "
                "Place a short dashcam clip named 'RED LIGHT CAR CRASH CAUGHT ON CAMERA.mp4' "
                "in the tests/ directory."
            )

        body, content_type = _make_multipart_form_body(TEST_VIDEO_PATH)

        import base64
        body_b64 = base64.b64encode(body).decode("utf-8")

        payload = {
            "httpMethod": "POST",
            "path": "/process-video",
            "headers": {
                "Content-Type": content_type,
                "Host": "localhost",
            },
            "queryStringParameters": None,
            "body": body_b64,
            "isBase64Encoded": True,
            "requestContext": {
                "elb": {
                    "targetGroupArn": "arn:aws:elasticloadbalancing:us-east-1:336090301206:targetgroup/lightship-mvp-backend-tg/92ec81e852ab285d"
                }
            },
        }

        resp = lambda_client.invoke(
            FunctionName="lightship-mvp-backend",
            InvocationType="RequestResponse",
            Payload=json.dumps(payload),
        )
        raw = resp["Payload"].read().decode("utf-8")
        result = json.loads(raw)
        fn_error = resp.get("FunctionError")

        return result, fn_error

    def test_submit_returns_200_and_job_id(self, submitted_job):
        """POST /process-video should return 200 with a job_id."""
        result, fn_error = submitted_job
        assert fn_error is None, (
            f"Lambda FunctionError on submit: {fn_error}. "
            f"Response body: {result}. "
            "Likely cause: missing python-multipart (rebuild Docker image)."
        )
        assert result.get("statusCode") == 200, (
            f"Expected 200 from /process-video, got {result.get('statusCode')}. "
            f"Body: {result.get('body')}"
        )
        body = json.loads(result.get("body", "{}"))
        assert "job_id" in body, f"Response missing job_id: {body}"

    def test_job_appears_in_dynamodb(self, submitted_job, dynamodb_client):
        """After submit, DynamoDB should have a record with status QUEUED or PROCESSING."""
        result, fn_error = submitted_job
        if fn_error:
            pytest.skip("Skipping DynamoDB check: Lambda returned FunctionError on submit")

        body = json.loads(result.get("body", "{}"))
        job_id = body.get("job_id")
        assert job_id, "No job_id in response"

        # DynamoDB may take a moment
        time.sleep(2)
        item = dynamodb_client.get_item(
            TableName=DYNAMODB_TABLE,
            Key={"job_id": {"S": job_id}},
        )
        assert "Item" in item, (
            f"Job {job_id} not found in DynamoDB table {DYNAMODB_TABLE}. "
            "Lambda may not be writing job status."
        )
        status = item["Item"].get("status", {}).get("S", "")
        assert status in ("QUEUED", "PROCESSING", "COMPLETED", "FAILED"), (
            f"Unexpected job status: {status}"
        )

    def test_pipeline_completes(self, submitted_job, dynamodb_client):
        """Poll status until COMPLETED or FAILED, within PIPELINE_TIMEOUT_SECONDS."""
        result, fn_error = submitted_job
        if fn_error:
            pytest.skip("Skipping pipeline completion check: Lambda returned FunctionError on submit")

        body = json.loads(result.get("body", "{}"))
        job_id = body.get("job_id")
        assert job_id, "No job_id"

        deadline = time.time() + PIPELINE_TIMEOUT_SECONDS
        final_status = None
        while time.time() < deadline:
            item = dynamodb_client.get_item(
                TableName=DYNAMODB_TABLE,
                Key={"job_id": {"S": job_id}},
            )
            status = item.get("Item", {}).get("status", {}).get("S", "")
            if status in ("COMPLETED", "FAILED"):
                final_status = status
                break
            print(f"  [{time.strftime('%H:%M:%S')}] job={job_id} status={status} – waiting …")
            time.sleep(POLL_INTERVAL_SECONDS)

        assert final_status is not None, (
            f"Job {job_id} did not reach COMPLETED/FAILED within {PIPELINE_TIMEOUT_SECONDS}s. "
            f"Last status: {status}"
        )
        assert final_status == "COMPLETED", (
            f"Job {job_id} finished with status FAILED. "
            "Check Lambda CloudWatch logs for error details."
        )

    def test_results_written_to_s3(self, submitted_job, s3_client):
        """After COMPLETED, S3 results/ prefix should contain detection_summary.json."""
        result, fn_error = submitted_job
        if fn_error:
            pytest.skip("Skipping S3 results check: Lambda returned FunctionError on submit")

        body = json.loads(result.get("body", "{}"))
        job_id = body.get("job_id")
        assert job_id

        prefix = f"results/default/{job_id}/"
        resp = s3_client.list_objects_v2(Bucket=PROCESSING_BUCKET, Prefix=prefix)
        objects = resp.get("Contents", [])
        keys = [o["Key"] for o in objects]

        assert len(objects) > 0, (
            f"No S3 objects under {PROCESSING_BUCKET}/{prefix}. "
            "Pipeline may not have written results."
        )

        result_file = next((k for k in keys if "detection_summary" in k or "result" in k), None)
        assert result_file is not None, (
            f"No detection_summary/result file found in: {keys}"
        )

    def test_vision_audit_present_in_output_json(self, submitted_job, s3_client):
        """output.json must contain a ``vision_audit`` block proving VisionLabeler ran.

        The audit is authored by ``VisionLabeler`` and embedded by
        ``Pipeline.process_video``; absence means either the labeler did not
        execute or the embed step regressed.
        """
        result, fn_error = submitted_job
        if fn_error:
            pytest.skip("Skipping audit check: Lambda returned FunctionError on submit")

        body = json.loads(result.get("body", "{}"))
        job_id = body.get("job_id")
        assert job_id

        key = f"results/{job_id}/output.json"
        resp = s3_client.get_object(Bucket=PROCESSING_BUCKET, Key=key)
        output_doc = json.loads(resp["Body"].read())

        audit = output_doc.get("vision_audit")
        assert audit is not None, (
            f"vision_audit missing from s3://{PROCESSING_BUCKET}/{key}. "
            "VisionLabeler did not run or the embed step regressed. "
            "Check CloudWatch for 'Failed to embed vision_audit'."
        )
        assert audit.get("frames_evaluated", 0) > 0, (
            f"VisionLabeler reported 0 frames evaluated. audit={audit}"
        )
        assert audit.get("backend") in ("florence2", "yolo", "detectron2", "mixed"), (
            f"Unexpected backend value: {audit.get('backend')}"
        )
        per_frame = audit.get("per_frame", [])
        assert per_frame, "vision_audit.per_frame was empty"
        first = per_frame[0]
        for required in (
            "frame_path", "timestamp_ms", "primary_kept_instances",
            "primary_backend", "fallback_used", "lane_backend",
        ):
            assert required in first, f"per_frame entry missing {required}: {first}"

    def test_cloudwatch_logs_have_job_entries(self, submitted_job, logs_client):
        """Lambda logs should contain at least one entry mentioning the job_id."""
        result, fn_error = submitted_job
        if fn_error:
            pytest.skip("Skipping log check: Lambda returned FunctionError on submit")

        body = json.loads(result.get("body", "{}"))
        job_id = body.get("job_id")
        assert job_id

        # Give CloudWatch a moment to ingest
        time.sleep(5)

        since_ms = int(time.time() * 1000) - 10 * 60 * 1000  # last 10 min
        try:
            streams = logs_client.describe_log_streams(
                logGroupName=BACKEND_LOG_GROUP,
                orderBy="LastEventTime",
                descending=True,
                limit=5,
            ).get("logStreams", [])
        except logs_client.exceptions.ResourceNotFoundException:
            pytest.skip("Lambda log group does not exist yet")

        job_id_found = False
        for stream in streams:
            events = logs_client.get_log_events(
                logGroupName=BACKEND_LOG_GROUP,
                logStreamName=stream["logStreamName"],
                startTime=since_ms,
            ).get("events", [])
            if any(job_id in e["message"] for e in events):
                job_id_found = True
                break

        assert job_id_found, (
            f"job_id {job_id} not found in recent Lambda log entries. "
            "Pipeline may not be logging job IDs."
        )
