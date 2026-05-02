"""Frame sampling helpers shared by the V2 pipeline.

The important invariant is that ``frame_idx`` and ``timestamp_ms`` describe
the same point in the source video. Keeping those two aligned prevents the
extractor from seeking to an unrelated raster while downstream code thinks it
is looking at a later timestamp.
"""
from __future__ import annotations

from typing import List

from src.config import MAX_FRAMES_PER_VIDEO
from src.schemas import SnapshotInfo, VideoMetadata


def _total_frames(video_metadata: VideoMetadata) -> int:
    if video_metadata.total_frames and video_metadata.total_frames > 0:
        return int(video_metadata.total_frames)
    fps = max(float(video_metadata.fps or 0.0), 1e-6)
    return max(1, int(round((float(video_metadata.duration_ms or 0.0) / 1000.0) * fps)))


def _timestamp_for_frame(frame_idx: int, fps: float) -> float:
    if fps <= 0:
        return 0.0
    return (float(frame_idx) / fps) * 1000.0


def _frame_for_timestamp(timestamp_ms: float, fps: float, total_frames: int) -> int:
    frame_idx = int(round((max(0.0, timestamp_ms) / 1000.0) * fps))
    return max(0, min(frame_idx, max(0, total_frames - 1)))


def _uniformly_downsample(snapshots: List[SnapshotInfo], max_count: int) -> List[SnapshotInfo]:
    if max_count <= 0:
        return []
    if len(snapshots) <= max_count:
        return snapshots
    if max_count == 1:
        return [snapshots[len(snapshots) // 2]]

    step = (len(snapshots) - 1) / float(max_count - 1)
    selected_positions: List[int] = []
    seen = set()
    for i in range(max_count):
        pos = int(round(i * step))
        if pos not in seen:
            selected_positions.append(pos)
            seen.add(pos)

    if len(selected_positions) < max_count:
        for pos in range(len(snapshots)):
            if pos in seen:
                continue
            selected_positions.append(pos)
            seen.add(pos)
            if len(selected_positions) == max_count:
                break

    return [snapshots[pos] for pos in sorted(selected_positions)]


def generate_dense_snapshots(
    video_metadata: VideoMetadata,
    interval_ms: int,
    *,
    max_frames: int = MAX_FRAMES_PER_VIDEO,
) -> List[SnapshotInfo]:
    """Sample by wall-clock interval while keeping frame index aligned.

    For example, a 30 FPS video sampled every 500 ms yields frame indices
    roughly ``0, 15, 30, ...`` rather than ``0, 1, 2, ...``.
    """
    fps = max(float(video_metadata.fps or 0.0), 1e-6)
    total_frames = _total_frames(video_metadata)
    duration_ms = max(float(video_metadata.duration_ms or 0.0), 0.0)
    interval = max(1, int(interval_ms))
    snapshots: List[SnapshotInfo] = []
    seen_frames = set()

    current_time_ms = 0.0
    while current_time_ms < duration_ms:
        frame_idx = _frame_for_timestamp(current_time_ms, fps, total_frames)
        if frame_idx not in seen_frames:
            seen_frames.add(frame_idx)
            snapshots.append(
                SnapshotInfo(
                    frame_idx=frame_idx,
                    timestamp_ms=_timestamp_for_frame(frame_idx, fps),
                    reason=f"Dense sampling ({interval}ms interval)",
                ),
            )
        current_time_ms += interval

    if not snapshots:
        snapshots.append(
            SnapshotInfo(
                frame_idx=0,
                timestamp_ms=0.0,
                reason=f"Dense sampling ({interval}ms interval)",
            ),
        )

    return _uniformly_downsample(snapshots, max_frames)


def generate_uniform_snapshots(video_metadata: VideoMetadata, count: int) -> List[SnapshotInfo]:
    """Select an exact frame-count target with even jumps across the video."""
    requested = max(1, int(count))
    fps = max(float(video_metadata.fps or 0.0), 1e-6)
    total_frames = _total_frames(video_metadata)

    if requested == 1:
        frame_indices = [total_frames // 2]
    else:
        step = (total_frames - 1) / float(requested - 1)
        frame_indices = [int(round(i * step)) for i in range(requested)]

    snapshots: List[SnapshotInfo] = []
    seen_frames = set()
    for frame_idx in frame_indices:
        clamped_idx = max(0, min(frame_idx, total_frames - 1))
        if clamped_idx in seen_frames:
            continue
        seen_frames.add(clamped_idx)
        snapshots.append(
            SnapshotInfo(
                frame_idx=clamped_idx,
                timestamp_ms=_timestamp_for_frame(clamped_idx, fps),
                reason=f"Native frame count ({requested} frames)",
            ),
        )

    return snapshots
