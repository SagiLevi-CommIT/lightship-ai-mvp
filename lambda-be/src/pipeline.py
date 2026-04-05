"""Pipeline orchestrator for end-to-end video processing.

Coordinates all components to process videos from input to final JSON output.
Supports both V1 (LLM image labeler) and V2 (CV + Temporal LLM) pipelines.
"""
import logging
import os
from typing import List, Optional, Dict, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.video_loader import VideoLoader
from src.snapshot_selector import SnapshotSelector
from src.frame_extractor import FrameExtractor
from src.scene_labeler import SceneLabeler
from src.cv_labeler import CVLabeler
from src.hazard_assessor import HazardAssessor
from src.frame_annotator import FrameAnnotator
from src.frame_refiner import FrameRefiner, RefinerStatus
from src.camera_profiles import detect_camera_from_filename, get_camera_profile
# Note: derive_threat_levels no longer used - LLM assigns threat levels directly
# from src.threat_derivation import derive_threat_levels
from src.merger import Merger
from src.schemas import VideoMetadata, ObjectLabel, HazardEvent, SnapshotInfo
from src.config import (
    MIN_OBJECTS_FOR_SELECTION,
    FRAME_REFINER_MAX_RETRIES,
    FRAME_REFINER_FALLBACK_ENABLED,
    CV_PARALLEL_WORKERS
)

logger = logging.getLogger(__name__)


class Pipeline:
    """End-to-end pipeline for video processing and object labeling."""

    def __init__(
        self,
        snapshot_strategy: str = "naive",
        max_snapshots: int = 3,
        cleanup_frames: bool = False,  # Changed default to False for V2
        use_cv_labeler: bool = True  # Use V2 CV pipeline by default
    ):
        """Initialize Pipeline with all components.

        Args:
            snapshot_strategy: Strategy for snapshot selection ('naive' or 'scene_change')
            max_snapshots: Maximum number of snapshots to select per video
            cleanup_frames: Whether to delete temporary frame files after processing
            use_cv_labeler: If True, use V2 CV pipeline; if False, use V1 LLM pipeline
        """
        self.video_loader = VideoLoader()
        self.snapshot_selector = SnapshotSelector(strategy=snapshot_strategy, max_snapshots=max_snapshots)
        self.frame_extractor = FrameExtractor()

        logger.info(f"Pipeline configured with max_snapshots={max_snapshots}, strategy={snapshot_strategy}")

        # Choose labeler based on version
        self.use_cv_labeler = use_cv_labeler
        if use_cv_labeler:
            # Note: CVLabeler will be re-initialized per video with camera-specific profile
            self.cv_labeler = None  # Will be set in process_video
            self.frame_annotator = FrameAnnotator()
            self.frame_refiner = FrameRefiner()
            self.hazard_assessor = HazardAssessor()
            logger.info("Using V3 pipeline: CV Labeler (camera-specific) + Per-Frame Refinement + Hazard Assessor")
        else:
            self.scene_labeler = SceneLabeler()
            logger.info("Using V1 pipeline: LLM Scene Labeler")

        self.merger = Merger()
        self.cleanup_frames = cleanup_frames

        logger.info(
            f"Pipeline initialized (strategy={snapshot_strategy}, "
            f"max_snapshots={max_snapshots}, cleanup={cleanup_frames}, "
            f"version={'V2' if use_cv_labeler else 'V1'})"
        )

    def process_video(
        self,
        video_path: str,
        is_train: bool = False
    ) -> Optional[str]:
        """Process a single video through the full pipeline.

        Args:
            video_path: Path to video file
            is_train: Whether this is a training video

        Returns:
            Path to output JSON file, or None if processing failed
        """
        logger.info(f"{'='*80}")
        logger.info(f"Processing video: {video_path} (train={is_train})")
        logger.info(f"{'='*80}")

        try:
            # Step 1: Load video metadata
            logger.info("Step 1: Loading video metadata")
            video_metadata = self.video_loader.load_video_metadata(video_path)

            if self.use_cv_labeler:
                # Initialize camera-specific CV labeler
                video_filename = Path(video_path).name
                camera_type = detect_camera_from_filename(video_filename)
                camera_profile = get_camera_profile(camera_type)
                logger.info(f"Detected camera type: {camera_type}, using profile: {camera_profile.name}")
                self.cv_labeler = CVLabeler(camera_profile=camera_profile)
                # V2 Pipeline: Extract ALL frames first, CV on all, then select
                logger.info("Step 2 (V2): Generating snapshots for all frames")
                all_snapshots = self._generate_all_frame_snapshots(video_metadata)

                logger.info("Step 3 (V2): Extracting all frames")
                all_extracted_frames = self.frame_extractor.extract_frames(
                    video_metadata,
                    all_snapshots
                )

                if not all_extracted_frames:
                    logger.error("No frames extracted, aborting")
                    return None

                # V2 Pipeline: CV Labeler + Per-Frame Refinement + Hazard Assessor
                all_objects, hazard_events, inferred_metadata = self._process_v2(
                    all_snapshots, all_extracted_frames, video_metadata
                )
                hazard_events = hazard_events if hazard_events else []
            else:
                # V1 Pipeline: Use selective snapshot extraction
                logger.info("Step 2 (V1): Selecting snapshots")
                snapshots = self.snapshot_selector.select_snapshots(
                    video_metadata,
                    is_train=is_train
                )

                if not snapshots:
                    logger.error("No snapshots selected, aborting")
                    return None

                logger.info("Step 3 (V1): Extracting selected frames")
                extracted_frames = self.frame_extractor.extract_frames(
                    video_metadata,
                    snapshots
                )

                if not extracted_frames:
                    logger.error("No frames extracted, aborting")
                    return None

                # V1 Pipeline: LLM Scene Labeler
                all_objects = self._process_v1(
                    snapshots, extracted_frames, video_metadata
                )
                hazard_events = []  # V1 doesn't have hazard events

            # Step 5: Merge and save
            logger.info("Step 5: Merging and saving output")
            output_path = self.merger.merge_and_save(
                video_metadata, all_objects, hazard_events, inferred_metadata if self.use_cv_labeler else {}
            )

            # Get summary stats
            from src.schemas import VideoOutput
            with open(output_path, 'r') as f:
                import json
                data = json.load(f)
                video_output = VideoOutput(**data)

            summary = self.merger.get_summary_stats(video_output)
            logger.info(f"Summary: {summary}")

            # Step 6: Cleanup temporary frames
            if self.cleanup_frames and 'all_extracted_frames' in locals():
                logger.info("Step 6: Cleaning up temporary frames")
                frame_paths = list(all_extracted_frames.values())
                self.frame_extractor.cleanup_frames(frame_paths)
            elif self.cleanup_frames and 'extracted_frames' in locals():
                logger.info("Step 6: Cleaning up temporary frames")
                frame_paths = list(extracted_frames.values())
                self.frame_extractor.cleanup_frames(frame_paths)

            logger.info(f"{'='*80}")
            logger.info(f"SUCCESS: Output saved to {output_path}")
            logger.info(f"{'='*80}")

            return output_path

        except Exception as e:
            logger.error(f"Pipeline failed for {video_path}: {e}", exc_info=True)
            return None

    def _generate_all_frame_snapshots(
        self,
        video_metadata: VideoMetadata,
        interval_ms: int = 500
    ) -> List[SnapshotInfo]:
        """Generate snapshots for all frames (or dense sampling) of video.

        Args:
            video_metadata: Video metadata
            interval_ms: Interval between snapshots in milliseconds (default 500ms = 2fps equiv)

        Returns:
            List of SnapshotInfo for all sampled frames
        """
        snapshots = []
        current_time_ms = 0.0
        duration_ms = video_metadata.duration_ms

        frame_idx = 0
        while current_time_ms < duration_ms:
            snapshots.append(SnapshotInfo(
                frame_idx=frame_idx,
                timestamp_ms=current_time_ms
            ))
            current_time_ms += interval_ms
            frame_idx += 1

        logger.info(f"Generated {len(snapshots)} snapshots for full video analysis")
        return snapshots

    def _select_uniform_frames(
        self,
        frame_indices: List[int],
        max_count: int
    ) -> List[int]:
        """Select N frames uniformly from a list of frame indices.

        Args:
            frame_indices: List of available frame indices
            max_count: Maximum number of frames to select

        Returns:
            List of selected frame indices (uniformly distributed)
        """
        if len(frame_indices) <= max_count:
            return frame_indices

        # Select uniformly spaced indices
        step = len(frame_indices) / max_count
        selected = [frame_indices[int(i * step)] for i in range(max_count)]

        logger.info(f"Selected {len(selected)} frames uniformly from {len(frame_indices)} candidates")
        return selected

    def _refine_frame_with_retries(
        self,
        frame_idx: int,
        frame_path: str,
        objects: List[ObjectLabel],
        timestamp_ms: float,
        output_dir: Path
    ) -> Tuple[List[ObjectLabel], bool]:
        """Refine a single frame with retry logic.

        Args:
            frame_idx: Frame index
            frame_path: Path to original frame
            objects: CV-detected objects
            timestamp_ms: Frame timestamp
            output_dir: Directory for temporary annotated frames

        Returns:
            Tuple of (refined_objects, success):
            - refined_objects: List of refined objects (or original if failed)
            - success: True if refinement succeeded, False if failed completely
        """
        retry_count = 0
        current_objects = objects

        while retry_count <= FRAME_REFINER_MAX_RETRIES:
            # Annotate frame with current objects
            annotated_path = str(output_dir / f"annotated_frame_{frame_idx}_r{retry_count}.png")
            try:
                self.frame_annotator.annotate_frame(
                    frame_path,
                    current_objects,
                    annotated_path,
                    timestamp_ms
                )
            except Exception as e:
                logger.error(f"Failed to annotate frame {frame_idx}: {e}")
                return objects, False

            # Call per-frame refiner
            refined_objects, status, reason = self.frame_refiner.refine_frame(
                frame_path,
                annotated_path,
                current_objects,
                timestamp_ms
            )

            if status == RefinerStatus.SUCCESS:
                logger.info(f"Frame {frame_idx} refined successfully on attempt {retry_count + 1}")
                return refined_objects, True

            elif status == RefinerStatus.NEEDS_RETRY:
                if retry_count < FRAME_REFINER_MAX_RETRIES:
                    logger.info(f"Frame {frame_idx} needs retry (attempt {retry_count + 1}): {reason}")
                    retry_count += 1
                    current_objects = refined_objects  # Use LLM's partial refinements
                    continue
                else:
                    logger.warning(f"Frame {frame_idx} exceeded max retries ({FRAME_REFINER_MAX_RETRIES})")
                    return refined_objects, False

            else:  # FAILED
                logger.warning(f"Frame {frame_idx} refinement failed: {reason}")
                return objects, False

        # Should not reach here
        return objects, False

    def _find_nearest_unused_frame(
        self,
        target_idx: int,
        available_frames: Dict[int, List[ObjectLabel]],
        used_frames: set
    ) -> Optional[int]:
        """Find the nearest unused frame with objects.

        Args:
            target_idx: Target frame index (failed frame)
            available_frames: Dict of frame_idx -> objects for frames with objects
            used_frames: Set of already-used frame indices

        Returns:
            Nearest unused frame index, or None if no alternatives available
        """
        unused_frames = [idx for idx in available_frames.keys() if idx not in used_frames]

        if not unused_frames:
            return None

        # Find nearest by index distance
        nearest = min(unused_frames, key=lambda idx: abs(idx - target_idx))
        logger.info(f"Found nearest unused frame {nearest} for failed frame {target_idx}")
        return nearest

    def _process_v1(
        self,
        snapshots,
        extracted_frames: Dict[int, str],
        video_metadata: VideoMetadata
    ) -> List[ObjectLabel]:
        """Process frames using V1 LLM Scene Labeler.

        Args:
            snapshots: List of SnapshotInfo
            extracted_frames: Dict mapping frame_idx to image path
            video_metadata: Video metadata

        Returns:
            List of ObjectLabel instances
        """
        logger.info("Step 4: Labeling objects with V1 LLM Scene Labeler")
        all_objects: List[ObjectLabel] = []

        for snapshot in snapshots:
            frame_path = extracted_frames.get(snapshot.frame_idx)
            if not frame_path:
                logger.warning(f"Frame not found for snapshot at {snapshot.timestamp_ms}ms")
                continue

            objects = self.scene_labeler.label_frame(
                frame_path,
                snapshot.timestamp_ms,
                video_metadata.width,
                video_metadata.height
            )

            all_objects.extend(objects)
            logger.info(
                f"Labeled frame {snapshot.frame_idx}: {len(objects)} objects detected"
            )

        return all_objects

    def _process_v2(
        self,
        snapshots,
        extracted_frames: Dict[int, str],
        video_metadata: VideoMetadata
    ) -> tuple[List[ObjectLabel], List[HazardEvent], Dict]:
        """Process frames using V2 CV Labeler + Per-Frame Refinement + Hazard Assessor.

        New flow:
        1. Run CV detection on ALL extracted frames
        2. Filter to frames with >= MIN_OBJECTS_FOR_SELECTION objects
        3. Select N frames uniformly from filtered set
        4. (Per-frame refinement will be added in next step)
        5. Temporal LLM for hazard events

        Args:
            snapshots: List of ALL SnapshotInfo (all frames)
            extracted_frames: Dict mapping frame_idx to image path (all frames)
            video_metadata: Video metadata

        Returns:
            Tuple of (objects_list, hazard_events_list, inferred_video_metadata)
        """
        # Step 4a: CV Labeling on ALL frames (parallelized)
        logger.info(f"Step 4a: CV Labeling ALL frames in parallel")
        all_frame_objects: Dict[int, List[ObjectLabel]] = {}

        def process_frame(snapshot):
            """Process a single frame with CV labeler."""
            frame_path = extracted_frames.get(snapshot.frame_idx)
            if not frame_path:
                logger.warning(f"Frame not found for snapshot at {snapshot.timestamp_ms}ms")
                return snapshot.frame_idx, []

            objects = self.cv_labeler.label_frame(
                frame_path,
                snapshot.timestamp_ms,
                video_metadata.width,
                video_metadata.height
            )

            return snapshot.frame_idx, objects

        # Process frames sequentially (YOLO not thread-safe with some models)
        logger.info(f"Processing {len(snapshots)} frames sequentially")

        for i, snapshot in enumerate(snapshots):
            try:
                frame_idx, objects = process_frame(snapshot)
                all_frame_objects[frame_idx] = objects

                if (i + 1) % 5 == 0 or (i + 1) == len(snapshots):
                    logger.info(f"Progress: {i + 1}/{len(snapshots)} frames processed")
            except Exception as e:
                logger.error(f"Error processing frame {snapshot.frame_idx}: {e}", exc_info=True)
                all_frame_objects[snapshot.frame_idx] = []

        logger.info(f"CV labeled {len(all_frame_objects)} frames")

        # Step 4b: Filter to frames with sufficient objects
        logger.info(f"Step 4b: Filtering frames with >= {MIN_OBJECTS_FOR_SELECTION} objects")
        frames_with_objects = {
            frame_idx: objs for frame_idx, objs in all_frame_objects.items()
            if len(objs) >= MIN_OBJECTS_FOR_SELECTION
        }

        logger.info(f"Filtered to {len(frames_with_objects)} frames with objects "
                   f"(from {len(all_frame_objects)} total)")

        if not frames_with_objects:
            logger.warning("No frames with sufficient objects found!")
            return [], [], {}

        # Step 4c: Select N frames uniformly from filtered set
        selected_frame_indices = self._select_uniform_frames(
            list(frames_with_objects.keys()),
            self.snapshot_selector.max_snapshots
        )

        # Build selected frame_objects dict
        selected_frame_objects = {
            idx: frames_with_objects[idx] for idx in selected_frame_indices
        }

        # Build selected frame_images dict
        selected_frame_images = {
            idx: extracted_frames[idx] for idx in selected_frame_indices
        }

        logger.info(f"Selected {len(selected_frame_objects)} frames for processing")

        # Step 4d: Per-Frame LLM Refinement with retry/fallback
        logger.info("Step 4d: Per-frame LLM refinement with retry/fallback logic")
        refined_frame_objects: Dict[int, List[ObjectLabel]] = {}
        used_frames = set()
        output_dir = Path("output/temp_annotations")
        output_dir.mkdir(parents=True, exist_ok=True)

        for frame_idx in selected_frame_indices:
            current_frame_idx = frame_idx
            success = False

            # Try to refine this frame (with fallback to nearest unused frame if needed)
            while not success:
                frame_path = extracted_frames[current_frame_idx]
                objects = frames_with_objects[current_frame_idx]

                # Find corresponding snapshot for timestamp
                timestamp_ms = None
                for snapshot in snapshots:
                    if snapshot.frame_idx == current_frame_idx:
                        timestamp_ms = snapshot.timestamp_ms
                        break

                if timestamp_ms is None:
                    logger.warning(f"Could not find timestamp for frame {current_frame_idx}, using index as ms")
                    timestamp_ms = float(current_frame_idx * 1000)

                # Attempt refinement with retries
                refined_objects, success = self._refine_frame_with_retries(
                    current_frame_idx,
                    frame_path,
                    objects,
                    timestamp_ms,
                    output_dir
                )

                if success:
                    refined_frame_objects[current_frame_idx] = refined_objects
                    used_frames.add(current_frame_idx)
                    logger.info(f"Frame {current_frame_idx}: {len(objects)} -> {len(refined_objects)} objects")
                    break

                # Refinement failed - try fallback if enabled
                if FRAME_REFINER_FALLBACK_ENABLED:
                    nearest_frame = self._find_nearest_unused_frame(
                        current_frame_idx,
                        frames_with_objects,
                        used_frames
                    )

                    if nearest_frame is not None:
                        logger.info(f"Falling back from frame {current_frame_idx} to frame {nearest_frame}")
                        current_frame_idx = nearest_frame
                        continue  # Try again with fallback frame
                    else:
                        logger.warning(f"No fallback frames available for frame {current_frame_idx}")

                # No success and no fallback available - keep CV-only results
                logger.warning(f"Using CV-only results for frame {current_frame_idx}")
                refined_frame_objects[current_frame_idx] = objects
                used_frames.add(current_frame_idx)
                break

        logger.info(f"Per-frame refinement complete: {len(refined_frame_objects)} frames processed")

        # Step 4e: Re-annotate frames with refined objects (for temporal LLM + delivery)
        logger.info("Step 4e: Re-annotating frames with refined objects")
        refined_frame_images: Dict[int, str] = {}

        # Create delivery annotated frames directory (direct save for customer)
        video_name = Path(video_metadata.filename).stem
        annotated_output_dir = Path("delivery") / "annotated_frames" / video_name
        os.makedirs(annotated_output_dir, exist_ok=True)
        logger.info(f"Annotated frames will be saved to: {annotated_output_dir}")

        for frame_idx, refined_objs in refined_frame_objects.items():
            frame_path = extracted_frames[frame_idx]

            # Find timestamp
            timestamp_ms = None
            for snapshot in snapshots:
                if snapshot.frame_idx == frame_idx:
                    timestamp_ms = snapshot.timestamp_ms
                    break

            # Save annotated frame to permanent location for delivery
            permanent_annotated_path = str(annotated_output_dir / f"{video_name}_frame_{frame_idx}_{int(timestamp_ms)}ms_annotated.png")

            try:
                self.frame_annotator.annotate_frame(
                    frame_path,
                    refined_objs,
                    permanent_annotated_path,
                    timestamp_ms
                )
                refined_frame_images[frame_idx] = permanent_annotated_path
                logger.info(f"Saved annotated frame to: {permanent_annotated_path}")
            except Exception as e:
                logger.error(f"Failed to re-annotate frame {frame_idx}: {e}")
                # Use original frame as fallback
                refined_frame_images[frame_idx] = frame_path

        # Step 4f: Hazard Assessment (temporal LLM - hazard events only)
        logger.info("Step 4f: Assessing hazards with temporal LLM (hazard events only)")

        video_metadata_dict = {
            'camera': video_metadata.camera,
            'fps': video_metadata.fps,
            'duration_ms': video_metadata.duration_ms
        }

        hazard_events, final_objects, inferred_metadata = self.hazard_assessor.assess_hazards_only(
            frame_objects=refined_frame_objects,
            frame_images=refined_frame_images,
            video_metadata=video_metadata_dict
        )

        logger.info(f"Identified {len(hazard_events)} hazard events")
        logger.info(f"Final output: {len(final_objects)} objects")
        logger.info(f"LLM inferred video metadata: {inferred_metadata}")

        return final_objects, hazard_events, inferred_metadata

    def process_batch(
        self,
        video_paths: List[str],
        is_train: bool = False
    ) -> List[Optional[str]]:
        """Process a batch of videos.

        Args:
            video_paths: List of video file paths
            is_train: Whether these are training videos

        Returns:
            List of output JSON paths (None for failed videos)
        """
        logger.info(f"Processing batch of {len(video_paths)} videos")

        results = []
        for i, video_path in enumerate(video_paths, 1):
            logger.info(f"\n{'#'*80}")
            logger.info(f"# Video {i}/{len(video_paths)}")
            logger.info(f"{'#'*80}\n")

            output_path = self.process_video(video_path, is_train=is_train)
            results.append(output_path)

        # Summary
        successful = sum(1 for r in results if r is not None)
        failed = len(results) - successful

        logger.info(f"\n{'='*80}")
        logger.info(f"BATCH COMPLETE: {successful}/{len(results)} successful, {failed} failed")
        logger.info(f"{'='*80}\n")

        return results

