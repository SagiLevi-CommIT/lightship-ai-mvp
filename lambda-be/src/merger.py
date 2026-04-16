"""Merger module for combining frame outputs into per-video JSON.

Collects all labeled objects from all frames and merges them into a single
JSON file per video with schema validation.
"""
import json
import logging
import os
from pathlib import Path
from typing import List, Dict, Tuple
from pydantic import ValidationError
from src.schemas import ObjectLabel, VideoOutput, VideoMetadata, HazardEvent
from src.config import OUTPUT_DIR

logger = logging.getLogger(__name__)


class Merger:
    """Merges frame-level object detections into per-video JSON outputs."""

    def __init__(self, output_dir: str = OUTPUT_DIR):
        """Initialize Merger.

        Args:
            output_dir: Directory to save output JSON files
        """
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"Merger initialized. Output dir: {output_dir}")

    def merge_and_save(
        self,
        video_metadata: VideoMetadata,
        all_objects: List[ObjectLabel],
        hazard_events: List[HazardEvent] = None,
        inferred_metadata: Dict = None
    ) -> str:
        """Merge objects and hazard events, save to JSON file.

        Args:
            video_metadata: Video metadata
            all_objects: List of all detected objects from all frames
            hazard_events: List of hazard events (V2 only, optional)
            inferred_metadata: LLM-inferred metadata (description, traffic, etc.)

        Returns:
            Path to saved JSON file

        Raises:
            ValidationError: If output fails schema validation
        """
        if hazard_events is None:
            hazard_events = []
        if inferred_metadata is None:
            inferred_metadata = {}

        logger.info(
            f"Merging {len(all_objects)} objects and {len(hazard_events)} hazards "
            f"for {video_metadata.filename}"
        )

        # Sort objects by timestamp
        sorted_objects = sorted(all_objects, key=lambda obj: obj.start_time_ms)

        # Sort hazard events by timestamp
        sorted_hazards = sorted(hazard_events, key=lambda h: h.start_time_ms)

        # Create VideoOutput
        try:
            video_output = VideoOutput(
                filename=video_metadata.filename,
                camera=video_metadata.camera,
                fps=video_metadata.fps,
                description=inferred_metadata.get('description', ''),
                traffic=inferred_metadata.get('traffic', 'unknown'),
                lighting=inferred_metadata.get('lighting', 'unknown'),
                weather=inferred_metadata.get('weather', 'unknown'),
                collision=inferred_metadata.get('collision', 'none'),
                speed=inferred_metadata.get('speed', 'unknown'),
                video_duration_ms=video_metadata.duration_ms,
                objects=sorted_objects,
                hazard_events=sorted_hazards
            )

            logger.info(f"Video output validated successfully")

        except ValidationError as e:
            logger.error(f"Schema validation failed: {e}")
            raise

        # Save to JSON file
        output_filename = os.path.splitext(video_metadata.filename)[0] + ".json"
        output_path = os.path.join(self.output_dir, output_filename)

        self._save_json(video_output, output_path, gt_format=False)

        logger.info(f"Saved output to: {output_path}")
        return output_path

    def merge_and_save_dual_format(
        self,
        video_metadata: VideoMetadata,
        all_objects: List[ObjectLabel],
        hazard_events: List[HazardEvent],
        inferred_metadata: Dict,
        base_output_dir: str,
        relative_path: str = ""
    ) -> Tuple[str, str]:
        """Save output in both GT-compatible and enhanced formats.

        Args:
            video_metadata: Video metadata
            all_objects: List of all detected objects
            hazard_events: List of hazard events
            inferred_metadata: LLM-inferred metadata
            base_output_dir: Base directory for outputs (e.g., 'output/evaluation')
            relative_path: Relative path from data root (e.g., 'data/test')

        Returns:
            Tuple of (gt_format_path, enhanced_format_path)
        """
        # Sort objects and hazards
        sorted_objects = sorted(all_objects, key=lambda obj: obj.start_time_ms)
        sorted_hazards = sorted(hazard_events, key=lambda h: h.start_time_ms)

        # Create VideoOutput
        video_output = VideoOutput(
            filename=video_metadata.filename,
            camera=video_metadata.camera,
            fps=video_metadata.fps,
            description=inferred_metadata.get('description', ''),
            traffic=inferred_metadata.get('traffic', 'unknown'),
            lighting=inferred_metadata.get('lighting', 'unknown'),
            weather=inferred_metadata.get('weather', 'unknown'),
            collision=inferred_metadata.get('collision', 'none'),
            speed=inferred_metadata.get('speed', 'unknown'),
            video_duration_ms=video_metadata.duration_ms,
            objects=sorted_objects,
            hazard_events=sorted_hazards
        )

        output_filename = os.path.splitext(video_metadata.filename)[0] + ".json"

        # Save GT-compatible format
        gt_dir = os.path.join(base_output_dir, "gt_format", relative_path)
        os.makedirs(gt_dir, exist_ok=True)
        gt_path = os.path.join(gt_dir, output_filename)
        self._save_json(video_output, gt_path, gt_format=True)
        logger.info(f"Saved GT format to: {gt_path}")

        # Save enhanced format (with float timestamps)
        enhanced_dir = os.path.join(base_output_dir, "enhanced_format", relative_path)
        os.makedirs(enhanced_dir, exist_ok=True)
        enhanced_path = os.path.join(enhanced_dir, output_filename)
        self._save_json(video_output, enhanced_path, gt_format=False)
        logger.info(f"Saved enhanced format to: {enhanced_path}")

        return gt_path, enhanced_path

    def _save_json(self, video_output: VideoOutput, output_path: str, gt_format: bool = False) -> None:
        """Save VideoOutput to JSON file.

        Args:
            video_output: VideoOutput instance
            output_path: Path to save JSON file
            gt_format: If True, convert start_time_ms to string for GT compatibility
        """
        # Convert to dict using Pydantic's model_dump
        output_dict = video_output.model_dump(exclude_none=False)

        # Convert start_time_ms to string if GT format requested
        if gt_format:
            for obj in output_dict['objects']:
                if 'start_time_ms' in obj:
                    obj['start_time_ms'] = str(obj['start_time_ms'])

        # Convert nested Pydantic models to dicts
        for obj in output_dict['objects']:
            if 'center' in obj and obj['center']:
                # Keep as-is (already a dict)
                pass
            if 'polygon' in obj and obj['polygon']:
                # Keep as-is (already a list of dicts)
                pass

        # Write JSON with pretty formatting
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_dict, f, indent=2, ensure_ascii=False)

    def validate_output(self, json_path: str) -> bool:
        """Validate a saved JSON file against schema.

        Args:
            json_path: Path to JSON file

        Returns:
            True if valid, False otherwise
        """
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Validate using Pydantic
            VideoOutput(**data)
            logger.info(f"Validation successful: {json_path}")
            return True

        except ValidationError as e:
            logger.error(f"Validation failed for {json_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error validating {json_path}: {e}")
            return False

    def get_summary_stats(self, video_output: VideoOutput) -> dict:
        """Get summary statistics for a video output.

        Args:
            video_output: VideoOutput instance

        Returns:
            Dictionary with summary statistics
        """
        total_objects = len(video_output.objects)

        # Count by threat level
        priority_counts = {}
        for obj in video_output.objects:
            priority_counts[obj.priority] = priority_counts.get(obj.priority, 0) + 1

        # Count by distance
        distance_counts = {}
        for obj in video_output.objects:
            distance_counts[obj.distance] = distance_counts.get(obj.distance, 0) + 1

        # Count unique timestamps (snapshots)
        unique_timestamps = set(obj.start_time_ms for obj in video_output.objects)

        # Count hazard severities
        hazard_severity_counts = {}
        for hazard in video_output.hazard_events:
            sev = hazard.hazard_severity
            hazard_severity_counts[sev] = hazard_severity_counts.get(sev, 0) + 1

        summary = {
            "filename": video_output.filename,
            "total_objects": total_objects,
            "num_snapshots": len(unique_timestamps),
            "num_hazards": len(video_output.hazard_events),
            "priority_distribution": priority_counts,
            "distance_distribution": distance_counts,
            "hazard_severity_distribution": hazard_severity_counts
        }

        return summary

