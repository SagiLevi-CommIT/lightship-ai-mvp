"""Client config JSON generator.

Converts Lightship pipeline `VideoOutput` (schemas.py) into the four client
config families requested in the project brief:

    - reactivity         (braking / reaction / collision-avoidance)
    - educational        (Q&A / training clips)
    - hazard             (hazard-labelled clips for model training / review)
    - jobsite            (construction / job-site detections)

The aim of this module is schema-ready mapping: each family returns a
structured dict that the customer pipeline can consume, without requiring
further LLM work.  If additional LLM enrichment is desired (e.g. Q&A
generation), that is layered on top of the base mappings via optional
Bedrock calls.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Any, Iterable

from src.schemas import VideoOutput, ObjectLabel, HazardEvent

logger = logging.getLogger(__name__)


# Coarse category → client family mapping.  Using substring matches against
# the object.description field so we work with both Rekognition labels and
# LLM-refined descriptions.
_JOBSITE_KEYWORDS = (
    "cone", "barrier", "barricade", "worker", "hi-vis", "high visibility",
    "excavator", "crane", "scaffold", "sign", "fence", "construction",
    "cement", "mixer",
)
_HAZARD_DEFAULT_SEVERITIES = {"Critical", "High", "Medium"}


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keywords)


def _classify_video(out: VideoOutput) -> str:
    """Return one of the four families based on detection content.

    The rule set is intentionally simple so it can be unit-tested and
    overridden by an LLM call when more context is available.
    """
    if _contains_any(out.description, ("question", "educational", "training", "q&a")):
        return "educational"
    jobsite_hits = sum(
        1 for o in out.objects
        if _contains_any(o.description, _JOBSITE_KEYWORDS)
    )
    if jobsite_hits >= max(2, len(out.objects) // 6):
        return "jobsite"
    if out.hazard_events:
        return "hazard"
    return "reactivity"


def _hazard_window(objects: List[ObjectLabel], hazard: HazardEvent,
                   pre_ms: float = 1500.0, post_ms: float = 1500.0) -> Dict[str, Any]:
    """Build a windowed clip spec around a hazard event."""
    start = max(0.0, hazard.start_time_ms - pre_ms)
    end = hazard.start_time_ms + (hazard.duration_ms or 0.0) + post_ms
    related = [o for o in objects if start <= o.start_time_ms <= end]
    return {
        "start_time_ms": start,
        "end_time_ms": end,
        "hazard_type": hazard.hazard_type,
        "severity": hazard.hazard_severity,
        "road_conditions": hazard.road_conditions,
        "description": hazard.hazard_description,
        "involved_objects": [
            {
                "description": o.description,
                "distance": o.distance,
                "priority": o.priority,
                "timestamp_ms": o.start_time_ms,
            }
            for o in related
        ],
    }


def _reactivity_config(out: VideoOutput) -> Dict[str, Any]:
    critical = [h for h in out.hazard_events
                if h.hazard_severity in _HAZARD_DEFAULT_SEVERITIES]
    return {
        "filename": out.filename,
        "video_class": "reactivity",
        "scene": {
            "weather": out.weather,
            "lighting": out.lighting,
            "traffic": out.traffic,
            "speed": out.speed,
        },
        "events": [_hazard_window(out.objects, h) for h in critical],
        "summary": {
            "total_events": len(critical),
            "collision": out.collision,
        },
    }


def _educational_config(out: VideoOutput) -> Dict[str, Any]:
    # Rough Q&A scaffold.  Real deployments should replace this with an LLM
    # call to generate distractors and a canonical answer.
    qa: List[Dict[str, Any]] = []
    for h in out.hazard_events:
        qa.append({
            "timestamp_ms": h.start_time_ms,
            "question": (
                f"At this moment the driver observes a {h.hazard_type.lower()} "
                f"on a road that is {h.road_conditions.lower()}. What is the safest action?"
            ),
            "hint": h.hazard_description,
            "severity": h.hazard_severity,
        })
    return {
        "filename": out.filename,
        "video_class": "educational",
        "scene": {
            "weather": out.weather,
            "lighting": out.lighting,
            "traffic": out.traffic,
        },
        "qa_items": qa,
        "summary": {"num_qa": len(qa)},
    }


def _hazard_config(out: VideoOutput) -> Dict[str, Any]:
    return {
        "filename": out.filename,
        "video_class": "hazard",
        "scene": {
            "weather": out.weather,
            "lighting": out.lighting,
            "traffic": out.traffic,
            "speed": out.speed,
        },
        "hazards": [
            {
                "start_time_ms": h.start_time_ms,
                "duration_ms": h.duration_ms,
                "hazard_type": h.hazard_type,
                "severity": h.hazard_severity,
                "description": h.hazard_description,
            }
            for h in out.hazard_events
        ],
        "annotations": [
            {
                "timestamp_ms": o.start_time_ms,
                "description": o.description,
                "distance": o.distance,
                "priority": o.priority,
                "bbox": None if o.x_min is None else [o.x_min, o.y_min, o.x_max, o.y_max],
            }
            for o in out.objects
        ],
    }


def _jobsite_config(out: VideoOutput) -> Dict[str, Any]:
    jobsite_objs = [
        o for o in out.objects
        if _contains_any(o.description, _JOBSITE_KEYWORDS)
    ]
    # Per-timestamp grouping
    buckets: Dict[float, List[ObjectLabel]] = {}
    for o in jobsite_objs:
        buckets.setdefault(o.start_time_ms, []).append(o)
    return {
        "filename": out.filename,
        "video_class": "jobsite",
        "scene": {
            "weather": out.weather,
            "lighting": out.lighting,
        },
        "frames": [
            {
                "timestamp_ms": ts,
                "detections": [
                    {
                        "description": o.description,
                        "distance": o.distance,
                        "priority": o.priority,
                        "bbox": None if o.x_min is None else [
                            o.x_min, o.y_min, o.x_max, o.y_max,
                        ],
                    }
                    for o in objs
                ],
            }
            for ts, objs in sorted(buckets.items())
        ],
        "summary": {
            "total_jobsite_objects": len(jobsite_objs),
            "unique_frames": len(buckets),
        },
    }


_FAMILY_BUILDERS = {
    "reactivity": _reactivity_config,
    "educational": _educational_config,
    "hazard": _hazard_config,
    "jobsite": _jobsite_config,
}


def generate_client_configs(output: VideoOutput) -> Dict[str, Any]:
    """Generate all four client config families for a given VideoOutput.

    Returns a dict of the form::

        {
          "video_class": "hazard",
          "configs": {
              "reactivity":  {...},
              "educational": {...},
              "hazard":      {...},
              "jobsite":     {...},
          }
        }
    """
    video_class = _classify_video(output)
    configs = {name: fn(output) for name, fn in _FAMILY_BUILDERS.items()}
    logger.info(
        "Generated %d client configs for %s (primary_class=%s)",
        len(configs), output.filename, video_class,
    )
    return {"video_class": video_class, "configs": configs}


def write_client_configs(output: VideoOutput, out_dir: str | Path) -> Dict[str, str]:
    """Write generated configs to `out_dir/<filename>.<family>.json` files.

    Returns a mapping from family name → file path.
    """
    import json

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = generate_client_configs(output)
    stem = Path(output.filename).stem
    paths: Dict[str, str] = {}
    for family, cfg in generated["configs"].items():
        p = out_dir / f"{stem}.{family}.json"
        with p.open("w", encoding="utf-8") as fp:
            json.dump(cfg, fp, indent=2, ensure_ascii=False)
        paths[family] = str(p)
    # Also persist the summary
    summary_path = out_dir / f"{stem}.summary.json"
    with summary_path.open("w", encoding="utf-8") as fp:
        json.dump(
            {"video_class": generated["video_class"], "families": list(paths)},
            fp, indent=2,
        )
    paths["summary"] = str(summary_path)
    return paths
