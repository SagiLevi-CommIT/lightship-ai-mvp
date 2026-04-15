"""Configuration module for Lightship MVP.

All configuration constants, enums, and settings are centralized here.
Aligned with client email agreements and golden dataset schema.
"""
import os
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

# ============================================================================
# AWS Configuration
# ============================================================================

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# ============================================================================
# Bedrock Configuration
# ============================================================================

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.1"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "15000"))
TOP_P = float(os.getenv("TOP_P", "1.0"))
TOP_K = int(os.getenv("TOP_K", "250"))

# ============================================================================
# Rekognition Configuration
# ============================================================================

REKOGNITION_MIN_CONFIDENCE = float(os.getenv("REKOGNITION_MIN_CONFIDENCE", "60.0"))
REKOGNITION_MAX_LABELS = int(os.getenv("REKOGNITION_MAX_LABELS", "50"))

# ============================================================================
# Video Classification Types
# ============================================================================

VIDEO_CLASS_ENUM = [
    "reactivity_braking",
    "qa_educational",
    "hazard_detection",
    "job_site_detection",
]

# ============================================================================
# Distance Configuration (5 categories per email agreement)
# ============================================================================

DISTANCE_ENUM = [
    "n/a",
    "danger_close",
    "near",
    "mid",
    "far",
    "very_far",
]

DISTANCE_DESCRIPTIONS = {
    "n/a": "Not applicable (e.g., lane markings, visual context)",
    "danger_close": "Dangerously close - immediate threat zone (<5m)",
    "near": "Near - high caution required (5-15m)",
    "mid": "Mid range - requires attention (15-40m)",
    "far": "Far distance - comfortably distant (40-80m)",
    "very_far": "Very far - well beyond concern (>80m)",
}

DISTANCE_THRESHOLDS_M = {
    "danger_close": 5.0,
    "near": 15.0,
    "mid": 40.0,
    "far": 80.0,
    "very_far": float("inf"),
}

# ============================================================================
# Road Type Taxonomy (per email: highway, city, town, rural)
# ============================================================================

ROAD_TYPE_ENUM = ["highway", "city", "town", "rural", "unknown"]

# ============================================================================
# Speed Format (per email: road speed limit, not vehicle speed)
# ============================================================================

SPEED_CATEGORIES = [
    "<15_mph",
    "15-25_mph",
    "25-40_mph",
    "40-55_mph",
    "55-70_mph",
    ">70_mph",
    "unknown",
]

# ============================================================================
# Threat / Severity Levels
# ============================================================================

THREAT_LEVEL_ENUM = ["none", "low", "medium", "high", "critical"]
HAZARD_SEVERITY_ENUM = ["Critical", "High", "Medium", "Low", "None"]

PRIORITY_THRESHOLD = "high"

THREAT_LEVEL_GUIDELINES = {
    "none": "Context-only or informational objects with no immediate danger",
    "low": "Relevant objects but unlikely to require driver action",
    "medium": "Objects worth attention; may require monitoring",
    "high": "Plausible hazards requiring close monitoring or potential action",
    "critical": "Immediate danger requiring urgent driver response",
}

HAZARD_SEVERITY_RANK = {
    "None": 0,
    "Low": 1,
    "Medium": 2,
    "High": 3,
    "Critical": 4,
}

HAZARD_SEVERITY_TO_THREAT = {
    "Critical": "critical",
    "High": "high",
    "Medium": "medium",
    "Low": "low",
    "None": "none",
}

# ============================================================================
# Object Labels (GT-aligned per email)
# ============================================================================

OBJECT_LABELS = [
    "car", "truck", "bus", "motorcycle", "bicycle",
    "pedestrian", "construction_worker",
    "cone", "barrier", "heavy_equipment",
    "construction_sign", "fencing", "debris",
    "animal", "other",
]

SIGN_LABELS = [
    "speed_limit", "stop", "yield", "warning",
    "construction", "info",
]

TRAFFIC_SIGNAL_LABELS = [
    "red", "yellow", "green",
    "flashing_red", "flashing_yellow",
    "off", "other",
]

# Rekognition label -> Lightship object class mapping
REKOGNITION_LABEL_MAP = {
    "Car": "car",
    "Automobile": "car",
    "Vehicle": "car",
    "SUV": "car",
    "Van": "car",
    "Sedan": "car",
    "Coupe": "car",
    "Hatchback": "car",
    "Minivan": "car",
    "Truck": "truck",
    "Pickup Truck": "truck",
    "Semi Truck": "truck",
    "Trailer Truck": "truck",
    "Bus": "bus",
    "School Bus": "bus",
    "Motorcycle": "motorcycle",
    "Motorbike": "motorcycle",
    "Bicycle": "bicycle",
    "Bike": "bicycle",
    "Person": "pedestrian",
    "Pedestrian": "pedestrian",
    "Human": "pedestrian",
    "Traffic Light": "traffic_signal",
    "Traffic Signal": "traffic_signal",
    "Stop Sign": "stop_sign",
    "Road Sign": "road_sign",
    "Sign": "road_sign",
    "Street Sign": "road_sign",
    "Cone": "cone",
    "Traffic Cone": "cone",
    "Barrier": "barrier",
    "Barricade": "barrier",
    "Fence": "fencing",
    "Fencing": "fencing",
    "Excavator": "heavy_equipment",
    "Bulldozer": "heavy_equipment",
    "Crane": "heavy_equipment",
    "Backhoe": "heavy_equipment",
    "Construction Vehicle": "heavy_equipment",
    "Animal": "animal",
    "Dog": "animal",
    "Cat": "animal",
    "Deer": "animal",
    "Road": "road_surface",
    "Crosswalk": "crosswalk",
    "Intersection": "intersection",
    "Highway": "road_surface",
    "Asphalt": "road_surface",
    "Lane Marking": "lane_marking",
}

# ============================================================================
# Snapshot Selection Configuration
# ============================================================================

SNAPSHOT_STRATEGY: Literal["naive", "scene_change"] = "naive"
MAX_SNAPSHOTS_PER_VIDEO: int = 5

EVAL_TOLERANCE_MS: int = 1000
SCENE_CHANGE_THRESHOLD: float = 0.3
SCENE_CHANGE_MIN_INTERVAL_MS: int = 1000

# ============================================================================
# Hazard Assessment Configuration
# ============================================================================

HAZARD_LLM_MODE = "sliding_window"
WINDOW_SIZE = 3
WINDOW_OVERLAP = 1
MERGE_POLICY = "max_severity"

# ============================================================================
# Per-Frame LLM Refinement Configuration
# ============================================================================

FRAME_REFINER_MAX_RETRIES = 2
FRAME_REFINER_FALLBACK_ENABLED = True
MIN_OBJECTS_FOR_SELECTION = 1
CV_PARALLEL_WORKERS = 4

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

_IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
OUTPUT_DIR = "/tmp/output" if _IS_LAMBDA else "output"
TEMP_FRAMES_DIR = os.path.join(OUTPUT_DIR, "temp_frames")

# ============================================================================
# Video Processing
# ============================================================================

FRAME_FORMAT = "png"
FRAME_QUALITY = 95

# Frame sampling for Rekognition pipeline
ANALYSIS_STRIDE_MS = 1000
MAX_FRAMES_PER_VIDEO = 50

# S3 results persistence
RESULTS_BUCKET = os.getenv("RESULTS_BUCKET", os.getenv("PROCESSING_BUCKET", ""))
RESULTS_PREFIX = "results"
