"""Detectron2 Mask R-CNN R50-FPN (COCO) — real Facebook Detectron2 backend.

Intended for the **ECS inference worker** image where Detectron2 is installed
from the community CPU wheel matching torch 2.6.

Public API matches other vision backends:
``detect(frame_path, timestamp_ms, video_width, video_height) ->
(List[ObjectLabel], raw_summary)``.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from src.schemas import ObjectLabel, PolygonPoint

logger = logging.getLogger(__name__)

# Reuse COCO→Lightship mapping + box helper from YOLO backend (same COCO IDs).
from src.backends.yolo_backend import (  # noqa: WPS347
    _COCO_TO_LIGHTSHIP,
    _KEPT_COCO_IDS,
    _box_to_label,
)


class Detectron2Backend:
    """COCO Mask R-CNN R50-FPN via Detectron2 ``DefaultPredictor``."""

    def __init__(self) -> None:
        self._predictor: Any = None
        self._load_error: Optional[str] = None

    @property
    def backend_name(self) -> str:
        return "detectron2_mask_rcnn_r50_fpn"

    def _ensure_loaded(self) -> bool:
        if self._predictor is not None:
            return True
        if self._load_error is not None:
            return False
        try:
            import cv2
            from detectron2 import model_zoo
            from detectron2.config import get_cfg
            from detectron2.engine import DefaultPredictor

            cfg = get_cfg()
            cfg.merge_from_file(
                model_zoo.get_config_file(
                    "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml",
                )
            )
            cfg.MODEL.WEIGHTS = model_zoo.get_checkpoint_url(
                "COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml",
            )
            cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.5
            cfg.MODEL.DEVICE = "cpu"
            # Deterministic CPU inference
            cfg.INPUT.FORMAT = "BGR"
            self._predictor = DefaultPredictor(cfg)
            # Warm-up import for OpenCV imread path used by predictor
            _ = cv2.__version__  # noqa: WPS437
            logger.info("Detectron2Backend loaded Mask R-CNN R50-FPN (CPU)")
            return True
        except Exception as exc:
            self._load_error = str(exc)
            logger.error("Detectron2Backend failed to load: %s", exc)
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
            import cv2

            img = cv2.imread(frame_path)
            if img is None:
                logger.warning("Detectron2: could not read %s", frame_path)
                return [], []
            outputs = self._predictor(img)
        except Exception as exc:
            logger.warning("Detectron2 inference failed on %s: %s", Path(frame_path).name, exc)
            return [], []

        instances = outputs["instances"].to("cpu")
        if len(instances) == 0:
            return [], []

        boxes = instances.pred_boxes.tensor.numpy()
        classes = instances.pred_classes.numpy()
        scores = instances.scores.numpy()

        results: List[ObjectLabel] = []
        raw: List[Dict[str, Any]] = []
        h, w = img.shape[:2]

        for i in range(len(classes)):
            cls_id = int(classes[i])
            if cls_id not in _KEPT_COCO_IDS:
                continue
            canonical = _COCO_TO_LIGHTSHIP[cls_id]
            conf = float(scores[i])
            x1, y1, x2, y2 = [float(v) for v in boxes[i]]
            raw.append(
                {
                    "name": canonical,
                    "confidence": round(conf, 3),
                    "source": "detectron2_mask_rcnn",
                    "coco_class_id": cls_id,
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
                video_width or w,
                video_height or h,
            )
            if obj is not None:
                # Optional instance mask -> polygon (simplified) for UI richness
                try:
                    if instances.has("pred_masks"):
                        mask = instances.pred_masks[i].numpy().astype(np.uint8)
                        ys, xs = np.where(mask > 0)
                        if len(xs) > 8:
                            step = max(1, len(xs) // 32)
                            poly_pts = [
                                PolygonPoint(x=float(xs[j]), y=float(ys[j]))
                                for j in range(0, len(xs), step)
                            ][:64]
                            obj = obj.model_copy(update={"polygon": poly_pts})
                except Exception:  # noqa: BLE001
                    pass
                results.append(obj)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Detectron2Backend on %s -> %d detections (%.0f ms)",
            Path(frame_path).name,
            len(results),
            elapsed_ms,
        )
        return results, raw
