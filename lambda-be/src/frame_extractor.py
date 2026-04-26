"""Frame extractor — deterministic frame extraction with validation.

Responsibilities:

* Open the video once and iterate snapshots in ascending order (``cv2``
  seeks are extremely slow backwards).
* Validate every decoded frame (non-empty, correct dimensions, not a
  fully-uniform colour) before committing it to disk — this prevents
  silent "gray placeholder" frames from entering the annotation stage.
* On read failure: attempt a short seek-back retry, then a linear walk
  forward up to ``FRAME_READ_FALLBACK_STEPS`` frames to find a real
  neighbouring frame. This is the difference between "the pipeline
  silently drops a frame" and "the pipeline returns one close to where
  the user asked for".
* Return a rich ``extraction_manifest`` alongside the usual frame
  dictionary so callers can record, per frame:
  - ``frame_idx`` / ``timestamp_ms`` / ``source`` / ``dimensions``
  - ``status`` (``ok`` / ``substituted`` / ``failed``)
  - ``error`` text when a frame is skipped
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.schemas import SnapshotInfo, VideoMetadata
from src.config import TEMP_FRAMES_DIR, FRAME_FORMAT, FRAME_QUALITY

logger = logging.getLogger(__name__)

# Linear-walk budget when the requested frame index is unreadable.
FRAME_READ_FALLBACK_STEPS = 6
# Minimum per-frame pixel standard deviation we accept. Pure-grey or
# fully-black frames have ``std < 1.0`` — they are almost certainly
# codec artefacts at seek points and must be rejected before annotation.
MIN_FRAME_STD = 1.0


@dataclass
class FrameEntry:
    """Rich manifest metadata for a single extracted frame."""

    frame_idx: int
    timestamp_ms: float
    source: str  # "requested" or "substituted"
    status: str  # "ok" / "substituted" / "failed"
    width: int = 0
    height: int = 0
    path: Optional[str] = None
    error: Optional[str] = None
    # Index actually decoded when fallback kicks in (may differ from
    # ``frame_idx`` on ``source == "substituted"``).
    decoded_idx: Optional[int] = None


@dataclass
class ExtractionResult:
    """Structured result: frames keyed by requested index + manifest."""

    frames: Dict[int, str] = field(default_factory=dict)
    manifest: List[FrameEntry] = field(default_factory=list)


def _is_real_frame(frame: Optional[np.ndarray]) -> bool:
    """Reject ``None`` / empty / uniform-colour decoded frames.

    OpenCV sometimes returns "success" with a frame that is fully
    grey/black when the demuxer hits a missing keyframe. Those frames
    must never reach the annotator: they render as grey placeholders
    and silently corrupt downstream analytics.
    """
    if frame is None or not hasattr(frame, "size") or frame.size == 0:
        return False
    if frame.ndim < 2:
        return False
    h, w = frame.shape[:2]
    if h == 0 or w == 0:
        return False
    try:
        std = float(frame.std())
    except Exception:
        return False
    return std >= MIN_FRAME_STD


class FrameExtractor:
    """Extracts frames from videos at specified timestamps."""

    def __init__(self, output_dir: str = TEMP_FRAMES_DIR):
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"FrameExtractor initialized. Output dir: {output_dir}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def extract_frames(
        self,
        video_metadata: VideoMetadata,
        snapshots: List[SnapshotInfo],
    ) -> Dict[int, str]:
        """Backward-compatible wrapper: returns the ``frames`` dict.

        The richer manifest is always available via
        :meth:`extract_frames_with_manifest`, and the pipeline code
        consumes both.
        """
        result = self.extract_frames_with_manifest(video_metadata, snapshots)
        return result.frames

    def extract_frames_with_manifest(
        self,
        video_metadata: VideoMetadata,
        snapshots: List[SnapshotInfo],
    ) -> ExtractionResult:
        cap = cv2.VideoCapture(video_metadata.filepath)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_metadata.filepath}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or video_metadata.total_frames
        video_name = os.path.splitext(video_metadata.filename)[0]

        # Always iterate in ascending order — backwards seeks are an
        # order of magnitude slower and sometimes return placeholder
        # frames when the demuxer hits a missing keyframe.
        ordered = sorted(snapshots, key=lambda s: s.frame_idx)

        result = ExtractionResult()
        try:
            for snapshot in ordered:
                entry, output_path = self._extract_one(
                    cap=cap,
                    snapshot=snapshot,
                    video_name=video_name,
                    total_frames=total_frames,
                )
                result.manifest.append(entry)
                if output_path is not None:
                    result.frames[snapshot.frame_idx] = output_path
        finally:
            cap.release()

        ok = sum(1 for e in result.manifest if e.status == "ok")
        subs = sum(1 for e in result.manifest if e.status == "substituted")
        failed = sum(1 for e in result.manifest if e.status == "failed")
        logger.info(
            "FrameExtractor: requested=%d ok=%d substituted=%d failed=%d",
            len(snapshots), ok, subs, failed,
        )
        return result

    def cleanup_frames(self, frame_paths: List[str]) -> None:
        deleted = 0
        for path in frame_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete frame {path}: {e}")
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} temporary frame files")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _extract_one(
        self,
        cap: cv2.VideoCapture,
        snapshot: SnapshotInfo,
        video_name: str,
        total_frames: int,
    ) -> Tuple[FrameEntry, Optional[str]]:
        target = max(0, min(snapshot.frame_idx, max(0, total_frames - 1)))
        entry = FrameEntry(
            frame_idx=snapshot.frame_idx,
            timestamp_ms=float(snapshot.timestamp_ms),
            source="requested",
            status="failed",
        )

        frame, decoded_idx = self._read_validated_frame(cap, target, total_frames)

        if frame is None or decoded_idx is None:
            entry.error = "decode_failed_after_fallback"
            logger.error(
                "FrameExtractor: unable to decode a real frame near idx=%d "
                "(%dms). Skipping.",
                snapshot.frame_idx, int(snapshot.timestamp_ms),
            )
            return entry, None

        if decoded_idx != target:
            entry.source = "substituted"
        entry.decoded_idx = decoded_idx
        entry.height, entry.width = frame.shape[:2]

        output_filename = (
            f"{video_name}_frame_{snapshot.frame_idx}_"
            f"{snapshot.timestamp_ms:.0f}ms.{FRAME_FORMAT}"
        )
        output_path = os.path.join(self.output_dir, output_filename)
        if not self._write_frame(output_path, frame):
            entry.error = "imwrite_failed"
            return entry, None

        entry.path = output_path
        entry.status = "substituted" if entry.source == "substituted" else "ok"
        logger.info(
            "Extracted frame %d (%.2fms) -> %s %s",
            snapshot.frame_idx, snapshot.timestamp_ms, output_filename,
            f"(substituted from {decoded_idx})" if entry.source == "substituted" else "",
        )
        return entry, output_path

    @staticmethod
    def _read_validated_frame(
        cap: cv2.VideoCapture,
        target_idx: int,
        total_frames: int,
    ) -> Tuple[Optional[np.ndarray], Optional[int]]:
        """Seek to ``target_idx`` and return ``(frame, decoded_idx)``.

        On failure: (a) try a 1-frame seek-back and re-read, (b) walk
        forward up to ``FRAME_READ_FALLBACK_STEPS`` frames, (c) give up.
        ``decoded_idx`` is always the index of the frame we actually
        returned to the caller, so manifest entries can reflect when a
        substitution happened.
        """
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
        ret, frame = cap.read()
        if ret and _is_real_frame(frame):
            return frame, target_idx

        # 1) Seek-back-and-retry. Useful when the demuxer delivers an
        #    empty slot on an initial keyframe-less seek.
        if target_idx > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, target_idx - 1))
            cap.read()  # discard
            ret, frame = cap.read()
            if ret and _is_real_frame(frame):
                return frame, target_idx

        # 2) Linear forward walk. We re-seek to target and then step
        #    forward up to FRAME_READ_FALLBACK_STEPS frames, returning
        #    the first readable one.
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
        for offset in range(1, FRAME_READ_FALLBACK_STEPS + 1):
            if target_idx + offset >= total_frames:
                break
            ret, frame = cap.read()
            if ret and _is_real_frame(frame):
                return frame, target_idx + offset

        return None, None

    @staticmethod
    def _write_frame(output_path: str, frame: np.ndarray) -> bool:
        try:
            if FRAME_FORMAT.lower() in ("jpg", "jpeg"):
                ok = cv2.imwrite(
                    output_path, frame,
                    [cv2.IMWRITE_JPEG_QUALITY, FRAME_QUALITY],
                )
            elif FRAME_FORMAT.lower() == "png":
                compression = 9 - int(FRAME_QUALITY / 10)
                ok = cv2.imwrite(
                    output_path, frame,
                    [cv2.IMWRITE_PNG_COMPRESSION, max(0, min(9, compression))],
                )
            else:
                ok = cv2.imwrite(output_path, frame)
            return bool(ok)
        except Exception as e:
            logger.error("imwrite failed for %s: %s", output_path, e)
            return False
