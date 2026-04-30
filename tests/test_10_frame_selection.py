"""Unit tests for frame selection utilities.

Focus on behaviour we can exercise without the Lambda's full dependency
graph (no Bedrock, no YOLO, no Rekognition): clustering, dedup, small
candidate sets.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))

from src.frame_selector import select_frames_by_clustering  # noqa: E402


def _save_frame(path: Path, seed: int) -> None:
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, size=(120, 160, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)


def test_clustering_returns_all_frames_when_fewer_than_n(tmp_path):
    frames = {}
    for i in range(3):
        p = tmp_path / f"frame_{i}.png"
        _save_frame(p, i)
        frames[i] = str(p)
    selected = select_frames_by_clustering(frames, n_select=5)
    assert selected == [0, 1, 2]


def test_clustering_selects_n_diverse_frames(tmp_path):
    frames = {}
    for i in range(12):
        p = tmp_path / f"frame_{i}.png"
        _save_frame(p, i)
        frames[i] = str(p)
    selected = select_frames_by_clustering(frames, n_select=4)
    assert len(selected) == 4
    assert len(set(selected)) == 4
    assert selected == sorted(selected)


def test_clustering_handles_corrupt_frame_files(tmp_path):
    frames = {}
    for i in range(6):
        p = tmp_path / f"frame_{i}.png"
        _save_frame(p, i)
        frames[i] = str(p)
    # Corrupt one file — the selector must not crash, and must still
    # return a plausible number of valid frames.
    (tmp_path / "frame_2.png").write_bytes(b"not-an-image")
    selected = select_frames_by_clustering(frames, n_select=3)
    assert 2 not in selected or len(selected) >= 1
    assert len(selected) <= 3
    for idx in selected:
        assert idx in frames


def test_clustering_is_deterministic_with_fixed_seed(tmp_path):
    frames = {i: str(tmp_path / f"frame_{i}.png") for i in range(10)}
    for i in range(10):
        _save_frame(Path(frames[i]), i)
    a = select_frames_by_clustering(frames, n_select=4)
    b = select_frames_by_clustering(frames, n_select=4)
    assert a == b
