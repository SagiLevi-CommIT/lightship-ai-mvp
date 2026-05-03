"""Customer object taxonomy normalisation tests."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))

from src.object_taxonomy import normalize_object_description, sanitize_object_labels  # noqa: E402
from src.schemas import Center, ObjectLabel  # noqa: E402


def _obj(description: str) -> ObjectLabel:
    return ObjectLabel(
        description=description,
        start_time_ms=0.0,
        distance="moderate",
        priority="low",
        center=Center(x=10, y=10),
        x_min=0.0,
        y_min=0.0,
        x_max=20.0,
        y_max=20.0,
        width=20.0,
        height=20.0,
    )


def test_wheel_labels_are_not_customer_objects():
    assert normalize_object_description("wheel") is None
    assert normalize_object_description("WHELL") is None


def test_detector_aliases_normalize_to_customer_classes():
    assert normalize_object_description("bicyclist") == "bicycle"
    assert normalize_object_description("traffic cone") == "cone"
    assert normalize_object_description("random thing", unknown_to_other=True) == "other"


def test_sanitize_object_labels_drops_parts_and_keeps_customer_objects():
    objects = sanitize_object_labels([_obj("WHELL"), _obj("bicyclist"), _obj("car")])

    assert [obj.description for obj in objects] == ["bicycle", "car"]
