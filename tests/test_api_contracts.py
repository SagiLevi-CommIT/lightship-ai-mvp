"""Offline contract tests for the FastAPI surface.

Purpose
-------
Lock in the request/response shape of every endpoint the UI calls. Unlike
``test_02_backend_api`` and ``test_e2e_live``, these tests do NOT require
AWS credentials or a live Lambda — they use FastAPI's ``TestClient`` with
stubbed S3/DynamoDB clients.

To avoid the 30-second cold start that comes from importing YOLO /
transformers / torch we pre-install lightweight stubs in ``sys.modules``
before ``api_server`` is imported. The stubs give the ``Pipeline`` symbol
the minimum API the server references at module load time.

Contract assertions are deliberately strict on status codes and top-level
keys (those are the stable bit of the API) and lenient on values that
depend on mocked infrastructure.
"""
from __future__ import annotations

import io
import sys
import types
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest


_BE_SRC = Path(__file__).resolve().parent.parent / "lambda-be"
if str(_BE_SRC) not in sys.path:
    sys.path.insert(0, str(_BE_SRC))


def _install_pipeline_stub() -> None:
    """Stub out the heavyweight ``src.pipeline`` module.

    ``api_server`` only references ``Pipeline`` at class-body level; no
    instance is constructed until ``process_video_task`` runs. A stub class
    with the right name is therefore enough to satisfy module-load.
    """
    if "src.pipeline" in sys.modules:
        return

    stub = types.ModuleType("src.pipeline")

    class _StubPipeline:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.merger = MagicMock()
            self.merger.output_dir = "/tmp"
            self.video_loader = MagicMock()

        def process_video(self, *args: Any, **kwargs: Any) -> None:
            return None

    stub.Pipeline = _StubPipeline
    sys.modules["src.pipeline"] = stub


def _install_config_generator_stub() -> None:
    # Real module is lightweight (no ML deps); avoid stubbing so
    # ``tests/test_07_config_generator.py`` running in the same pytest
    # session still sees the actual ``generate_client_configs``.
    return


class _StubTable:
    """Minimal DynamoDB ``Table`` stand-in.

    Behaves like a single-key table on ``job_id``. Accepts any attribute
    names — in particular the aliased reserved-word updates from
    ``job_status.update_status``.
    """

    def __init__(self) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}

    def put_item(self, Item: Dict[str, Any]) -> Dict[str, Any]:
        self.items[Item["job_id"]] = dict(Item)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_item(self, Key: Dict[str, Any]) -> Dict[str, Any]:
        item = self.items.get(Key["job_id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, **kwargs: Any) -> Dict[str, Any]:
        job_id = kwargs["Key"]["job_id"]
        current = self.items.setdefault(job_id, {"job_id": job_id})
        names = kwargs.get("ExpressionAttributeNames", {})
        values = kwargs.get("ExpressionAttributeValues", {})
        for placeholder, attr_name in names.items():
            placeholder_value = placeholder.replace("#", ":")
            if placeholder_value in values:
                current[attr_name] = values[placeholder_value]
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def scan(self, Limit: int = 50) -> Dict[str, Any]:
        items = list(self.items.values())
        return {"Items": items[:Limit]}


class _StubS3Client:
    """In-memory S3 double used for presign and upload round-trips."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}

    def generate_presigned_url(self, operation: str, Params: Dict[str, Any],
                               ExpiresIn: int = 900) -> str:
        return (
            f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}"
            f"?X-Amz-SignedHeaders=content-type"
        )

    def upload_file(self, Filename: str, Bucket: str, Key: str,
                    ExtraArgs: Dict[str, Any] = None) -> None:
        with open(Filename, "rb") as fp:
            self.objects[f"{Bucket}/{Key}"] = fp.read()

    def put_object(self, Bucket: str, Key: str, Body: bytes,
                   ContentType: str = "application/octet-stream",
                   ServerSideEncryption: str = None) -> Dict[str, Any]:
        self.objects[f"{Bucket}/{Key}"] = Body
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        data = self.objects.get(f"{Bucket}/{Key}")
        if data is None:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not found"}},
                "GetObject",
            )
        with open(Filename, "wb") as fp:
            fp.write(data)

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:
        data = self.objects.get(f"{Bucket}/{Key}")
        if data is None:
            from botocore.exceptions import ClientError
            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}}, "GetObject",
            )
        return {"Body": io.BytesIO(data)}


@pytest.fixture(scope="module")
def api_client():
    """Boot the FastAPI app with in-memory stubs and yield a TestClient.

    Using ``scope="module"`` so the expensive import happens once; tests
    reset the stub state between runs via the ``reset`` fixture.
    """
    _install_pipeline_stub()
    _install_config_generator_stub()

    from fastapi.testclient import TestClient

    from src import api_server, job_status

    s3_client = _StubS3Client()
    table = _StubTable()

    api_server._s3_client = s3_client
    api_server._jobs_table = table
    api_server._lambda_client = None
    api_server.LAMBDA_FUNCTION_NAME = ""
    api_server.PROCESSING_BUCKET = "lightship-test-bucket"

    job_status.set_table(table)
    job_status.clear()

    client = TestClient(api_server.app)

    yield client, api_server, job_status, table, s3_client

    job_status.clear()


@pytest.fixture(autouse=True)
def reset(api_client):
    _, api_server, job_status, table, s3_client = api_client
    api_server.processing_results.clear()
    job_status.clear()
    table.items.clear()
    s3_client.objects.clear()


def test_root_returns_version_info(api_client):
    client = api_client[0]
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert set(["message", "version", "status"]).issubset(body.keys())


def test_health_returns_healthy(api_client):
    client = api_client[0]
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy"}


def test_jobs_lists_created_jobs(api_client):
    client, _, _, table, _ = api_client
    table.put_item(Item={"job_id": "j1", "status": "COMPLETED",
                         "created_at": "2026-04-19T00:00:00"})
    table.put_item(Item={"job_id": "j2", "status": "PROCESSING",
                         "created_at": "2026-04-19T01:00:00"})

    r = client.get("/jobs?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert "jobs" in body
    job_ids = {j["job_id"] for j in body["jobs"]}
    assert job_ids == {"j1", "j2"}


def test_presign_upload_returns_url_and_key(api_client):
    client = api_client[0]
    r = client.get("/presign-upload?filename=clip.mp4")
    assert r.status_code == 200
    body = r.json()
    assert "presign_url" in body
    assert body["s3_key"].endswith("/clip.mp4")
    assert body["required_headers"]["Content-Type"] == "video/mp4"
    assert body["required_headers"]["x-amz-server-side-encryption"] == "aws:kms"


def test_process_video_rejects_empty_body(api_client):
    client = api_client[0]
    r = client.post("/process-video")
    assert r.status_code in (400, 422)


def test_process_video_with_s3_key_queues_job(api_client):
    """Happy path: UI called ``/presign-upload``, PUT the file to S3, then
    hit ``/process-video`` with the returned s3_key. Backend must create a
    QUEUED Dynamo row and return the job_id immediately.
    """
    client, api_server, _, table, s3_client = api_client

    # Pre-seed S3 with the object referenced by s3_key so the local dev
    # fallback path can "download" it without a NoSuchKey error.
    s3_client.objects[
        f"{api_server.PROCESSING_BUCKET}/input/videos/x/clip.mp4"
    ] = b"fake mp4 bytes"

    r = client.post(
        "/process-video",
        data={"s3_key": "input/videos/x/clip.mp4"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # The API must acknowledge QUEUED immediately — the subsequent background
    # task may move the row to FAILED when the stub Pipeline returns None,
    # but that's orthogonal to the contract we're locking in here.
    assert body["status"] == "QUEUED"
    assert len(body["job_id"]) > 0
    assert body["job_id"] in table.items


def test_status_returns_dynamo_progress_cross_invocation(api_client):
    """Simulates a cold Lambda instance serving ``/status`` — the warm
    dict is empty so the handler must read from DynamoDB.
    """
    client, _, js, table, _ = api_client
    table.items["cold"] = {
        "job_id": "cold",
        "status": "PROCESSING",
        "progress": Decimal("0.3"),
        "message": "Processing video with pipeline",
        "current_step": "processing",
    }
    assert "cold" not in js.processing_status

    r = client.get("/status/cold")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "PROCESSING"
    assert body["progress"] == 0.3
    assert body["message"] == "Processing video with pipeline"
    assert body["current_step"] == "processing"


def test_status_404_on_unknown_job(api_client):
    client = api_client[0]
    r = client.get("/status/does-not-exist")
    assert r.status_code == 404


def test_results_404_on_unknown_job(api_client):
    client = api_client[0]
    r = client.get("/results/does-not-exist")
    assert r.status_code == 404


def test_download_json_404_on_unknown_job(api_client):
    client = api_client[0]
    r = client.get("/download/json/does-not-exist")
    assert r.status_code == 404


def test_download_frame_404_on_unknown_job(api_client):
    client = api_client[0]
    r = client.get("/download/frame/does-not-exist/0")
    assert r.status_code == 404


def test_frames_404_for_unknown_job(api_client):
    """Unknown job id returns 404 rather than an empty list, so the UI can
    differentiate "not started" from "started but no frames yet"."""
    client = api_client[0]
    r = client.get("/frames/does-not-exist")
    assert r.status_code == 404


def test_video_class_404_on_unknown_job(api_client):
    client = api_client[0]
    r = client.get("/video-class/does-not-exist")
    assert r.status_code == 404


def test_client_configs_404_on_unknown_job(api_client):
    client = api_client[0]
    r = client.get("/client-configs/does-not-exist")
    assert r.status_code == 404


def test_cleanup_always_returns_ok(api_client):
    client = api_client[0]
    r = client.delete("/cleanup/any-id-at-all")
    assert r.status_code == 200


def test_process_image_requires_input(api_client):
    client = api_client[0]
    r = client.post("/process-image")
    assert r.status_code in (400, 422)


def test_process_s3_video_requires_uri(api_client):
    client = api_client[0]
    r = client.post("/process-s3-video", json={})
    assert r.status_code in (400, 422)
