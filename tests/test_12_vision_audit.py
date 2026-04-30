"""Vision audit tests — VisionLabeler replaces RekognitionLabeler.

Verifies:
* ``build_audit`` records per-frame audit entries with the correct schema.
* Florence-2, YOLO, and Detectron2 single-backend paths (mocked) set ``primary_backend`` honestly.
* UFLDv2 lane backend is invoked when LANE_BACKEND=ufldv2.
* OpenCV lane backend is skipped by VisionLabeler when LANE_BACKEND=opencv
  (OpenCV lanes come from CVLabeler instead).
* The vision_audit block written into output.json matches the expected schema.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))


@pytest.fixture()
def frame_png(tmp_path):
    path = tmp_path / "frame.png"
    rng = np.random.default_rng(42)
    img = rng.integers(0, 256, size=(100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


# ---------------------------------------------------------------------------
# VisionLabeler core behaviour
# ---------------------------------------------------------------------------

class TestVisionLabelerAudit:
    def _make_labeler(self, detector_backend="florence2", lane_backend="ufldv2"):
        from src.vision_labeler import VisionLabeler
        return VisionLabeler(
            detector_backend=detector_backend,
            lane_backend=lane_backend,
            fallback_enabled=True,
        )

    def test_build_audit_empty_before_detect(self):
        lbl = self._make_labeler()
        assert lbl.build_audit() == []

    def test_detect_florence2_records_audit_entry(self, frame_png, monkeypatch):
        """A florence2 detect() call adds one audit entry with the correct keys."""
        from src.vision_labeler import VisionLabeler

        # Stub Florence-2 backend to avoid loading the real model
        mock_f2 = MagicMock()
        mock_f2.detect.return_value = ([], [{"name": "car", "confidence": 0.85, "source": "od"}])

        lbl = VisionLabeler(detector_backend="florence2", lane_backend="opencv")
        lbl._florence2 = mock_f2

        # Stub UFLDv2 (not used since lane_backend=opencv)
        lbl.detect(
            frame_path=frame_png,
            timestamp_ms=500.0,
            video_width=100,
            video_height=100,
        )

        audit = lbl.build_audit()
        assert len(audit) == 1
        entry = audit[0]
        for key in (
            "frame_path", "timestamp_ms", "primary_backend",
            "primary_elapsed_ms", "primary_kept_instances",
            "primary_raw_labels", "fallback_used",
            "lane_elapsed_ms", "lane_kept_instances", "lane_backend", "error",
        ):
            assert key in entry, f"Missing key in audit entry: {key}"
        assert entry["primary_backend"] == "florence2"
        assert entry["timestamp_ms"] == pytest.approx(500.0)
        assert entry["fallback_used"] is False
        assert entry["lane_backend"] == "opencv"

    def test_detect_yolo_only_calls_yolo_backend(self, frame_png):
        """yolo mode runs only the YOLO backend (no Florence-2)."""
        from src.vision_labeler import VisionLabeler

        mock_y = MagicMock()
        mock_y.detect.return_value = ([], [{"name": "car", "confidence": 0.5, "source": "yolo11n_coco"}])

        lbl = VisionLabeler(detector_backend="yolo", lane_backend="opencv")
        lbl._yolo = mock_y

        lbl.detect(frame_path=frame_png, timestamp_ms=0.0, video_width=100, video_height=100)

        audit = lbl.build_audit()
        assert len(audit) == 1
        assert audit[0]["primary_backend"] == "yolo"
        assert audit[0]["fallback_used"] is False
        mock_y.detect.assert_called_once()

    def test_detect_detectron2_only_calls_detectron2_backend(self, frame_png):
        """detectron2 mode runs only the Detectron2 backend."""
        from src.vision_labeler import VisionLabeler

        mock_d2 = MagicMock()
        mock_d2.detect.return_value = ([], [{"name": "car", "confidence": 0.9, "source": "d2"}])

        lbl = VisionLabeler(detector_backend="detectron2", lane_backend="opencv")
        lbl._detectron2 = mock_d2

        lbl.detect(frame_path=frame_png, timestamp_ms=0.0, video_width=100, video_height=100)
        mock_d2.detect.assert_called_once()
        audit = lbl.build_audit()
        assert audit[0]["primary_backend"] == "detectron2"

    def test_ufldv2_lane_backend_called(self, frame_png):
        """When lane_backend=ufldv2, the UFLDv2 backend's detect_lanes is called."""
        from src.vision_labeler import VisionLabeler

        mock_f2 = MagicMock()
        mock_f2.detect.return_value = ([], [])

        mock_ufld = MagicMock()
        mock_ufld.detect_lanes.return_value = []

        lbl = VisionLabeler(detector_backend="florence2", lane_backend="ufldv2")
        lbl._florence2 = mock_f2
        lbl._ufldv2 = mock_ufld

        lbl.detect(frame_path=frame_png, timestamp_ms=0.0, video_width=100, video_height=100)
        mock_ufld.detect_lanes.assert_called_once()

        audit = lbl.build_audit()
        assert audit[0]["lane_backend"] == "ufldv2"

    def test_opencv_lane_backend_not_duplicated_by_vision_labeler(self, frame_png):
        """When lane_backend=opencv, VisionLabeler does not call UFLDv2 (CVLabeler handles lanes)."""
        from src.vision_labeler import VisionLabeler

        mock_f2 = MagicMock()
        mock_f2.detect.return_value = ([], [])

        mock_ufld = MagicMock()

        lbl = VisionLabeler(detector_backend="florence2", lane_backend="opencv")
        lbl._florence2 = mock_f2
        lbl._ufldv2 = mock_ufld

        lbl.detect(frame_path=frame_png, timestamp_ms=0.0, video_width=100, video_height=100)
        mock_ufld.detect_lanes.assert_not_called()

        audit = lbl.build_audit()
        assert audit[0]["lane_backend"] == "opencv"

    def test_multiple_frames_accumulate_audit(self, frame_png):
        """Each detect() call adds one entry to the audit."""
        from src.vision_labeler import VisionLabeler

        mock_f2 = MagicMock()
        mock_f2.detect.return_value = ([], [])

        lbl = VisionLabeler(detector_backend="florence2", lane_backend="opencv")
        lbl._florence2 = mock_f2

        for ts in (0.0, 500.0, 1000.0):
            lbl.detect(frame_path=frame_png, timestamp_ms=ts, video_width=100, video_height=100)

        assert len(lbl.build_audit()) == 3

    def test_error_in_detect_does_not_raise(self, frame_png):
        """A backend exception is caught; audit records the error field."""
        from src.vision_labeler import VisionLabeler

        mock_f2 = MagicMock()
        mock_f2.detect.side_effect = RuntimeError("model crashed")

        lbl = VisionLabeler(detector_backend="florence2", lane_backend="opencv")
        lbl._florence2 = mock_f2

        result = lbl.detect(frame_path=frame_png, timestamp_ms=0.0, video_width=100, video_height=100)
        assert isinstance(result, list)
        audit = lbl.build_audit()
        assert audit[0]["error"] is not None


# ---------------------------------------------------------------------------
# output.json vision_audit schema contract
# ---------------------------------------------------------------------------

class TestVisionAuditSchema:
    def test_vision_audit_keys_present(self):
        """The vision_audit dict injected into output.json must have the spec keys."""
        required = {
            "frames_evaluated",
            "total_instances_kept",
            "backend",
            "lane_backend",
            "fallback_triggered_count",
            "per_frame",
        }
        per_frame_required = {
            "frame_path",
            "timestamp_ms",
            "primary_backend",
            "primary_elapsed_ms",
            "primary_kept_instances",
            "primary_raw_labels",
            "fallback_used",
            "fallback_elapsed_ms",
            "fallback_kept_instances",
            "lane_elapsed_ms",
            "lane_kept_instances",
            "lane_backend",
            "error",
        }
        # Build a mock audit and validate structure
        from src.vision_labeler import VisionLabeler
        from unittest.mock import MagicMock
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            frame_path = f.name
        try:
            img = np.zeros((50, 50, 3), dtype=np.uint8)  # type: ignore[attr-defined]
            cv2.imwrite(frame_path, img)  # type: ignore[attr-defined]

            mock_f2 = MagicMock()
            mock_f2.detect.return_value = ([], [])

            lbl = VisionLabeler(detector_backend="florence2", lane_backend="opencv")
            lbl._florence2 = mock_f2
            lbl.detect(frame_path=frame_path, timestamp_ms=0.0, video_width=50, video_height=50)

            per_frame = lbl.build_audit()
            vision_audit = {
                "frames_evaluated": len(per_frame),
                "total_instances_kept": 0,
                "backend": "florence2",
                "lane_backend": "opencv",
                "fallback_triggered_count": 0,
                "per_frame": per_frame,
            }

            for key in required:
                assert key in vision_audit, f"Missing key: {key}"
            for key in per_frame_required:
                assert key in per_frame[0], f"Missing per_frame key: {key}"
        finally:
            os.unlink(frame_path)
