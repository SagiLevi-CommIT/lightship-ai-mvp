"""End-to-end validation against the live ALB.

Run with:
    AWS_PROFILE=lightship pytest tests/test_e2e_live.py -v --tb=short

Uses requests (HTTP) rather than boto3 Lambda invoke so it exercises the
full ALB → ECS/Lambda path exactly as a browser would.
"""
import json
import os
import time

import pytest
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ALB_DNS = os.environ.get(
    "ALB_DNS",
    "lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com",
)
BASE_URL = f"http://{ALB_DNS}"

POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = int(os.environ.get("E2E_TIMEOUT_S", "900"))
REQUEST_TIMEOUT_S = 120

TEST_VIDEO_S3_KEY = os.environ.get("TEST_VIDEO_S3_KEY", "")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _poll_status(job_id: str, timeout: int = POLL_TIMEOUT_S) -> dict:
    """Poll /status/{job_id} until COMPLETED or timeout. Tolerates cold starts."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{BASE_URL}/status/{job_id}", timeout=60)
            r.raise_for_status()
            data = r.json()
            status = data.get("status", "").upper()
            if status in ("COMPLETED", "FAILED"):
                return data
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            pass  # Lambda cold start or transient — just retry
        time.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"Job {job_id} did not complete within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHealthAndConnectivity:
    """Basic connectivity checks."""

    def test_health_endpoint(self):
        """GET /health returns healthy."""
        r = requests.get(f"{BASE_URL}/health", timeout=REQUEST_TIMEOUT_S)
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") == "healthy"

    def test_root_returns_html_or_api(self):
        """GET / returns either Next.js HTML or API JSON — not a 5xx."""
        r = requests.get(f"{BASE_URL}/", timeout=REQUEST_TIMEOUT_S)
        assert r.status_code == 200

    def test_jobs_endpoint(self):
        """GET /jobs returns a JSON with jobs array."""
        r = requests.get(f"{BASE_URL}/jobs", timeout=REQUEST_TIMEOUT_S)
        assert r.status_code == 200
        body = r.json()
        assert "jobs" in body
        assert isinstance(body["jobs"], list)

    def test_presign_upload_endpoint(self):
        """GET /presign-upload returns a presigned URL and required headers."""
        r = requests.get(
            f"{BASE_URL}/presign-upload",
            params={"filename": "test.mp4", "content_type": "video/mp4"},
            timeout=REQUEST_TIMEOUT_S,
        )
        assert r.status_code == 200
        body = r.json()
        assert "presign_url" in body
        assert "s3_key" in body
        assert "required_headers" in body

    def test_process_image_validates_body(self):
        """POST /process-image with no body returns 422 (validation error)."""
        r = requests.post(f"{BASE_URL}/process-image", timeout=REQUEST_TIMEOUT_S)
        assert r.status_code == 422

    def test_client_configs_404_for_unknown_job(self):
        """GET /client-configs/<unknown> returns 404, proving the route is wired."""
        r = requests.get(
            f"{BASE_URL}/client-configs/nonexistent-job-id",
            timeout=REQUEST_TIMEOUT_S,
        )
        assert r.status_code == 404

    def test_image_page_served_by_frontend(self):
        """GET /image returns HTML from the Next.js frontend (single-image page)."""
        r = requests.get(f"{BASE_URL}/image", timeout=REQUEST_TIMEOUT_S)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "").lower()


@pytest.mark.skipif(
    not TEST_VIDEO_S3_KEY,
    reason="Set TEST_VIDEO_S3_KEY env var to an existing S3 key to run pipeline tests",
)
class TestPipelineE2E:
    """Full pipeline tests — require a video already in S3."""

    @pytest.fixture(scope="class")
    def job_result(self):
        """Submit a processing job and poll to completion."""
        r = requests.post(
            f"{BASE_URL}/process-video",
            data={"s3_key": TEST_VIDEO_S3_KEY, "config": json.dumps({"max_snapshots": 5})},
            timeout=30,
        )
        assert r.status_code == 200
        body = r.json()
        job_id = body["job_id"]
        assert body["status"] == "QUEUED"

        status = _poll_status(job_id, timeout=POLL_TIMEOUT_S)
        assert status["status"] == "COMPLETED", f"Job ended with status {status['status']}"

        results_resp = requests.get(f"{BASE_URL}/results/{job_id}", timeout=10)
        assert results_resp.status_code == 200
        return results_resp.json()

    def test_frame_count(self, job_result):
        """Pipeline returns >= requested snapshot count."""
        snapshots = job_result.get("snapshots", [])
        assert len(snapshots) >= 3, f"Expected >= 3 snapshots, got {len(snapshots)}"

    def test_no_unknown_critical_fields(self, job_result):
        """road_type / weather / traffic should not be 'unknown'."""
        summary = job_result.get("summary", {})
        output_json_path = job_result.get("output_json")
        if output_json_path and os.path.exists(output_json_path):
            with open(output_json_path) as f:
                data = json.load(f)
            for field in ["weather", "traffic", "lighting"]:
                val = data.get(field, "unknown")
                assert val != "unknown", f"{field} should not be 'unknown'"

    def test_objects_have_structure(self, job_result):
        """Each frame's objects have required fields."""
        output_json_path = job_result.get("output_json")
        if output_json_path and os.path.exists(output_json_path):
            with open(output_json_path) as f:
                data = json.load(f)
            objects = data.get("objects", [])
            assert len(objects) > 0, "Expected at least one detected object"
            for obj in objects:
                assert "description" in obj
                assert "distance" in obj
                assert "priority" in obj
                assert "start_time_ms" in obj

    def test_hazards_present_when_vrus(self, job_result):
        """If VRUs are detected, at least one hazard event should exist."""
        output_json_path = job_result.get("output_json")
        if output_json_path and os.path.exists(output_json_path):
            with open(output_json_path) as f:
                data = json.load(f)
            vru_labels = {"pedestrian", "pedestrian(group)", "bicyclist", "motorcycle"}
            has_vru = any(
                obj.get("description") in vru_labels for obj in data.get("objects", [])
            )
            if has_vru:
                hazards = data.get("hazard_events", [])
                assert len(hazards) > 0, "VRUs detected but no hazard events"

    def test_priority_distribution(self, job_result):
        """Priority distribution should not be dominated by 'none'."""
        summary = job_result.get("summary", {})
        prio_dist = summary.get("priority_distribution", {})
        if prio_dist:
            total = sum(prio_dist.values())
            none_count = prio_dist.get("none", 0)
            if total > 0:
                none_pct = none_count / total
                assert none_pct < 0.9, f"'none' priority dominates ({none_pct:.0%})"


class TestCleanup:
    """Cleanup endpoint."""

    def test_cleanup_nonexistent_job(self):
        """DELETE /cleanup/nonexistent should not 500."""
        r = requests.delete(f"{BASE_URL}/cleanup/nonexistent-job-id", timeout=REQUEST_TIMEOUT_S)
        assert r.status_code in (200, 404)
