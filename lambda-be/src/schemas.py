"""Pydantic schemas for JSON validation.

Defines data models matching the output contract for the Lightship MVP V2.
"""
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from src.config import DISTANCE_ENUM, THREAT_LEVEL_ENUM


class HazardEvent(BaseModel):
    """A detected hazard event."""
    start_time_ms: float = Field(..., description="Hazard start time in milliseconds")
    hazard_type: str = Field(..., description="Brief category of the hazard")
    hazard_description: str = Field(..., description="Detailed hazard description")
    hazard_severity: str = Field(..., description="Severity: Critical, High, Medium, Low, None")
    road_conditions: str = Field(..., description="Current road/weather conditions")
    duration_ms: Optional[float] = Field(None, description="Duration of hazard in milliseconds")

    @field_validator('hazard_severity')
    @classmethod
    def validate_hazard_severity(cls, v: str) -> str:
        """Validate hazard_severity is from allowed values."""
        allowed = ["Critical", "High", "Medium", "Low", "None"]
        if v not in allowed:
            raise ValueError(f"Hazard severity '{v}' not in allowed values: {allowed}")
        return v


class Center(BaseModel):
    """Object center coordinates."""
    x: int
    y: int


class PolygonPoint(BaseModel):
    """Polygon vertex coordinates."""
    x: float
    y: float


class ObjectLabel(BaseModel):
    """Object label with geometry, distance, and priority level.

    Represents a single detected object in a frame at a specific timestamp.
    """
    description: str = Field(..., description="Object label/description")
    start_time_ms: float = Field(..., description="Timestamp in milliseconds")
    distance: str = Field(..., description="Distance category")
    priority: str = Field(..., description="Priority/threat level assessment")
    location_description: Optional[str] = Field(
        default="",
        description="Spatial location description for vehicles and pedestrians only"
    )

    # Geometry (at least one required)
    center: Optional[Center] = Field(None, description="Object center point")
    polygon: Optional[List[PolygonPoint]] = Field(default_factory=list, description="Object polygon vertices")
    x_min: Optional[float] = Field(None, description="Bounding box min x")
    y_min: Optional[float] = Field(None, description="Bounding box min y")
    x_max: Optional[float] = Field(None, description="Bounding box max x")
    y_max: Optional[float] = Field(None, description="Bounding box max y")
    width: Optional[float] = Field(None, description="Bounding box width")
    height: Optional[float] = Field(None, description="Bounding box height")

    @field_validator('distance')
    @classmethod
    def validate_distance(cls, v: str) -> str:
        """Validate distance is from allowed enum."""
        if v not in DISTANCE_ENUM:
            raise ValueError(f"Distance '{v}' not in allowed values: {DISTANCE_ENUM}")
        return v

    @field_validator('priority')
    @classmethod
    def validate_priority(cls, v: str) -> str:
        """Validate priority is from allowed enum."""
        if v not in THREAT_LEVEL_ENUM:
            raise ValueError(f"Priority '{v}' not in allowed values: {THREAT_LEVEL_ENUM}")
        return v

    def model_post_init(self, __context) -> None:
        """Validate that at least one geometry field is provided."""
        has_center = self.center is not None
        has_polygon = self.polygon is not None and len(self.polygon) > 0
        has_bbox = all([
            self.x_min is not None,
            self.y_min is not None,
            self.x_max is not None,
            self.y_max is not None
        ])

        if not (has_center or has_polygon or has_bbox):
            raise ValueError("At least one geometry field (center, polygon, or bbox) must be provided")


class VideoOutput(BaseModel):
    """Complete video output with all detected objects and hazard events.

    Represents the final JSON output for a single video (aligned with GT format).
    """
    filename: str = Field(..., description="Video filename")
    fps: float = Field(..., description="Frames per second")
    camera: str = Field(..., description="Camera vendor/type")
    description: str = Field(default="", description="Video description/summary")
    traffic: str = Field(default="unknown", description="Traffic density: light/moderate/heavy/unknown")
    lighting: str = Field(default="unknown", description="Lighting conditions: daylight/dusk/night/unknown")
    weather: str = Field(default="unknown", description="Weather conditions: clear/rain/snow/fog/unknown")
    collision: str = Field(default="none", description="Collision type/location if applicable")
    speed: str = Field(default="unknown", description="Approximate vehicle speed range")
    video_duration_ms: float = Field(..., description="Total video duration in milliseconds")
    objects: List[ObjectLabel] = Field(default_factory=list, description="List of all detected objects")
    hazard_events: List[HazardEvent] = Field(default_factory=list, description="List of detected hazard events")

    @field_validator('fps')
    @classmethod
    def validate_fps(cls, v: float) -> float:
        """Validate fps is positive."""
        if v <= 0:
            raise ValueError(f"FPS must be positive, got {v}")
        return v

    @field_validator('video_duration_ms')
    @classmethod
    def validate_duration(cls, v: float) -> float:
        """Validate video_duration_ms is positive."""
        if v <= 0:
            raise ValueError(f"Duration must be positive, got {v}")
        return v


class SnapshotInfo(BaseModel):
    """Information about a selected snapshot.

    Internal model for tracking snapshot selection.
    """
    frame_idx: int = Field(..., description="Frame index in video")
    timestamp_ms: float = Field(..., description="Timestamp in milliseconds")
    reason: Optional[str] = Field(None, description="Reason for selection (e.g., 'GT match', 'scene change')")


class VideoMetadata(BaseModel):
    """Video metadata extracted from file.

    Internal model for video processing.
    """
    filename: str
    filepath: str
    camera: str
    fps: float
    duration_ms: float
    total_frames: int
    width: int
    height: int

