"""Config JSON generator producing client-format output.

Generates type-specific config JSONs matching Lightship's application format
(detection, decisions, reactions configs).  Jobsite is an architectural
placeholder.
"""
import logging
from typing import Dict, List, Any, Optional

from src.schemas import (
    ObjectLabel,
    HazardEvent,
    VideoMetadata,
    DetectionConfigOutput,
    DecisionsConfigOutput,
    ReactionsConfigOutput,
    JobsiteConfigOutput,
)

logger = logging.getLogger(__name__)


class ConfigGenerator:
    """Generates client-ready config JSONs per video type."""

    def generate(
        self,
        video_class: str,
        video_metadata: VideoMetadata,
        objects: List[ObjectLabel],
        hazard_events: List[HazardEvent],
        classification_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate the appropriate config for the classified video type.

        Returns:
            Config dict ready for JSON serialisation.
        """
        common = self._build_common_fields(video_metadata, classification_result)
        detection_summary = self._build_detection_summary(objects, hazard_events)

        if video_class == "hazard_detection":
            return self._generate_detection_config(
                common, objects, hazard_events, detection_summary, classification_result
            )
        elif video_class == "qa_educational":
            return self._generate_decisions_config(
                common, detection_summary, classification_result
            )
        elif video_class == "reactivity_braking":
            return self._generate_reactions_config(
                common, objects, hazard_events, detection_summary, classification_result
            )
        elif video_class == "job_site_detection":
            return self._generate_jobsite_config(
                common, objects, hazard_events, detection_summary, classification_result
            )
        else:
            logger.warning("Unknown video_class '%s', defaulting to detection", video_class)
            return self._generate_detection_config(
                common, objects, hazard_events, detection_summary, classification_result
            )

    def _build_common_fields(
        self,
        video_metadata: VideoMetadata,
        cr: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "filename": video_metadata.filename,
            "road": cr.get("road_type", "unknown"),
            "speed": cr.get("speed", "unknown"),
            "traffic": cr.get("traffic", "unknown"),
            "weather": cr.get("weather", "unknown"),
            "collision": cr.get("collision", "none"),
            "space": "open",
            "trial_start_prompt": cr.get("trial_start_prompt", ""),
            "video_end_time": video_metadata.duration_ms / 1000.0,
        }

    def _build_detection_summary(
        self,
        objects: List[ObjectLabel],
        hazard_events: List[HazardEvent],
    ) -> Dict[str, Any]:
        class_counts: Dict[str, int] = {}
        for obj in objects:
            class_counts[obj.description] = class_counts.get(obj.description, 0) + 1

        return {
            "total_objects": len(objects),
            "total_hazards": len(hazard_events),
            "class_counts": class_counts,
        }

    def _generate_detection_config(
        self,
        common: Dict[str, Any],
        objects: List[ObjectLabel],
        hazard_events: List[HazardEvent],
        detection_summary: Dict[str, Any],
        cr: Dict[str, Any],
    ) -> Dict[str, Any]:
        hazard_x: List[float] = []
        hazard_y: List[float] = []
        hazard_size: List[float] = []
        hazard_desc: List[str] = []

        for obj in objects:
            if obj.priority in ("high", "critical") and obj.center:
                hazard_x.append(float(obj.center.x))
                hazard_y.append(float(obj.center.y))
                hazard_size.append(float(obj.width or 0) * float(obj.height or 0))
                hazard_desc.append(
                    f"{obj.description} at {obj.distance} ({obj.priority})"
                )

        for event in hazard_events:
            hazard_desc.append(f"[event] {event.hazard_description}")

        cfg = DetectionConfigOutput(
            **common,
            video_class="hazard_detection",
            hazard_x=hazard_x,
            hazard_y=hazard_y,
            hazard_size=hazard_size,
            hazard_desc=hazard_desc,
            hazard_view_duration=min(5.0, (common.get("video_end_time") or 5.0) / 2),
            detection_summary=detection_summary,
            objects=[
                {
                    "class": o.description,
                    "distance": o.distance,
                    "priority": o.priority,
                    "center": {"x": o.center.x, "y": o.center.y} if o.center else None,
                    "bbox": {
                        "x_min": o.x_min, "y_min": o.y_min,
                        "x_max": o.x_max, "y_max": o.y_max,
                    } if o.x_min is not None else None,
                    "timestamp_ms": o.start_time_ms,
                    "confidence": o.confidence,
                }
                for o in objects
            ],
        )
        return cfg.model_dump(exclude_none=True)

    def _generate_decisions_config(
        self,
        common: Dict[str, Any],
        detection_summary: Dict[str, Any],
        cr: Dict[str, Any],
    ) -> Dict[str, Any]:
        questions = cr.get("questions", [])
        if not questions:
            questions = [
                {
                    "question": "What should the driver do in this scenario?",
                    "options": [
                        "Speed up to pass",
                        "Maintain current speed",
                        "Slow down and observe",
                        "Stop immediately",
                    ],
                    "correct_answer": "C",
                    "explanation": "The safest action is to slow down and observe the surroundings.",
                }
            ]

        cfg = DecisionsConfigOutput(
            **common,
            video_class="qa_educational",
            questions=questions,
            detection_summary=detection_summary,
        )
        return cfg.model_dump(exclude_none=True)

    def _generate_reactions_config(
        self,
        common: Dict[str, Any],
        objects: List[ObjectLabel],
        hazard_events: List[HazardEvent],
        detection_summary: Dict[str, Any],
        cr: Dict[str, Any],
    ) -> Dict[str, Any]:
        hazard_x: List[float] = []
        hazard_y: List[float] = []
        hazard_size: List[float] = []
        hazard_desc: List[str] = []

        for obj in objects:
            if obj.priority in ("high", "critical") and obj.center:
                hazard_x.append(float(obj.center.x))
                hazard_y.append(float(obj.center.y))
                hazard_size.append(float(obj.width or 0) * float(obj.height or 0))
                hazard_desc.append(
                    f"{obj.description} at {obj.distance} ({obj.priority})"
                )

        cfg = ReactionsConfigOutput(
            **common,
            video_class="reactivity_braking",
            reaction_time_window=2.0,
            hazard_x=hazard_x,
            hazard_y=hazard_y,
            hazard_size=hazard_size,
            hazard_desc=hazard_desc,
            hazard_view_duration=min(3.0, (common.get("video_end_time") or 3.0) / 3),
            detection_summary=detection_summary,
        )
        return cfg.model_dump(exclude_none=True)

    def _generate_jobsite_config(
        self,
        common: Dict[str, Any],
        objects: List[ObjectLabel],
        hazard_events: List[HazardEvent],
        detection_summary: Dict[str, Any],
        cr: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Architectural placeholder - jobsite config template not yet provided by client."""
        objects_list = [
            {
                "class": o.description,
                "distance": o.distance,
                "priority": o.priority,
                "center": {"x": o.center.x, "y": o.center.y} if o.center else None,
                "timestamp_ms": o.start_time_ms,
                "confidence": o.confidence,
            }
            for o in objects
        ]
        hazards_list = [
            {
                "type": h.hazard_type,
                "description": h.hazard_description,
                "severity": h.hazard_severity,
                "timestamp_ms": h.start_time_ms,
            }
            for h in hazard_events
        ]

        cfg = JobsiteConfigOutput(
            filename=common["filename"],
            video_class="job_site_detection",
            weather=common.get("weather", "unknown"),
            site_type="construction",
            objects_detected=objects_list,
            hazards=hazards_list,
            detection_summary=detection_summary,
        )
        return cfg.model_dump(exclude_none=True)
