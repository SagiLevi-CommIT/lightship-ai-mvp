"""Pipeline orchestrator for end-to-end video processing.

Rekognition-only pipeline — crash-proof with graceful degradation.
If LLM steps fail, still produces valid output from Rekognition detections.
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

from src.video_loader import VideoLoader
from src.snapshot_selector import SnapshotSelector
from src.frame_extractor import FrameExtractor
from src.rekognition_labeler import RekognitionLabeler
from src.frame_annotator import FrameAnnotator
from src.merger import Merger
from src.schemas import (
    VideoMetadata,
    ObjectLabel,
    HazardEvent,
    SnapshotInfo,
)
from src.config import (
    MIN_OBJECTS_FOR_SELECTION,
    OUTPUT_DIR,
)

logger = logging.getLogger(__name__)


def _safe_import_hazard_assessor():
    try:
        from src.hazard_assessor import HazardAssessor
        return HazardAssessor()
    except Exception as e:
        logger.warning("HazardAssessor unavailable: %s", e)
        return None


def _safe_import_video_classifier():
    try:
        from src.video_classifier import VideoClassifier
        return VideoClassifier()
    except Exception as e:
        logger.warning("VideoClassifier unavailable: %s", e)
        return None


def _safe_import_config_generator():
    try:
        from src.config_generator import ConfigGenerator
        return ConfigGenerator()
    except Exception as e:
        logger.warning("ConfigGenerator unavailable: %s", e)
        return None


class Pipeline:
    """End-to-end Rekognition-based pipeline for dashcam video processing."""

    def __init__(
        self,
        snapshot_strategy: str = "naive",
        max_snapshots: int = 5,
        cleanup_frames: bool = False,
        use_cv_labeler: bool = True,
    ):
        self.video_loader = VideoLoader()
        self.snapshot_selector = SnapshotSelector(
            strategy=snapshot_strategy, max_snapshots=max_snapshots
        )
        self.frame_extractor = FrameExtractor()
        self.rekognition_labeler = RekognitionLabeler()
        self.frame_annotator = FrameAnnotator()
        self.merger = Merger()
        self.cleanup_frames = cleanup_frames

        self.hazard_assessor = _safe_import_hazard_assessor()
        self.video_classifier = _safe_import_video_classifier()
        self.config_generator = _safe_import_config_generator()

        logger.info(
            "Pipeline initialised (Rekognition-only, strategy=%s, max_snapshots=%d)",
            snapshot_strategy, max_snapshots,
        )

    def process_video(
        self,
        video_path: str,
        is_train: bool = False,
    ) -> Optional[str]:
        """Process a single video. Always returns valid JSON on success."""
        logger.info("=" * 60)
        logger.info("Processing video: %s", video_path)

        try:
            video_metadata = self.video_loader.load_video_metadata(video_path)
        except Exception as e:
            logger.error("Failed to load video metadata: %s", e, exc_info=True)
            return None

        try:
            all_snapshots = self._generate_all_frame_snapshots(video_metadata)
            extracted_frames = self.frame_extractor.extract_frames(video_metadata, all_snapshots)
            if not extracted_frames:
                logger.error("No frames extracted")
                return None
        except Exception as e:
            logger.error("Frame extraction failed: %s", e, exc_info=True)
            return None

        # Rekognition detection — core step
        logger.info("Step: Rekognition detection on %d frames", len(extracted_frames))
        all_frame_objects: Dict[int, List[ObjectLabel]] = {}
        for snapshot in all_snapshots:
            fpath = extracted_frames.get(snapshot.frame_idx)
            if not fpath:
                continue
            try:
                objects = self.rekognition_labeler.label_frame_from_path(
                    fpath, snapshot.timestamp_ms,
                    video_metadata.width, video_metadata.height,
                )
                all_frame_objects[snapshot.frame_idx] = objects
            except Exception as e:
                logger.warning("Rekognition failed for frame %d: %s", snapshot.frame_idx, e)
                all_frame_objects[snapshot.frame_idx] = []

        # Select best frames
        frames_with_objects = {
            idx: objs for idx, objs in all_frame_objects.items()
            if len(objs) >= MIN_OBJECTS_FOR_SELECTION
        }
        if not frames_with_objects:
            frames_with_objects = all_frame_objects

        selected_indices = self._select_uniform_frames(
            list(frames_with_objects.keys()),
            self.snapshot_selector.max_snapshots,
        )
        selected_frame_objects = {idx: frames_with_objects.get(idx, []) for idx in selected_indices}
        selected_frame_images = {idx: extracted_frames[idx] for idx in selected_indices if idx in extracted_frames}

        # Collect all detected objects
        all_objects: List[ObjectLabel] = []
        for objs in selected_frame_objects.values():
            all_objects.extend(objs)

        # Hazard assessment (graceful degradation)
        hazard_events: List[HazardEvent] = []
        inferred_metadata: Dict[str, Any] = {}
        if self.hazard_assessor and selected_frame_objects:
            try:
                logger.info("Step: Hazard assessment via LLM")
                h_events, h_objects, h_meta = self.hazard_assessor.assess_hazards_only(
                    frame_objects=selected_frame_objects,
                    frame_images=selected_frame_images,
                    video_metadata={
                        "camera": video_metadata.camera,
                        "fps": video_metadata.fps,
                        "duration_ms": video_metadata.duration_ms,
                        "width": video_metadata.width,
                        "height": video_metadata.height,
                    },
                )
                hazard_events = h_events or []
                if h_objects:
                    all_objects = h_objects
                inferred_metadata = h_meta or {}
            except Exception as e:
                logger.warning("Hazard assessment failed (continuing without): %s", e)

        # Video classification (graceful degradation)
        classification_result: Dict[str, Any] = {
            "video_class": "hazard_detection",
            "road_type": "unknown",
            "speed": "unknown",
            "traffic": "unknown",
            "weather": "unknown",
            "collision": "none",
            "trial_start_prompt": "",
            "description": "",
            "questions": [],
        }
        if self.video_classifier and selected_frame_images:
            try:
                logger.info("Step: Video classification via LLM")
                classification_result = self.video_classifier.classify(
                    frame_objects=selected_frame_objects,
                    frame_image_paths=selected_frame_images,
                    hazard_events=hazard_events,
                    video_metadata={
                        "camera": video_metadata.camera,
                        "fps": video_metadata.fps,
                        "duration_ms": video_metadata.duration_ms,
                        "width": video_metadata.width,
                        "height": video_metadata.height,
                    },
                )
            except Exception as e:
                logger.warning("Classification failed (using defaults): %s", e)

        video_class = classification_result.get("video_class", "hazard_detection")

        # Config generation (graceful degradation)
        client_config: Optional[Dict[str, Any]] = None
        if self.config_generator:
            try:
                logger.info("Step: Generating client config JSON (type=%s)", video_class)
                client_config = self.config_generator.generate(
                    video_class=video_class,
                    video_metadata=video_metadata,
                    objects=all_objects,
                    hazard_events=hazard_events,
                    classification_result=classification_result,
                )
            except Exception as e:
                logger.warning("Config generation failed: %s", e)

        # Annotate frames
        try:
            logger.info("Step: Annotating frames")
            video_name = Path(video_metadata.filename).stem
            annotated_dir = Path(OUTPUT_DIR) / "delivery" / "annotated_frames" / video_name
            os.makedirs(annotated_dir, exist_ok=True)

            objects_by_time: Dict[float, List[ObjectLabel]] = {}
            for obj in all_objects:
                objects_by_time.setdefault(obj.start_time_ms, []).append(obj)

            for idx in selected_indices:
                fpath = extracted_frames.get(idx)
                if not fpath:
                    continue
                ts = next(
                    (s.timestamp_ms for s in all_snapshots if s.frame_idx == idx),
                    None,
                )
                objs_at_ts = objects_by_time.get(ts, []) if ts is not None else []
                ann_path = str(annotated_dir / f"{video_name}_frame_{idx}_{int(ts or 0)}ms_annotated.png")
                try:
                    self.frame_annotator.annotate_frame(fpath, objs_at_ts, ann_path, ts)
                except Exception as e:
                    logger.warning("Annotation failed for frame %d: %s", idx, e)
        except Exception as e:
            logger.warning("Frame annotation step failed: %s", e)

        # Save outputs — always succeeds if we have video_metadata
        logger.info("Step: Saving outputs")
        merged_metadata = {
            **(inferred_metadata or {}),
            "road_type": classification_result.get("road_type", "unknown"),
            "video_class": video_class,
            "description": classification_result.get("description", ""),
            "traffic": classification_result.get("traffic", "unknown"),
            "weather": classification_result.get("weather", "unknown"),
            "speed": classification_result.get("speed", "unknown"),
            "collision": classification_result.get("collision", "none"),
        }
        try:
            output_path = self.merger.merge_and_save(
                video_metadata, all_objects, hazard_events,
                merged_metadata, client_config=client_config,
            )
        except Exception as e:
            logger.error("Merger failed: %s", e, exc_info=True)
            return None

        logger.info("SUCCESS: Output saved to %s", output_path)
        return output_path

    def process_batch(
        self, video_paths: List[str], is_train: bool = False,
    ) -> List[Optional[str]]:
        results = []
        for i, vp in enumerate(video_paths, 1):
            logger.info("# Video %d/%d", i, len(video_paths))
            results.append(self.process_video(vp, is_train=is_train))
        successful = sum(1 for r in results if r is not None)
        logger.info("BATCH COMPLETE: %d/%d successful", successful, len(results))
        return results

    def _generate_all_frame_snapshots(
        self, video_metadata: VideoMetadata, interval_ms: int = 500,
    ) -> List[SnapshotInfo]:
        snapshots = []
        t = 0.0
        idx = 0
        while t < video_metadata.duration_ms:
            snapshots.append(SnapshotInfo(frame_idx=idx, timestamp_ms=t))
            t += interval_ms
            idx += 1
        logger.info("Generated %d snapshots for full video", len(snapshots))
        return snapshots

    @staticmethod
    def _select_uniform_frames(frame_indices: List[int], max_count: int) -> List[int]:
        if not frame_indices:
            return []
        if len(frame_indices) <= max_count:
            return frame_indices
        step = len(frame_indices) / max_count
        return [frame_indices[int(i * step)] for i in range(max_count)]
