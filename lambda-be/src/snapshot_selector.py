"""Snapshot selector module for choosing key frames from videos.

Implements two strategies:
1. Naive: Match GT timestamps for train videos, uniform sampling for test
2. Scene change detection: Use CV algorithms to detect scene transitions
"""
import json
import logging
import os
from typing import List, Optional
import cv2
import numpy as np
from src.schemas import SnapshotInfo, VideoMetadata
from src.config import (
    SNAPSHOT_STRATEGY,
    MAX_SNAPSHOTS_PER_VIDEO,
    EVAL_TOLERANCE_MS,
    SCENE_CHANGE_THRESHOLD,
    SCENE_CHANGE_MIN_INTERVAL_MS,
    TRAIN_DIR
)

logger = logging.getLogger(__name__)


class SnapshotSelector:
    """Selects key snapshots from videos using different strategies."""

    def __init__(self, strategy: str = SNAPSHOT_STRATEGY, max_snapshots: int = MAX_SNAPSHOTS_PER_VIDEO):
        """Initialize SnapshotSelector.

        Args:
            strategy: Selection strategy ('naive' or 'scene_change')
            max_snapshots: Maximum number of snapshots to select
        """
        self.strategy = strategy
        self.max_snapshots = max_snapshots
        logger.info(f"SnapshotSelector initialized with strategy: {strategy}, max_snapshots: {max_snapshots}")

    def select_snapshots(
        self,
        video_metadata: VideoMetadata,
        is_train: bool = False
    ) -> List[SnapshotInfo]:
        """Select snapshots from video based on strategy.

        Args:
            video_metadata: Video metadata
            is_train: Whether this is a training video

        Returns:
            List of SnapshotInfo objects
        """
        if self.strategy == "naive":
            return self._select_naive(video_metadata, is_train)
        elif self.strategy == "scene_change":
            return self._select_scene_change(video_metadata)
        else:
            raise ValueError(f"Unknown strategy: {self.strategy}")

    def _select_naive(
        self,
        video_metadata: VideoMetadata,
        is_train: bool
    ) -> List[SnapshotInfo]:
        """Naive selection strategy.

        For train videos: Match GT timestamps
        For test videos: Uniform sampling

        Args:
            video_metadata: Video metadata
            is_train: Whether this is a training video

        Returns:
            List of SnapshotInfo objects
        """
        if is_train:
            return self._select_from_gt(video_metadata)
        else:
            return self._select_uniform(video_metadata)

    def _select_from_gt(self, video_metadata: VideoMetadata) -> List[SnapshotInfo]:
        """Select snapshots matching GT timestamps for train videos.

        Args:
            video_metadata: Video metadata

        Returns:
            List of SnapshotInfo objects matching GT frames
        """
        # Find GT JSON files for this video
        video_name = os.path.splitext(video_metadata.filename)[0]
        gt_files = self._find_gt_files(video_name)

        if not gt_files:
            logger.warning(f"No GT files found for {video_name}, falling back to uniform sampling")
            return self._select_uniform(video_metadata)

        snapshots = []
        for gt_file in gt_files:
            try:
                with open(gt_file, 'r') as f:
                    gt_data = json.load(f)

                # Extract timestamp from first object
                if gt_data.get('objects'):
                    timestamp_str = gt_data['objects'][0]['start_time_ms']
                    timestamp_ms = float(timestamp_str)

                    # Convert to frame index
                    frame_idx = round((timestamp_ms / 1000) * video_metadata.fps)

                    # Validate frame index
                    if 0 <= frame_idx < video_metadata.total_frames:
                        snapshot = SnapshotInfo(
                            frame_idx=frame_idx,
                            timestamp_ms=timestamp_ms,
                            reason=f"GT match from {os.path.basename(gt_file)}"
                        )
                        snapshots.append(snapshot)
                        logger.info(
                            f"Selected GT snapshot: frame {frame_idx} at {timestamp_ms:.2f}ms"
                        )
                    else:
                        logger.warning(
                            f"GT frame index {frame_idx} out of range for {video_metadata.filename}"
                        )
            except Exception as e:
                logger.error(f"Error reading GT file {gt_file}: {e}")
                continue

        if not snapshots:
            logger.warning(f"No valid GT snapshots found, falling back to uniform sampling")
            return self._select_uniform(video_metadata)

        # Sort by timestamp
        snapshots.sort(key=lambda s: s.timestamp_ms)

        logger.info(f"Selected {len(snapshots)} GT snapshots for {video_metadata.filename}")
        return snapshots

    def _find_gt_files(self, video_name: str) -> List[str]:
        """Find GT JSON files for a video.

        Args:
            video_name: Video name without extension (e.g., 'lytx_1')

        Returns:
            List of GT JSON file paths
        """
        gt_files = []

        # Look for pattern: video_name-{1,2,3}.json
        for i in range(1, 10):  # Check up to 9 frames
            gt_path = os.path.join(TRAIN_DIR, f"{video_name}-{i}.json")
            if os.path.exists(gt_path):
                gt_files.append(gt_path)

        return gt_files

    def _select_uniform(self, video_metadata: VideoMetadata) -> List[SnapshotInfo]:
        """Select snapshots using uniform sampling.

        Args:
            video_metadata: Video metadata

        Returns:
            List of SnapshotInfo objects uniformly distributed
        """
        total_frames = video_metadata.total_frames
        fps = video_metadata.fps

        # Calculate frame indices for uniform distribution
        # E.g., for 3 snapshots: 25%, 50%, 75% of video
        snapshots = []
        for i in range(1, self.max_snapshots + 1):
            fraction = i / (self.max_snapshots + 1)
            frame_idx = int(total_frames * fraction)
            timestamp_ms = (frame_idx / fps) * 1000

            snapshot = SnapshotInfo(
                frame_idx=frame_idx,
                timestamp_ms=timestamp_ms,
                reason=f"Uniform sampling ({fraction*100:.0f}%)"
            )
            snapshots.append(snapshot)
            logger.info(
                f"Selected uniform snapshot: frame {frame_idx} at {timestamp_ms:.2f}ms"
            )

        logger.info(
            f"Selected {len(snapshots)} uniform snapshots for {video_metadata.filename}"
        )
        return snapshots

    def _histogram_scene_changes(
        self,
        video_metadata: VideoMetadata,
        threshold: float,
    ) -> List[tuple]:
        """Return list of (frame_idx, timestamp_ms, diff) scene-change candidates."""
        cap = cv2.VideoCapture(video_metadata.filepath)
        if not cap.isOpened():
            return []
        scene_changes: List[tuple] = []
        try:
            prev_hist = None
            frame_idx = 0
            last_change_time = -float("inf")
            sample_rate = max(1, int(video_metadata.fps / 2))

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % sample_rate == 0:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
                    hist = cv2.normalize(hist, hist).flatten()

                    if prev_hist is not None:
                        diff = 1 - cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
                        timestamp_ms = (frame_idx / video_metadata.fps) * 1000
                        if (
                            diff > threshold
                            and timestamp_ms - last_change_time > SCENE_CHANGE_MIN_INTERVAL_MS
                        ):
                            scene_changes.append((frame_idx, timestamp_ms, diff))
                            last_change_time = timestamp_ms
                    prev_hist = hist
                frame_idx += 1
        finally:
            cap.release()
        return scene_changes

    def _pixeldiff_scene_changes(self, video_metadata: VideoMetadata) -> List[tuple]:
        """Fallback: mean absolute difference on downscaled grayscale pairs."""
        cap = cv2.VideoCapture(video_metadata.filepath)
        if not cap.isOpened():
            return []
        out: List[tuple] = []
        try:
            prev_small = None
            frame_idx = 0
            last_change_time = -float("inf")
            sample_rate = max(1, int(video_metadata.fps))

            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                if frame_idx % sample_rate == 0:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    small = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)
                    if prev_small is not None:
                        mad = float(np.mean(cv2.absdiff(prev_small, small))) / 255.0
                        timestamp_ms = (frame_idx / video_metadata.fps) * 1000
                        if (
                            mad > 0.06
                            and timestamp_ms - last_change_time > SCENE_CHANGE_MIN_INTERVAL_MS
                        ):
                            out.append((frame_idx, timestamp_ms, mad))
                            last_change_time = timestamp_ms
                    prev_small = small
                frame_idx += 1
        finally:
            cap.release()
        return out

    def _merge_scene_candidates(self, *buckets: List[tuple]) -> List[tuple]:
        """Merge (frame_idx, ts, score) tuples, keeping temporal spacing."""
        merged: List[tuple] = []
        last_ts = -float("inf")
        flat = sorted(
            (t for b in buckets for t in b),
            key=lambda x: x[2],
            reverse=True,
        )
        for frame_idx, ts, diff in flat:
            if ts - last_ts > SCENE_CHANGE_MIN_INTERVAL_MS * 0.9:
                merged.append((frame_idx, ts, diff))
                last_ts = ts
        merged.sort(key=lambda x: x[1])
        return merged

    def _select_scene_change(self, video_metadata: VideoMetadata) -> List[SnapshotInfo]:
        """Select snapshots using scene change detection.

        Uses histogram difference to detect significant scene transitions.
        On long clips where the default threshold yields very few events,
        automatically retries with a looser threshold and a pixel-diff fallback.

        Args:
            video_metadata: Video metadata

        Returns:
            List of SnapshotInfo objects at scene changes
        """
        logger.info(f"Detecting scene changes for {video_metadata.filename}")

        primary = self._histogram_scene_changes(video_metadata, SCENE_CHANGE_THRESHOLD)
        buckets = [primary]

        long_clip = video_metadata.duration_ms >= 30_000
        if long_clip and len(primary) < 3:
            relaxed = self._histogram_scene_changes(video_metadata, 0.18)
            logger.info(
                "Scene-change retry: threshold 0.18 (primary had %d peaks on %.1fs video)",
                len(primary),
                video_metadata.duration_ms / 1000.0,
            )
            buckets.append(relaxed)
        if long_clip and len(self._merge_scene_candidates(*buckets)) < 3:
            pix = self._pixeldiff_scene_changes(video_metadata)
            logger.info("Scene-change pixel-diff fallback added %d candidates", len(pix))
            buckets.append(pix)

        scene_changes = list(buckets[0])
        if len(buckets) > 1:
            scene_changes = self._merge_scene_candidates(*buckets)

        scene_changes.sort(key=lambda x: x[2], reverse=True)
        selected_changes = scene_changes[: self.max_snapshots]
        selected_changes.sort(key=lambda x: x[1])

        if not selected_changes:
            logger.warning("No scene changes detected, falling back to uniform sampling")
            return self._select_uniform(video_metadata)

        snapshots = [
            SnapshotInfo(
                frame_idx=frame_idx,
                timestamp_ms=timestamp_ms,
                reason=f"Scene change (diff={diff:.3f})",
            )
            for frame_idx, timestamp_ms, diff in selected_changes
        ]
        logger.info(
            f"Selected {len(snapshots)} scene change snapshots for {video_metadata.filename}"
        )
        return snapshots

