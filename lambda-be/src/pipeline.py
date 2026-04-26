"""Pipeline orchestrator for end-to-end video processing.

Coordinates all components to process videos from input to final JSON output.
Supports both V1 (LLM image labeler) and V2 (CV + Temporal LLM) pipelines.
"""
import logging
import os
from typing import Callable, List, Optional, Dict, Tuple
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# A progress reporter receives structured stage updates from the pipeline so
# the caller (the Lambda worker, a CLI, or a unit test) can translate them
# into DynamoDB / UI progress beacons. The callable is intentionally tiny:
# ``fn(progress: float, step: str, message: str)``. Progress values are in
# [0.0, 1.0] and always monotonically non-decreasing across a single run.
ProgressReporter = Callable[[float, str, str], None]


def _noop_progress(_progress: float, _step: str, _message: str) -> None:
    """Default progress reporter: does nothing."""
    return None
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
    CV_PARALLEL_WORKERS,
    OUTPUT_DIR,
    SNAPSHOT_STRATEGY,
    ENABLE_FRAME_PREPROCESSING,
)
from src.frame_selector import select_frames_by_clustering
from src.frame_preprocessor import preprocess_frame
from src.config_generator import write_client_configs
from src.rekognition_labeler import RekognitionLabeler

logger = logging.getLogger(__name__)


class Pipeline:
    """End-to-end pipeline for video processing and object labeling."""

    def __init__(
        self,
        snapshot_strategy: str = "naive",
        max_snapshots: int = 3,
        cleanup_frames: bool = False,  # Changed default to False for V2
        use_cv_labeler: bool = True,  # Use V2 CV pipeline by default
        native_fps: Optional[float] = None,
    ):
        """Initialize Pipeline with all components.

        Args:
            snapshot_strategy: Strategy for snapshot selection ('naive' or 'scene_change')
            max_snapshots: Maximum number of snapshots to select per video
            cleanup_frames: Whether to delete temporary frame files after processing
            use_cv_labeler: If True, use V2 CV pipeline; if False, use V1 LLM pipeline
            native_fps: Optional dense-sampling rate (Hz) for V2 pre-selection.
                None = use default ``2 Hz`` (500 ms interval). The higher
                this is, the more candidate frames the CV labeler sees.
        """
        self.video_loader = VideoLoader()
        self.snapshot_strategy = snapshot_strategy
        self.max_snapshots = max_snapshots
        self.native_fps = native_fps
        self.snapshot_selector = SnapshotSelector(strategy=snapshot_strategy, max_snapshots=max_snapshots)
        self.frame_extractor = FrameExtractor()

        logger.info(f"Pipeline configured with max_snapshots={max_snapshots}, strategy={snapshot_strategy}")

        # Choose labeler based on version
        self.use_cv_labeler = use_cv_labeler
        if use_cv_labeler:
            # Note: CVLabeler will be re-initialized per video with camera-specific profile
            self.cv_labeler = None  # Will be set in process_video
            self.rekognition = RekognitionLabeler()
            self.frame_annotator = FrameAnnotator()
            self.frame_refiner = FrameRefiner()
            self.hazard_assessor = HazardAssessor()
            logger.info("Using V3 pipeline: CV + Rekognition + Per-Frame Refinement + Hazard Assessor")
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

    # Last-run artefacts populated by process_video so callers can find
    # selected/annotated per-frame paths without re-running the pipeline.
    last_selected_frames: Dict[int, str] = {}
    last_annotated_frames: Dict[int, str] = {}
    last_frame_timestamps: Dict[int, float] = {}
    # Per-frame extraction manifest (rich metadata: dimensions, source,
    # status, substitution info). Populated by ``process_video`` so the
    # API server can embed it in the per-frame JSON exposed to the UI.
    last_extraction_manifest: List[Dict] = []

    def process_video(
        self,
        video_path: str,
        is_train: bool = False,
        progress_cb: Optional[ProgressReporter] = None,
    ) -> Optional[str]:
        """Process a single video through the full pipeline.

        Args:
            video_path: Path to video file
            is_train: Whether this is a training video
            progress_cb: Optional reporter invoked with
                ``(progress, step, message)`` at each granular stage so the
                caller can translate the updates into DynamoDB rows or UI
                messages. ``progress`` is in [0.0, 1.0] and is
                monotonically non-decreasing for the lifetime of one run.

        Returns:
            Path to output JSON file, or None if processing failed
        """
        report: ProgressReporter = progress_cb or _noop_progress

        # Reset per-run artefacts
        self.last_selected_frames = {}
        self.last_annotated_frames = {}
        self.last_frame_timestamps = {}
        self.last_extraction_manifest = []
        logger.info(f"{'='*80}")
        logger.info(f"Processing video: {video_path} (train={is_train})")
        logger.info(f"{'='*80}")

        try:
            report(0.05, "loading_video", "Loading video metadata")
            logger.info("Step 1: Loading video metadata")
            video_metadata = self.video_loader.load_video_metadata(video_path)

            if self.use_cv_labeler:
                # Initialize camera-specific CV labeler
                video_filename = Path(video_path).name
                camera_type = detect_camera_from_filename(video_filename)
                camera_profile = get_camera_profile(camera_type)
                logger.info(f"Detected camera type: {camera_type}, using profile: {camera_profile.name}")
                self.cv_labeler = CVLabeler(camera_profile=camera_profile)

                # Honour the user's ``native_fps`` — front-end sends e.g. 2
                # for "2 Hz sampling" which corresponds to a 500 ms
                # interval. We bound the interval to [100 ms, 2000 ms]
                # so a pathological value can't explode ML cost or
                # starve the pipeline of data.
                interval_ms = 500
                if self.native_fps and self.native_fps > 0:
                    interval_ms = int(1000.0 / self.native_fps)
                    interval_ms = max(100, min(2000, interval_ms))
                report(0.10, "sampling_frames",
                       f"Sampling frames every {interval_ms} ms from "
                       f"{int(video_metadata.duration_ms / 1000)}s video")
                all_snapshots = self._generate_all_frame_snapshots(
                    video_metadata, interval_ms=interval_ms,
                )

                report(0.15, "extracting_frames",
                       f"Extracting {len(all_snapshots)} candidate frames")
                extraction = self.frame_extractor.extract_frames_with_manifest(
                    video_metadata,
                    all_snapshots,
                )
                all_extracted_frames = extraction.frames
                # Persist manifest snapshot on self so _process_v2 and
                # callers can surface per-frame extraction metadata.
                self.last_extraction_manifest = [
                    {
                        "frame_idx": e.frame_idx,
                        "timestamp_ms": e.timestamp_ms,
                        "source": e.source,
                        "status": e.status,
                        "width": e.width,
                        "height": e.height,
                        "decoded_idx": e.decoded_idx,
                        "error": e.error,
                    }
                    for e in extraction.manifest
                ]

                if not all_extracted_frames:
                    logger.error("No frames extracted, aborting")
                    return None

                all_objects, hazard_events, inferred_metadata = self._process_v2(
                    all_snapshots, all_extracted_frames, video_metadata,
                    progress_cb=report,
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

            report(0.93, "writing_output", "Merging and saving output JSON")
            logger.info("Step 5: Merging and saving output")
            output_path = self.merger.merge_and_save(
                video_metadata, all_objects, hazard_events, inferred_metadata if self.use_cv_labeler else {}
            )

            # Inject the Rekognition audit trail into output.json so every
            # completed run carries proof that managed-vision inference was
            # actually invoked. The audit is authored by ``RekognitionLabeler``
            # one entry per frame (raw label names, confidence, kept count,
            # latency) and tests assert on its presence.
            import json as _json
            if self.use_cv_labeler and self.rekognition is not None:
                try:
                    with open(output_path, "r", encoding="utf-8") as fp:
                        output_doc = _json.load(fp)
                    audit = self.rekognition.build_audit()
                    output_doc["rekognition_audit"] = {
                        "frames_evaluated": len(audit),
                        "total_instances_kept": sum(
                            int(entry.get("kept_instances", 0)) for entry in audit
                        ),
                        "region": getattr(self.rekognition, "region_name", None),
                        "min_confidence": getattr(self.rekognition, "min_confidence", None),
                        "per_frame": audit,
                    }
                    with open(output_path, "w", encoding="utf-8") as fp:
                        _json.dump(output_doc, fp, indent=2)
                except Exception as audit_err:
                    logger.warning("Failed to embed rekognition_audit: %s", audit_err)

            # Get summary stats
            from src.schemas import VideoOutput
            with open(output_path, 'r') as f:
                import json
                data = json.load(f)
                video_output = VideoOutput(**data)

            summary = self.merger.get_summary_stats(video_output)
            logger.info(f"Summary: {summary}")

            # Step 5b: Generate client-ready config families alongside the
            # core output JSON so downstream consumers can pull ready-to-use
            # reactivity/educational/hazard/jobsite configs.
            try:
                configs_dir = Path(output_path).parent / "client_configs"
                written = write_client_configs(video_output, configs_dir)
                logger.info(f"Wrote client configs: {list(written)}")
            except Exception as cfg_err:  # noqa: BLE001
                logger.warning(f"Client config generation failed: {cfg_err}")

            # Step 6: Cleanup temporary frames
            if self.cleanup_frames and 'all_extracted_frames' in locals():
                logger.info("Step 6: Cleaning up temporary frames")
                frame_paths = list(all_extracted_frames.values())
                self.frame_extractor.cleanup_frames(frame_paths)
            elif self.cleanup_frames and 'extracted_frames' in locals():
                logger.info("Step 6: Cleaning up temporary frames")
                frame_paths = list(extracted_frames.values())
                self.frame_extractor.cleanup_frames(frame_paths)

            report(0.97, "finalizing", "Finalising output")
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
        video_metadata: VideoMetadata,
        progress_cb: Optional[ProgressReporter] = None,
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
        report: ProgressReporter = progress_cb or _noop_progress

        # Step 4a: CV Labeling on ALL frames (parallelized)
        logger.info(f"Step 4a: CV Labeling ALL frames in parallel")
        report(0.20, "detecting_objects",
               f"Running CV detection on {len(snapshots)} frames")
        all_frame_objects: Dict[int, List[ObjectLabel]] = {}

        def process_frame(snapshot):
            """Process a single frame with optional preprocessing + CV labeler."""
            frame_path = extracted_frames.get(snapshot.frame_idx)
            if not frame_path:
                logger.warning(f"Frame not found for snapshot at {snapshot.timestamp_ms}ms")
                return snapshot.frame_idx, []

            if ENABLE_FRAME_PREPROCESSING:
                import cv2 as _cv2
                raw = _cv2.imread(frame_path)
                if raw is not None:
                    preprocessed = preprocess_frame(raw)
                    _cv2.imwrite(frame_path, preprocessed)

            objects = self.cv_labeler.label_frame(
                frame_path,
                snapshot.timestamp_ms,
                video_metadata.width,
                video_metadata.height
            )

            return snapshot.frame_idx, objects

        # Process frames sequentially (YOLO not thread-safe with some models)
        logger.info(f"Processing {len(snapshots)} frames sequentially")

        total_snaps = max(1, len(snapshots))
        for i, snapshot in enumerate(snapshots):
            try:
                frame_idx, objects = process_frame(snapshot)
                all_frame_objects[frame_idx] = objects

                if (i + 1) % 5 == 0 or (i + 1) == len(snapshots):
                    logger.info(f"Progress: {i + 1}/{len(snapshots)} frames processed")
                # Report CV progress monotonically in [0.20, 0.50]
                frac = (i + 1) / total_snaps
                report(
                    0.20 + 0.30 * frac,
                    "detecting_objects",
                    f"Detected objects on {i + 1}/{total_snaps} frames",
                )
            except Exception as e:
                logger.error(f"Error processing frame {snapshot.frame_idx}: {e}", exc_info=True)
                all_frame_objects[snapshot.frame_idx] = []

        logger.info(f"CV labeled {len(all_frame_objects)} frames")

        report(0.50, "selecting_frames", "Selecting key frames")
        # Step 4b: Select N frames using the user's chosen strategy.
        # - scene_change  : use SnapshotSelector._select_scene_change, then
        #                   map its timestamps onto our dense frame grid.
        # - clustering    : HOG+PCA+KMeans diversity selector on the
        #                   dense frame images.
        # - naive (default): rank by number of detections, top N.
        max_snapshots = self.snapshot_selector.max_snapshots
        chosen_strategy = (self.snapshot_strategy or SNAPSHOT_STRATEGY or "naive").lower()
        logger.info(
            "Step 4b: Selecting %d frames with strategy=%s from %d candidates",
            max_snapshots, chosen_strategy, len(all_frame_objects),
        )

        selected_indices: List[int] = []

        if chosen_strategy == "scene_change":
            try:
                sc_snaps = self.snapshot_selector._select_scene_change(video_metadata)
                # Map each scene-change timestamp onto the nearest dense frame
                dense_by_ms = {s.timestamp_ms: s.frame_idx for s in snapshots}
                dense_ms_sorted = sorted(dense_by_ms.keys())
                picked = []
                for sc in sc_snaps:
                    if not dense_ms_sorted:
                        break
                    # nearest dense ms
                    nearest_ms = min(dense_ms_sorted, key=lambda m: abs(m - sc.timestamp_ms))
                    idx = dense_by_ms[nearest_ms]
                    if idx not in picked:
                        picked.append(idx)
                selected_indices = picked[:max_snapshots]
                logger.info("scene_change selected %d frames", len(selected_indices))
            except Exception as e:  # noqa: BLE001
                logger.warning("scene_change selection failed (%s), falling back to ranked", e)
                selected_indices = []

        if chosen_strategy == "clustering" or (chosen_strategy == "scene_change" and not selected_indices):
            try:
                selected_indices = select_frames_by_clustering(
                    extracted_frames, n_select=max_snapshots,
                )
                logger.info("Clustering frame selection succeeded")
            except Exception as e:  # noqa: BLE001
                logger.warning("Clustering failed (%s), falling back to ranked", e)
                selected_indices = []

        if not selected_indices:
            ranked = sorted(
                all_frame_objects.keys(),
                key=lambda idx: len(all_frame_objects[idx]),
                reverse=True,
            )
            selected_indices = ranked[:max_snapshots]
            if len(selected_indices) < max_snapshots:
                remaining = [idx for idx in all_frame_objects.keys() if idx not in selected_indices]
                selected_indices.extend(remaining[: max_snapshots - len(selected_indices)])

        # Always sort selected frames chronologically for UI/render coherence,
        # drop indices whose extracted image is missing/unreadable — this
        # protects the annotator from feeding it frames that never made it
        # through ``FrameExtractor`` validation.
        selected_indices = [
            idx for idx in sorted(set(selected_indices))
            if idx in extracted_frames and os.path.exists(extracted_frames[idx])
        ][:max_snapshots]

        report(0.55, "rekognition",
               f"Running AWS Rekognition on {len(selected_indices)} selected frames")
        # Step 4c: Merge Rekognition labels into selected-frame CV detections.
        # Rekognition adds broad managed-model labels (cones, workers,
        # pedestrians, signs, signals) that YOLO often misses. We only call
        # it on the small final set, not the full pre-selection sweep, to
        # keep the cost bounded.
        try:
            for fidx in selected_indices:
                frame_path = extracted_frames.get(fidx)
                if not frame_path:
                    continue
                # Find matching timestamp for this frame
                ts_ms = 0.0
                for snap in snapshots:
                    if snap.frame_idx == fidx:
                        ts_ms = snap.timestamp_ms
                        break
                rk_objs = self.rekognition.detect(
                    frame_path=frame_path,
                    timestamp_ms=ts_ms,
                    video_width=video_metadata.width,
                    video_height=video_metadata.height,
                )
                if rk_objs:
                    existing = all_frame_objects.setdefault(fidx, [])
                    existing.extend(rk_objs)
                    logger.info(
                        "Rekognition added %d detections on frame %d (total now %d)",
                        len(rk_objs), fidx, len(existing),
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("Rekognition merge failed: %s", e)

        logger.info(
            f"Selected {len(selected_indices)}/{len(all_frame_objects)} frames "
            f"(requested {max_snapshots}). Object counts: "
            f"{[len(all_frame_objects.get(i, [])) for i in selected_indices]}"
        )

        if not selected_indices:
            logger.warning("No frames available for selection!")
            return [], [], {}

        frames_with_objects = {
            idx: all_frame_objects[idx] for idx in selected_indices
        }

        selected_frame_indices = selected_indices

        # Build selected frame_objects dict
        selected_frame_objects = {
            idx: frames_with_objects[idx] for idx in selected_frame_indices
        }

        # Build selected frame_images dict
        selected_frame_images = {
            idx: extracted_frames[idx] for idx in selected_frame_indices
            if idx in extracted_frames
        }

        logger.info(f"Selected {len(selected_frame_objects)} frames for processing")

        # Step 4d: Per-Frame LLM Refinement with retry/fallback
        logger.info("Step 4d: Per-frame LLM refinement with retry/fallback logic")
        report(0.65, "refining_frames",
               f"Refining {len(selected_frame_indices)} frames with LLM")
        refined_frame_objects: Dict[int, List[ObjectLabel]] = {}
        used_frames = set()
        output_dir = Path(OUTPUT_DIR) / "temp_annotations"
        output_dir.mkdir(parents=True, exist_ok=True)

        total_sel = max(1, len(selected_frame_indices))
        for refine_i, frame_idx in enumerate(selected_frame_indices):
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

                # Refinement failed - mark this frame as used (so it isn't
                # picked as its own nearest neighbor) and try fallback if enabled.
                used_frames.add(current_frame_idx)
                if FRAME_REFINER_FALLBACK_ENABLED:
                    nearest_frame = self._find_nearest_unused_frame(
                        current_frame_idx,
                        frames_with_objects,
                        used_frames
                    )

                    if nearest_frame is not None and nearest_frame != current_frame_idx:
                        logger.info(f"Falling back from frame {current_frame_idx} to frame {nearest_frame}")
                        current_frame_idx = nearest_frame
                        continue  # Try again with fallback frame
                    else:
                        logger.warning(f"No fallback frames available for frame {current_frame_idx}")

                # No success and no fallback available - keep CV-only results
                logger.warning(f"Using CV-only results for frame {current_frame_idx}")
                refined_frame_objects[current_frame_idx] = objects
                break

            # Report refine progress monotonically in [0.65, 0.80].
            frac = (refine_i + 1) / total_sel
            report(
                0.65 + 0.15 * frac,
                "refining_frames",
                f"Refined {refine_i + 1}/{total_sel} frames",
            )

        logger.info(f"Per-frame refinement complete: {len(refined_frame_objects)} frames processed")

        # Step 4e: Re-annotate frames with refined objects (for temporal LLM + delivery)
        logger.info("Step 4e: Re-annotating frames with refined objects")
        report(0.82, "annotating_frames",
               f"Drawing annotations on {len(refined_frame_objects)} frames")
        refined_frame_images: Dict[int, str] = {}

        # Create delivery annotated frames directory (direct save for customer)
        video_name = Path(video_metadata.filename).stem
        annotated_output_dir = Path(OUTPUT_DIR) / "delivery" / "annotated_frames" / video_name
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

            # Record for callers (so API can persist per-frame images + JSON)
            self.last_selected_frames[frame_idx] = frame_path
            self.last_annotated_frames[frame_idx] = refined_frame_images[frame_idx]
            self.last_frame_timestamps[frame_idx] = timestamp_ms

        # Step 4f: Hazard Assessment (temporal LLM - hazard events only)
        logger.info("Step 4f: Assessing hazards with temporal LLM (hazard events only)")
        report(0.88, "assessing_hazards", "Assessing hazards with temporal LLM")

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

        # Quality check on inferred metadata
        quality_fields = ["description", "traffic", "lighting", "weather", "speed"]
        populated = sum(
            1
            for f in quality_fields
            if inferred_metadata.get(f)
            and str(inferred_metadata[f]).strip()
            and str(inferred_metadata[f]).strip().lower() != "unknown"
        )
        quality_pct = (populated / len(quality_fields)) * 100
        if quality_pct < 50:
            logger.warning(
                "Low quality classification (%d/%d fields populated, %.0f%%)",
                populated, len(quality_fields), quality_pct,
            )

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

