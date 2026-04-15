"""Pipeline orchestrator for end-to-end video processing.

Rekognition-only pipeline:
  1. Load video metadata
  2. Extract frames (temporal sampling)
  3. Detect objects via Amazon Rekognition
  4. Assess hazards via Bedrock LLM
  5. Classify video (4 types) via Bedrock LLM
  6. Generate client-format config JSON
  7. Annotate frames
  8. Persist results
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from src.video_loader import VideoLoader
from src.snapshot_selector import SnapshotSelector
from src.frame_extractor import FrameExtractor
from src.rekognition_labeler import RekognitionLabeler
from src.hazard_assessor import HazardAssessor
from src.video_classifier import VideoClassifier
from src.config_generator import ConfigGenerator
from src.frame_annotator import FrameAnnotator
from src.merger import Merger
from src.schemas import (
    VideoMetadata,
    ObjectLabel,
    HazardEvent,
    SnapshotInfo,
    VideoOutput,
)
from src.config import (
    MIN_OBJECTS_FOR_SELECTION,
    OUTPUT_DIR,
)

logger = logging.getLogger(__name__)


class Pipeline:
    """End-to-end Rekognition-based pipeline for dashcam video processing."""

    def __init__(
        self,
        snapshot_strategy: str = "naive",
        max_snapshots: int = 5,
        cleanup_frames: bool = False,
        use_cv_labeler: bool = True,  # kept for backward-compat API; ignored
    ):
        self.video_loader = VideoLoader()
        self.snapshot_selector = SnapshotSelector(
            strategy=snapshot_strategy, max_snapshots=max_snapshots
        )
        self.frame_extractor = FrameExtractor()

        self.rekognition_labeler = RekognitionLabeler()
        self.hazard_assessor = HazardAssessor()
        self.video_classifier = VideoClassifier()
        self.config_generator = ConfigGenerator()
        self.frame_annotator = FrameAnnotator()
        self.merger = Merger()
        self.cleanup_frames = cleanup_frames

        logger.info(
            "Pipeline initialised (Rekognition-only, strategy=%s, max_snapshots=%d)",
            snapshot_strategy,
            max_snapshots,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_video(
        self,
        video_path: str,
        is_train: bool = False,
    ) -> Optional[str]:
        """Process a single video through the full pipeline.

        Returns:
            Path to output config JSON, or None on failure.
        """
        logger.info("=" * 80)
        logger.info("Processing video: %s", video_path)
        logger.info("=" * 80)

        try:
            # Step 1 ─ Load video metadata
            logger.info("Step 1: Loading video metadata")
            video_metadata = self.video_loader.load_video_metadata(video_path)

            # Step 2 ─ Generate & extract frames
            logger.info("Step 2: Generating frame snapshots")
            all_snapshots = self._generate_all_frame_snapshots(video_metadata)

            logger.info("Step 3: Extracting frames")
            extracted_frames = self.frame_extractor.extract_frames(
                video_metadata, all_snapshots
            )
            if not extracted_frames:
                logger.error("No frames extracted, aborting")
                return None

            # Step 4 ─ Rekognition detection on all frames
            logger.info("Step 4: Running Rekognition detection on %d frames", len(extracted_frames))
            all_frame_objects: Dict[int, List[ObjectLabel]] = {}
            for snapshot in all_snapshots:
                fpath = extracted_frames.get(snapshot.frame_idx)
                if not fpath:
                    continue
                objects = self.rekognition_labeler.label_frame_from_path(
                    fpath,
                    snapshot.timestamp_ms,
                    video_metadata.width,
                    video_metadata.height,
                )
                all_frame_objects[snapshot.frame_idx] = objects

            # Step 4b ─ Filter to frames with objects and select best N
            frames_with_objects = {
                idx: objs
                for idx, objs in all_frame_objects.items()
                if len(objs) >= MIN_OBJECTS_FOR_SELECTION
            }
            logger.info(
                "Filtered to %d frames with objects (from %d total)",
                len(frames_with_objects),
                len(all_frame_objects),
            )

            if not frames_with_objects:
                frames_with_objects = all_frame_objects
                logger.warning("No frames met object threshold, using all frames")

            selected_indices = self._select_uniform_frames(
                list(frames_with_objects.keys()),
                self.snapshot_selector.max_snapshots,
            )
            selected_frame_objects = {
                idx: frames_with_objects[idx] for idx in selected_indices
            }
            selected_frame_images = {
                idx: extracted_frames[idx]
                for idx in selected_indices
                if idx in extracted_frames
            }

            # Step 5 ─ Hazard assessment via LLM
            logger.info("Step 5: Hazard assessment via LLM")
            hazard_events, final_objects, inferred_metadata = (
                self.hazard_assessor.assess_hazards_only(
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
            )
            hazard_events = hazard_events or []
            final_objects = final_objects or []

            # Step 6 ─ Video classification
            logger.info("Step 6: Classifying video")
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
            video_class = classification_result.get("video_class", "hazard_detection")
            logger.info("Classified video as: %s", video_class)

            # Step 7 ─ Generate client-format config JSON
            logger.info("Step 7: Generating client config JSON (type=%s)", video_class)
            client_config = self.config_generator.generate(
                video_class=video_class,
                video_metadata=video_metadata,
                objects=final_objects,
                hazard_events=hazard_events,
                classification_result=classification_result,
            )

            # Step 8 ─ Annotate frames for delivery
            logger.info("Step 8: Annotating frames")
            video_name = Path(video_metadata.filename).stem
            annotated_dir = Path(OUTPUT_DIR) / "delivery" / "annotated_frames" / video_name
            os.makedirs(annotated_dir, exist_ok=True)

            objects_by_time: Dict[float, List[ObjectLabel]] = {}
            for obj in final_objects:
                ts = obj.start_time_ms
                objects_by_time.setdefault(ts, []).append(obj)

            for idx in selected_indices:
                fpath = extracted_frames.get(idx)
                if not fpath:
                    continue
                ts = None
                for snap in all_snapshots:
                    if snap.frame_idx == idx:
                        ts = snap.timestamp_ms
                        break
                objs_at_ts = objects_by_time.get(ts, []) if ts is not None else []
                ann_path = str(annotated_dir / f"{video_name}_frame_{idx}_{int(ts or 0)}ms_annotated.png")
                try:
                    self.frame_annotator.annotate_frame(fpath, objs_at_ts, ann_path, ts)
                except Exception as e:
                    logger.error("Failed to annotate frame %d: %s", idx, e)

            # Step 9 ─ Save output JSON (merger for intermediate + client config)
            logger.info("Step 9: Saving outputs")
            merged_metadata = {
                **(inferred_metadata or {}),
                "road_type": classification_result.get("road_type", "unknown"),
                "video_class": video_class,
            }
            output_path = self.merger.merge_and_save(
                video_metadata,
                final_objects,
                hazard_events,
                merged_metadata,
                client_config=client_config,
            )

            logger.info("=" * 80)
            logger.info("SUCCESS: Output saved to %s", output_path)
            logger.info("=" * 80)
            return output_path

        except Exception as e:
            logger.error("Pipeline failed for %s: %s", video_path, e, exc_info=True)
            return None

    def process_batch(
        self,
        video_paths: List[str],
        is_train: bool = False,
    ) -> List[Optional[str]]:
        results = []
        for i, vp in enumerate(video_paths, 1):
            logger.info("# Video %d/%d", i, len(video_paths))
            results.append(self.process_video(vp, is_train=is_train))
        successful = sum(1 for r in results if r is not None)
        logger.info(
            "BATCH COMPLETE: %d/%d successful", successful, len(results)
        )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _generate_all_frame_snapshots(
        self,
        video_metadata: VideoMetadata,
        interval_ms: int = 500,
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
    def _select_uniform_frames(
        frame_indices: List[int],
        max_count: int,
    ) -> List[int]:
        if len(frame_indices) <= max_count:
            return frame_indices
        step = len(frame_indices) / max_count
        return [frame_indices[int(i * step)] for i in range(max_count)]
