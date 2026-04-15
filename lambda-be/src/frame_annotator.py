"""Frame annotation utilities for pipeline use.

Extracted from ui/src/visualization.py for use in the main pipeline.
Annotates frames with bounding boxes and labels for LLM input.
"""
import cv2
import numpy as np
import logging
from typing import List, Tuple, Any
from pathlib import Path

from src.schemas import ObjectLabel

logger = logging.getLogger(__name__)


class FrameAnnotator:
    """Annotates frames with bounding boxes and labels for LLM processing."""

    # Color scheme for priority levels (BGR format)
    PRIORITY_COLORS = {
        'critical': (0, 0, 255),       # Bright Red
        'high': (0, 69, 255),          # Orange-Red
        'medium': (255, 140, 0),       # Deep Sky Blue
        'low': (0, 180, 0),            # Dark Green
        'none': (128, 128, 128)        # Gray
    }

    # Color for different object types (BGR format)
    OBJECT_COLORS = {
        'pedestrian': (128, 0, 200),      # Purple
        'bicyclist': (200, 0, 150),       # Magenta
        'vehicle': (0, 128, 255),         # Orange
        'truck': (255, 100, 0),           # Deep Blue
        'bus': (180, 0, 180),             # Purple-Magenta
        'motorcycle': (128, 128, 0),      # Teal
        'lane': (0, 150, 0),              # Dark Green
        'lane(current)': (0, 200, 100),   # Cyan-Green
        'double_yellow': (50, 50, 255),   # Bright Red
        'crosswalk': (255, 150, 0),       # Deep Sky Blue
        'traffic_signal': (0, 100, 200),  # Dark Orange
        'stop_sign': (0, 0, 180),         # Dark Red
        'construction': (0, 140, 255),    # Orange
        'default': (100, 100, 100)        # Gray
    }

    def __init__(self, bbox_thickness: int = 2, font_scale: float = 0.6):
        """Initialize frame annotator.

        Args:
            bbox_thickness: Thickness of bounding box lines
            font_scale: Scale factor for text labels
        """
        self.bbox_thickness = bbox_thickness
        self.font_scale = font_scale
        self.font = cv2.FONT_HERSHEY_SIMPLEX

    def get_color_for_object(self, obj: ObjectLabel) -> Tuple[int, int, int]:
        """Get color for an object based on priority or type.

        Args:
            obj: ObjectLabel instance

        Returns:
            BGR color tuple
        """
        # First try to match by exact description
        description = obj.description.lower()
        if description in self.OBJECT_COLORS:
            return self.OBJECT_COLORS[description]

        # Then try priority-based coloring for high-priority objects
        priority = obj.priority
        if priority in ['critical', 'high', 'medium'] and priority in self.PRIORITY_COLORS:
            return self.PRIORITY_COLORS[priority]

        # Fallback to partial object type matching
        for key, color in self.OBJECT_COLORS.items():
            if key in description:
                return color

        return self.OBJECT_COLORS['default']

    def draw_bbox(
        self,
        image: np.ndarray,
        obj: ObjectLabel,
        color: Tuple[int, int, int]
    ) -> np.ndarray:
        """Draw bounding box on image.

        Args:
            image: Image array
            obj: ObjectLabel instance
            color: BGR color tuple

        Returns:
            Modified image
        """
        img_height, img_width = image.shape[:2]

        # Get bounding box coordinates and validate they're within image bounds
        x_min = int(max(0, min(obj.x_min, img_width)))
        y_min = int(max(0, min(obj.y_min, img_height)))
        x_max = int(max(0, min(obj.x_max, img_width)))
        y_max = int(max(0, min(obj.y_max, img_height)))

        # Validate box is not inverted
        if x_max <= x_min or y_max <= y_min:
            logger.warning(f"Invalid bbox for {obj.description}: "
                          f"({x_min}, {y_min}, {x_max}, {y_max})")
            return image

        # Draw rectangle
        cv2.rectangle(
            image,
            (x_min, y_min),
            (x_max, y_max),
            color,
            self.bbox_thickness
        )

        # Draw center point if available
        if obj.center:
            cx = int(max(0, min(obj.center.x, img_width)))
            cy = int(max(0, min(obj.center.y, img_height)))
            cv2.circle(image, (cx, cy), 4, color, -1)

        return image

    def draw_label(
        self,
        image: np.ndarray,
        obj: ObjectLabel,
        color: Tuple[int, int, int]
    ) -> np.ndarray:
        """Draw label text on image.

        Args:
            image: Image array
            obj: ObjectLabel instance
            color: BGR color tuple

        Returns:
            Modified image
        """
        img_height, img_width = image.shape[:2]

        # Prepare label text
        description = obj.description
        priority = obj.priority
        distance = obj.distance

        # Format label (show priority if critical/high, otherwise just description and distance)
        if priority in ['critical', 'high']:
            label = f"{description} [{priority}] ({distance})"
        else:
            label = f"{description} ({distance})"

        # Get position (above bbox)
        x_min = int(max(0, min(obj.x_min, img_width)))
        y_min = int(max(0, min(obj.y_min, img_height)))

        # Calculate text size
        (text_width, text_height), baseline = cv2.getTextSize(
            label,
            self.font,
            self.font_scale,
            thickness=2
        )

        # Draw background rectangle for text
        bg_y_min = max(0, y_min - text_height - baseline - 8)
        bg_y_max = max(bg_y_min + text_height + baseline + 6, y_min - 2)
        bg_x_min = x_min
        bg_x_max = min(img_width, x_min + text_width + 8)

        # Ensure background is visible (minimum size)
        if bg_y_max - bg_y_min < 20:
            bg_y_min = max(0, bg_y_max - 20)

        cv2.rectangle(
            image,
            (bg_x_min, bg_y_min),
            (bg_x_max, bg_y_max),
            color,
            -1  # Filled
        )

        # Draw text in white (good contrast with all dark colors)
        text_y = min(img_height - 5, bg_y_max - baseline - 2)
        cv2.putText(
            image,
            label,
            (bg_x_min + 4, text_y),
            self.font,
            self.font_scale,
            (255, 255, 255),  # White text
            thickness=2,
            lineType=cv2.LINE_AA
        )

        return image

    def annotate_frame(
        self,
        frame_path: str,
        objects: List[ObjectLabel],
        output_path: str,
        timestamp_ms: float = None
    ) -> str:
        """Annotate frame with all detected objects and save.

        Args:
            frame_path: Path to input frame image
            objects: List of ObjectLabel instances to draw
            output_path: Path where annotated image will be saved
            timestamp_ms: Optional timestamp to display on frame

        Returns:
            Path to saved annotated image

        Raises:
            ValueError: If frame cannot be read
        """
        # Read image
        image = cv2.imread(frame_path)
        if image is None:
            raise ValueError(f"Cannot read image: {frame_path}")

        # Sort objects by distance (far to close) so closer objects are drawn on top
        distance_order = {
            'very_far': 0,
            'far': 1,
            'mid': 2,
            'near': 3,
            'danger_close': 4,
            'n/a': 5,
        }
        sorted_objects = sorted(
            objects,
            key=lambda x: distance_order.get(x.distance, 2)
        )

        # Draw each object
        for obj in sorted_objects:
            color = self.get_color_for_object(obj)

            # Draw bounding box
            image = self.draw_bbox(image, obj, color)

            # Draw label
            image = self.draw_label(image, obj, color)

        # Add timestamp overlay if provided
        if timestamp_ms is not None:
            timestamp_text = f"Timestamp: {timestamp_ms:.2f}ms"
            cv2.putText(
                image,
                timestamp_text,
                (10, 30),
                self.font,
                0.7,
                (255, 255, 255),
                thickness=2,
                lineType=cv2.LINE_AA
            )

        # Add object count
        count_text = f"Objects: {len(objects)}"
        cv2.putText(
            image,
            count_text,
            (10, 60),
            self.font,
            0.7,
            (255, 255, 255),
            thickness=2,
            lineType=cv2.LINE_AA
        )

        # Ensure output directory exists
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save annotated image
        success = cv2.imwrite(output_path, image)
        if not success:
            raise ValueError(f"Failed to save annotated image to {output_path}")

        logger.debug(f"Saved annotated frame to {output_path}")
        return output_path

