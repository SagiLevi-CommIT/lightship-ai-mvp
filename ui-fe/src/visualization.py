"""Visualization utilities for annotating frames with bounding boxes and labels.

Similar to ground truth annotations shown in train data.
"""
import cv2
import numpy as np
import logging
from typing import List, Dict, Tuple, Any
import tempfile
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class FrameVisualizer:
    """Visualizes objects on frames with bounding boxes and labels."""

    # Color scheme for priority levels (BGR format)
    # Using colors with good contrast against white text (NO YELLOW - hard to see)
    PRIORITY_COLORS = {
        'critical': (0, 0, 255),       # Bright Red (highest priority)
        'high': (0, 69, 255),          # Orange-Red
        'medium': (255, 140, 0),       # Deep Sky Blue
        'low': (0, 180, 0),            # Dark Green
        'none': (128, 128, 128)        # Gray (neutral)
    }

    # Color for different object types (BGR format, diverse colors, NO YELLOW)
    OBJECT_COLORS = {
        'pedestrian': (128, 0, 200),      # Purple
        'bicyclist': (200, 0, 150),       # Magenta
        'vehicle': (0, 128, 255),         # Orange
        'truck': (255, 100, 0),           # Deep Blue
        'bus': (180, 0, 180),             # Purple-Magenta
        'motorcycle': (128, 128, 0),      # Teal
        'lane': (0, 150, 0),              # Dark Green
        'lane(current)': (0, 200, 100),   # Cyan-Green (distinct from lane)
        'double_yellow': (50, 50, 255),   # Bright Red (high visibility)
        'crosswalk': (255, 150, 0),       # Deep Sky Blue (distinct)
        'traffic_signal': (0, 100, 200),  # Dark Orange
        'stop_sign': (0, 0, 180),         # Dark Red
        'construction': (0, 140, 255),    # Orange
        'default': (100, 100, 100)        # Gray
    }

    def __init__(self, bbox_thickness: int = 2, font_scale: float = 0.6):
        """Initialize visualizer.

        Args:
            bbox_thickness: Thickness of bounding box lines
            font_scale: Scale factor for text labels
        """
        self.bbox_thickness = bbox_thickness
        self.font_scale = font_scale
        self.font = cv2.FONT_HERSHEY_SIMPLEX
        self.temp_dir = tempfile.mkdtemp()

    def get_color_for_object(self, obj: Dict[str, Any]) -> Tuple[int, int, int]:
        """Get color for an object based on priority or type.

        Args:
            obj: Object dictionary

        Returns:
            BGR color tuple
        """
        # First try to match by exact description (for lanes, double_yellow, etc.)
        description = obj.get('description', '').lower()
        if description in self.OBJECT_COLORS:
            return self.OBJECT_COLORS[description]

        # Then try priority-based coloring for traffic objects with high priority
        priority = obj.get('priority', 'none')
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
        obj: Dict[str, Any],
        color: Tuple[int, int, int]
    ) -> np.ndarray:
        """Draw bounding box on image.

        Args:
            image: Image array
            obj: Object dictionary with bbox coordinates
            color: BGR color tuple

        Returns:
            Modified image
        """
        img_height, img_width = image.shape[:2]

        # Get bounding box coordinates and validate they're within image bounds
        x_min = int(max(0, min(obj.get('x_min', 0), img_width)))
        y_min = int(max(0, min(obj.get('y_min', 0), img_height)))
        x_max = int(max(0, min(obj.get('x_max', 0), img_width)))
        y_max = int(max(0, min(obj.get('y_max', 0), img_height)))

        # Validate box is not inverted
        if x_max <= x_min or y_max <= y_min:
            logger.warning(f"Invalid bbox for {obj.get('description', 'unknown')}: "
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
        center = obj.get('center')
        if center:
            cx = int(max(0, min(center.get('x', 0), img_width)))
            cy = int(max(0, min(center.get('y', 0), img_height)))
            cv2.circle(image, (cx, cy), 4, color, -1)

        return image

    def draw_label(
        self,
        image: np.ndarray,
        obj: Dict[str, Any],
        color: Tuple[int, int, int]
    ) -> np.ndarray:
        """Draw label text on image.

        Args:
            image: Image array
            obj: Object dictionary
            color: BGR color tuple

        Returns:
            Modified image
        """
        img_height, img_width = image.shape[:2]

        # Prepare label text
        description = obj.get('description', 'unknown')
        priority = obj.get('priority', '')
        distance = obj.get('distance', '')

        # Format label (show priority if critical/high, otherwise just description and distance)
        if priority in ['critical', 'high']:
            label = f"{description} [{priority}] ({distance})"
        else:
            label = f"{description} ({distance})"

        # Get position (above bbox)
        x_min = int(max(0, min(obj.get('x_min', 0), img_width)))
        y_min = int(max(0, min(obj.get('y_min', 0), img_height)))

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
        objects: List[Dict[str, Any]],
        timestamp: float
    ) -> Tuple[np.ndarray, str]:
        """Annotate frame with all detected objects.

        Args:
            frame_path: Path to frame image
            objects: List of object dictionaries
            timestamp: Frame timestamp in milliseconds

        Returns:
            Tuple of (annotated_image_array, path_to_saved_image)
        """
        # Read image
        image = cv2.imread(frame_path)
        if image is None:
            raise ValueError(f"Cannot read image: {frame_path}")

        # Sort objects by distance (far to close) so closer objects are drawn on top
        distance_order = {
            'very_far': 0,
            'far': 1,
            'moderate': 2,
            'close': 3,
            'very_close': 4,
            'dangerously_close': 5,
            'n/a': 6  # Draw last (usually lane markings, should be on top)
        }
        sorted_objects = sorted(
            objects,
            key=lambda x: distance_order.get(x.get('distance', 'moderate'), 2)
        )

        # Draw each object
        for obj in sorted_objects:
            color = self.get_color_for_object(obj)

            # Draw bounding box
            image = self.draw_bbox(image, obj, color)

            # Draw label
            image = self.draw_label(image, obj, color)

        # Add timestamp overlay
        timestamp_text = f"Timestamp: {timestamp:.2f}ms"
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

        # Save annotated image
        output_filename = f"annotated_{int(timestamp)}.png"
        output_path = os.path.join(self.temp_dir, output_filename)
        cv2.imwrite(output_path, image)

        # Convert BGR to RGB for Streamlit display
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        return image_rgb, output_path

    def create_video_with_overlays(
        self,
        video_path: str,
        objects_by_timestamp: Dict[float, List[Dict[str, Any]]],
        output_path: str,
        fps: float
    ) -> str:
        """Create video with overlay annotations.

        Args:
            video_path: Path to original video
            objects_by_timestamp: Dictionary mapping timestamps to objects
            output_path: Path to save annotated video
            fps: Video FPS

        Returns:
            Path to annotated video
        """
        # Open video
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_path}")

        # Get video properties
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Create video writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Calculate timestamp
            timestamp_ms = (frame_idx / fps) * 1000

            # Find objects near this timestamp (within 100ms)
            objects_to_draw = []
            for ts, objects in objects_by_timestamp.items():
                if abs(ts - timestamp_ms) < 100:
                    objects_to_draw.extend(objects)

            # Annotate frame if objects present
            if objects_to_draw:
                for obj in objects_to_draw:
                    color = self.get_color_for_object(obj)
                    frame = self.draw_bbox(frame, obj, color)
                    frame = self.draw_label(frame, obj, color)

            # Write frame
            out.write(frame)
            frame_idx += 1

        # Release resources
        cap.release()
        out.release()

        return output_path

    def cleanup(self):
        """Clean up temporary files."""
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

