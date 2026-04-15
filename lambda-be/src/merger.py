"""Merger module for combining frame outputs into per-video JSON.

Saves both:
  - Internal pipeline output (VideoOutput schema)
  - Client-format config JSON (detection/decisions/reactions/jobsite)
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from pydantic import ValidationError

from src.schemas import (
    ObjectLabel,
    VideoOutput,
    VideoMetadata,
    HazardEvent,
)
from src.config import OUTPUT_DIR

logger = logging.getLogger(__name__)


class Merger:
    """Merges frame-level detections into per-video JSON outputs."""

    def __init__(self, output_dir: str = OUTPUT_DIR):
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        logger.info("Merger initialised.  Output dir: %s", output_dir)

    def merge_and_save(
        self,
        video_metadata: VideoMetadata,
        all_objects: List[ObjectLabel],
        hazard_events: Optional[List[HazardEvent]] = None,
        inferred_metadata: Optional[Dict] = None,
        client_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Merge objects + hazards, save internal output and client config.

        Returns:
            Path to saved output JSON (client config if available, else internal).
        """
        if hazard_events is None:
            hazard_events = []
        if inferred_metadata is None:
            inferred_metadata = {}

        logger.info(
            "Merging %d objects, %d hazards for %s",
            len(all_objects),
            len(hazard_events),
            video_metadata.filename,
        )

        sorted_objects = sorted(all_objects, key=lambda o: o.start_time_ms)
        sorted_hazards = sorted(hazard_events, key=lambda h: h.start_time_ms)

        # Build internal VideoOutput
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

        # Save internal pipeline output
        internal_path = os.path.join(self.output_dir, f"{stem}_pipeline.json")
        self._save_json_dict(video_output.model_dump(exclude_none=False), internal_path)
        logger.info("Saved internal output to %s", internal_path)

        # Save client config (the primary deliverable)
        if client_config:
            config_path = os.path.join(self.output_dir, f"{stem}_config.json")
            self._save_json_dict(client_config, config_path)
            logger.info("Saved client config to %s", config_path)
            primary_path = config_path
        else:
            primary_path = internal_path

        # Also save a combined output.json for backward compat
        combined = video_output.model_dump(exclude_none=False)
        combined["client_config"] = client_config
        combined_path = os.path.join(self.output_dir, f"{stem}.json")
        self._save_json_dict(combined, combined_path)

        return combined_path

    @staticmethod
    def _save_json_dict(data: dict, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def validate_output(self, json_path: str) -> bool:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            VideoOutput(**data)
            logger.info("Validation successful: %s", json_path)
            return True
        except (ValidationError, Exception) as e:
            logger.error("Validation failed for %s: %s", json_path, e)
            return False

    @staticmethod
    def get_summary_stats(video_output: VideoOutput) -> dict:
        total_objects = len(video_output.objects)
        priority_counts: Dict[str, int] = {}
        distance_counts: Dict[str, int] = {}
        for obj in video_output.objects:
            priority_counts[obj.priority] = priority_counts.get(obj.priority, 0) + 1
            distance_counts[obj.distance] = distance_counts.get(obj.distance, 0) + 1

        unique_timestamps = set(obj.start_time_ms for obj in video_output.objects)

        hazard_severity_counts: Dict[str, int] = {}
        for h in video_output.hazard_events:
            hazard_severity_counts[h.hazard_severity] = (
                hazard_severity_counts.get(h.hazard_severity, 0) + 1
            )

        return {
            "filename": video_output.filename,
            "video_class": video_output.video_class,
            "road_type": video_output.road_type,
            "total_objects": total_objects,
            "num_snapshots": len(unique_timestamps),
            "num_hazards": len(video_output.hazard_events),
            "priority_distribution": priority_counts,
            "distance_distribution": distance_counts,
            "hazard_severity_distribution": hazard_severity_counts,
        }
