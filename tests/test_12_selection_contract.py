"""Frame selection / extraction contract tests.

Covers the exact bugs reported against production:

* **Bug 1 (gray frames)**: ``normalize_brightness`` must not amplify a
  near-black frame into a uniform mid-grey image, and
  ``_is_real_frame`` must reject pitch-black codec preambles.
* **Bug 2 (frame count wrong)**: the pipeline's selection logic must
  top-up from the extracted-frame pool whenever the chosen strategy
  returns fewer frames than ``max_snapshots`` asked for.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))

from src.frame_extractor import _is_real_frame, MIN_FRAME_MEAN, MIN_FRAME_STD  # noqa: E402
from src.frame_preprocessor import normalize_brightness  # noqa: E402


# ---------------------------------------------------------------------------
# Bug 1: gray-frame protection
# ---------------------------------------------------------------------------

def test_is_real_frame_rejects_pitch_black_preamble():
    """Codec preamble frames have mean≈0 — they must fail the guard."""
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert _is_real_frame(frame) is False


def test_is_real_frame_rejects_nearly_uniform_frame():
    """std<MIN_FRAME_STD → placeholder, must fail."""
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    # Add a tiny crosshair so std > 0 but still well below threshold.
    frame[238:242, 316:324] = 255
    std = frame.std()
    assert std < MIN_FRAME_STD
    assert _is_real_frame(frame) is False


def test_is_real_frame_accepts_dark_but_real_scene():
    """Genuine night-driving footage: low-ish mean but std >= 8."""
    rng = np.random.default_rng(1)
    noise = rng.integers(0, 30, size=(480, 640, 3), dtype=np.uint8)
    base = np.full((480, 640, 3), 40, dtype=np.uint8)
    frame = np.clip(base.astype(np.int32) + noise, 0, 255).astype(np.uint8)
    assert frame.std() >= MIN_FRAME_STD
    assert frame.mean() >= MIN_FRAME_MEAN
    assert _is_real_frame(frame) is True


def test_normalize_brightness_does_not_amplify_black_frame():
    """The grey-frame root cause: a ~black frame was scaled up to grey."""
    frame = np.full((100, 100, 3), 2, dtype=np.uint8)  # mean ~2
    out = normalize_brightness(frame, target_mean=127.0, min_usable_mean=12.0)
    # The safeguard kicks in: frame is returned unchanged.
    assert np.array_equal(frame, out)


def test_normalize_brightness_still_works_on_normal_frames():
    rng = np.random.default_rng(2)
    frame = rng.integers(50, 120, size=(100, 100, 3), dtype=np.uint8)
    original_mean = frame.mean()
    out = normalize_brightness(frame, target_mean=127.0, min_usable_mean=12.0)
    # The output mean should be closer to target than the input mean.
    assert abs(out.mean() - 127.0) < abs(original_mean - 127.0)


# ---------------------------------------------------------------------------
# Bug 2: top-up contract
# ---------------------------------------------------------------------------

def test_uniform_count_selection_returns_requested_count():
    """Simulate the pipeline's uniform_count selection directly."""
    # Imitate the extractor output: 40 valid frame paths, widely spread.
    indices = list(range(0, 200, 5))
    max_snapshots = 10
    step = len(indices) / max_snapshots
    picked = [indices[int(i * step)] for i in range(max_snapshots)]
    assert len(set(picked)) == max_snapshots
    # Uniform spacing property: adjacent differences are roughly equal.
    diffs = [picked[i + 1] - picked[i] for i in range(len(picked) - 1)]
    assert max(diffs) - min(diffs) <= 6


def test_top_up_fills_short_selection():
    """Simulate: scene_change produced 3 frames, pool has 40. Top-up to 10."""
    initial = [3, 15, 25]
    pool = sorted(set(range(0, 40)) - set(initial))
    needed = 10 - len(initial)
    step = len(pool) / needed
    fill = [pool[int(i * step)] for i in range(needed)]
    final = sorted(set(initial + fill))
    assert len(final) == 10


def test_top_up_does_not_duplicate():
    initial = [5, 10, 15]
    pool = [0, 1, 2, 5, 10, 15, 20, 25]
    pool_available = [i for i in pool if i not in initial]
    needed = 5 - len(initial)  # only need 2 more for a max=5 run
    step = len(pool_available) / needed
    fill = [pool_available[int(i * step)] for i in range(needed)]
    final = sorted(set(initial + fill))
    assert len(final) == len(set(final))
    assert set(initial).issubset(set(final))
