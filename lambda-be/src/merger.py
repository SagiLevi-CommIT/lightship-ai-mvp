"""Merger module — saves pipeline + client config + detection_summary.

Outputs to temp_dir:
  {stem}_pipeline.json   — full internal pipeline output
  config.json            — client-format config (primary deliverable)
  detection_summary.json — summary statistics
  output.json            — combined backward-compat output
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from pydantic import ValidationError

from src.schemas import ObjectLabel, VideoOutput, VideoMetadata, HazardEvent
from src.config import OUTPUT_DIR

logger = logging.getLogger(__name__)


class Merger:

    def __init__(self, output_dir: str = OUTPUT_DIR):
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def merge_and_save(
        self,
        video_metadata: VideoMetadata,
        all_objects: List[ObjectLabel],
        hazard_events: Optional[List[HazardEvent]] = None,
        inferred_metadata: Optional[Dict] = None,
        client_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        hazard_events = hazard_events or []
        inferred_metadata = inferred_metadata or {}

        sorted_objects = sorted(all_objects, key=lambda o: o.start_time_ms)
        sorted_hazards = sorted(hazard_events, key=lambda h: h.start_time_ms)

        try:
            video_output = VideoOutput(
                filename=video_metadata.filename,
                camera=video_metadata.camera,
                fps=video_metadata.fps,
                description=inferred_metadata.get("description", ""),
                traffic=inferred_metadata.get("traffic", "unknown"),
                lighting=inferred_metadata.get("lighting", "unknown"),
                weather=inferred_metadata.get("weather", "unknown"),
                collision=inferred_metadata.get("collision", "none"),
                speed=inferred_metadata.get("speed", "unknown"),
                road_type=inferred_metadata.get("road_type", "unknown"),
                video_class=inferred_metadata.get("video_class", "unknown"),
                video_duration_ms=video_metadata.duration_ms,
                objects=sorted_objects,
                hazard_events=sorted_hazards,
            )
        except ValidationError as e:
            logger.error("Schema validation failed: %s", e)
            raise

        stem = os.path.splitext(video_metadata.filename)[0]

        # 1. Internal pipeline output
        internal_path = os.path.join(self.output_dir, f"{stem}_pipeline.json")
        self._save(video_output.model_dump(exclude_none=False), internal_path)

        # 2. Client config.json (the primary deliverable)
        if client_config:
            config_path = os.path.join(self.output_dir, "config.json")
            self._save(client_config, config_path)

        # 3. detection_summary.json
        summary = self.get_summary_stats(video_output)
        summary_path = os.path.join(self.output_dir, "detection_summary.json")
        self._save(summary, summary_path)

        # 4. Combined output.json (backward compat)
        combined = video_output.model_dump(exclude_none=False)
        combined["client_config"] = client_config
        combined["detection_summary"] = summary
        combined_path = os.path.join(self.output_dir, f"{stem}.json")
        self._save(combined, combined_path)

        # Also save as output.json for api_server
        output_path = os.path.join(self.output_dir, "output.json")
        self._save(combined, output_path)

        return output_path

    @staticmethod
    def _save(data: dict, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def validate_output(self, json_path: str) -> bool:
        try:
            with open(json_path, "r") as f:
                data = json.load(f)
            VideoOutput(**data)
            return True
        except Exception as e:
            logger.error("Validation failed for %s: %s", json_path, e)
            return False

    @staticmethod
    def get_summary_stats(video_output: VideoOutput) -> dict:
        priority_counts: Dict[str, int] = {}
        distance_counts: Dict[str, int] = {}
        class_counts: Dict[str, int] = {}
        for obj in video_output.objects:
            priority_counts[obj.priority] = priority_counts.get(obj.priority, 0) + 1
            distance_counts[obj.distance] = distance_counts.get(obj.distance, 0) + 1
            class_counts[obj.description] = class_counts.get(obj.description, 0) + 1

        hazard_severity_counts: Dict[str, int] = {}
        for h in video_output.hazard_events:
            hazard_severity_counts[h.hazard_severity] = (
                hazard_severity_counts.get(h.hazard_severity, 0) + 1
            )

        return {
            "filename": video_output.filename,
            "video_class": video_output.video_class,
            "road_type": video_output.road_type,
            "total_objects": len(video_output.objects),
            "num_snapshots": len(set(obj.start_time_ms for obj in video_output.objects)),
            "num_hazards": len(video_output.hazard_events),
            "class_counts": class_counts,
            "priority_distribution": priority_counts,
            "distance_distribution": distance_counts,
            "hazard_severity_distribution": hazard_severity_counts,
        }
