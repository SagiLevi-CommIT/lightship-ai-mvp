"""Configuration module for Lightship MVP.

All configuration constants, enums, and settings are centralized here.
"""
import os
from typing import Literal
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# ============================================================================
# Snapshot Selection Configuration
# ============================================================================

SNAPSHOT_STRATEGY: Literal["naive", "scene_change", "clustering"] = os.getenv(
    "SNAPSHOT_STRATEGY", "clustering"
)
"""Strategy for selecting snapshots: 'naive', 'scene_change', or 'clustering'"""

MAX_SNAPSHOTS_PER_VIDEO: int = 10
"""Maximum number of snapshots to select per video"""

EVAL_TOLERANCE_MS: int = 1000
"""Tolerance window in milliseconds for matching model snapshots to GT (±1000ms)"""

# Scene change detection parameters (for CV-based strategy)
SCENE_CHANGE_THRESHOLD: float = 0.3
"""Histogram difference threshold for detecting scene changes (0-1 range)"""

SCENE_CHANGE_MIN_INTERVAL_MS: int = 1000
"""Minimum time interval between scene changes in milliseconds"""

# ============================================================================
# Threat Level Configuration
# ============================================================================

THREAT_LEVEL_ENUM = ["none", "low", "medium", "high", "critical"]
"""Priority/threat level enumeration from lowest to highest"""

PRIORITY_THRESHOLD = "high"
"""Threshold for priority hazards (objects at this level or above are priority)"""

# Priority/threat level guidelines for LLM
THREAT_LEVEL_GUIDELINES = {
    "none": "Context-only or informational objects with no immediate danger",
    "low": "Relevant objects but unlikely to require driver action",
    "medium": "Objects worth attention; may require monitoring",
    "high": "Plausible hazards requiring close monitoring or potential action",
    "critical": "Immediate danger requiring urgent driver response"
}

# ============================================================================
# Distance Configuration
# ============================================================================

DISTANCE_ENUM = ["n/a", "dangerously_close", "very_close", "close", "moderate", "far", "very_far"]
"""Distance enumeration from closest to farthest (n/a for non-distance objects like lanes)"""

DISTANCE_DESCRIPTIONS = {
    "n/a": "Not applicable (e.g., lane markings, visual context)",
    "dangerously_close": "Dangerously close - immediate threat zone (<3m)",
    "very_close": "Very close - high caution (3-7m)",
    "close": "Close range - requires careful watching (7-15m)",
    "moderate": "Moderate distance - safe but alert (15-30m)",
    "far": "Far distance - comfortably distant (30-60m)",
    "very_far": "Very far - well beyond concern (>60m)"
}

# ============================================================================
# Object Labels
# ============================================================================

OBJECT_LABELS = [
    # Vulnerable Road Users (VRUs)
    "pedestrian",
    "pedestrian(group)",
    "bicyclist",
    "motorcycle",

    # Vehicles
    "vehicle",
    "vehicle(parked)",
    "truck",
    "bus",
    "emergency_vehicle",

    # Lane markings
    "lane",
    "lane(current)",
    "lane(left_turn)",
    "lane(right_turn)",
    "double_yellow",

    # Road infrastructure
    "crosswalk",
    "intersection_boundary",

    # Traffic signals and signs
    "traffic_signal(green)",
    "traffic_signal(yellow)",
    "traffic_signal(red)",
    "stop_sign",
    "speed_limit",
    "one_way_sign",
    "do_not_enter_sign",
    "yield_sign",
    "road_work_ahead_sign",
    "children_at_play_sign",
    "merging_traffic_sign",
    "railroad_crossing_sign",
    "roundabout_ahead_sign",
    "signal_ahead_sign",
    "pedestrian_crossing_sign",
    "low_clearance_sign",
    "unknown_sign",

    # Hazards and obstructions
    "visual_obstruction",
    "construction",
    "overhead_clearance_hazard",

    # Other objects
    "animal",
    "tree",
    "pole",

    # Vehicle indicators
    "brake_lights",
    "turn_signal",
    "hazard_lights"
]
"""Complete list of object labels that can be identified"""

# ============================================================================
# AWS Bedrock Configuration
# ============================================================================

AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")

# LLM parameters
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.1"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "15000"))
TOP_P = float(os.getenv("TOP_P", "1.0"))
TOP_K = int(os.getenv("TOP_K", "250"))

# ============================================================================
# Logging Configuration
# ============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = ".logs"
LOG_FILE = "app.log"

# ============================================================================
# Data Paths
# ============================================================================

DATA_DIR = "data"
TRAIN_DIR = os.path.join(DATA_DIR, "train")
TEST_DIR = os.path.join(DATA_DIR, "test")

# Use /tmp in Lambda (read-only filesystem); local otherwise
_IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
OUTPUT_DIR = "/tmp/output" if _IS_LAMBDA else "output"
TEMP_FRAMES_DIR = os.path.join(OUTPUT_DIR, "temp_frames")

# ============================================================================
# Video Processing
# ============================================================================

FRAME_FORMAT = "png"
"""Format for extracted frames"""

FRAME_QUALITY = 95
"""Quality for extracted frames (1-100 for JPEG/PNG)"""

# ============================================================================
# CV Labeler Configuration (V2)
# ============================================================================

ANALYSIS_STRIDE_MS = 1000
"""Frame sampling interval in milliseconds for V2 pipeline"""

MAX_FRAMES_PER_VIDEO = 50
"""Safety cap on maximum frames to process per video"""

# Detection thresholds
MIN_DET_CONF = 0.35
"""Minimum detection confidence for YOLO"""

NMS_IOU_THRESHOLD = 0.45
"""Non-maximum suppression IoU threshold"""

MIN_MASK_AREA_PX = 500
"""Minimum polygon area in pixels (increased from 100 to reduce false positives)"""

# Lane Detection Post-Processing Configuration
MAX_LANES_PER_FRAME = 5
"""Maximum number of lane markings to keep per frame (reduces redundancy)"""

MAX_DOUBLE_YELLOW_PER_FRAME = 2
"""Maximum number of double_yellow markings to keep per frame"""

MAX_CROSSWALKS_PER_FRAME = 3
"""Maximum number of crosswalks to keep per frame"""

LANE_CLUSTERING_THRESHOLD_PX = 100
"""Distance threshold in pixels for clustering nearby lane contours (increased to merge dashed lane fragments)"""

MIN_LANE_ASPECT_RATIO = 0.1
"""Minimum aspect ratio for lane markings (width/height or height/width)"""

MAX_LANE_ASPECT_RATIO = 10.0
"""Maximum aspect ratio for lane markings (filters out non-lane shapes)"""

# Double yellow specific validation
MIN_DOUBLE_YELLOW_WIDTH = 60
"""Minimum width in pixels for double yellow detection (must span significant road width)"""

MIN_DOUBLE_YELLOW_HEIGHT = 100
"""Minimum height in pixels for double yellow detection (must be reasonably long)"""

MIN_DOUBLE_YELLOW_AREA_PX = 3500
"""Minimum area in pixels for double yellow detection (larger than regular lanes)"""

# HSV Color Thresholds for Lane Detection
YELLOW_HSV_LOWER = [15, 80, 100]
"""Lower HSV threshold for yellow lane markings (relaxed to detect faded markings)"""

YELLOW_HSV_UPPER = [30, 255, 255]
"""Upper HSV threshold for yellow lane markings"""

WHITE_HSV_LOWER = [0, 0, 200]
"""Lower HSV threshold for white lane markings (relaxed to detect dimmer whites)"""

WHITE_HSV_UPPER = [180, 25, 255]
"""Upper HSV threshold for white lane markings (lower saturation to exclude colored surfaces)"""

# Lane Validation Toggles (for debugging - can disable individual checks)
ENABLE_LINEARITY_VALIDATION = True
"""Enable linearity check for lane candidates"""

ENABLE_SPATIAL_VALIDATION = True
"""Enable spatial context filtering (edge margins, road region)"""

ENABLE_ORIENTATION_VALIDATION = True
"""Enable orientation validation (lanes should be roughly vertical)"""

ENABLE_EDGE_VALIDATION = False
"""Enable edge-based Hough validation (Phase B - disabled by default)"""

# Lane Validation Thresholds
MIN_LANE_LINEARITY_SCORE = 0.75
"""Minimum linearity score (0-1) for lane contours - relaxed to allow slight curves"""

LANE_EDGE_MARGIN_PERCENT = 0.03
"""Reject detections in outer X% of frame width (relaxed to allow edge lanes)"""

LANE_MIN_Y_PERCENT = 0.35
"""Reject detections starting above this percentage of frame height (relaxed for distant lanes)"""

LANE_ORIENTATION_TOLERANCE_DEG = 45
"""Maximum angle deviation from vertical for lane markings (relaxed for angled lanes)"""

# Geometry NMS Configuration
GEOMETRY_NMS_IOU_THRESHOLD = 0.3
"""IoU threshold for non-maximum suppression of overlapping geometry objects"""

# Crosswalk Detection Configuration
CROSSWALK_MIN_Y_PERCENT = 0.35
"""Reject crosswalk detections starting above this percentage of frame height"""

CROSSWALK_MIN_STRIPES = 2
"""Minimum number of parallel stripes required to confirm crosswalk (relaxed for partial views)"""

CROSSWALK_STRIPE_ANGLE_TOLERANCE = 15
"""Maximum angle deviation from horizontal in degrees for crosswalk stripes"""

CROSSWALK_HOUGH_THRESHOLD = 40
"""Hough Transform threshold for line detection in crosswalks (relaxed for fainter lines)"""

CROSSWALK_MIN_LINE_LENGTH = 40
"""Minimum line length in pixels for Hough line detection"""

CROSSWALK_MAX_LINE_GAP = 10
"""Maximum gap in pixels between line segments for Hough line detection"""

CROSSWALK_STRIPE_SPACING_MIN = 20
"""Minimum spacing in pixels between parallel stripes"""

CROSSWALK_STRIPE_SPACING_MAX = 100
"""Maximum spacing in pixels between parallel stripes"""

# Depth/Distance Configuration
DISTANCE_THRESHOLDS = {
    "dangerously_close": 3.0,       # < 3m
    "very_close": 7.0,              # 3-7m
    "close": 15.0,                  # 7-15m
    "moderate": 30.0,               # 15-30m
    "far": 60.0,                    # 30-60m
    "very_far": float('inf')        # > 60m
}
"""Distance bucket thresholds in meters"""

# Reference object size priors for depth calibration (per spec §6.2)
REFERENCE_OBJECT_PRIORS = {
    "pedestrian": {"dimension": "height", "min": 1.5, "max": 2.0},
    "bicyclist": {"dimension": "height", "min": 1.5, "max": 2.2},
    "motorcycle": {"dimension": "height", "min": 1.4, "max": 2.1},
    "vehicle": {"dimension": "width", "min": 1.6, "max": 2.0},
    "truck": {"dimension": "width", "min": 2.4, "max": 2.6},
    "bus": {"dimension": "width", "min": 2.4, "max": 2.6},
}
"""Reference object size priors for depth calibration (meters)"""

# ============================================================================
# Hazard Assessment Configuration (V2)
# ============================================================================

HAZARD_LLM_MODE = "sliding_window"
"""LLM hazard assessment mode: 'full_video' or 'sliding_window'"""

WINDOW_SIZE = 3
"""Number of frames per window for sliding window mode"""

WINDOW_OVERLAP = 1
"""Overlap in frames between consecutive windows"""

MERGE_POLICY = "max_severity"
"""Window merge policy: 'max_severity' or 'latest_window'"""

# Hazard severity to threat_level mapping
HAZARD_SEVERITY_TO_THREAT = {
    "Critical": "critical",
    "High": "high",
    "Medium": "medium",
    "Low": "low",
    "None": "none"
}
"""Mapping from hazard severity to object threat level"""

# Hazard severity ranking (for max_severity merge policy)
HAZARD_SEVERITY_RANK = {
    "None": 0,
    "Low": 1,
    "Medium": 2,
    "High": 3,
    "Critical": 4
}
"""Ranking of hazard severities for comparison"""

# ============================================================================
# Per-Frame LLM Refinement Configuration
# ============================================================================

FRAME_REFINER_MAX_RETRIES = 2
"""Maximum number of retries per frame when LLM indicates refinement needs_retry"""

FRAME_REFINER_FALLBACK_ENABLED = True
"""Enable fallback to nearest unused frame when per-frame LLM completely fails"""

MIN_OBJECTS_FOR_SELECTION = 1
"""Minimum number of objects required for a frame to be selected for processing"""

CV_PARALLEL_WORKERS = 4
"""Maximum number of parallel workers for CV detection on all frames (threading)"""

ENABLE_FRAME_PREPROCESSING = os.getenv("ENABLE_FRAME_PREPROCESSING", "true").lower() == "true"
"""Enable CLAHE + sharpen preprocessing before CV detection"""

