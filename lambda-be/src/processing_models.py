"""Pydantic models shared by the HTTP API and workers (no heavy ML imports)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


class ProcessingConfig(BaseModel):
    """Configuration for video processing."""
    snapshot_strategy: str = "naive"
    max_snapshots: int = 3
    cleanup_frames: bool = False  # Keep frames for UI display
    use_cv_labeler: bool = True   # V2 pipeline by default
    hazard_mode: str = "sliding_window"
    window_size: int = 3
    window_overlap: int = 1
    # Native sampling rate when ``snapshot_strategy == "naive"``.
    # If omitted, native mode samples exactly ``max_snapshots`` frames with
    # an even jump. If provided, native mode samples at this FPS.
    # Accepts strings for convenience because the UI posts form data and
    # frequently sends ``"2"`` rather than ``2``.
    native_fps: Optional[float] = None
    # Explicit Native UI mode. ``count`` ignores native_fps and returns
    # exactly max_snapshots final frames; ``fps`` uses native_fps.
    native_sampling_mode: str = "count"
    # Vision labeler backend (overrides DETECTOR_BACKEND env var).
    # ``florence2`` | ``yolo`` | ``detectron2``. Legacy ``auto`` is accepted
    # and normalised to ``florence2`` (no silent YOLO fallback).
    detector_backend: str = "florence2"
    # Lane detection backend (overrides LANE_BACKEND env var).
    # "ufldv2" = Ultra-Fast Lane Detection V2 (default)
    # "opencv" = legacy OpenCV HSV + Hough lane detection (opt-in)
    lane_backend: str = "ufldv2"
    # Interactive detector runs default to detector-only boxes. Enable this
    # only for quality-oriented runs that need per-frame Bedrock refinement.
    enable_llm_refinement: bool = False
    # Temporal hazard LLM is also opt-in for interactive speed tests.
    enable_hazard_llm: bool = False

    @field_validator("native_sampling_mode", mode="before")
    @classmethod
    def _normalize_native_sampling_mode(cls, v):  # noqa: ANN001
        if v is None:
            return "count"
        s = str(v).strip().lower()
        if s not in ("count", "fps"):
            raise ValueError("native_sampling_mode must be 'count' or 'fps'")
        return s

    @field_validator("detector_backend", mode="before")
    @classmethod
    def _normalize_detector_backend(cls, v):  # noqa: ANN001
        if v is None:
            return "florence2"
        s = str(v).strip().lower()
        if s == "auto":
            return "florence2"
        allowed = ("florence2", "yolo", "detectron2")
        if s not in allowed:
            raise ValueError(
                f"detector_backend must be one of {allowed} (or legacy 'auto'), got {v!r}",
            )
        return s

    @model_validator(mode="after")
    def _drop_native_fps_outside_fps_mode(self):
        if self.native_sampling_mode != "fps":
            self.native_fps = None
        return self


class ProcessingStatus(BaseModel):
    """Status response model."""
    status: str
    progress: float
    message: str
    current_step: Optional[str] = None
