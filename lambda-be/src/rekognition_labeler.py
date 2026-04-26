"""AWS Rekognition integration for per-frame object detection.

Complements the YOLO-based CVLabeler with managed Rekognition labels so the
pipeline covers a wider object vocabulary (construction cones, workers,
protective equipment, vehicles, pedestrians, etc.) without training custom
models. Rekognition detections are converted into the same ObjectLabel
schema as CV detections and merged downstream.

Budget-wise this adds one Rekognition DetectLabels call per selected frame
and is therefore only invoked on the final selected set, never on the full
dense pre-selection sweep.

Also produces a per-frame **audit record** (``last_audit`` /
``build_audit``) that the pipeline persists into ``output.json`` so every
successful run carries proof that Rekognition actually ran — closing the
"Rekognition is in code but not proven in outputs" gap from Phase 2.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from src.schemas import ObjectLabel, Center
from src.utils import metrics

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
    # Custom Labels — Lightship driving model labels (kept verbatim where
    # they already are canonical so the rest of the pipeline uses them as-is).
    "crosswalk": "crosswalk",
    "vehicle(parked)": "vehicle(parked)",
    "vehicle": "vehicle",
    "lane": "lane",
    "lane(current)": "lane(current)",
    "intersection_boundary": "intersection_boundary",
    "visual_obstruction": "visual_obstruction",
    "debris": "debris",
    "unknown_sign": "sign",
    "traffic_signal(red)": "traffic_signal",
    "traffic_signal(green)": "traffic_signal",
    "traffic_signal(yellow)": "traffic_signal",
    "double_yellow": "double_yellow",
}

_PRIORITY_FOR_LABEL = {
    "pedestrian": "high",
    "pedestrian(group)": "high",
    "bicyclist": "high",
    "motorcycle": "high",
    "traffic_signal": "high",
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
    "crosswalk": "high",
    "vehicle(parked)": "low",
    "vehicle": "medium",
    "lane": "low",
    "lane(current)": "medium",
    "intersection_boundary": "medium",
    "visual_obstruction": "medium",
    "debris": "high",
    "double_yellow": "medium",
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
        custom_model_arn: Optional[str] = None,
    ):
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self.min_confidence = min_confidence
        self.max_labels = max_labels
        self.custom_model_arn = (
            custom_model_arn
            if custom_model_arn is not None
            else os.environ.get("REKOGNITION_CUSTOM_MODEL_ARN", "").strip() or None
        )
        self._audit_records: List[Dict[str, Any]] = []
        try:
            self.client = boto3.client("rekognition", region_name=self.region_name)
            logger.info(
                "RekognitionLabeler initialised (region=%s, min_confidence=%d, max_labels=%d, custom_model=%s)",
                self.region_name, self.min_confidence, self.max_labels,
                "yes" if self.custom_model_arn else "no",
            )
        except Exception as e:
            logger.warning("Rekognition client init failed: %s", e)
            self.client = None

    def build_audit(self) -> List[Dict[str, Any]]:
        """Return the per-frame audit trail accumulated during this run.

        Pipeline callers write this into ``output.json`` so downstream
        analysis and the ``test_06_e2e_pipeline`` assertion can prove that
        Rekognition was actually invoked during the run.
        """
        return list(self._audit_records)

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

        audit_entry: Dict[str, Any] = {
            "frame_path": Path(frame_path).name,
            "timestamp_ms": float(timestamp_ms),
            "min_confidence": self.min_confidence,
            "raw_labels": [],
            "kept_instances": 0,
            "error": None,
            "custom_labels_invoked": False,
            "custom_model_arn": self.custom_model_arn,
            "custom_raw_labels": [],
            "custom_kept_instances": 0,
            "custom_error": None,
        }

        start = time.monotonic()
        try:
            resp = self.client.detect_labels(
                Image={"Bytes": image_bytes},
                MaxLabels=self.max_labels,
                MinConfidence=self.min_confidence,
            )
        except ClientError as e:
            logger.warning(
                "Rekognition DetectLabels failed on %s: %s",
                frame_path, e,
                extra={"frame": Path(frame_path).name, "timestamp_ms": float(timestamp_ms)},
            )
            audit_entry["error"] = str(e)
            self._audit_records.append(audit_entry)
            metrics.count("RekognitionFailures")
            return []

        elapsed_ms = (time.monotonic() - start) * 1000.0

        raw_label_summary: List[Dict[str, Any]] = []
        results: List[ObjectLabel] = []
        for label in resp.get("Labels", []):
            name = label.get("Name", "")
            confidence = float(label.get("Confidence", 0.0))
            instance_count = len(label.get("Instances", []) or [])
            raw_label_summary.append(
                {"name": name, "confidence": round(confidence, 2), "instances": instance_count}
            )

            canonical = self._canonicalise(name)
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

        audit_entry["raw_labels"] = raw_label_summary
        audit_entry["kept_instances"] = len(results)
        audit_entry["elapsed_ms"] = round(elapsed_ms, 1)

        if self.custom_model_arn:
            results.extend(
                self._run_custom_labels(
                    image_bytes=image_bytes,
                    timestamp_ms=timestamp_ms,
                    video_width=video_width,
                    video_height=video_height,
                    audit_entry=audit_entry,
                )
            )

        self._audit_records.append(audit_entry)

        metrics.put_metrics(
            {
                "RekognitionCalls": 1.0,
                "RekognitionLabelsReturned": float(len(raw_label_summary)),
                "RekognitionInstancesKept": float(len(results)),
            }
        )
        metrics.duration_ms("RekognitionCallMs", elapsed_ms)

        logger.info(
            "Rekognition on %s -> %d labelled instances (timestamp=%dms, elapsed=%.0fms)",
            Path(frame_path).name, len(results), int(timestamp_ms), elapsed_ms,
            extra={
                "frame": Path(frame_path).name,
                "timestamp_ms": float(timestamp_ms),
                "raw_labels_returned": len(raw_label_summary),
                "instances_kept": len(results),
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )
        return results

    def _run_custom_labels(
        self,
        image_bytes: bytes,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
        audit_entry: Dict[str, Any],
    ) -> List[ObjectLabel]:
        """Call DetectCustomLabels and convert geometry-bearing detections."""
        audit_entry["custom_labels_invoked"] = True
        start = time.monotonic()
        try:
            resp = self.client.detect_custom_labels(
                ProjectVersionArn=self.custom_model_arn,
                Image={"Bytes": image_bytes},
                MinConfidence=self.min_confidence,
            )
        except ClientError as e:
            audit_entry["custom_error"] = str(e)
            metrics.count("RekognitionCustomFailures")
            logger.warning("DetectCustomLabels failed: %s", e)
            return []

        elapsed_ms = (time.monotonic() - start) * 1000.0
        audit_entry["custom_elapsed_ms"] = round(elapsed_ms, 1)

        custom_summary: List[Dict[str, Any]] = []
        results: List[ObjectLabel] = []
        for label in resp.get("CustomLabels", []) or []:
            name = label.get("Name", "")
            confidence = float(label.get("Confidence", 0.0))
            geometry = label.get("Geometry") or {}
            bbox = geometry.get("BoundingBox") or {}
            custom_summary.append({
                "name": name,
                "confidence": round(confidence, 2),
                "has_bbox": bool(bbox),
            })
            if not bbox:
                continue
            canonical = self._canonicalise(name) or name
            obj = self._bbox_to_object(
                canonical=canonical,
                bbox=bbox,
                confidence=confidence,
                timestamp_ms=timestamp_ms,
                video_width=video_width,
                video_height=video_height,
            )
            if obj is not None:
                results.append(obj)

        audit_entry["custom_raw_labels"] = custom_summary
        audit_entry["custom_kept_instances"] = len(results)
        metrics.put_metrics({
            "RekognitionCustomCalls": 1.0,
            "RekognitionCustomLabelsReturned": float(len(custom_summary)),
            "RekognitionCustomInstancesKept": float(len(results)),
        })
        metrics.duration_ms("RekognitionCustomCallMs", elapsed_ms)
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
