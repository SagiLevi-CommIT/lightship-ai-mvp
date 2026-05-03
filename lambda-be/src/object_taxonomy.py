"""Customer-facing object taxonomy helpers.

The customer requirements define a small object-class set for delivered
outputs. Detector-specific labels can be richer or slightly misspelled, so
normalise them at the backend boundary and before final persistence.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional

from src.schemas import ObjectLabel


CUSTOMER_OBJECT_CLASSES = {
    "car",
    "truck",
    "bus",
    "motorcycle",
    "bicycle",
    "pedestrian",
    "construction_worker",
    "cone",
    "barrier",
    "heavy_equipment",
    "construction_sign",
    "fencing",
    "debris",
    "animal",
    "other",
}

DROP_OBJECT_LABELS = {
    "wheel",
    "whell",
    "wheels",
    "tire",
    "tires",
    "tyre",
    "tyres",
    "rim",
    "rims",
    "hubcap",
    "hubcaps",
    "steering_wheel",
}

OBJECT_LABEL_ALIASES = {
    "person": "pedestrian",
    "people": "pedestrian",
    "human": "pedestrian",
    "pedestrian_group": "pedestrian",
    "bicyclist": "bicycle",
    "cyclist": "bicycle",
    "bike": "bicycle",
    "traffic_cone": "cone",
    "construction_cone": "cone",
    "fence": "fencing",
    "fences": "fencing",
    "road_work_sign": "construction_sign",
    "road_work_ahead_sign": "construction_sign",
    "construction": "construction_sign",
    "excavator": "heavy_equipment",
    "bulldozer": "heavy_equipment",
    "crane": "heavy_equipment",
    "backhoe": "heavy_equipment",
    "vehicle": "car",
    "emergency_vehicle": "car",
}

# Labels that are useful to the existing UI/output schema even though the
# customer object list models them in adjacent fields (lanes/signals/signs).
STRUCTURAL_OUTPUT_LABELS = {
    "lane",
    "lane_current",
    "lane_left_turn",
    "lane_right_turn",
    "double_yellow",
    "crosswalk",
    "intersection_boundary",
    "traffic_signal",
    "traffic_signal_green",
    "traffic_signal_yellow",
    "traffic_signal_red",
    "stop_sign",
    "speed_limit",
    "one_way_sign",
    "do_not_enter_sign",
    "yield_sign",
    "children_at_play_sign",
    "merging_traffic_sign",
    "railroad_crossing_sign",
}


def _label_key(label: object) -> str:
    text = str(label or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def normalize_object_description(
    label: object,
    *,
    unknown_to_other: bool = False,
    preserve_structural: bool = True,
) -> Optional[str]:
    """Return the customer-facing label, or None when it must be dropped."""
    raw = str(label or "").strip()
    key = _label_key(raw)
    if not key:
        return None
    if key in DROP_OBJECT_LABELS:
        return None
    if key in OBJECT_LABEL_ALIASES:
        return OBJECT_LABEL_ALIASES[key]
    if key in CUSTOMER_OBJECT_CLASSES:
        return key
    if preserve_structural and key in STRUCTURAL_OUTPUT_LABELS:
        return raw.lower()
    if unknown_to_other:
        return "other"
    return raw.lower()


def sanitize_object_label(
    obj: ObjectLabel,
    *,
    unknown_to_other: bool = False,
    preserve_structural: bool = True,
) -> Optional[ObjectLabel]:
    """Return an object copy with a normalised description, or None to drop."""
    description = normalize_object_description(
        obj.description,
        unknown_to_other=unknown_to_other,
        preserve_structural=preserve_structural,
    )
    if description is None:
        return None
    if description == obj.description:
        return obj
    return obj.model_copy(update={"description": description})


def sanitize_object_labels(
    objects: Iterable[ObjectLabel],
    *,
    unknown_to_other: bool = False,
    preserve_structural: bool = True,
) -> List[ObjectLabel]:
    """Normalise a sequence of labels and drop banned part-level detections."""
    sanitized: List[ObjectLabel] = []
    for obj in objects:
        clean = sanitize_object_label(
            obj,
            unknown_to_other=unknown_to_other,
            preserve_structural=preserve_structural,
        )
        if clean is not None:
            sanitized.append(clean)
    return sanitized
