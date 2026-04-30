"""Florence-2 open-vocabulary detection backend.

Uses ``microsoft/Florence-2-base`` (MIT licence, 232M params) for:
  - ``<OD>`` — COCO-class object detection
  - ``<OPEN_VOCABULARY_DETECTION>`` — zero-shot prompt-based detection

Returns detections in the same ``ObjectLabel`` schema as the rest of the
pipeline, so the caller (VisionLabeler) can merge them uniformly.

Model is loaded lazily on first call and cached on the instance to keep
Lambda cold-start costs low.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.schemas import ObjectLabel, Center
from src.config import (
    FLORENCE2_MODEL_ID,
    FLORENCE2_OD_PROMPTS,
    VISION_CRITICAL_CLASSES,
    VISION_FALLBACK_CONFIDENCE_THRESHOLD,
)

logger = logging.getLogger(__name__)

# Lightship canonical label aliases for Florence-2 COCO predictions
_FLORENCE_TO_LIGHTSHIP: Dict[str, str] = {
    "car": "car",
    "truck": "truck",
    "bus": "bus",
    "motorcycle": "motorcycle",
    "bicycle": "bicyclist",
    "person": "pedestrian",
    "traffic light": "traffic_signal",
    "stop sign": "stop_sign",
    "traffic cone": "cone",
    "cone": "cone",
    "barrier": "barrier",
    "pedestrian": "pedestrian",
    "construction worker": "construction_worker",
    "heavy equipment": "heavy_equipment",
    "debris": "debris",
}

_PRIORITY_MAP: Dict[str, str] = {
    "pedestrian": "high",
    "motorcycle": "high",
    "bicyclist": "high",
    "traffic_signal": "high",
    "stop_sign": "high",
    "construction_worker": "high",
    "debris": "high",
    "cone": "medium",
    "barrier": "medium",
    "car": "medium",
    "truck": "medium",
    "bus": "medium",
    "heavy_equipment": "medium",
}


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


class Florence2Backend:
    """Zero-shot / open-vocabulary detection via Florence-2."""

    def __init__(self, model_id: str = FLORENCE2_MODEL_ID) -> None:
        self.model_id = model_id
        self._processor: Any = None
        self._model: Any = None
        self._device: str = "cpu"
        self._load_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Lazy model loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        """Load model and processor on first call.  Returns True on success."""
        if self._model is not None:
            return True
        if self._load_error is not None:
            return False
        try:
            import torch
            from transformers import AutoProcessor, AutoModelForCausalLM

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("Loading Florence-2 from %s on %s …", self.model_id, self._device)
            self._processor = AutoProcessor.from_pretrained(
                self.model_id, trust_remote_code=True
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_id, trust_remote_code=True
            ).to(self._device)
            self._model.eval()
            logger.info("Florence-2 loaded successfully")
            return True
        except Exception as exc:
            self._load_error = str(exc)
            logger.warning("Florence-2 unavailable (%s) — backend disabled", exc)
            return False

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _run_task(self, image: Any, task: str, text: str = "") -> Dict[str, Any]:
        """Run a single Florence-2 task and return the decoded result dict."""
        import torch

        inputs = self._processor(text=task + text, images=image, return_tensors="pt")
        inputs = {k: v.to(self._device) for k, v in inputs.items()}
        with torch.no_grad():
            generated_ids = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
                do_sample=False,
            )
        generated_text = self._processor.batch_decode(
            generated_ids, skip_special_tokens=False
        )[0]
        return self._processor.post_process_generation(
            generated_text, task=task + text, image_size=(image.width, image.height)
        )

    # ------------------------------------------------------------------
    # Object conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _bbox_to_label(
        raw_label: str,
        bbox: List[float],
        confidence: float,
        timestamp_ms: float,
        img_w: int,
        img_h: int,
    ) -> Optional[ObjectLabel]:
        canonical = _FLORENCE_TO_LIGHTSHIP.get(raw_label.lower().strip(), raw_label.lower().strip())
        if not canonical:
            return None
        try:
            x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        except (IndexError, TypeError, ValueError):
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        frame_path: str,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
    ) -> Tuple[List[ObjectLabel], List[Dict[str, Any]]]:
        """Run Florence-2 on one frame.

        Returns:
            (object_labels, raw_label_summary) — the summary is used by
            VisionLabeler to build the audit trail and decide whether the
            Detectron2 fallback should be triggered.
        """
        if not self._ensure_loaded():
            return [], []

        try:
            from PIL import Image as PILImage
            image = PILImage.open(frame_path).convert("RGB")
        except Exception as exc:
            logger.warning("Cannot open frame %s: %s", frame_path, exc)
            return [], []

        img_w, img_h = image.size
        results: List[ObjectLabel] = []
        raw_summary: List[Dict[str, Any]] = []

        t0 = time.monotonic()

        # 1) Standard OD (COCO vocabulary) — fast, catches common classes
        try:
            od_result = self._run_task(image, "<OD>")
            od_data = od_result.get("<OD>", {})
            bboxes = od_data.get("bboxes", [])
            labels = od_data.get("labels", [])
            for label, bbox in zip(labels, bboxes):
                # Florence-2 OD does not return per-instance confidence;
                # treat all standard OD hits as high-confidence (0.80).
                raw_summary.append({"name": label, "confidence": 0.80, "source": "od"})
                obj = self._bbox_to_label(label, bbox, 0.80, timestamp_ms, img_w, img_h)
                if obj is not None:
                    results.append(obj)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Florence-2 <OD> failed on %s: %s", Path(frame_path).name, exc)

        # 2) Open-vocabulary detection for domain-specific prompts
        open_vocab_prompts = ", ".join(FLORENCE2_OD_PROMPTS)
        try:
            ov_result = self._run_task(image, "<OPEN_VOCABULARY_DETECTION>", open_vocab_prompts)
            ov_data = ov_result.get("<OPEN_VOCABULARY_DETECTION>", {})
            bboxes = ov_data.get("bboxes", [])
            labels = ov_data.get("labels", [])
            for label, bbox in zip(labels, bboxes):
                raw_summary.append({"name": label, "confidence": 0.70, "source": "ovd"})
                obj = self._bbox_to_label(label, bbox, 0.70, timestamp_ms, img_w, img_h)
                if obj is not None:
                    # De-duplicate against standard OD results (centre distance > 20px)
                    cx, cy = int(bbox[0] + (bbox[2] - bbox[0]) / 2), int(bbox[1] + (bbox[3] - bbox[1]) / 2)
                    is_dup = any(
                        abs(r.center.x - cx) < 20 and abs(r.center.y - cy) < 20
                        and r.description == obj.description
                        for r in results
                    )
                    if not is_dup:
                        results.append(obj)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Florence-2 <OPEN_VOCABULARY_DETECTION> failed on %s: %s",
                Path(frame_path).name, exc,
            )

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "Florence-2 on %s → %d detections (%.0f ms)",
            Path(frame_path).name, len(results), elapsed_ms,
        )
        return results, raw_summary

    def needs_fallback(
        self,
        raw_summary: List[Dict[str, Any]],
        results: List[ObjectLabel],
    ) -> bool:
        """Return True if Detectron2 fallback should be triggered.

        Conditions:
          - Any critical class is completely absent from results, OR
          - The highest-confidence detection of a critical class is below the
            configured threshold.
        """
        found_classes = {r.description for r in results}
        # Check for completely missing critical classes that SHOULD be present
        # (we don't know without context which are "expected", so we use a
        # simpler heuristic: if ALL detected confidences of critical classes
        # are below the threshold, trigger fallback).
        critical_confs = [
            entry["confidence"]
            for entry in raw_summary
            if entry.get("name", "").lower() in VISION_CRITICAL_CLASSES
            or _FLORENCE_TO_LIGHTSHIP.get(entry.get("name", "").lower()) in VISION_CRITICAL_CLASSES
        ]
        if not results:
            # Zero detections — always fall back
            return True
        if critical_confs and max(critical_confs) < VISION_FALLBACK_CONFIDENCE_THRESHOLD:
            return True
        return False
