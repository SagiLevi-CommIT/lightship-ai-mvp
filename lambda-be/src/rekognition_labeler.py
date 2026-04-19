"""AWS Rekognition integration for per-frame object detection.

Complements the YOLO-based CVLabeler with managed Rekognition labels so the
pipeline covers a wider object vocabulary (construction cones, workers,
protective equipment, vehicles, pedestrians, etc.) without training custom
models. Rekognition detections are converted into the same ObjectLabel
schema as CV detections and merged downstream.

Budget-wise this adds one Rekognition DetectLabels call per selected frame
and is therefore only invoked on the final selected set, never on the full
dense pre-selection sweep.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError

from src.schemas import ObjectLabel, Center

logger = logging.getLogger(__name__)

# Description normalisation: Rekognition returns things like "Person" which
# we want to map onto the project's canonical vocabulary.
_DESCRIPTION_ALIASES = {
    "person": "pedestrian",
    "human": "pedestrian",
    "people": "pedestrian(group)",
    "pedestrian": "pedestrian",
    "bicyclist": "bicyclist",
    "biker": "bicyclist",
    "motorcyclist": "motorcycle",
    "motorbike": "motorcycle",
    "car": "car",
    "automobile": "car",
    "suv": "car",
    "pickup truck": "truck",
    "truck": "truck",
    "bus": "bus",
    "traffic light": "traffic_signal",
    "traffic signal": "traffic_signal",
    "stop sign": "sign(stop)",
    "yield sign": "sign(yield)",
    "road sign": "sign",
    "sign": "sign",
    "cone": "cone",
    "traffic cone": "cone",
    "barricade": "barrier",
    "barrier": "barrier",
    "worker": "construction_worker",
    "construction": "construction",
    "excavator": "excavator",
    "crane": "crane",
    "scaffold": "scaffold",
    "hard hat": "hard_hat",
    "helmet": "hard_hat",
    "vest": "safety_vest",
    "safety vest": "safety_vest",
}

_PRIORITY_FOR_LABEL = {
    "pedestrian": "high",
    "pedestrian(group)": "high",
    "bicyclist": "high",
    "motorcycle": "high",
    "traffic_signal": "medium",
    "sign(stop)": "high",
    "sign(yield)": "medium",
    "sign": "low",
    "cone": "medium",
    "barrier": "medium",
    "construction_worker": "high",
    "construction": "medium",
    "hard_hat": "low",
    "safety_vest": "low",
    "car": "medium",
    "truck": "medium",
    "bus": "medium",
    "excavator": "medium",
    "crane": "low",
    "scaffold": "low",
}


def _estimate_distance(bbox_rel_area: float) -> str:
    """Categorise object distance based on relative bbox area (0-1)."""
    if bbox_rel_area > 0.25:
        return "dangerously_close"
    if bbox_rel_area > 0.12:
        return "very_close"
    if bbox_rel_area > 0.05:
        return "close"
    if bbox_rel_area > 0.015:
        return "moderate"
    if bbox_rel_area > 0.003:
        return "far"
    return "very_far"


class RekognitionLabeler:
    """Calls AWS Rekognition DetectLabels on a frame image and returns
    normalised :class:`ObjectLabel` instances.
    """

    def __init__(
        self,
        region_name: Optional[str] = None,
        min_confidence: int = 60,
        max_labels: int = 50,
    ):
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self.min_confidence = min_confidence
        self.max_labels = max_labels
        try:
            self.client = boto3.client("rekognition", region_name=self.region_name)
            logger.info(
                "RekognitionLabeler initialised (region=%s, min_confidence=%d, max_labels=%d)",
                self.region_name, self.min_confidence, self.max_labels,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Rekognition client init failed: %s", e)
            self.client = None

    def detect(
        self,
        frame_path: str,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
    ) -> List[ObjectLabel]:
        """Run Rekognition on one frame and return ObjectLabel list."""
        if self.client is None:
            return []

        try:
            with open(frame_path, "rb") as fp:
                image_bytes = fp.read()
        except OSError as e:
            logger.warning("Cannot read frame %s: %s", frame_path, e)
            return []

        try:
            resp = self.client.detect_labels(
                Image={"Bytes": image_bytes},
                MaxLabels=self.max_labels,
                MinConfidence=self.min_confidence,
            )
        except ClientError as e:
            logger.warning("Rekognition DetectLabels failed on %s: %s", frame_path, e)
            return []

        results: List[ObjectLabel] = []
        for label in resp.get("Labels", []):
            canonical = self._canonicalise(label.get("Name", ""))
            if not canonical:
                continue
            for inst in label.get("Instances", []) or []:
                bbox = inst.get("BoundingBox") or {}
                if not bbox:
                    continue
                obj = self._bbox_to_object(
                    canonical=canonical,
                    bbox=bbox,
                    confidence=inst.get("Confidence", 0.0),
                    timestamp_ms=timestamp_ms,
                    video_width=video_width,
                    video_height=video_height,
                )
                if obj is not None:
                    results.append(obj)

        logger.info(
            "Rekognition on %s -> %d labelled instances (timestamp=%dms)",
            Path(frame_path).name, len(results), int(timestamp_ms),
        )
        return results

    @staticmethod
    def _canonicalise(name: str) -> Optional[str]:
        if not name:
            return None
        key = name.strip().lower()
        if key in _DESCRIPTION_ALIASES:
            return _DESCRIPTION_ALIASES[key]
        # Direct fallback for common plain labels the pipeline cares about.
        if any(token in key for token in ("person", "pedestrian")):
            return "pedestrian"
        if "vehicle" in key or "sedan" in key or "car" in key:
            return "car"
        if "truck" in key:
            return "truck"
        if "sign" in key:
            return "sign"
        if "cone" in key:
            return "cone"
        return None

    @staticmethod
    def _bbox_to_object(
        canonical: str,
        bbox: dict,
        confidence: float,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
    ) -> Optional[ObjectLabel]:
        try:
            left = max(0.0, float(bbox["Left"]))
            top = max(0.0, float(bbox["Top"]))
            w_rel = max(0.0, float(bbox["Width"]))
            h_rel = max(0.0, float(bbox["Height"]))
        except (KeyError, TypeError, ValueError):
            return None

        x_min = round(left * video_width, 1)
        y_min = round(top * video_height, 1)
        x_max = round((left + w_rel) * video_width, 1)
        y_max = round((top + h_rel) * video_height, 1)
        width = round(max(0.0, x_max - x_min), 1)
        height = round(max(0.0, y_max - y_min), 1)
        if width <= 0 or height <= 0:
            return None

        distance = _estimate_distance(w_rel * h_rel)
        priority = _PRIORITY_FOR_LABEL.get(canonical, "low")
        try:
            return ObjectLabel(
                description=canonical,
                start_time_ms=float(timestamp_ms),
                distance=distance,
                priority=priority,
                location_description="",
                center=Center(
                    x=int((x_min + x_max) / 2),
                    y=int((y_min + y_max) / 2),
                ),
                x_min=x_min,
                y_min=y_min,
                x_max=x_max,
                y_max=y_max,
                width=width,
                height=height,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Rekognition bbox rejected (conf=%.1f): %s", confidence, e)
            return None
