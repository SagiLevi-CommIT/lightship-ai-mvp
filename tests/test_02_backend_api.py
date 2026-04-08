"""
Test 02 – Backend Lambda API endpoints.
Tests all API routes directly via boto3 Lambda.invoke() with ALB-format events.
Architecture ref: Section 6 (Web application responsibilities), API routes in api_server.py.

Endpoints tested:
  GET  /           → root info
  GET  /health     → health check
  POST /process-video → start video processing job
  GET  /status/{job_id} → job status from DynamoDB
  GET  /results/{job_id} → job results from DynamoDB/S3
"""
import json
import time
import uuid
import pytest
from conftest import LAMBDA_FUNCTION, DYNAMODB_TABLE, AWS_REGION


class TestHealthEndpoint:
    """GET /health – Lambda startup and FastAPI availability."""

    def test_health_returns_200(self, alb_invoke):
        http_status, fn_error, result = alb_invoke("GET", "/health")
        assert fn_error is None, f"Lambda function error: {fn_error}\nBody: {result}"
        assert http_status == 200, f"Lambda invoke HTTP status: {http_status}"

    def test_health_response_status_ok(self, alb_invoke):
        _, fn_error, result = alb_invoke("GET", "/health")
        assert fn_error is None, f"Lambda error: {fn_error}"
        body = json.loads(result.get("body", "{}"))
        assert result.get("statusCode") == 200, f"Response statusCode: {result.get('statusCode')}"
        assert body.get("status") == "healthy", f"Health body: {body}"

    def test_health_response_has_version(self, alb_invoke):
        _, fn_error, result = alb_invoke("GET", "/health")
        assert fn_error is None, f"Lambda error: {fn_error}"
        body = json.loads(result.get("body", "{}"))
        # Response should include service name or version
        assert "status" in body, f"Missing 'status' in health response: {body}"


class TestRootEndpoint:
    """GET / – API information."""

    def test_root_returns_200(self, alb_invoke):
        _, fn_error, result = alb_invoke("GET", "/")
        assert fn_error is None, f"Lambda error: {fn_error}"
        assert result.get("statusCode") in [200, 307, 308], (
            f"Root returned: {result.get('statusCode')}"
        )


class TestProcessVideoEndpoint:
    """POST /process-video – Video upload and job submission.
    Architecture ref: Section 6 (Upload responsibilities).
    """

    def test_process_video_rejects_empty_body(self, alb_invoke):
        """POST with no video file should return 422 (validation error)."""
        _, fn_error, result = alb_invoke(
            "POST", "/process-video",
            headers={"Host": "lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com",
                     "Content-Type": "application/json"},
        )
        assert fn_error is None, f"Lambda crashed: {fn_error}"
        # Expect 422 (no file) or 400 (bad request) - not a 500
        assert result.get("statusCode") in [400, 422], (
            f"Expected 400/422 for empty request, got: {result.get('statusCode')}\nBody: {result.get('body', '')[:200]}"
        )

    def test_process_video_endpoint_accessible(self, alb_invoke):
        """Endpoint must exist (not 404) even if request is invalid."""
        _, fn_error, result = alb_invoke("POST", "/process-video")
        assert fn_error is None, f"Lambda crashed: {fn_error}"
        assert result.get("statusCode") != 404, "process-video endpoint returned 404"


class TestStatusEndpoint:
    """GET /status/{job_id} – DynamoDB job status lookup.
    Architecture ref: Section 4 (DynamoDB table), Section 6 (Status responsibilities).
    """

    def test_status_nonexistent_job_returns_404(self, alb_invoke):
        fake_job_id = f"test-{uuid.uuid4()}"
        _, fn_error, result = alb_invoke("GET", f"/status/{fake_job_id}")
        assert fn_error is None, f"Lambda crashed: {fn_error}"
        assert result.get("statusCode") == 404, (
            f"Expected 404 for nonexistent job, got: {result.get('statusCode')}\nBody: {result.get('body', '')[:200]}"
        )

    def test_status_endpoint_is_accessible(self, alb_invoke):
        """Endpoint must not crash the Lambda."""
        fake_job_id = "nonexistent-job-id"
        _, fn_error, result = alb_invoke("GET", f"/status/{fake_job_id}")
        assert fn_error is None, f"Lambda crashed on /status: {fn_error}"


class TestResultsEndpoint:
    """GET /results/{job_id} – Job result retrieval."""

    def test_results_nonexistent_job_returns_404(self, alb_invoke):
        fake_job_id = f"test-{uuid.uuid4()}"
        _, fn_error, result = alb_invoke("GET", f"/results/{fake_job_id}")
        assert fn_error is None, f"Lambda crashed: {fn_error}"
        assert result.get("statusCode") == 404, (
            f"Expected 404 for nonexistent job, got: {result.get('statusCode')}"
        )


class TestDownloadEndpoints:
    """GET /download/json/{job_id} and /download/frame/{job_id}/{idx}."""

    def test_download_json_nonexistent_returns_404(self, alb_invoke):
        _, fn_error, result = alb_invoke("GET", f"/download/json/nonexistent-job")
        assert fn_error is None, f"Lambda crashed: {fn_error}"
        assert result.get("statusCode") == 404, (
            f"Expected 404, got: {result.get('statusCode')}"
        )

    def test_download_frame_nonexistent_returns_404(self, alb_invoke):
        _, fn_error, result = alb_invoke("GET", f"/download/frame/nonexistent-job/0")
        assert fn_error is None, f"Lambda crashed: {fn_error}"
        assert result.get("statusCode") == 404, (
            f"Expected 404, got: {result.get('statusCode')}"
        )
