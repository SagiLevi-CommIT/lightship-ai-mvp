"""VisionLabeler — drop-in replacement for RekognitionLabeler.

Orchestrates (exactly one object detector per job — no silent fallback chain):
  - florence2   : Florence-2 (zero-shot)
  - yolo        : YOLO11n COCO
  - detectron2  : Detectron2 Mask R-CNN R50-FPN COCO

Lane detector: UFLDv2 (default) or OpenCV lanes (opt-in via LANE_BACKEND).

Public API matches the old ``RekognitionLabeler``:
  - ``detect(frame_path, timestamp_ms, video_width, video_height)``
      → ``List[ObjectLabel]``
  - ``build_audit()`` → per-frame records for ``vision_audit`` in output.json.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.schemas import ObjectLabel
from src.config import (
    DETECTOR_BACKEND,
    LANE_BACKEND,
)
from src.utils import metrics

logger = logging.getLogger(__name__)


class VisionLabeler:
    """Open-vocabulary vision labeler (Florence-2 / YOLO / Detectron2 + lanes)."""

    def __init__(
        self,
        detector_backend: str = DETECTOR_BACKEND,
        lane_backend: str = LANE_BACKEND,
        fallback_enabled: bool = True,  # noqa: ARG002 — retained for API compat; unused.
    ) -> None:
        self.detector_backend = detector_backend.lower()
        self.lane_backend = lane_backend.lower()
        self._audit_records: List[Dict[str, Any]] = []

        self._florence2: Any = None
        self._yolo: Any = None
        self._detectron2: Any = None
        self._ufldv2: Any = None

        logger.info(
            "VisionLabeler initialised (detector=%s, lane=%s)",
            self.detector_backend,
            self.lane_backend,
        )

    def _get_florence2(self):
        if self._florence2 is None:
            from src.backends.florence2_backend import Florence2Backend

            self._florence2 = Florence2Backend()
        return self._florence2

    def _get_yolo(self):
        if self._yolo is None:
            from src.backends.yolo_backend import YoloBackend

            self._yolo = YoloBackend()
        return self._yolo

    def _get_detectron2(self):
        if self._detectron2 is None:
            from src.backends.detectron2_real_backend import Detectron2Backend

            self._detectron2 = Detectron2Backend()
        return self._detectron2

    def _get_ufldv2(self):
        if self._ufldv2 is None:
            from src.backends.ufldv2_backend import UFLDv2Backend

            self._ufldv2 = UFLDv2Backend()
        return self._ufldv2

    def build_audit(self) -> List[Dict[str, Any]]:
        """Return per-frame audit trail for injection into output.json."""
        return list(self._audit_records)

    def detect(
        self,
        frame_path: str,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
    ) -> List[ObjectLabel]:
        """Detect objects and lane lines in one frame."""
        audit: Dict[str, Any] = {
            "frame_path": Path(frame_path).name,
            "timestamp_ms": float(timestamp_ms),
            "primary_backend": self.detector_backend,
            "primary_elapsed_ms": 0.0,
            "primary_kept_instances": 0,
            "primary_raw_labels": [],
            "fallback_used": False,
            "fallback_elapsed_ms": None,
            "fallback_kept_instances": None,
            "lane_elapsed_ms": 0.0,
            "lane_kept_instances": 0,
            "lane_backend": "none",
            "error": None,
        }

        results: List[ObjectLabel] = []
        t0 = time.monotonic()

        try:
            if self.detector_backend == "florence2":
                f2 = self._get_florence2()
                f2_results, f2_raw = f2.detect(
                    frame_path, timestamp_ms, video_width, video_height
                )
                audit["primary_backend"] = "florence2"
                audit["primary_raw_labels"] = f2_raw
                audit["primary_kept_instances"] = len(f2_results)
                results.extend(f2_results)

            elif self.detector_backend == "yolo":
                y = self._get_yolo()
                y_results, y_raw = y.detect(
                    frame_path, timestamp_ms, video_width, video_height
                )
                audit["primary_backend"] = "yolo"
                audit["primary_raw_labels"] = y_raw
                audit["primary_kept_instances"] = len(y_results)
                results.extend(y_results)

            elif self.detector_backend == "detectron2":
                d2 = self._get_detectron2()
                d2_results, d2_raw = d2.detect(
                    frame_path, timestamp_ms, video_width, video_height
                )
                audit["primary_backend"] = "detectron2"
                audit["primary_raw_labels"] = d2_raw
                audit["primary_kept_instances"] = len(d2_results)
                results.extend(d2_results)

            else:
                audit["error"] = f"unsupported detector_backend: {self.detector_backend}"

        except Exception as exc:  # noqa: BLE001
            audit["error"] = str(exc)
            logger.warning("VisionLabeler detect error on %s: %s", Path(frame_path).name, exc)

        audit["primary_elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)

        t_lane = time.monotonic()
        lane_objects: List[ObjectLabel] = []

        if self.lane_backend == "ufldv2":
            try:
                ufld = self._get_ufldv2()
                lane_objects = ufld.detect_lanes(
                    frame_path, timestamp_ms, video_width, video_height
                )
                audit["lane_backend"] = "ufldv2"
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "UFLDv2 lane detection failed on %s: %s", Path(frame_path).name, exc
                )
                audit["lane_backend"] = "ufldv2_error"
        elif self.lane_backend == "opencv":
            audit["lane_backend"] = "opencv"

        audit["lane_elapsed_ms"] = round((time.monotonic() - t_lane) * 1000, 1)
        audit["lane_kept_instances"] = len(lane_objects)
        results.extend(lane_objects)

        self._audit_records.append(audit)
        metrics.put_metrics(
            {
                "VisionLabelerCalls": 1.0,
                "VisionLabelerInstancesKept": float(len(results) - len(lane_objects)),
                "VisionLabelerFallbackTriggered": 0.0,
                "LaneBackendLanesKept": float(len(lane_objects)),
            }
        )
        metrics.duration_ms("VisionLabelerCallMs", audit["primary_elapsed_ms"])

        logger.info(
            "VisionLabeler on %s → %d objects + %d lanes (primary=%s, lane=%s)",
            Path(frame_path).name,
            len(results) - len(lane_objects),
            len(lane_objects),
            audit["primary_backend"],
            audit["lane_backend"],
        )
        return results
