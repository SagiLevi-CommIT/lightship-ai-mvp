"""YOLOv11n object detection backend (first-class peer to Florence-2 / Detectron2).

Model
-----
- **YOLOv11n** (Ultralytics 8.x), COCO-trained.
- License: ``ultralytics`` is **AGPL-3.0** (see project docs for redistribution notes).
- Weights: ``yolo11n.pt`` — pre-downloaded in the ECS worker image.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.object_taxonomy import normalize_object_description
from src.schemas import ObjectLabel, Center

logger = logging.getLogger(__name__)

# COCO 80-class ID -> Lightship label (road-relevant subset).
_COCO_TO_LIGHTSHIP: Dict[int, str] = {
    0: "pedestrian",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    9: "traffic_signal",
    11: "stop_sign",
}

_PRIORITY_MAP: Dict[str, str] = {
    "pedestrian": "high",
    "motorcycle": "high",
    "bicycle": "high",
    "traffic_signal": "high",
    "stop_sign": "high",
    "car": "medium",
    "truck": "medium",
    "bus": "medium",
}

_KEPT_COCO_IDS = set(_COCO_TO_LIGHTSHIP.keys())


def _estimate_distance(rel_area: float) -> str:
    if rel_area > 0.25:
        return "dangerously_close"
    if rel_area > 0.12:
        return "very_close"
    if rel_area > 0.05:
        return "close"
    if rel_area > 0.015:
        return "moderate"
    if rel_area > 0.003:
        return "far"
    return "very_far"


def _box_to_label(
    canonical: str,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    confidence: float,
    timestamp_ms: float,
    img_w: int,
    img_h: int,
) -> Optional[ObjectLabel]:
    canonical = normalize_object_description(canonical)
    if not canonical:
        return None
    w = max(0.0, x2 - x1)
    h = max(0.0, y2 - y1)
    if w <= 0 or h <= 0:
        return None
    rel_area = (w / max(img_w, 1)) * (h / max(img_h, 1))
    return ObjectLabel(
        description=canonical,
        start_time_ms=float(timestamp_ms),
        distance=_estimate_distance(rel_area),
        priority=_PRIORITY_MAP.get(canonical, "low"),
        location_description="",
        center=Center(x=int(x1 + w / 2), y=int(y1 + h / 2)),
        x_min=round(x1, 1),
        y_min=round(y1, 1),
        x_max=round(x2, 1),
        y_max=round(y2, 1),
        width=round(w, 1),
        height=round(h, 1),
    )


class YoloBackend:
    """YOLOv11n COCO detector."""

    def __init__(self) -> None:
        self._yolo_model: Any = None
        self._load_error: Optional[str] = None

    @property
    def backend_name(self) -> str:
        return "yolo11n_coco"

    def _ensure_loaded(self) -> bool:
        if self._yolo_model is not None:
            return True
        if self._load_error is not None:
            return False
        try:
            from ultralytics import YOLO

            self._yolo_model = YOLO("yolo11n.pt")
            logger.info("YoloBackend loaded YOLOv11n (ultralytics)")
            return True
        except Exception as exc:
            self._load_error = str(exc)
            logger.error("YoloBackend failed to load: %s", exc)
            return False

    def detect(
        self,
        frame_path: str,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
    ) -> Tuple[List[ObjectLabel], List[Dict[str, Any]]]:
        if not self._ensure_loaded():
            return [], []

        t0 = time.monotonic()
        try:
            results_yolo = self._yolo_model(frame_path, conf=0.35, verbose=False)
        except Exception as exc:
            logger.warning("YOLO inference failed on %s: %s", Path(frame_path).name, exc)
            return [], []

        results: List[ObjectLabel] = []
        raw: List[Dict[str, Any]] = []
        for res in results_yolo:
            for box in res.boxes:
                cls_id = int(box.cls[0])
                if cls_id not in _KEPT_COCO_IDS:
                    continue
                canonical = _COCO_TO_LIGHTSHIP[cls_id]
                conf = float(box.conf[0])
                x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                raw.append(
                    {
                        "name": canonical,
                        "confidence": round(conf, 3),
                        "source": "yolo11n_coco",
                    }
                )
                obj = _box_to_label(
                    canonical,
                    x1,
                    y1,
                    x2,
                    y2,
                    conf,
                    timestamp_ms,
                    video_width,
                    video_height,
                )
                if obj is not None:
                    results.append(obj)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "YoloBackend on %s -> %d detections (%.0f ms)",
            Path(frame_path).name,
            len(results),
            elapsed_ms,
        )
        return results, raw
