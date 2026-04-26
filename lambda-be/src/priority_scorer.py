"""Rule-based priority scoring for detected objects.

Replaces the hardcoded priority="none" from Rekognition with a meaningful
score derived from object class danger, distance proximity, and Y-position
in the frame (lower = closer in dashcam perspective).
"""
import logging
from typing import List

from src.schemas import ObjectLabel
from src.config import (
    OBJECT_DANGER_WEIGHT,
    DISTANCE_PROXIMITY_WEIGHT,
    THREAT_LEVEL_ENUM,
)

logger = logging.getLogger(__name__)

PRIORITY_THRESHOLDS = [
    (0.70, "critical"),
    (0.50, "high"),
    (0.30, "medium"),
    (0.12, "low"),
    (0.00, "none"),
]


def score_priority(obj: ObjectLabel, frame_height: int = 720) -> str:
    """Compute a priority label from object properties.

    Factors:
      1. Class danger weight (pedestrian > car > cone)
      2. Distance proximity weight (danger_close > near > far)
      3. Y-position factor: objects in lower third of frame are closer
    """
    class_w = OBJECT_DANGER_WEIGHT.get(obj.description, 0.3)
    dist_w = DISTANCE_PROXIMITY_WEIGHT.get(obj.distance, 0.3)

    y_factor = 0.5
    if obj.center and frame_height > 0:
        y_ratio = obj.center.y / frame_height
        y_factor = min(1.0, 0.3 + 0.7 * y_ratio)

    raw_score = class_w * 0.45 + dist_w * 0.40 + y_factor * 0.15

    for threshold, label in PRIORITY_THRESHOLDS:
        if raw_score >= threshold:
            return label
    return "none"


def assign_priorities(
    objects: List[ObjectLabel],
    frame_height: int = 720,
) -> List[ObjectLabel]:
    """Return new ObjectLabel list with priorities assigned via scoring."""
    result = []
    for obj in objects:
        priority = score_priority(obj, frame_height)
        updated = obj.model_copy(update={"priority": priority})
        result.append(updated)
    return result
