"""Tests for config_generator and evaluation_harness (plan Phase 4/5).

These tests run fully offline — no AWS calls, no Lambda invocation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the lambda-be source importable when running tests from /workspace
_BE_SRC = Path(__file__).resolve().parent.parent / "lambda-be"
if str(_BE_SRC) not in sys.path:
    sys.path.insert(0, str(_BE_SRC))


@pytest.fixture
def video_output():
    from src.schemas import VideoOutput, ObjectLabel, HazardEvent

    return VideoOutput(
        filename="test_clip.mp4",
        fps=30.0,
        camera="omnivision",
        description="Driver approaches a construction zone on a rainy evening",
        traffic="moderate",
        lighting="dusk",
        weather="rain",
        collision="none",
        speed="30-40 km/h",
        video_duration_ms=10_000.0,
        objects=[
            ObjectLabel(
                description="motorcycle",
                start_time_ms=1_000.0,
                distance="close",
                priority="high",
                x_min=100, y_min=100, x_max=200, y_max=200,
            ),
            ObjectLabel(
                description="traffic cone",
                start_time_ms=1_500.0,
                distance="moderate",
                priority="medium",
                x_min=300, y_min=300, x_max=340, y_max=380,
            ),
            ObjectLabel(
                description="construction worker",
                start_time_ms=1_500.0,
                distance="moderate",
                priority="medium",
                x_min=500, y_min=400, x_max=560, y_max=500,
            ),
        ],
        hazard_events=[
            HazardEvent(
                start_time_ms=1_200.0,
                hazard_type="motorcycle lane entry",
                hazard_description="Motorcycle entered ego lane unexpectedly",
                hazard_severity="High",
                road_conditions="Wet, dusk lighting",
                duration_ms=800.0,
            )
        ],
    )


def test_generate_client_configs_returns_four_families(video_output):
    from src.config_generator import generate_client_configs

    result = generate_client_configs(video_output)
    assert set(result["configs"]) == {"reactivity", "educational", "hazard", "jobsite"}
    assert result["video_class"] in {"reactivity", "educational", "hazard", "jobsite"}


def test_write_client_configs_writes_files(tmp_path, video_output):
    from src.config_generator import write_client_configs

    paths = write_client_configs(video_output, tmp_path)
    for family in ("reactivity", "educational", "hazard", "jobsite", "summary"):
        assert family in paths
        assert Path(paths[family]).exists()
        with open(paths[family], encoding="utf-8") as fp:
            json.load(fp)  # must be valid JSON


def test_jobsite_config_detects_jobsite_objects(video_output):
    from src.config_generator import _jobsite_config

    cfg = _jobsite_config(video_output)
    summary = cfg["summary"]
    assert summary["total_jobsite_objects"] >= 2  # cone + worker


def test_evaluation_harness_scores_matching_objects(tmp_path):
    from src.evaluation_harness import build_manifest, score_sample

    gt_dir = tmp_path / "gt"
    pd_dir = tmp_path / "pred"
    gt_dir.mkdir()
    pd_dir.mkdir()

    gt_doc = {
        "filename": "demo.mp4",
        "weather": "clear",
        "lighting": "daylight",
        "traffic": "light",
        "objects": [
            {"description": "motorcycle", "x_min": 0, "y_min": 0, "x_max": 100, "y_max": 100},
            {"description": "stop sign", "x_min": 500, "y_min": 500, "x_max": 560, "y_max": 560},
        ],
    }
    pred_doc = {
        "filename": "demo.mp4",
        "weather": "clear",
        "lighting": "daylight",
        "traffic": "heavy",
        "objects": [
            {"description": "motorcycle", "x_min": 5, "y_min": 5, "x_max": 95, "y_max": 95},
            {"description": "cone", "x_min": 200, "y_min": 200, "x_max": 230, "y_max": 260},
        ],
    }
    (gt_dir / "demo.json").write_text(json.dumps(gt_doc))
    (pd_dir / "demo.json").write_text(json.dumps(pred_doc))

    samples = build_manifest(pd_dir, gt_dir)
    assert samples and samples[0].prediction_path is not None
    out = score_sample(samples[0])
    assert out["status"] == "scored"
    assert out["per_category"]["motorcycle"]["tp"] == 1
    assert out["per_category"]["sign"]["fn"] == 1
    assert out["classification"]["weather_match"] == 1
    assert out["classification"]["traffic_match"] == 0


# ---------------------------------------------------------------------------
# HazardAssessor distance/priority normalizer (protects VideoOutput schema
# from out-of-enum LLM outputs like "mid" / "near" / "medium" seen in prod)
# ---------------------------------------------------------------------------


def test_hazard_assessor_normalizer_maps_known_aliases():
    from src.hazard_assessor import HazardAssessor

    assert HazardAssessor._normalize_distance("mid") == "moderate"
    assert HazardAssessor._normalize_distance("MID") == "moderate"
    assert HazardAssessor._normalize_distance("near") == "close"
    assert HazardAssessor._normalize_distance("na") == "n/a"
    assert HazardAssessor._normalize_distance("moderate") == "moderate"
    assert HazardAssessor._normalize_distance("") == "moderate"

    assert HazardAssessor._normalize_priority("mid") == "medium"
    assert HazardAssessor._normalize_priority("moderate") == "medium"
    assert HazardAssessor._normalize_priority("info") == "none"
    assert HazardAssessor._normalize_priority("") == "none"
    assert HazardAssessor._normalize_priority("high") == "high"
