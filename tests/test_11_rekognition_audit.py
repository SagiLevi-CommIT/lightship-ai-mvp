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
from unittest.mock import MagicMock

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


def test_custom_labels_skipped_when_arn_absent(frame_png, monkeypatch):
    monkeypatch.delenv("REKOGNITION_CUSTOM_MODEL_ARN", raising=False)
    import boto3
    monkeypatch.setattr(boto3, "client", MagicMock())

    from src.rekognition_labeler import RekognitionLabeler
    labeler = RekognitionLabeler(region_name="us-east-1")
    labeler.client = MagicMock()
    labeler.client.detect_labels.return_value = {"Labels": []}

    labeler.detect(
        frame_path=frame_png, timestamp_ms=0.0,
        video_width=100, video_height=100,
    )
    audit = labeler.build_audit()
    assert audit[0]["custom_labels_invoked"] is False
    assert audit[0]["custom_model_arn"] is None
    labeler.client.detect_custom_labels.assert_not_called()


def test_custom_labels_invoked_when_arn_present(frame_png, monkeypatch):
    arn = "arn:aws:rekognition:us-east-1:111111111111:project/p/version/v/1"
    monkeypatch.setenv("REKOGNITION_CUSTOM_MODEL_ARN", arn)
    import boto3
    monkeypatch.setattr(boto3, "client", MagicMock())

    from src.rekognition_labeler import RekognitionLabeler
    labeler = RekognitionLabeler(region_name="us-east-1")
    labeler.client = MagicMock()
    labeler.client.detect_labels.return_value = {"Labels": []}
    labeler.client.detect_custom_labels.return_value = {
        "CustomLabels": [
            {
                "Name": "crosswalk",
                "Confidence": 87.5,
                "Geometry": {"BoundingBox": {
                    "Left": 0.1, "Top": 0.2, "Width": 0.3, "Height": 0.4,
                }},
            },
            {
                "Name": "vehicle",
                "Confidence": 62.0,
                # Image-level (no Geometry) — must be ignored, not crash.
            },
        ]
    }

    out = labeler.detect(
        frame_path=frame_png, timestamp_ms=10.0,
        video_width=200, video_height=100,
    )
    audit = labeler.build_audit()
    assert audit[0]["custom_labels_invoked"] is True
    assert audit[0]["custom_model_arn"] == arn
    assert audit[0]["custom_kept_instances"] == 1
    assert any(o.description == "crosswalk" for o in out)
    labeler.client.detect_custom_labels.assert_called_once()
    call_kwargs = labeler.client.detect_custom_labels.call_args.kwargs
    assert call_kwargs["ProjectVersionArn"] == arn
    assert call_kwargs["MinConfidence"] == labeler.min_confidence


def test_custom_labels_failure_falls_back_safely(frame_png, monkeypatch):
    """Custom endpoint down / model not running — pipeline keeps standard path."""
    arn = "arn:aws:rekognition:us-east-1:111111111111:project/p/version/v/1"
    monkeypatch.setenv("REKOGNITION_CUSTOM_MODEL_ARN", arn)
    import boto3
    monkeypatch.setattr(boto3, "client", MagicMock())

    from botocore.exceptions import ClientError
    from src.rekognition_labeler import RekognitionLabeler

    labeler = RekognitionLabeler(region_name="us-east-1")
    labeler.client = MagicMock()
    labeler.client.detect_labels.return_value = {"Labels": []}
    labeler.client.detect_custom_labels.side_effect = ClientError(
        {"Error": {"Code": "InvalidParameterException", "Message": "not running"}},
        "DetectCustomLabels",
    )

    out = labeler.detect(
        frame_path=frame_png, timestamp_ms=5.0,
        video_width=100, video_height=100,
    )
    audit = labeler.build_audit()
    assert out == []
    assert audit[0]["custom_labels_invoked"] is True
    assert audit[0]["custom_error"]
    assert audit[0]["custom_kept_instances"] == 0
    assert audit[0]["custom_raw_labels"] == []
    assert audit[0]["error"] is None
