"""Frame preprocessing pipeline for improving detection quality.

Applies computer-vision preprocessing to frames before object detection:
- CLAHE histogram equalisation on luminance channel
- Unsharp-mask sharpening
- Brightness normalisation
- Optional 2x2 grid cropping with overlap for small-object detection
"""
import logging
from typing import List, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLAHE contrast enhancement
# ---------------------------------------------------------------------------

def enhance_contrast(
    frame: np.ndarray,
    clip_limit: float = 2.0,
    tile_grid_size: Tuple[int, int] = (8, 8),
) -> np.ndarray:
    """Apply CLAHE on the L channel of LAB colour space.

    Args:
        frame: BGR image (uint8).
        clip_limit: CLAHE clip limit.
        tile_grid_size: Grid size for CLAHE tiles.

    Returns:
        Contrast-enhanced BGR image.
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
    l_enhanced = clahe.apply(l_channel)

    merged = cv2.merge([l_enhanced, a_channel, b_channel])
    result = cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
    return result


# ---------------------------------------------------------------------------
# Sharpening (unsharp mask)
# ---------------------------------------------------------------------------

def sharpen(frame: np.ndarray, sigma: float = 1.0, strength: float = 1.5) -> np.ndarray:
    """Apply unsharp-mask sharpening.

    Args:
        frame: BGR image (uint8).
        sigma: Gaussian blur sigma.
        strength: Sharpening strength multiplier.

    Returns:
        Sharpened BGR image.
    """
    blurred = cv2.GaussianBlur(frame, (0, 0), sigma)
    sharpened = cv2.addWeighted(frame, 1.0 + strength, blurred, -strength, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Brightness normalisation
# ---------------------------------------------------------------------------

def normalize_brightness(frame: np.ndarray, target_mean: float = 127.0) -> np.ndarray:
    """Normalise frame to a consistent mean brightness.

    Args:
        frame: BGR image (uint8).
        target_mean: Desired mean luminance value.

    Returns:
        Brightness-adjusted BGR image.
    """
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel = lab[:, :, 0].astype(np.float32)
    current_mean = l_channel.mean()
    if current_mean == 0:
        return frame

    scale = target_mean / current_mean
    l_channel = np.clip(l_channel * scale, 0, 255).astype(np.uint8)
    lab[:, :, 0] = l_channel
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


# ---------------------------------------------------------------------------
# Grid cropping with overlap (for small-object detection)
# ---------------------------------------------------------------------------

def generate_crops(
    frame: np.ndarray,
    grid_size: int = 2,
    overlap_frac: float = 0.25,
) -> List[Tuple[np.ndarray, Tuple[int, int, int, int]]]:
    """Split frame into overlapping grid crops.

    Args:
        frame: BGR image.
        grid_size: Number of divisions per axis (2 → 4 crops).
        overlap_frac: Fraction of overlap between adjacent crops.

    Returns:
        List of (crop_image, (x_offset, y_offset, crop_w, crop_h)).
    """
    h, w = frame.shape[:2]
    crop_h = h // grid_size
    crop_w = w // grid_size
    overlap_h = int(crop_h * overlap_frac)
    overlap_w = int(crop_w * overlap_frac)

    crops = []
    for row in range(grid_size):
        for col in range(grid_size):
            y1 = max(0, row * crop_h - overlap_h)
            x1 = max(0, col * crop_w - overlap_w)
            y2 = min(h, (row + 1) * crop_h + overlap_h)
            x2 = min(w, (col + 1) * crop_w + overlap_w)

            crop = frame[y1:y2, x1:x2].copy()
            crops.append((crop, (x1, y1, x2 - x1, y2 - y1)))

    return crops


# ---------------------------------------------------------------------------
# Full preprocessing pipeline
# ---------------------------------------------------------------------------

def preprocess_frame(
    frame: np.ndarray,
    do_contrast: bool = True,
    do_sharpen: bool = True,
    do_brightness: bool = True,
) -> np.ndarray:
    """Apply the full preprocessing pipeline to a single frame.

    Args:
        frame: BGR image (uint8).
        do_contrast: Apply CLAHE contrast enhancement.
        do_sharpen: Apply unsharp-mask sharpening.
        do_brightness: Apply brightness normalisation.

    Returns:
        Preprocessed BGR image.
    """
    result = frame.copy()
    if do_contrast:
        result = enhance_contrast(result)
    if do_brightness:
        result = normalize_brightness(result)
    if do_sharpen:
        result = sharpen(result)
    return result
