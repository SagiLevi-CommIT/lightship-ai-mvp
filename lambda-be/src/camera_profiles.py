"""Camera-specific parameter profiles for CV detection.

Each camera type has optimized parameters for geometry detection based on:
- Camera resolution and field of view
- Typical lighting conditions
- Road marking visibility
- False positive patterns observed in testing
"""

from typing import Dict, Any


class CameraProfile:
    """Parameter profile for a specific camera type."""

    def __init__(self, name: str, params: Dict[str, Any]):
        self.name = name
        self.params = params

    def get(self, param_name: str, default=None):
        """Get a parameter value."""
        return self.params.get(param_name, default)

    def __getitem__(self, param_name: str):
        """Allow dict-style access."""
        return self.params[param_name]


# LYTX Camera Profile (baseline - best performing)
LYTX_PROFILE = CameraProfile(
    name="lytx",
    params={
        # HSV Color Thresholds - RELAXED (working well)
        "YELLOW_HSV_LOWER": [15, 80, 100],
        "YELLOW_HSV_UPPER": [30, 255, 255],
        "WHITE_HSV_LOWER": [0, 0, 200],
        "WHITE_HSV_UPPER": [180, 25, 255],

        # Lane Validation - RELAXED (achieving 100% recall)
        "MIN_LANE_LINEARITY_SCORE": 0.75,
        "LANE_EDGE_MARGIN_PERCENT": 0.03,
        "LANE_MIN_Y_PERCENT": 0.35,
        "LANE_ORIENTATION_TOLERANCE_DEG": 45,

        # Double Yellow Validation - LENIENT (prioritize recall for lytx)
        "MIN_DOUBLE_YELLOW_WIDTH": 25,
        "MIN_DOUBLE_YELLOW_HEIGHT": 50,
        "MIN_DOUBLE_YELLOW_AREA_PX": 1000,

        # Mask Area Threshold - LYTX baseline (no dimension filter needed)
        "MIN_MASK_AREA_PX": 500,  # Keep high for LYTX (noise reduction)
        "MIN_LANE_WIDTH_PX": 0,  # No additional filtering needed
        "MIN_LANE_HEIGHT_PX": 0,  # No additional filtering needed

        # Crosswalk Detection - KEEP WORKING PARAMETERS (achieved 100% recall)
        "CROSSWALK_MIN_Y_PERCENT": 0.35,
        "CROSSWALK_MIN_STRIPES": 2,  # Works well for lytx
        "CROSSWALK_HOUGH_THRESHOLD": 40,  # Original working value
        "CROSSWALK_MIN_LINE_LENGTH": 40,
        "CROSSWALK_MAX_LINE_GAP": 10,
        "CROSSWALK_STRIPE_ANGLE_TOLERANCE": 15,
        "CROSSWALK_STRIPE_SPACING_MIN": 20,
        "CROSSWALK_STRIPE_SPACING_MAX": 100,

        # Validation Toggles
        "ENABLE_LINEARITY_VALIDATION": True,
        "ENABLE_SPATIAL_VALIDATION": True,
        "ENABLE_ORIENTATION_VALIDATION": True,
        "ENABLE_EDGE_VALIDATION": False,
    }
)


# NETRADYNE Camera Profile
NETRADYNE_PROFILE = CameraProfile(
    name="netradyne",
    params={
        # HSV Color Thresholds - RELAXED FOR FADED MARKINGS
        "YELLOW_HSV_LOWER": [12, 60, 85],  # Detect faded yellows
        "YELLOW_HSV_UPPER": [31, 255, 255],
        "WHITE_HSV_LOWER": [0, 0, 170],  # Lowered for faded whites
        "WHITE_HSV_UPPER": [180, 30, 255],

        # Lane Validation - VERY RELAXED (maximize recall for faded markings)
        "MIN_LANE_LINEARITY_SCORE": 0.60,  # Allow curves
        "LANE_EDGE_MARGIN_PERCENT": 0.02,  # Allow closer to edges
        "LANE_MIN_Y_PERCENT": 0.30,  # Allow higher in frame
        "LANE_ORIENTATION_TOLERANCE_DEG": 70,  # Very permissive for angled lanes

        # Mask Area Threshold - VERY LOW for dashed segments + dimension filtering
        "MIN_MASK_AREA_PX": 50,  # Catch small dashed segments
        "MIN_LANE_WIDTH_PX": 5,  # Noise rejection (width)
        "MIN_LANE_HEIGHT_PX": 5,  # Noise rejection (height)

        # Double Yellow Validation - STRICT (high FP rate observed)
        "MIN_DOUBLE_YELLOW_WIDTH": 70,
        "MIN_DOUBLE_YELLOW_HEIGHT": 120,
        "MIN_DOUBLE_YELLOW_AREA_PX": 5000,

        # Crosswalk Detection - BALANCED
        "CROSSWALK_MIN_Y_PERCENT": 0.35,  # Less restrictive ROI
        "CROSSWALK_MIN_STRIPES": 2,  # Relaxed
        "CROSSWALK_HOUGH_THRESHOLD": 45,  # Moderate threshold
        "CROSSWALK_MIN_LINE_LENGTH": 45,
        "CROSSWALK_MAX_LINE_GAP": 8,
        "CROSSWALK_STRIPE_ANGLE_TOLERANCE": 12,
        "CROSSWALK_STRIPE_SPACING_MIN": 25,
        "CROSSWALK_STRIPE_SPACING_MAX": 90,

        # Validation Toggles
        "ENABLE_LINEARITY_VALIDATION": True,
        "ENABLE_SPATIAL_VALIDATION": True,
        "ENABLE_ORIENTATION_VALIDATION": True,
        "ENABLE_EDGE_VALIDATION": False,
    }
)


# SAMSARA Camera Profile
SAMSARA_PROFILE = CameraProfile(
    name="samsara",
    params={
        # HSV Color Thresholds - NIGHT OPTIMIZED (detect reflective markings)
        "YELLOW_HSV_LOWER": [10, 50, 70],
        "YELLOW_HSV_UPPER": [31, 255, 255],
        "WHITE_HSV_LOWER": [0, 0, 140],
        "WHITE_HSV_UPPER": [180, 50, 255],

        # Lane Validation - VERY RELAXED FOR NIGHT (highway lanes may curve)
        "MIN_LANE_LINEARITY_SCORE": 0.55,  # Very permissive
        "LANE_EDGE_MARGIN_PERCENT": 0.01,
        "LANE_MIN_Y_PERCENT": 0.20,
        "LANE_ORIENTATION_TOLERANCE_DEG": 70,  # Very permissive for night angles

        # Mask Area Threshold - VERY LOW for night + dimension filtering
        "MIN_MASK_AREA_PX": 50,  # Catch small dashed segments (60-127px range)
        "MIN_LANE_WIDTH_PX": 5,  # Noise rejection: reject 1-2px specks
        "MIN_LANE_HEIGHT_PX": 5,  # Noise rejection: reject 1-2px specks

        # Double Yellow Validation - NIGHT with max constraints
        "MIN_DOUBLE_YELLOW_WIDTH": 20,
        "MAX_DOUBLE_YELLOW_WIDTH": 150,  # Reject massive blobs
        "MIN_DOUBLE_YELLOW_HEIGHT": 40,
        "MIN_DOUBLE_YELLOW_AREA_PX": 800,
        "MAX_DOUBLE_YELLOW_AREA_PX": 15000,  # Reject huge yellow surfaces
        "MIN_DOUBLE_YELLOW_ASPECT_RATIO": 1.5,  # height/width > 1.5 (elongated)

        # Crosswalk Detection - RELAXED FOR NIGHT (partial visibility)
        "CROSSWALK_MIN_Y_PERCENT": 0.38,
        "CROSSWALK_MIN_STRIPES": 2,  # Relaxed for night
        "CROSSWALK_HOUGH_THRESHOLD": 35,  # Lower for fainter lines
        "CROSSWALK_MIN_LINE_LENGTH": 50,
        "CROSSWALK_MAX_LINE_GAP": 8,
        "CROSSWALK_STRIPE_ANGLE_TOLERANCE": 10,
        "CROSSWALK_STRIPE_SPACING_MIN": 30,
        "CROSSWALK_STRIPE_SPACING_MAX": 85,

        # Validation Toggles
        "ENABLE_LINEARITY_VALIDATION": True,
        "ENABLE_SPATIAL_VALIDATION": True,
        "ENABLE_ORIENTATION_VALIDATION": True,
        "ENABLE_EDGE_VALIDATION": False,
    }
)


# VERIZON Camera Profile
VERIZON_PROFILE = CameraProfile(
    name="verizon",
    params={
        # HSV Color Thresholds - NIGHT OPTIMIZED (reflective markings)
        "YELLOW_HSV_LOWER": [12, 70, 90],  # Relaxed for night
        "YELLOW_HSV_UPPER": [28, 255, 255],
        "WHITE_HSV_LOWER": [0, 0, 150],  # Much more permissive for night
        "WHITE_HSV_UPPER": [180, 35, 255],

        # Lane Validation - VERY RELAXED FOR NIGHT
        "MIN_LANE_LINEARITY_SCORE": 0.60,  # Very permissive
        "LANE_EDGE_MARGIN_PERCENT": 0.05,  # Avoid dashboard area
        "LANE_MIN_Y_PERCENT": 0.30,
        "LANE_ORIENTATION_TOLERANCE_DEG": 70,  # Very permissive for night angles

        # Mask Area Threshold - VERY LOW for night + dimension filtering
        "MIN_MASK_AREA_PX": 50,  # Catch small dashed segments (28-140px range)
        "MIN_LANE_WIDTH_PX": 5,  # Noise rejection: reject 1-2px specks
        "MIN_LANE_HEIGHT_PX": 5,  # Noise rejection: reject 1-2px specks

        # Double Yellow Validation - NIGHT with max constraints
        "MIN_DOUBLE_YELLOW_WIDTH": 20,
        "MAX_DOUBLE_YELLOW_WIDTH": 150,  # Reject massive blobs
        "MIN_DOUBLE_YELLOW_HEIGHT": 40,
        "MIN_DOUBLE_YELLOW_AREA_PX": 800,
        "MAX_DOUBLE_YELLOW_AREA_PX": 15000,  # Reject huge yellow surfaces
        "MIN_DOUBLE_YELLOW_ASPECT_RATIO": 1.5,  # height/width > 1.5 (elongated)

        # Crosswalk Detection - RELAXED FOR NIGHT
        "CROSSWALK_MIN_Y_PERCENT": 0.35,  # Less restrictive
        "CROSSWALK_MIN_STRIPES": 2,  # Relaxed for night
        "CROSSWALK_HOUGH_THRESHOLD": 40,  # Lower for fainter lines
        "CROSSWALK_MIN_LINE_LENGTH": 55,
        "CROSSWALK_MAX_LINE_GAP": 6,
        "CROSSWALK_STRIPE_ANGLE_TOLERANCE": 10,
        "CROSSWALK_STRIPE_SPACING_MIN": 30,
        "CROSSWALK_STRIPE_SPACING_MAX": 80,

        # Validation Toggles
        "ENABLE_LINEARITY_VALIDATION": True,
        "ENABLE_SPATIAL_VALIDATION": True,
        "ENABLE_ORIENTATION_VALIDATION": True,
        "ENABLE_EDGE_VALIDATION": False,
    }
)


# Profile registry
CAMERA_PROFILES = {
    "lytx": LYTX_PROFILE,
    "netradyne": NETRADYNE_PROFILE,
    "samsara": SAMSARA_PROFILE,
    "verizon": VERIZON_PROFILE,
}


def get_camera_profile(camera_type: str) -> CameraProfile:
    """Get parameter profile for a camera type.

    Args:
        camera_type: Camera identifier (lytx, netradyne, samsara, verizon)

    Returns:
        CameraProfile instance with optimized parameters
        Falls back to LYTX profile if camera type not found
    """
    camera_type_lower = camera_type.lower()

    if camera_type_lower in CAMERA_PROFILES:
        return CAMERA_PROFILES[camera_type_lower]

    # Fallback to LYTX profile (baseline)
    print(f"Warning: Unknown camera type '{camera_type}', using LYTX profile as fallback")
    return LYTX_PROFILE


def detect_camera_from_filename(filename: str) -> str:
    """Detect camera type from video filename.

    Args:
        filename: Video filename (e.g., "lytx_1.mp4", "data/train/netradyne_2.mp4")

    Returns:
        Camera type string (lytx, netradyne, samsara, or verizon)
    """
    filename_lower = filename.lower()

    for camera_type in CAMERA_PROFILES.keys():
        if camera_type in filename_lower:
            return camera_type

    # Default to lytx if can't determine
    return "lytx"

