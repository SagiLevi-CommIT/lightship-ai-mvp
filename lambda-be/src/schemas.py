"""Pydantic schemas for Lightship MVP.

Defines data models matching the client config output formats (detection,
decisions, reactions) plus internal pipeline models.  Aligned with golden
dataset GT schema and email-agreed taxonomy.
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
from src.config import (
    DISTANCE_ENUM,
    THREAT_LEVEL_ENUM,
    VIDEO_CLASS_ENUM,
    ROAD_TYPE_ENUM,
    HAZARD_SEVERITY_ENUM,
)


# ============================================================================
# Internal pipeline models
# ============================================================================


class Center(BaseModel):
    x: int
    y: int


class PolygonPoint(BaseModel):
    x: float
    y: float


class BBox(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    width: float
    height: float


class HazardEvent(BaseModel):
    """A detected hazard event in the video timeline."""

    start_time_ms: float = Field(..., description="Hazard start time in milliseconds")
    hazard_type: str = Field(..., description="Brief category of the hazard")
    hazard_description: str = Field(..., description="Detailed hazard description")
    hazard_severity: str = Field(..., description="Severity: Critical, High, Medium, Low, None")
    road_conditions: str = Field(..., description="Current road/weather conditions")
    duration_ms: Optional[float] = Field(None, description="Duration of hazard in milliseconds")

    @field_validator("hazard_severity")
    @classmethod
    def validate_hazard_severity(cls, v: str) -> str:
        if v not in HAZARD_SEVERITY_ENUM:
            raise ValueError(f"Hazard severity '{v}' not in {HAZARD_SEVERITY_ENUM}")
        return v


class ObjectLabel(BaseModel):
    """Detected object with geometry, distance, and priority."""

    description: str = Field(..., description="Object label/class")
    start_time_ms: float = Field(..., description="Timestamp in milliseconds")
    distance: str = Field(..., description="Distance category")
    priority: str = Field(..., description="Priority/threat level")
    location_description: Optional[str] = Field(default="", description="Spatial location")

    center: Optional[Center] = Field(None)
    polygon: Optional[List[PolygonPoint]] = Field(default_factory=list)
    x_min: Optional[float] = Field(None)
    y_min: Optional[float] = Field(None)
    x_max: Optional[float] = Field(None)
    y_max: Optional[float] = Field(None)
    width: Optional[float] = Field(None)
    height: Optional[float] = Field(None)

    confidence: Optional[float] = Field(None, description="Detection confidence 0-1")
    rekognition_label: Optional[str] = Field(None, description="Original Rekognition label")

    @field_validator("distance")
    @classmethod
    def validate_distance(cls, v: str) -> str:
        if v not in DISTANCE_ENUM:
            raise ValueError(f"Distance '{v}' not in {DISTANCE_ENUM}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        if v not in THREAT_LEVEL_ENUM:
            raise ValueError(f"Priority '{v}' not in {THREAT_LEVEL_ENUM}")
        return v

    def model_post_init(self, __context) -> None:
        has_center = self.center is not None
        has_polygon = self.polygon is not None and len(self.polygon) > 0
        has_bbox = all(
            v is not None for v in [self.x_min, self.y_min, self.x_max, self.y_max]
        )
        if not (has_center or has_polygon or has_bbox):
            raise ValueError("At least one geometry field (center, polygon, or bbox) required")


class SnapshotInfo(BaseModel):
    frame_idx: int = Field(..., description="Frame index in video")
    timestamp_ms: float = Field(..., description="Timestamp in milliseconds")
    reason: Optional[str] = Field(None)


class VideoMetadata(BaseModel):
    filename: str
    filepath: str
    camera: str
    fps: float
    duration_ms: float
    total_frames: int
    width: int
    height: int


# ============================================================================
# Pipeline output (internal intermediate format)
# ============================================================================


class VideoOutput(BaseModel):
    """Complete video output with all detected objects and hazard events."""

    filename: str
    fps: float
    camera: str
    description: str = ""
    traffic: str = "unknown"
    lighting: str = "unknown"
    weather: str = "unknown"
    collision: str = "none"
    speed: str = "unknown"
    road_type: str = "unknown"
    video_class: str = "unknown"
    video_duration_ms: float
    objects: List[ObjectLabel] = Field(default_factory=list)
    hazard_events: List[HazardEvent] = Field(default_factory=list)

    @field_validator("fps")
    @classmethod
    def validate_fps(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"FPS must be positive, got {v}")
        return v

    @field_validator("video_duration_ms")
    @classmethod
    def validate_duration(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"Duration must be positive, got {v}")
        return v


# ============================================================================
# Client config output formats (detection, decisions, reactions)
# Per golden dataset config examples from emails
# ============================================================================


class DetectionConfigOutput(BaseModel):
    """Client detection config format (hazard_detection type).

    Matches detection_config_example.json from the golden dataset.
    """

    filename: str
    video_class: str = "hazard_detection"
    road: str = Field(default="unknown", description="Road type: highway/city/town/rural")
    speed: str = Field(default="unknown", description="Road speed limit category")
    traffic: str = Field(default="unknown", description="Traffic density")
    weather: str = Field(default="unknown", description="Weather conditions")
    collision: str = Field(default="none", description="Collision description or none")
    space: str = Field(default="open", description="Space type: open/confined/urban")

    trial_start_prompt: str = Field(
        default="",
        description="Narrative text describing what the driver sees",
    )
    video_end_time: Optional[float] = Field(None, description="Video end time in seconds")
    hazard_view_duration: Optional[float] = Field(
        None, description="Duration to view hazard in seconds"
    )

    hazard_x: List[float] = Field(default_factory=list, description="Hazard X coordinate arrays")
    hazard_y: List[float] = Field(default_factory=list, description="Hazard Y coordinate arrays")
    hazard_size: List[float] = Field(default_factory=list, description="Hazard size arrays")
    hazard_desc: List[str] = Field(default_factory=list, description="Hazard description arrays")

    detection_summary: Optional[Dict[str, Any]] = Field(None)
    objects: Optional[List[Dict[str, Any]]] = Field(None)


class DecisionsConfigOutput(BaseModel):
    """Client decisions config format (qa_educational type).

    Matches decisions_config_example.json from the golden dataset.
    """

    filename: str
    video_class: str = "qa_educational"
    road: str = "unknown"
    speed: str = "unknown"
    traffic: str = "unknown"
    weather: str = "unknown"
    collision: str = "none"
    space: str = "open"

    trial_start_prompt: str = ""
    video_end_time: Optional[float] = None

    questions: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Q&A questions with answer options",
    )

    detection_summary: Optional[Dict[str, Any]] = Field(None)


class ReactionsConfigOutput(BaseModel):
    """Client reactions config format (reactivity_braking type).

    Matches reactions_config_example.json from the golden dataset.
    """

    filename: str
    video_class: str = "reactivity_braking"
    road: str = "unknown"
    speed: str = "unknown"
    traffic: str = "unknown"
    weather: str = "unknown"
    collision: str = "none"
    space: str = "open"

    trial_start_prompt: str = ""
    video_end_time: Optional[float] = None
    reaction_time_window: Optional[float] = Field(
        None, description="Reaction time window in seconds"
    )

    hazard_x: List[float] = Field(default_factory=list)
    hazard_y: List[float] = Field(default_factory=list)
    hazard_size: List[float] = Field(default_factory=list)
    hazard_desc: List[str] = Field(default_factory=list)
    hazard_view_duration: Optional[float] = None

    detection_summary: Optional[Dict[str, Any]] = Field(None)


class JobsiteConfigOutput(BaseModel):
    """Jobsite config placeholder (job_site_detection type).

    Architectural placeholder - not fully implemented.
    Client TODO: provide jobsite config template.
    """

    filename: str
    video_class: str = "job_site_detection"
    weather: str = "unknown"
    site_type: str = "unknown"

    objects_detected: List[Dict[str, Any]] = Field(default_factory=list)
    hazards: List[Dict[str, Any]] = Field(default_factory=list)
    detection_summary: Optional[Dict[str, Any]] = Field(None)
