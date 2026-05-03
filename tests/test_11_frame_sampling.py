"""Frame sampling tests for timestamp/frame-index alignment."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))

from src.frame_sampling import generate_dense_snapshots, generate_uniform_snapshots  # noqa: E402
from src.schemas import VideoMetadata  # noqa: E402


def _meta() -> VideoMetadata:
    return VideoMetadata(
        filename="clip.mp4",
        filepath="/tmp/clip.mp4",
        camera="unknown",
        fps=30.0,
        duration_ms=10_000.0,
        total_frames=300,
        width=1280,
        height=720,
    )


def test_dense_sampling_aligns_frame_index_with_timestamp():
    snapshots = generate_dense_snapshots(_meta(), interval_ms=500, max_frames=50)

    assert [s.frame_idx for s in snapshots[:4]] == [0, 15, 30, 45]
    assert [round(s.timestamp_ms) for s in snapshots[:4]] == [0, 500, 1000, 1500]


def test_dense_sampling_respects_safety_cap():
    snapshots = generate_dense_snapshots(_meta(), interval_ms=100, max_frames=5)

    assert len(snapshots) == 5
    assert snapshots == sorted(snapshots, key=lambda s: s.frame_idx)


def test_uniform_sampling_returns_requested_jump_count():
    snapshots = generate_uniform_snapshots(_meta(), count=5)

    assert [s.frame_idx for s in snapshots] == [50, 100, 150, 200, 250]
    assert len(snapshots) == 5


def test_uniform_sampling_two_frames_avoids_decoder_boundaries():
    snapshots = generate_uniform_snapshots(_meta(), count=2)

    assert [s.frame_idx for s in snapshots] == [100, 200]


def test_uniform_sampling_single_frame_uses_middle():
    snapshots = generate_uniform_snapshots(_meta(), count=1)

    assert len(snapshots) == 1
    assert snapshots[0].frame_idx == 150
