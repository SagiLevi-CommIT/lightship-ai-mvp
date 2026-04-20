"""Rekognition audit tests.

Verifies:

* ``build_audit`` captures per-frame metrics (raw labels, confidences,
  kept-instance counts, latency) even when Rekognition is offline.
* Rekognition is only ever invoked on **raw** frames, never on an
  annotated image — that's the Task 6 contract (clean RGB in, labels
  out).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))


@pytest.fixture()
def frame_png(tmp_path):
    path = tmp_path / "frame.png"
    rng = np.random.default_rng(7)
    img = rng.integers(0, 256, size=(100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def test_build_audit_records_failure(frame_png, monkeypatch):
    # Stub out boto3 so the Rekognition client init doesn't hit AWS.
    import boto3
    fake = MagicMock()
    fake.detect_labels.side_effect = Exception("network down")
    monkeypatch.setattr(boto3, "client", MagicMock(return_value=fake))

    from src.rekognition_labeler import RekognitionLabeler  # noqa: WPS433
    labeler = RekognitionLabeler(region_name="us-east-1")
    # Manually wire in a ClientError so the real code path runs
    from botocore.exceptions import ClientError
    labeler.client = MagicMock()
    labeler.client.detect_labels.side_effect = ClientError(
        {"Error": {"Code": "Boom", "Message": "kaboom"}}, "DetectLabels",
    )

    out = labeler.detect(
        frame_path=frame_png, timestamp_ms=100.0,
        video_width=100, video_height=100,
    )
    audit = labeler.build_audit()
    assert out == []
    assert len(audit) == 1
    assert audit[0]["error"]
    assert audit[0]["kept_instances"] == 0
    assert audit[0]["timestamp_ms"] == pytest.approx(100.0)


def test_rekognition_receives_raw_frame_bytes(frame_png, monkeypatch):
    """``detect`` reads the clean PNG bytes — never an annotated image."""
    import boto3
    monkeypatch.setattr(boto3, "client", MagicMock())

    from src.rekognition_labeler import RekognitionLabeler  # noqa: WPS433
    labeler = RekognitionLabeler(region_name="us-east-1")

    captured = {}

    def fake_detect_labels(Image, MaxLabels, MinConfidence):
        captured["image_bytes"] = Image["Bytes"]
        captured["MinConfidence"] = MinConfidence
        return {"Labels": []}

    labeler.client = MagicMock()
    labeler.client.detect_labels.side_effect = fake_detect_labels

    labeler.detect(
        frame_path=frame_png, timestamp_ms=0.0,
        video_width=100, video_height=100,
    )

    with open(frame_png, "rb") as fp:
        expected = fp.read()
    assert captured["image_bytes"] == expected
    # min_confidence must be forwarded without modification.
    assert captured["MinConfidence"] == labeler.min_confidence
