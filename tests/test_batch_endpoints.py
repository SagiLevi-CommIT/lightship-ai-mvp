"""Phase 4 tests: batch submit, batch status, frames-zip.

Exercises the three new endpoints with the same in-memory S3+Dynamo
stubs we use for the contract tests. The goal is to lock in the
request/response shapes the UI depends on; the actual pipeline is still
stubbed.
"""
from __future__ import annotations

import io
import json
import sys
import types
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest


_BE_SRC = Path(__file__).resolve().parent.parent / "lambda-be"
if str(_BE_SRC) not in sys.path:
    sys.path.insert(0, str(_BE_SRC))


def _install_stubs() -> None:
    # Only ``src.pipeline`` has heavyweight ML imports; everything else (schemas,
    # config_generator, etc.) is lightweight and should run as the real module so
    # sibling test files that exercise it can import the actual implementation.
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


class _StubTable:
    def __init__(self) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}

    def put_item(self, Item: Dict[str, Any]) -> Dict[str, Any]:
        self.items[Item["job_id"]] = dict(Item)
        return {}

    def get_item(self, Key: Dict[str, Any]) -> Dict[str, Any]:
        return {"Item": self.items.get(Key["job_id"])}

    def update_item(self, **kwargs: Any) -> Dict[str, Any]:
        import re
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

    def scan(self, Limit: int = 50) -> Dict[str, Any]:
        return {"Items": list(self.items.values())[:Limit]}


class _StubS3Client:
    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.copies: list = []

    def generate_presigned_url(self, operation: str, Params: Dict[str, Any],
                               ExpiresIn: int = 900) -> str:
        return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}"

    def upload_file(self, Filename: str, Bucket: str, Key: str,
                    ExtraArgs: Dict[str, Any] = None) -> None:
        with open(Filename, "rb") as fp:
            self.objects[f"{Bucket}/{Key}"] = fp.read()

    def put_object(self, **kwargs: Any) -> Dict[str, Any]:
        self.objects[f"{kwargs['Bucket']}/{kwargs['Key']}"] = kwargs["Body"]
        return {}

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:
        data = self.objects.get(f"{Bucket}/{Key}")
        if data is None:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(data)}

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        data = self.objects.get(f"{Bucket}/{Key}")
        if data is None:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        with open(Filename, "wb") as fp:
            fp.write(data)

    def copy_object(self, **kwargs: Any) -> Dict[str, Any]:
        source = kwargs["CopySource"]
        src_key = f"{source['Bucket']}/{source['Key']}"
        dst_key = f"{kwargs['Bucket']}/{kwargs['Key']}"
        if src_key in self.objects:
            self.objects[dst_key] = self.objects[src_key]
        self.copies.append((src_key, dst_key))
        return {}

    def get_paginator(self, operation: str) -> Any:
        parent = self

        class _Paginator:
            def paginate(self, Bucket: str, Prefix: str = ""):
                contents = []
                for full_key, _bytes in parent.objects.items():
                    bucket_in, _, key = full_key.partition("/")
                    if bucket_in == Bucket and key.startswith(Prefix):
                        contents.append({"Key": key})
                yield {"Contents": contents}

        return _Paginator()


@pytest.fixture(scope="module")
def api_client():
    _install_stubs()

    from fastapi.testclient import TestClient
    from src import api_server, job_status

    s3_client = _StubS3Client()
    table = _StubTable()

    api_server._s3_client = s3_client
    api_server._jobs_table = table
    api_server._lambda_client = None
    api_server._sqs_client = None
    api_server.LAMBDA_FUNCTION_NAME = ""
    api_server.PROCESSING_QUEUE_URL = ""
    api_server.PROCESSING_BUCKET = "lightship-test-bucket"

    job_status.set_table(table)
    job_status.clear()

    client = TestClient(api_server.app)
    yield client, api_server, job_status, table, s3_client

    job_status.clear()


@pytest.fixture(autouse=True)
def reset(api_client):
    _, api_server, job_status_mod, table, s3_client = api_client
    api_server.processing_results.clear()
    job_status_mod.clear()
    table.items.clear()
    s3_client.objects.clear()
    s3_client.copies.clear()


def test_batch_process_enqueues_two_s3_keys(api_client):
    client, api_server, _, table, _ = api_client

    r = client.post(
        "/batch/process",
        json={
            "items": [
                {"s3_key": "input/videos/a/a.mp4", "filename": "a.mp4"},
                {"s3_key": "input/videos/b/b.mp4", "filename": "b.mp4"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert len(body["jobs"]) == 2
    names = {j["filename"] for j in body["jobs"]}
    assert names == {"a.mp4", "b.mp4"}
    for job in body["jobs"]:
        assert job["job_id"] in table.items
        assert job["status"] == "QUEUED"
        assert job["dispatch"] == "background"


def test_batch_process_expands_prefix(api_client):
    client, api_server, _, table, s3_client = api_client

    # Seed the processing bucket with three mp4s and one non-video.
    for key in (
        "batch-input/a.mp4",
        "batch-input/b.mp4",
        "batch-input/sub/c.mov",
        "batch-input/readme.txt",
    ):
        s3_client.objects[f"{api_server.PROCESSING_BUCKET}/{key}"] = b"fake"

    r = client.post(
        "/batch/process",
        json={"items": [{"s3_prefix": "batch-input/"}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3  # excludes readme.txt
    filenames = sorted(j["filename"] for j in body["jobs"])
    assert filenames == ["a.mp4", "b.mp4", "c.mov"]


def test_process_s3_prefix_shortcut(api_client):
    client, api_server, _, _, s3_client = api_client
    s3_client.objects[f"{api_server.PROCESSING_BUCKET}/pfx/v1.mp4"] = b"x"

    r = client.post("/process-s3-prefix", json={"s3_prefix": "pfx/"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["jobs"][0]["filename"] == "v1.mp4"


def test_batch_status_returns_per_job_rows(api_client):
    client, _, _, table, _ = api_client
    table.items["j1"] = {
        "job_id": "j1", "status": "COMPLETED",
        "progress": Decimal("1.0"), "message": "done",
    }
    table.items["j2"] = {
        "job_id": "j2", "status": "PROCESSING",
        "progress": Decimal("0.3"), "message": "halfway",
    }

    r = client.get("/batch/status?job_ids=j1,j2,unknown")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    by_id = {row["job_id"]: row for row in body["jobs"]}
    assert by_id["j1"]["status"] == "COMPLETED"
    assert by_id["j1"]["progress"] == 1.0
    assert by_id["j2"]["progress"] == 0.3
    assert by_id["unknown"]["status"] == "NOT_FOUND"


def test_batch_status_requires_job_ids(api_client):
    client = api_client[0]
    r = client.get("/batch/status")
    assert r.status_code in (400, 422)
    r2 = client.get("/batch/status?job_ids=")
    assert r2.status_code == 422


def test_frames_zip_404_for_unknown_job(api_client):
    client = api_client[0]
    r = client.get("/download/frames-zip/does-not-exist")
    assert r.status_code == 404


def test_frames_zip_streams_manifest_contents(api_client):
    """Seed a frames manifest in the warm cache and verify the ZIP pulls
    every entry (annotated PNG + per-frame JSON) from S3 and embeds them.
    """
    client, api_server, _, table, s3_client = api_client
    bucket = api_server.PROCESSING_BUCKET

    # Dynamo row so /download/frames-zip's 404 guard passes
    table.put_item(Item={"job_id": "job-zip", "status": "COMPLETED"})

    # Frames in S3
    s3_client.objects[f"{bucket}/results/job-zip/frames/frame_0000_annotated.png"] = b"PNG-0"
    s3_client.objects[f"{bucket}/results/job-zip/frames/frame_0000.json"] = b'{"frame":0}'
    s3_client.objects[f"{bucket}/results/job-zip/frames/frame_0001_annotated.png"] = b"PNG-1"
    s3_client.objects[f"{bucket}/results/job-zip/frames/frame_0001.json"] = b'{"frame":1}'
    s3_client.objects[f"{bucket}/results/job-zip/output.json"] = b'{"ok":true}'

    api_server.processing_results["job-zip"] = {
        "frames_manifest": [
            {
                "frame_idx": 0,
                "timestamp_ms": 0,
                "num_objects": 0,
                "annotated_key": "results/job-zip/frames/frame_0000_annotated.png",
                "raw_key": None,
                "json_key": "results/job-zip/frames/frame_0000.json",
            },
            {
                "frame_idx": 1,
                "timestamp_ms": 1000,
                "num_objects": 0,
                "annotated_key": "results/job-zip/frames/frame_0001_annotated.png",
                "raw_key": None,
                "json_key": "results/job-zip/frames/frame_0001.json",
            },
        ],
    }

    r = client.get("/download/frames-zip/job-zip")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers.get("content-disposition", "")

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert "frames/frame_0000.png" in names
        assert "frames/frame_0001.png" in names
        assert "frames/frame_0000.json" in names
        assert "frames/frame_0001.json" in names
        assert "output.json" in names
        assert zf.read("frames/frame_0000.png") == b"PNG-0"
        assert json.loads(zf.read("output.json")) == {"ok": True}
