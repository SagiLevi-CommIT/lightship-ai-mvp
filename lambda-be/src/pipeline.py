"""Pipeline orchestrator for end-to-end video processing.

Coordinates all components to process videos from input to final JSON output.
Supports both V1 (LLM image labeler) and V2 (CV + Temporal LLM) pipelines.
"""
import logging
import os
import time
from typing import Any, Callable, List, Optional, Dict, Tuple
from pathlib import Path

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
from src.hazard_assessor import HazardAssessor
from src.frame_annotator import FrameAnnotator
from src.frame_refiner import FrameRefiner, RefinerStatus
# Note: derive_threat_levels no longer used - LLM assigns threat levels directly
# from src.threat_derivation import derive_threat_levels
from src.merger import Merger
from src.schemas import VideoMetadata, ObjectLabel, HazardEvent, SnapshotInfo
from src.config import (
    FRAME_REFINER_MAX_RETRIES,
    FRAME_REFINER_FALLBACK_ENABLED,
    MAX_FRAMES_PER_VIDEO,
    OUTPUT_DIR,
    SNAPSHOT_STRATEGY,
    ENABLE_FRAME_PREPROCESSING,
    SUBSTITUTED_FRAME_VISION_POLICY,
)
from src.frame_selector import select_frames_by_clustering
from src.frame_sampling import generate_dense_snapshots, generate_uniform_snapshots
from src.frame_preprocessor import preprocess_frame
from src.config_generator import write_client_configs
from src.vision_labeler import VisionLabeler
from src.utils import metrics

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
        native_sampling_mode: str = "count",
        detector_backend: str = "florence2",
        lane_backend: str = "ufldv2",
    ):
        """Initialize Pipeline with all components.

        Args:
            snapshot_strategy: Strategy for snapshot selection ('naive' or 'scene_change')
            max_snapshots: Maximum number of snapshots to select per video
            cleanup_frames: Whether to delete temporary frame files after processing
            use_cv_labeler: If True, use V2 CV pipeline; if False, use V1 LLM pipeline
            native_fps: Optional sampling rate (Hz) for native FPS mode.
                None = native frame-count mode, which samples exactly
                ``max_snapshots`` frames with an even jump through the video.
            native_sampling_mode: ``"count"`` means ``max_snapshots`` final
                frames; ``"fps"`` means dense FPS sampling using
                ``native_fps``.
        """
        self.video_loader = VideoLoader()
        self.snapshot_strategy = snapshot_strategy
        self.max_snapshots = max_snapshots
        self.native_fps = native_fps
        self.native_sampling_mode = (native_sampling_mode or "count").lower()
        self.snapshot_selector = SnapshotSelector(strategy=snapshot_strategy, max_snapshots=max_snapshots)
        self.frame_extractor = FrameExtractor()

        logger.info(f"Pipeline configured with max_snapshots={max_snapshots}, strategy={snapshot_strategy}")

        # Choose labeler based on version
        self.use_cv_labeler = use_cv_labeler
        if use_cv_labeler:
            self.cv_labeler = None  # Legacy attribute retained for compatibility.
            self.vision_labeler = VisionLabeler(
                detector_backend=detector_backend,
                lane_backend=lane_backend,
            )
            self.frame_annotator = FrameAnnotator()
            self.frame_refiner = FrameRefiner()
            self.hazard_assessor = HazardAssessor()
            logger.info("Using V3 pipeline: CV + VisionLabeler (Florence-2+UFLDv2) + Refinement + Hazard Assessor")
        else:
            self.scene_labeler = SceneLabeler()
            logger.info("Using V1 pipeline: LLM Scene Labeler")

        self.merger = Merger()
        self.cleanup_frames = cleanup_frames

        logger.info(
            f"Pipeline initialized (strategy={snapshot_strategy}, "
            f"max_snapshots={max_snapshots}, native_sampling_mode={self.native_sampling_mode}, "
            f"cleanup={cleanup_frames}, "
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
    last_timing_ms: Dict[str, float] = {}

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
        self.last_timing_ms = {}
        logger.info(f"{'='*80}")
        logger.info(f"Processing video: {video_path} (train={is_train})")
        logger.info(f"{'='*80}")

        try:
            report(0.05, "loading_video", "Loading video metadata")
            logger.info("Step 1: Loading video metadata")
            video_metadata = self.video_loader.load_video_metadata(video_path)

            if self.use_cv_labeler:
                # V2 is now final-frame-first: decide the final timestamps
                # before any detector is invoked. This keeps YOLO/Detectron2/
                # Florence-2 bounded to the frames the user actually asked for.
                report(0.10, "selecting_frames", "Selecting final frames")
                final_snapshots, all_extracted_frames = self._prepare_v2_final_frames(
                    video_metadata,
                    report,
                )

                if not all_extracted_frames:
                    logger.error("No frames extracted, aborting")
                    return None

                all_objects, hazard_events, inferred_metadata = self._process_v2_final_frames(
                    final_snapshots, all_extracted_frames, video_metadata,
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

            # Inject the VisionLabeler audit trail into output.json so every
            # completed run carries a per-frame record of which backend ran,
            # what it returned, and whether the Detectron2 fallback was used.
            import json as _json
            if self.use_cv_labeler and self.vision_labeler is not None:
                try:
                    with open(output_path, "r", encoding="utf-8") as fp:
                        output_doc = _json.load(fp)
                    per_frame = self.vision_labeler.build_audit()
                    configured_backend = getattr(
                        self.vision_labeler, "detector_backend", "unknown",
                    )
                    output_doc["vision_audit"] = {
                        "frames_evaluated": len(per_frame),
                        "total_instances_kept": sum(
                            int(e.get("primary_kept_instances", 0))
                            + int(e.get("fallback_kept_instances") or 0)
                            for e in per_frame
                        ),
                        "backend": configured_backend,
                        "lane_backend": getattr(self.vision_labeler, "lane_backend", "unknown"),
                        "fallback_triggered_count": sum(
                            1 for e in per_frame if e.get("fallback_used")
                        ),
                        "per_frame": per_frame,
                    }
                    output_doc["pipeline_timings_ms"] = dict(self.last_timing_ms)
                    with open(output_path, "w", encoding="utf-8") as fp:
                        _json.dump(output_doc, fp, indent=2)
                except Exception as audit_err:
                    logger.warning("Failed to embed vision_audit: %s", audit_err)

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
        snapshots = generate_dense_snapshots(
            video_metadata,
            interval_ms=interval_ms,
            max_frames=MAX_FRAMES_PER_VIDEO,
        )
        logger.info(f"Generated {len(snapshots)} snapshots for full video analysis")
        return snapshots

    def _record_timing(self, stage: str, elapsed_ms: float, **fields: Any) -> None:
        """Record a human-readable timing line and CloudWatch EMF metric."""
        elapsed = round(float(elapsed_ms), 1)
        self.last_timing_ms[stage] = elapsed
        suffix = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        logger.info(
            "TIMING stage=%s elapsed_ms=%.1f%s",
            stage,
            elapsed,
            f" {suffix}" if suffix else "",
        )
        try:
            metric_name = "".join(part.capitalize() for part in stage.split("_")) + "Ms"
            dims = {
                str(k): str(v)
                for k, v in fields.items()
                if k in {"strategy", "backend", "mode", "enabled"}
            }
            dims.setdefault("Stage", stage)
            metrics.duration_ms(metric_name, elapsed, dimensions=dims)
        except Exception:
            pass

    def _native_fps_mode_enabled(self, chosen_strategy: str) -> bool:
        """Return True only for explicit Native -> FPS mode."""
        return (
            chosen_strategy == "naive"
            and self.native_sampling_mode == "fps"
            and self.native_fps is not None
            and self.native_fps > 0
        )

    @staticmethod
    def _manifest_entry_to_dict(entry) -> Dict[str, Any]:  # noqa: ANN001
        return {
            "frame_idx": entry.frame_idx,
            "timestamp_ms": entry.timestamp_ms,
            "source": entry.source,
            "status": entry.status,
            "width": entry.width,
            "height": entry.height,
            "decoded_idx": entry.decoded_idx,
            "error": entry.error,
        }

    def _dedupe_snapshots(self, snapshots: List[SnapshotInfo]) -> List[SnapshotInfo]:
        seen = set()
        deduped: List[SnapshotInfo] = []
        for snapshot in sorted(snapshots, key=lambda s: (s.timestamp_ms, s.frame_idx)):
            idx = int(snapshot.frame_idx)
            if idx in seen:
                continue
            seen.add(idx)
            deduped.append(snapshot)
        return deduped

    def _fill_to_requested_count(
        self,
        video_metadata: VideoMetadata,
        snapshots: List[SnapshotInfo],
        requested_count: int,
    ) -> List[SnapshotInfo]:
        """Backfill selection without using detector results."""
        requested = max(1, int(requested_count))
        selected = self._dedupe_snapshots(snapshots)[:requested]
        if len(selected) >= requested:
            return selected

        seen = {int(s.frame_idx) for s in selected}
        for candidate in generate_uniform_snapshots(video_metadata, requested):
            idx = int(candidate.frame_idx)
            if idx in seen:
                continue
            seen.add(idx)
            selected.append(
                SnapshotInfo(
                    frame_idx=candidate.frame_idx,
                    timestamp_ms=candidate.timestamp_ms,
                    reason=f"{candidate.reason}; detector-free backfill",
                ),
            )
            if len(selected) >= requested:
                break

        selected = self._dedupe_snapshots(selected)[:requested]
        if len(selected) < requested:
            logger.warning(
                "Frame count below requested after detector-free selection: got %d / %d",
                len(selected),
                requested,
            )
        return selected

    def _prepare_v2_final_frames(
        self,
        video_metadata: VideoMetadata,
        report: ProgressReporter,
    ) -> Tuple[List[SnapshotInfo], Dict[int, str]]:
        """Select final V2 frames first, then extract only those frames.

        The only exception is clustering, whose selector needs decoded stills
        for image features. Even there, no detector runs before final indices
        are chosen.
        """
        chosen_strategy = (self.snapshot_strategy or SNAPSHOT_STRATEGY or "naive").lower()
        requested_count = max(1, int(self.max_snapshots or 1))
        selection_started = time.monotonic()

        if chosen_strategy == "clustering":
            candidate_snapshots = self._generate_all_frame_snapshots(
                video_metadata,
                interval_ms=500,
            )
            self._record_timing(
                "selection_candidates",
                (time.monotonic() - selection_started) * 1000.0,
                strategy=chosen_strategy,
                frames=len(candidate_snapshots),
            )

            report(
                0.15,
                "extracting_frames",
                f"Extracting {len(candidate_snapshots)} candidate frames for clustering",
            )
            extraction_started = time.monotonic()
            extraction = self.frame_extractor.extract_frames_with_manifest(
                video_metadata,
                candidate_snapshots,
            )
            self._record_timing(
                "frame_extraction",
                (time.monotonic() - extraction_started) * 1000.0,
                strategy=chosen_strategy,
                frames=len(candidate_snapshots),
            )

            cluster_started = time.monotonic()
            selected_indices: List[int] = []
            try:
                selected_indices = select_frames_by_clustering(
                    extraction.frames,
                    n_select=requested_count,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Clustering failed (%s), falling back to uniform frames", exc)

            if not selected_indices:
                selected_indices = [
                    s.frame_idx for s in generate_uniform_snapshots(video_metadata, requested_count)
                ]

            selected_indices = [
                idx for idx in sorted(set(selected_indices))
                if idx in extraction.frames and os.path.exists(extraction.frames[idx])
            ][:requested_count]
            snapshot_by_idx = {int(s.frame_idx): s for s in candidate_snapshots}
            final_snapshots = [
                snapshot_by_idx[idx]
                for idx in selected_indices
                if idx in snapshot_by_idx
            ]
            final_frames = {idx: extraction.frames[idx] for idx in selected_indices}
            selected_set = set(selected_indices)
            self.last_extraction_manifest = [
                self._manifest_entry_to_dict(e)
                for e in extraction.manifest
                if int(e.frame_idx) in selected_set
            ]
            self._record_timing(
                "selection",
                (time.monotonic() - cluster_started) * 1000.0,
                strategy=chosen_strategy,
                frames=len(final_snapshots),
            )
            report(
                0.18,
                "selecting_frames",
                f"Selected {len(final_snapshots)} final frame(s)",
            )
            return final_snapshots, final_frames

        if chosen_strategy == "scene_change":
            final_snapshots = self.snapshot_selector._select_scene_change(video_metadata)
            final_snapshots = self._fill_to_requested_count(
                video_metadata,
                final_snapshots,
                requested_count,
            )
            mode = "scene_change"
        elif chosen_strategy == "naive" and self._native_fps_mode_enabled(chosen_strategy):
            interval_ms = int(1000.0 / float(self.native_fps))
            interval_ms = max(100, min(2000, interval_ms))
            final_snapshots = self._generate_all_frame_snapshots(
                video_metadata,
                interval_ms=interval_ms,
            )
            mode = "fps"
        else:
            if chosen_strategy == "naive" and self.native_fps and self.native_sampling_mode != "fps":
                logger.info(
                    "Ignoring native_fps=%s because native_sampling_mode=%s; using frame-count mode",
                    self.native_fps,
                    self.native_sampling_mode,
                )
            if chosen_strategy not in {"naive", "scene_change"}:
                logger.warning("Unknown snapshot strategy %s, falling back to native count", chosen_strategy)
            final_snapshots = generate_uniform_snapshots(video_metadata, requested_count)
            mode = "count"

        final_snapshots = self._dedupe_snapshots(final_snapshots)
        if chosen_strategy != "naive" or mode != "fps":
            final_snapshots = final_snapshots[:requested_count]

        self._record_timing(
            "selection",
            (time.monotonic() - selection_started) * 1000.0,
            strategy=chosen_strategy,
            mode=mode,
            frames=len(final_snapshots),
        )
        report(
            0.15,
            "extracting_frames",
            f"Extracting {len(final_snapshots)} final frame(s)",
        )

        extraction_started = time.monotonic()
        extraction = self.frame_extractor.extract_frames_with_manifest(
            video_metadata,
            final_snapshots,
        )
        self._record_timing(
            "frame_extraction",
            (time.monotonic() - extraction_started) * 1000.0,
            strategy=chosen_strategy,
            mode=mode,
            frames=len(final_snapshots),
        )
        self.last_extraction_manifest = [
            self._manifest_entry_to_dict(e) for e in extraction.manifest
        ]
        return final_snapshots, extraction.frames

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

    def _process_v2_final_frames(
        self,
        snapshots,
        extracted_frames: Dict[int, str],
        video_metadata: VideoMetadata,
        progress_cb: Optional[ProgressReporter] = None,
    ) -> tuple[List[ObjectLabel], List[HazardEvent], Dict]:
        """Run detector/LLM stages on final selected frames only."""
        report: ProgressReporter = progress_cb or _noop_progress
        snap_ts: Dict[int, float] = {int(s.frame_idx): float(s.timestamp_ms) for s in snapshots}
        selected_frame_indices = [
            int(s.frame_idx)
            for s in sorted(snapshots, key=lambda item: item.timestamp_ms)
            if int(s.frame_idx) in extracted_frames
            and os.path.exists(extracted_frames[int(s.frame_idx)])
        ]
        selected_frame_indices = list(dict.fromkeys(selected_frame_indices))

        if not selected_frame_indices:
            logger.warning("No final frames available for V2 processing")
            return [], [], {}

        logger.info(
            "Step 4a: Running VisionLabeler on %d final frame(s) only",
            len(selected_frame_indices),
        )
        report(
            0.20,
            "detecting_objects",
            f"Running {self.vision_labeler.detector_backend} on "
            f"{len(selected_frame_indices)} final frame(s)",
        )

        substituted_idxs = {
            int(e["frame_idx"])
            for e in self.last_extraction_manifest
            if e.get("status") == "substituted"
        }
        manifest_by_idx: Dict[int, Dict[str, Any]] = {
            int(e["frame_idx"]): e for e in self.last_extraction_manifest
        }

        if getattr(self.vision_labeler, "lane_backend", "") == "opencv":
            logger.warning(
                "lane_backend=opencv no longer invokes the legacy CVLabeler pre-sweep; "
                "select lane_backend=ufldv2 when lane output is required",
            )

        all_frame_objects: Dict[int, List[ObjectLabel]] = {
            idx: [] for idx in selected_frame_indices
        }
        audit_start = len(self.vision_labeler.build_audit())
        detector_started = time.monotonic()
        total_selected = max(1, len(selected_frame_indices))

        for detect_i, frame_idx in enumerate(selected_frame_indices):
            frame_path = extracted_frames.get(frame_idx)
            if not frame_path:
                continue

            if (
                SUBSTITUTED_FRAME_VISION_POLICY == "skip"
                and frame_idx in substituted_idxs
            ):
                row = manifest_by_idx.get(frame_idx)
                if row is not None:
                    row["vision_skipped"] = True
                    row["vision_skip_reason"] = "substituted_frame"
                logger.info(
                    "Skipping VisionLabeler for frame %d (substituted; policy=skip)",
                    frame_idx,
                )
            else:
                if ENABLE_FRAME_PREPROCESSING:
                    import cv2 as _cv2

                    raw = _cv2.imread(frame_path)
                    if raw is not None:
                        preprocessed = preprocess_frame(raw)
                        _cv2.imwrite(frame_path, preprocessed)

                timestamp_ms = snap_ts.get(frame_idx, 0.0)
                frame_started = time.monotonic()
                try:
                    all_frame_objects[frame_idx] = self.vision_labeler.detect(
                        frame_path=frame_path,
                        timestamp_ms=timestamp_ms,
                        video_width=video_metadata.width,
                        video_height=video_metadata.height,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "VisionLabeler failed on final frame %d: %s",
                        frame_idx,
                        exc,
                    )
                    all_frame_objects[frame_idx] = []
                self._record_timing(
                    "vision_frame",
                    (time.monotonic() - frame_started) * 1000.0,
                    backend=self.vision_labeler.detector_backend,
                    frame_idx=frame_idx,
                )

            frac = (detect_i + 1) / total_selected
            report(
                0.20 + 0.40 * frac,
                "detecting_objects",
                f"Detected objects on {detect_i + 1}/{total_selected} final frame(s)",
            )

        detector_elapsed_ms = (time.monotonic() - detector_started) * 1000.0
        audit_records = self.vision_labeler.build_audit()[audit_start:]
        primary_ms = sum(float(r.get("primary_elapsed_ms") or 0.0) for r in audit_records)
        lane_ms = sum(float(r.get("lane_elapsed_ms") or 0.0) for r in audit_records)
        backend = getattr(self.vision_labeler, "detector_backend", "unknown")
        self._record_timing(
            "vision_detector",
            detector_elapsed_ms,
            backend=backend,
            frames=len(audit_records),
        )
        self._record_timing(
            f"{backend}_detection",
            primary_ms,
            backend=backend,
            frames=len(audit_records),
        )
        self._record_timing(
            "lane_detection",
            lane_ms,
            backend=getattr(self.vision_labeler, "lane_backend", "unknown"),
            frames=len(audit_records),
        )
        self._record_timing("depth", 0.0, enabled=False, frames=0)

        logger.info(
            "Selected final frames: %s. Object counts: %s",
            selected_frame_indices,
            [len(all_frame_objects.get(i, [])) for i in selected_frame_indices],
        )

        frames_with_objects = {
            idx: all_frame_objects.get(idx, []) for idx in selected_frame_indices
        }
        selected_frame_objects = {
            idx: frames_with_objects[idx] for idx in selected_frame_indices
        }
        selected_frame_images = {
            idx: extracted_frames[idx] for idx in selected_frame_indices
            if idx in extracted_frames
        }
        logger.info(f"Selected {len(selected_frame_objects)} frames for processing")

        logger.info("Step 4b: Per-frame LLM refinement with retry/fallback logic")
        report(
            0.65,
            "refining_frames",
            f"Refining {len(selected_frame_indices)} frames with LLM",
        )
        refine_started = time.monotonic()
        refined_frame_objects: Dict[int, List[ObjectLabel]] = {}
        used_frames = set()
        output_dir = Path(OUTPUT_DIR) / "temp_annotations"
        output_dir.mkdir(parents=True, exist_ok=True)

        total_sel = max(1, len(selected_frame_indices))
        for refine_i, frame_idx in enumerate(selected_frame_indices):
            current_frame_idx = frame_idx
            success = False

            while not success:
                frame_path = extracted_frames[current_frame_idx]
                objects = frames_with_objects[current_frame_idx]
                timestamp_ms = snap_ts.get(current_frame_idx, float(current_frame_idx * 1000))

                refined_objects, success = self._refine_frame_with_retries(
                    current_frame_idx,
                    frame_path,
                    objects,
                    timestamp_ms,
                    output_dir,
                )

                if success:
                    refined_frame_objects[current_frame_idx] = refined_objects
                    used_frames.add(current_frame_idx)
                    logger.info(
                        "Frame %d: %d -> %d objects",
                        current_frame_idx,
                        len(objects),
                        len(refined_objects),
                    )
                    break

                used_frames.add(current_frame_idx)
                if FRAME_REFINER_FALLBACK_ENABLED:
                    nearest_frame = self._find_nearest_unused_frame(
                        current_frame_idx,
                        frames_with_objects,
                        used_frames,
                    )

                    if nearest_frame is not None and nearest_frame != current_frame_idx:
                        logger.info(
                            "Falling back from frame %d to frame %d",
                            current_frame_idx,
                            nearest_frame,
                        )
                        current_frame_idx = nearest_frame
                        continue
                    logger.warning("No fallback frames available for frame %d", current_frame_idx)

                logger.warning("Using detector-only results for frame %d", current_frame_idx)
                refined_frame_objects[current_frame_idx] = objects
                break

            frac = (refine_i + 1) / total_sel
            report(
                0.65 + 0.15 * frac,
                "refining_frames",
                f"Refined {refine_i + 1}/{total_sel} frames",
            )

        refine_elapsed_ms = (time.monotonic() - refine_started) * 1000.0
        self._record_timing(
            "llm_refinement",
            refine_elapsed_ms,
            frames=len(refined_frame_objects),
        )
        logger.info(
            "Per-frame refinement complete: %d frames processed",
            len(refined_frame_objects),
        )

        logger.info("Step 4c: Re-annotating frames with refined objects")
        report(
            0.82,
            "annotating_frames",
            f"Drawing annotations on {len(refined_frame_objects)} frames",
        )
        annotation_started = time.monotonic()
        refined_frame_images: Dict[int, str] = {}

        video_name = Path(video_metadata.filename).stem
        annotated_output_dir = Path(OUTPUT_DIR) / "delivery" / "annotated_frames" / video_name
        os.makedirs(annotated_output_dir, exist_ok=True)
        logger.info(f"Annotated frames will be saved to: {annotated_output_dir}")

        for frame_idx, refined_objs in refined_frame_objects.items():
            frame_path = extracted_frames[frame_idx]
            timestamp_ms = snap_ts.get(frame_idx, float(frame_idx * 1000))
            permanent_annotated_path = str(
                annotated_output_dir
                / f"{video_name}_frame_{frame_idx}_{int(timestamp_ms)}ms_annotated.png"
            )

            try:
                self.frame_annotator.annotate_frame(
                    frame_path,
                    refined_objs,
                    permanent_annotated_path,
                    timestamp_ms,
                )
                refined_frame_images[frame_idx] = permanent_annotated_path
                logger.info(f"Saved annotated frame to: {permanent_annotated_path}")
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to re-annotate frame %d: %s", frame_idx, exc)
                refined_frame_images[frame_idx] = frame_path

            self.last_selected_frames[frame_idx] = frame_path
            self.last_annotated_frames[frame_idx] = refined_frame_images[frame_idx]
            self.last_frame_timestamps[frame_idx] = timestamp_ms

        self._record_timing(
            "annotation",
            (time.monotonic() - annotation_started) * 1000.0,
            frames=len(refined_frame_images),
        )

        logger.info("Step 4d: Assessing hazards with temporal LLM")
        report(0.88, "assessing_hazards", "Assessing hazards with temporal LLM")
        hazard_started = time.monotonic()

        video_metadata_dict = {
            "camera": video_metadata.camera,
            "fps": video_metadata.fps,
            "duration_ms": video_metadata.duration_ms,
        }

        hazard_events, final_objects, inferred_metadata = self.hazard_assessor.assess_hazards_only(
            frame_objects=refined_frame_objects,
            frame_images=refined_frame_images,
            video_metadata=video_metadata_dict,
        )

        hazard_elapsed_ms = (time.monotonic() - hazard_started) * 1000.0
        self._record_timing("llm_hazard", hazard_elapsed_ms, frames=len(refined_frame_objects))
        self._record_timing(
            "llm_total",
            refine_elapsed_ms + hazard_elapsed_ms,
            frames=len(refined_frame_objects),
        )

        logger.info(f"Identified {len(hazard_events)} hazard events")
        logger.info(f"Final output: {len(final_objects)} objects")
        logger.info(f"LLM inferred video metadata: {inferred_metadata}")

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
                populated,
                len(quality_fields),
                quality_pct,
            )

        return final_objects, hazard_events, inferred_metadata

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
        logger.warning(
            "_process_v2 legacy pre-sweep path was called directly; "
            "treating provided snapshots as final frames",
        )
        return self._process_v2_final_frames(
            snapshots,
            extracted_frames,
            video_metadata,
            progress_cb=progress_cb,
        )

        report: ProgressReporter = progress_cb or _noop_progress

        # Step 4a: CV Labeling on ALL frames (parallelized)
        logger.info(f"Step 4a: CV Labeling ALL frames in parallel")
        report(0.20, "detecting_objects",
               f"Running CV detection on {len(snapshots)} frames")
        all_frame_objects: Dict[int, List[ObjectLabel]] = {}

        substituted_idxs = {
            int(e["frame_idx"])
            for e in self.last_extraction_manifest
            if e.get("status") == "substituted"
        }
        manifest_by_idx: Dict[int, Dict[str, Any]] = {
            int(e["frame_idx"]): e for e in self.last_extraction_manifest
        }

        def process_frame(snapshot):
            """Process a single frame with optional preprocessing + CV labeler."""
            frame_path = extracted_frames.get(snapshot.frame_idx)
            if not frame_path:
                logger.warning(f"Frame not found for snapshot at {snapshot.timestamp_ms}ms")
                return snapshot.frame_idx, []

            if (
                SUBSTITUTED_FRAME_VISION_POLICY == "skip"
                and snapshot.frame_idx in substituted_idxs
            ):
                row = manifest_by_idx.get(snapshot.frame_idx)
                if row is not None:
                    row["vision_skipped"] = True
                    row["vision_skip_reason"] = "substituted_frame"
                logger.info(
                    "Skipping CV for frame %d (substituted extraction; policy=skip)",
                    snapshot.frame_idx,
                )
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
        chosen_strategy = (self.snapshot_strategy or SNAPSHOT_STRATEGY or "naive").lower()
        max_snapshots = self.snapshot_selector.max_snapshots
        if chosen_strategy == "naive" and self.native_fps and self.native_fps > 0:
            max_snapshots = len(all_frame_objects)
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

        # Backfill: if the existence filter or an upstream strategy returned
        # fewer than max_snapshots, fill up from the remaining valid frames
        # ranked by detection count (richest content first).
        if len(selected_indices) < max_snapshots:
            already = set(selected_indices)
            backfill_pool = sorted(
                (
                    idx for idx in all_frame_objects
                    if idx not in already
                    and idx in extracted_frames
                    and os.path.exists(extracted_frames[idx])
                ),
                key=lambda i: len(all_frame_objects.get(i, [])),
                reverse=True,
            )
            need = max_snapshots - len(selected_indices)
            selected_indices = sorted(selected_indices + backfill_pool[:need])
            if len(selected_indices) < max_snapshots:
                logger.warning(
                    "Frame count below requested: got %d / %d "
                    "(not enough valid extracted frames in video)",
                    len(selected_indices), max_snapshots,
                )
            else:
                logger.info(
                    "Backfilled %d frame(s) to reach requested count of %d",
                    need, max_snapshots,
                )

        report(0.55, "vision_labeler",
               f"Running VisionLabeler on {len(selected_indices)} selected frames")
        # Step 4c: Merge VisionLabeler (Florence-2 + UFLDv2) detections into
        # the selected-frame CV results.  We only call VisionLabeler on the
        # small final selected set, not the dense pre-selection sweep, to
        # keep inference cost bounded (same pattern as former Rekognition step).
        try:
            snap_ts: Dict[int, float] = {s.frame_idx: s.timestamp_ms for s in snapshots}
            for fidx in selected_indices:
                frame_path = extracted_frames.get(fidx)
                if not frame_path:
                    continue
                if (
                    SUBSTITUTED_FRAME_VISION_POLICY == "skip"
                    and fidx in substituted_idxs
                ):
                    logger.info(
                        "Skipping VisionLabeler for frame %d (substituted; policy=skip)",
                        fidx,
                    )
                    continue
                ts_ms = snap_ts.get(fidx, 0.0)
                vl_objs = self.vision_labeler.detect(
                    frame_path=frame_path,
                    timestamp_ms=ts_ms,
                    video_width=video_metadata.width,
                    video_height=video_metadata.height,
                )
                if vl_objs:
                    existing = all_frame_objects.setdefault(fidx, [])
                    existing.extend(vl_objs)
                    logger.info(
                        "VisionLabeler added %d detections on frame %d (total now %d)",
                        len(vl_objs), fidx, len(existing),
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("VisionLabeler merge failed: %s", e)

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
