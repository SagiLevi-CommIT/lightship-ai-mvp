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

    def _select_scene_change(self, video_metadata: VideoMetadata) -> List[SnapshotInfo]:
        """Select snapshots using scene change detection.

        Uses histogram difference to detect significant scene transitions.

        Args:
            video_metadata: Video metadata

        Returns:
            List of SnapshotInfo objects at scene changes
        """
        logger.info(f"Detecting scene changes for {video_metadata.filename}")

        cap = cv2.VideoCapture(video_metadata.filepath)
        if not cap.isOpened():
            logger.error(f"Cannot open video for scene detection: {video_metadata.filepath}")
            return self._select_uniform(video_metadata)

        try:
            scene_changes = []
            prev_hist = None
            frame_idx = 0
            last_change_time = -float('inf')

            # Sample frames (not every frame for efficiency)
            sample_rate = max(1, int(video_metadata.fps / 2))  # 2 samples per second

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame_idx % sample_rate == 0:
                    # Compute histogram
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
                    hist = cv2.normalize(hist, hist).flatten()

                    # Compare with previous frame
                    if prev_hist is not None:
                        # Calculate histogram difference (correlation distance)
                        diff = 1 - cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)

                        timestamp_ms = (frame_idx / video_metadata.fps) * 1000

                        # Check if this is a significant scene change
                        if (diff > SCENE_CHANGE_THRESHOLD and
                            timestamp_ms - last_change_time > SCENE_CHANGE_MIN_INTERVAL_MS):

                            scene_changes.append((frame_idx, timestamp_ms, diff))
                            last_change_time = timestamp_ms
                            logger.debug(
                                f"Scene change detected at frame {frame_idx} "
                                f"({timestamp_ms:.2f}ms, diff={diff:.3f})"
                            )

                    prev_hist = hist

                frame_idx += 1

            cap.release()

            # Sort by difference score (most significant changes first)
            scene_changes.sort(key=lambda x: x[2], reverse=True)

            # Take top max_snapshots changes
            selected_changes = scene_changes[:self.max_snapshots]

            # Sort by timestamp
            selected_changes.sort(key=lambda x: x[1])

            if not selected_changes:
                logger.warning("No scene changes detected, falling back to uniform sampling")
                return self._select_uniform(video_metadata)

            snapshots = [
                SnapshotInfo(
                    frame_idx=frame_idx,
                    timestamp_ms=timestamp_ms,
                    reason=f"Scene change (diff={diff:.3f})"
                )
                for frame_idx, timestamp_ms, diff in selected_changes
            ]

            logger.info(
                f"Selected {len(snapshots)} scene change snapshots for {video_metadata.filename}"
            )
            return snapshots

        except Exception as e:
            logger.error(f"Error during scene change detection: {e}")
            cap.release()
            return self._select_uniform(video_metadata)

