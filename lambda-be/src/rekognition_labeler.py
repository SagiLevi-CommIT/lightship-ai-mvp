"""Rekognition-based object detection labeler.

Replaces the YOLO-based CVLabeler with Amazon Rekognition DetectLabels API.
No AGPL licensing risk.  Managed service, no model weights to deploy.
"""
import logging
from typing import List, Dict, Optional, Tuple
import boto3
from botocore.config import Config

from src.schemas import ObjectLabel, Center
from src.config import (
    AWS_REGION,
    REKOGNITION_MIN_CONFIDENCE,
    REKOGNITION_MAX_LABELS,
    REKOGNITION_LABEL_MAP,
    DISTANCE_ENUM,
)

logger = logging.getLogger(__name__)


class RekognitionLabeler:
    """Detects objects in frames using Amazon Rekognition."""

    def __init__(self):
        config = Config(
            read_timeout=30,
            connect_timeout=10,
            retries={"max_attempts": 3},
        )
        self.client = boto3.client(
            "rekognition",
            region_name=AWS_REGION,
            config=config,
        )
        logger.info("RekognitionLabeler initialised (region=%s)", AWS_REGION)

    def label_frame(
        self,
        frame_bytes: bytes,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
    ) -> List[ObjectLabel]:
        """Detect objects in a single frame image.

        Args:
            frame_bytes: PNG/JPEG image bytes
            timestamp_ms: Frame timestamp in milliseconds
            video_width: Frame width in pixels
            video_height: Frame height in pixels

        Returns:
            List of ObjectLabel instances
        """
        try:
            response = self.client.detect_labels(
                Image={"Bytes": frame_bytes},
                MinConfidence=REKOGNITION_MIN_CONFIDENCE,
                MaxLabels=REKOGNITION_MAX_LABELS,
                Features=["GENERAL_LABELS"],
                Settings={
                    "GeneralLabels": {
                        "LabelInclusionFilters": [],
                    },
                },
            )
        except Exception as e:
            logger.error("Rekognition DetectLabels failed: %s", e)
            return []

        objects: List[ObjectLabel] = []

        for label in response.get("Labels", []):
            label_name = label["Name"]
            mapped_class = REKOGNITION_LABEL_MAP.get(label_name)
            if mapped_class is None:
                continue

            # Skip non-physical classes that don't get bounding boxes
            if mapped_class in ("road_surface", "intersection"):
                continue

            for instance in label.get("Instances", []):
                bbox = instance.get("BoundingBox", {})
                confidence = instance.get("Confidence", label.get("Confidence", 0)) / 100.0

                if not bbox:
                    continue

                left = bbox.get("Left", 0) * video_width
                top = bbox.get("Top", 0) * video_height
                w = bbox.get("Width", 0) * video_width
                h = bbox.get("Height", 0) * video_height

                x_min = left
                y_min = top
                x_max = left + w
                y_max = top + h
                center_x = int(left + w / 2)
                center_y = int(top + h / 2)

                distance = self._estimate_distance(h, video_height)

                obj = ObjectLabel(
                    description=mapped_class,
                    start_time_ms=timestamp_ms,
                    distance=distance,
                    priority="none",
                    center=Center(x=center_x, y=center_y),
                    x_min=x_min,
                    y_min=y_min,
                    x_max=x_max,
                    y_max=y_max,
                    width=w,
                    height=h,
                    confidence=confidence,
                    rekognition_label=label_name,
                )
                objects.append(obj)

            # Labels without Instances but with Parents/categories (scene-level)
            if not label.get("Instances") and mapped_class == "lane_marking":
                objects.append(
                    ObjectLabel(
                        description="lane_marking",
                        start_time_ms=timestamp_ms,
                        distance="n/a",
                        priority="none",
                        center=Center(x=video_width // 2, y=video_height * 3 // 4),
                        x_min=0.0,
                        y_min=float(video_height // 2),
                        x_max=float(video_width),
                        y_max=float(video_height),
                        width=float(video_width),
                        height=float(video_height // 2),
                        confidence=label.get("Confidence", 0) / 100.0,
                        rekognition_label=label_name,
                    )
                )

        logger.info(
            "Rekognition detected %d objects at %.0fms",
            len(objects),
            timestamp_ms,
        )
        return objects

    def label_frame_from_path(
        self,
        frame_path: str,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
    ) -> List[ObjectLabel]:
        """Read an image file and run detection."""
        with open(frame_path, "rb") as f:
            image_bytes = f.read()
        return self.label_frame(image_bytes, timestamp_ms, video_width, video_height)

    @staticmethod
    def _estimate_distance(bbox_height_px: float, frame_height: int) -> str:
        """Estimate distance from bbox size as proportion of frame height.

        Uses the 5-category distance taxonomy agreed in client emails.
        """
        if frame_height <= 0:
            return "mid"

        ratio = bbox_height_px / frame_height

        if ratio > 0.50:
            return "danger_close"
        elif ratio > 0.25:
            return "near"
        elif ratio > 0.12:
            return "mid"
        elif ratio > 0.05:
            return "far"
        else:
            return "very_far"
