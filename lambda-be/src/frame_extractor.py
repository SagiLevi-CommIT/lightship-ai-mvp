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


def _decode_frame_pyav(
    filepath: str,
    target_idx: int,
    fps: float,
) -> Tuple[Optional[np.ndarray], Optional[int]]:
    """Decode a frame near ``target_idx`` using PyAV (more accurate than OpenCV seek).

    Returns ``(frame, decoded_idx)`` where ``decoded_idx`` is the estimated frame
    index for the returned raster. Returns ``(None, None)`` if PyAV is unavailable
    or cannot produce a validated frame close enough to the request.
    """
    try:
        import av  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("PyAV (av) not installed — skipping PyAV decode path")
        return None, None

    try:
        with av.open(filepath, mode="r") as container:
            stream = container.streams.video[0]
            tb = stream.time_base
            if tb is None or float(tb) == 0:
                tb = av.Rational(1, max(1, int(round(max(fps, 1e-6) * 1000))))
            rate = float(stream.average_rate) if stream.average_rate else max(float(fps), 1e-6)
            t_sec = target_idx / rate
            time_base_f = float(tb)
            try:
                seek_ts = max(0, int(t_sec / time_base_f) - int(0.5 / time_base_f))
                container.seek(seek_ts, backward=True, any_frame=False, stream=stream)
            except Exception as exc:
                logger.debug("PyAV seek failed for %s idx=%d: %s", filepath, target_idx, exc)
                try:
                    container.seek(0, backward=True, any_frame=True, stream=stream)
                except Exception:
                    pass

            best_frame: Optional[np.ndarray] = None
            best_idx: Optional[int] = None
            best_dist = 10**9

            for frame in container.decode(stream):
                if frame.pts is None:
                    continue
                try:
                    t = float(frame.pts * tb)
                except Exception:
                    continue
                idx_est = int(round(t * rate))
                dist = abs(idx_est - target_idx)
                try:
                    arr = frame.to_ndarray(format="bgr24")
                except Exception:
                    continue
                if not _is_real_frame(arr):
                    continue
                if dist < best_dist:
                    best_dist = dist
                    best_frame = arr
                    best_idx = idx_est
                if idx_est >= target_idx and dist <= 2:
                    break
                if idx_est > target_idx + 120 and best_frame is not None:
                    break

        if best_frame is None or best_idx is None:
            return None, None
        if best_dist > 30:
            return None, None
        return best_frame, best_idx
    except Exception as exc:
        logger.warning("PyAV decode failed for %s idx=%d: %s", filepath, target_idx, exc)
        return None, None


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
                    video_path=video_metadata.filepath,
                    fps=float(video_metadata.fps or 0.0),
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
        video_path: str,
        fps: float,
    ) -> Tuple[FrameEntry, Optional[str]]:
        target = max(0, min(snapshot.frame_idx, max(0, total_frames - 1)))
        entry = FrameEntry(
            frame_idx=snapshot.frame_idx,
            timestamp_ms=float(snapshot.timestamp_ms),
            source="requested",
            status="failed",
        )

        frame, decoded_idx = self._read_validated_frame(
            cap,
            target,
            total_frames,
            video_path=video_path,
            fps=fps,
        )

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
        *,
        video_path: Optional[str] = None,
        fps: Optional[float] = None,
    ) -> Tuple[Optional[np.ndarray], Optional[int]]:
        """Seek to ``target_idx`` and return ``(frame, decoded_idx)``.

        On failure: (a) try a 1-frame seek-back and re-read, (b) walk
        forward up to ``FRAME_READ_FALLBACK_STEPS`` frames, (c) try PyAV
        when OpenCV fails or only returns a substituted neighbour, (d) give up.
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
        #    forward up to FRAME_READ_FALLBACK_STEPS frames, capturing the
        #    first readable neighbour (may be a substitute for ``target_idx``).
        opencv_frame: Optional[np.ndarray] = None
        opencv_decoded: Optional[int] = None
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_idx)
        for offset in range(1, FRAME_READ_FALLBACK_STEPS + 1):
            if target_idx + offset >= total_frames:
                break
            ret, frame = cap.read()
            if ret and _is_real_frame(frame):
                opencv_frame = frame
                opencv_decoded = target_idx + offset
                break

        # 3) PyAV — refine when OpenCV failed entirely or only returned a neighbour.
        if video_path and fps and fps > 0:
            p_frame, p_idx = _decode_frame_pyav(video_path, target_idx, fps)
            if p_frame is not None and p_idx is not None:
                if opencv_frame is None or opencv_decoded is None:
                    return p_frame, p_idx
                if p_idx == target_idx:
                    return p_frame, p_idx
                if abs(p_idx - target_idx) < abs(opencv_decoded - target_idx):
                    return p_frame, p_idx

        return opencv_frame, opencv_decoded

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
