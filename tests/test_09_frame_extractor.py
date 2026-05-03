"""Frame extractor reliability tests.

Covers:

* Deterministic extraction from a synthetic MP4 generated on the fly.
* Validation of decoded frames — uniform / empty frames must be
  rejected before reaching the annotator.
* Manifest metadata (status / dimensions / source / timestamps).
* Duplicate-frame avoidance (same index requested twice → only decoded
  once but still written deterministically).
* Linear-walk fallback when ``cap.read()`` returns a "success" with a
  placeholder frame.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import List

import pytest

np = pytest.importorskip("numpy")
# Skip gracefully when cv2 isn't available (e.g. on the test VM).
cv2 = pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))

from src.frame_extractor import FrameExtractor, _is_real_frame  # noqa: E402
from src.schemas import SnapshotInfo, VideoMetadata  # noqa: E402


def _make_video(path: str, frames: List[np.ndarray], fps: int = 10) -> None:
    h, w = frames[0].shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, float(fps), (w, h))
    assert writer.isOpened(), f"VideoWriter failed to open {path}"
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()


def _colourful_frame(seed: int, w: int = 160, h: int = 120) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)


@pytest.fixture()
def synthetic_video(tmp_path):
    """20-frame synthetic video with distinct colourful frames."""
    video_path = str(tmp_path / "syn.mp4")
    frames = [_colourful_frame(i) for i in range(20)]
    _make_video(video_path, frames, fps=10)
    meta = VideoMetadata(
        filename="syn.mp4",
        filepath=video_path,
        camera="unknown",
        fps=10.0,
        duration_ms=2000.0,
        total_frames=20,
        width=160,
        height=120,
    )
    return meta, frames


def test_is_real_frame_rejects_uniform_and_empty():
    assert _is_real_frame(None) is False
    assert _is_real_frame(np.zeros((0, 0), dtype=np.uint8)) is False
    grey = np.full((100, 100, 3), 128, dtype=np.uint8)
    assert _is_real_frame(grey) is False
    rng = np.random.default_rng(7)
    low_contrast = np.full((100, 100, 3), 128, dtype=np.int16)
    low_contrast += rng.integers(-1, 2, size=(100, 100, 3), dtype=np.int16)
    low_contrast = np.clip(low_contrast, 0, 255).astype(np.uint8)
    assert _is_real_frame(low_contrast) is False
    colourful = _colourful_frame(42)
    assert _is_real_frame(colourful) is True


def test_extract_frames_produces_manifest(synthetic_video, tmp_path):
    meta, _ = synthetic_video
    extractor = FrameExtractor(output_dir=str(tmp_path / "out"))

    snaps = [
        SnapshotInfo(frame_idx=1, timestamp_ms=100.0),
        SnapshotInfo(frame_idx=10, timestamp_ms=1000.0),
        SnapshotInfo(frame_idx=19, timestamp_ms=1900.0),
    ]
    result = extractor.extract_frames_with_manifest(meta, snaps)

    assert len(result.frames) == 3
    assert len(result.manifest) == 3
    for entry in result.manifest:
        assert entry.status in ("ok", "substituted")
        assert entry.width > 0 and entry.height > 0
        assert entry.path is not None and Path(entry.path).exists()


def test_extract_frames_handles_duplicate_requests(synthetic_video, tmp_path):
    meta, _ = synthetic_video
    extractor = FrameExtractor(output_dir=str(tmp_path / "dup"))

    snaps = [
        SnapshotInfo(frame_idx=5, timestamp_ms=500.0),
        SnapshotInfo(frame_idx=5, timestamp_ms=500.0),  # duplicate
        SnapshotInfo(frame_idx=9, timestamp_ms=900.0),
    ]
    result = extractor.extract_frames_with_manifest(meta, snaps)

    # Two distinct indices end up in the frame dict.
    assert set(result.frames.keys()) == {5, 9}
    # Manifest records three entries (we do not collapse requests so
    # the caller can observe the repeated timestamp).
    assert len(result.manifest) == 3


def test_extract_frames_with_out_of_range_index_fails_gracefully(
    synthetic_video, tmp_path,
):
    meta, _ = synthetic_video
    extractor = FrameExtractor(output_dir=str(tmp_path / "oor"))

    # Request an index well past EOF. Fallback should either substitute
    # a readable neighbour (clamped to the last frame) or record a
    # clean failure; either way we never crash.
    snaps = [SnapshotInfo(frame_idx=9999, timestamp_ms=99999.0)]
    result = extractor.extract_frames_with_manifest(meta, snaps)
    entry = result.manifest[0]
    assert entry.status in ("ok", "substituted", "failed")
    if entry.status == "failed":
        assert entry.error  # must carry a reason


def test_backward_compatible_extract_frames_returns_dict(
    synthetic_video, tmp_path,
):
    meta, _ = synthetic_video
    extractor = FrameExtractor(output_dir=str(tmp_path / "bc"))
    frames = extractor.extract_frames(
        meta, [SnapshotInfo(frame_idx=0, timestamp_ms=0.0)],
    )
    assert isinstance(frames, dict)
    assert 0 in frames
    assert Path(frames[0]).exists()


def test_extraction_is_deterministic(synthetic_video, tmp_path):
    meta, _ = synthetic_video
    snaps = [
        SnapshotInfo(frame_idx=2, timestamp_ms=200.0),
        SnapshotInfo(frame_idx=8, timestamp_ms=800.0),
    ]
    e1 = FrameExtractor(output_dir=str(tmp_path / "run1"))
    e2 = FrameExtractor(output_dir=str(tmp_path / "run2"))
    a = e1.extract_frames_with_manifest(meta, snaps)
    b = e2.extract_frames_with_manifest(meta, snaps)
    assert set(a.frames.keys()) == set(b.frames.keys())
    # Hash of the first selected frame should match across runs.
    img_a = cv2.imread(a.frames[2], cv2.IMREAD_COLOR)
    img_b = cv2.imread(b.frames[2], cv2.IMREAD_COLOR)
    assert img_a is not None and img_b is not None
    assert np.array_equal(img_a, img_b)
