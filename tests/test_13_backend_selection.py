"""Backend selection — vision_audit reflects the configured detector only."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))


@pytest.fixture()
def frame_png(tmp_path):
    path = tmp_path / "frame.png"
    rng = np.random.default_rng(7)
    img = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


@pytest.mark.parametrize(
    "backend,stub_attr,stub_factory",
    [
        ("florence2", "_florence2", lambda: _stub_detect("florence2")),
        ("yolo", "_yolo", lambda: _stub_detect("yolo")),
        ("detectron2", "_detectron2", lambda: _stub_detect("detectron2")),
    ],
)
def test_vision_audit_primary_backend_matches_selection(frame_png, backend, stub_attr, stub_factory):
    """Each detector_backend runs only that path; audit primary_backend matches."""
    from src.vision_labeler import VisionLabeler

    stub = stub_factory()
    lbl = VisionLabeler(detector_backend=backend, lane_backend="opencv")
    setattr(lbl, stub_attr, stub)

    lbl.detect(frame_path=frame_png, timestamp_ms=100.0, video_width=64, video_height=64)
    audit = lbl.build_audit()
    assert len(audit) == 1
    assert audit[0]["primary_backend"] == backend
    assert audit[0]["primary_kept_instances"] == 1
    stub.detect.assert_called_once()


def test_processing_config_auto_maps_to_florence2():
    """ProcessingConfig normalises legacy 'auto' to florence2."""
    from src.processing_models import ProcessingConfig

    cfg = ProcessingConfig(detector_backend="auto")
    assert cfg.detector_backend == "florence2"


def test_processing_config_defaults_native_sampling_to_count():
    """Native FPS is ignored unless native_sampling_mode explicitly selects fps."""
    from src.processing_models import ProcessingConfig

    cfg = ProcessingConfig(native_fps=30)
    assert cfg.native_sampling_mode == "count"


def _stub_detect(name: str):
    m = MagicMock()
    from src.schemas import Center, ObjectLabel

    obj = ObjectLabel(
        description="car",
        start_time_ms=100.0,
        distance="moderate",
        priority="medium",
        center=Center(x=10, y=10),
        x_min=0.0,
        y_min=0.0,
        x_max=20.0,
        y_max=20.0,
        width=20.0,
        height=20.0,
    )
    m.detect.return_value = ([obj], [{"name": "car", "confidence": 0.9, "source": name}])
    return m

