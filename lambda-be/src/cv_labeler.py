"""CV Labeler module using YOLO11, Depth-Anything-V2, and road geometry detection.

Replaces the V1 LLM-based image labeler with classical CV and open-source models.
"""
import logging
from typing import List, Dict, Optional, Tuple
import numpy as np
import cv2
from ultralytics import YOLO
import torch
from transformers import AutoImageProcessor, AutoModelForDepthEstimation
from PIL import Image

from src.schemas import ObjectLabel, Center, PolygonPoint
from src.config import (
    MIN_DET_CONF,
    NMS_IOU_THRESHOLD,
    MIN_MASK_AREA_PX,
    DISTANCE_THRESHOLDS,
    REFERENCE_OBJECT_PRIORS,
    HAZARD_SEVERITY_RANK,
    MAX_LANES_PER_FRAME,
    MAX_DOUBLE_YELLOW_PER_FRAME,
    MAX_CROSSWALKS_PER_FRAME,
    LANE_CLUSTERING_THRESHOLD_PX,
    MIN_LANE_ASPECT_RATIO,
    MAX_LANE_ASPECT_RATIO,
    GEOMETRY_NMS_IOU_THRESHOLD
)
from src.camera_profiles import CameraProfile, get_camera_profile, detect_camera_from_filename

logger = logging.getLogger(__name__)


# COCO class ID to Lightship label mapping
COCO_TO_LIGHTSHIP = {
    0: "pedestrian",        # person
    1: "bicyclist",         # bicycle
    2: "vehicle",           # car
    3: "motorcycle",        # motorcycle
    5: "bus",               # bus
    7: "truck",             # truck
    9: "traffic_signal",    # traffic light
    11: "stop_sign",        # stop sign
    # Add more mappings as needed
}


class CVLabeler:
    """Labels objects in frames using CV models (YOLO + Depth-Anything)."""

    def __init__(self, camera_profile: CameraProfile = None):
        """Initialize CVLabeler with YOLO and Depth-Anything models.

        Args:
            camera_profile: Optional CameraProfile for camera-specific parameters.
                          If None, uses default LYTX profile.
        """
        logger.info("Initializing CVLabeler...")

        # Set camera profile (use LYTX as default)
        if camera_profile is None:
            from src.camera_profiles import LYTX_PROFILE
            camera_profile = LYTX_PROFILE

        self.profile = camera_profile
        logger.info(f"Using camera profile: {self.profile.name}")

        # Load YOLO11 model
        self.detector = self._load_yolo()

        # Load Depth-Anything-V2
        self.depth_processor, self.depth_model = self._load_depth_anything()

        logger.info("CVLabeler initialized successfully")

    def _load_yolo(self) -> YOLO:
        """Load YOLO11 detection model.

        Returns:
            Loaded YOLO model
        """
        logger.info("Loading YOLO11n model...")
        model = YOLO('yolo11n.pt')  # Nano model for speed
        logger.info(f"YOLO model loaded: {model.model_name}")
        return model

    def _load_depth_anything(self) -> Tuple:
        """Load Depth-Anything-V2-Small model.

        Returns:
            Tuple of (processor, model)
        """
        logger.info("Loading Depth-Anything-V2-Small model...")
        model_name = "depth-anything/Depth-Anything-V2-Small-hf"

        processor = AutoImageProcessor.from_pretrained(model_name)
        model = AutoModelForDepthEstimation.from_pretrained(model_name)

        # Move to GPU if available
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = model.to(device)
        logger.info(f"Depth-Anything model loaded on {device}")

        return processor, model

    def label_frame(
        self,
        frame_path: str,
        timestamp_ms: float,
        video_width: int,
        video_height: int,
        frame_height: int = None
    ) -> List[ObjectLabel]:
        """Label objects in a single frame using CV models.

        Args:
            frame_path: Path to frame image file
            timestamp_ms: Timestamp of frame in milliseconds
            video_width: Video width in pixels
            video_height: Video height in pixels

        Returns:
            List of ObjectLabel instances for detected objects
        """
        logger.info(f"CV Labeling frame: {frame_path} at {timestamp_ms:.2f}ms")

        # Read frame
        frame_bgr = cv2.imread(frame_path)
        if frame_bgr is None:
            logger.error(f"Failed to read frame: {frame_path}")
            return []

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        # Run YOLO detection
        detections = self._detect_objects(frame_rgb)

        # Run depth estimation
        depth_map = self._estimate_depth(frame_rgb)

        # Calibrate depth scale
        scale_factor = self._calibrate_depth(detections, depth_map, frame_rgb.shape)

        # Detect road geometry
        road_geometry = self._detect_road_geometry(frame_rgb, frame_bgr, timestamp_ms)

        # Build object instances
        objects = []
        for det in detections:
            # Compute distance (pass frame height for resolution-independent thresholds)
            distance = self._compute_distance(det, depth_map, scale_factor, frame_rgb.shape[0])

            # Create ObjectLabel (priority will be set later by HazardAssessor)
            obj = ObjectLabel(
                description=det['label'],
                start_time_ms=timestamp_ms,
                distance=distance,
                priority="none",  # Will be updated by hazard assessor
                center=Center(x=det['center_x'], y=det['center_y']),
                polygon=[],  # YOLO provides bbox, not polygon
                x_min=det['x_min'],
                y_min=det['y_min'],
                x_max=det['x_max'],
                y_max=det['y_max'],
                width=det['width'],
                height=det['height']
            )
            objects.append(obj)

        # Add road geometry objects
        objects.extend(road_geometry)

        logger.info(f"Detected {len(objects)} objects ({len(detections)} traffic, {len(road_geometry)} geometry) in frame")
        return objects

    def _detect_objects(self, frame_rgb: np.ndarray) -> List[Dict]:
        """Run YOLO object detection.

        Args:
            frame_rgb: RGB frame as numpy array

        Returns:
            List of detection dictionaries
        """
        # Run YOLO inference
        results = self.detector(frame_rgb, conf=MIN_DET_CONF, iou=NMS_IOU_THRESHOLD, verbose=False)

        detections = []
        for result in results:
            boxes = result.boxes

            for box in boxes:
                # Get bbox coordinates
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])

                # Map COCO class to Lightship label
                label = COCO_TO_LIGHTSHIP.get(cls_id, f"object_{cls_id}")

                # Calculate center
                center_x = int((x1 + x2) / 2)
                center_y = int((y1 + y2) / 2)

                detection = {
                    'label': label,
                    'confidence': conf,
                    'x_min': float(x1),
                    'y_min': float(y1),
                    'x_max': float(x2),
                    'y_max': float(y2),
                    'width': float(x2 - x1),
                    'height': float(y2 - y1),
                    'center_x': center_x,
                    'center_y': center_y,
                    'class_id': cls_id
                }
                detections.append(detection)

        return detections

    def _estimate_depth(self, frame_rgb: np.ndarray) -> np.ndarray:
        """Estimate depth map using Depth-Anything-V2.

        Args:
            frame_rgb: RGB frame as numpy array

        Returns:
            Depth map as numpy array
        """
        # Convert to PIL Image
        image_pil = Image.fromarray(frame_rgb)

        # Prepare inputs
        inputs = self.depth_processor(images=image_pil, return_tensors="pt")

        # Move to device
        device = next(self.depth_model.parameters()).device
        inputs = {k: v.to(device) for k, v in inputs.items()}

        # Run inference
        with torch.no_grad():
            outputs = self.depth_model(**inputs)
            predicted_depth = outputs.predicted_depth

        # Interpolate to original size
        depth_map = torch.nn.functional.interpolate(
            predicted_depth.unsqueeze(1),
            size=frame_rgb.shape[:2],
            mode="bicubic",
            align_corners=False,
        ).squeeze()

        return depth_map.cpu().numpy()

    def _calibrate_depth(
        self,
        detections: List[Dict],
        depth_map: np.ndarray,
        frame_shape: Tuple[int, int, int]
    ) -> float:
        """Calibrate depth scale using reference object size priors.

        Args:
            detections: List of detected objects
            depth_map: Depth map from model
            frame_shape: Shape of the frame (H, W, C)

        Returns:
            Scale factor to convert relative depth to meters
        """
        # Find reference objects (high confidence, known size)
        reference_candidates = []

        for det in detections:
            label = det['label']
            if label in REFERENCE_OBJECT_PRIORS and det['confidence'] > 0.5:
                prior = REFERENCE_OBJECT_PRIORS[label]

                # Get median depth in bbox
                y1, y2 = int(det['y_min']), int(det['y_max'])
                x1, x2 = int(det['x_min']), int(det['x_max'])

                # Clip to valid range
                y1, y2 = max(0, y1), min(depth_map.shape[0], y2)
                x1, x2 = max(0, x1), min(depth_map.shape[1], x2)

                if y2 > y1 and x2 > x1:
                    bbox_depth = np.median(depth_map[y1:y2, x1:x2])

                    # Estimate pixel size
                    if prior['dimension'] == 'height':
                        pixel_size = det['height']
                    else:  # width
                        pixel_size = det['width']

                    # Calculate scale: real_size / (pixel_size * relative_depth)
                    avg_real_size = (prior['min'] + prior['max']) / 2
                    scale = avg_real_size / (pixel_size * bbox_depth) if bbox_depth > 0 else 0

                    reference_candidates.append({
                        'scale': scale,
                        'confidence': det['confidence'],
                        'label': label
                    })

        # Use weighted average of top candidates
        if reference_candidates:
            # Sort by confidence
            reference_candidates.sort(key=lambda x: x['confidence'], reverse=True)

            # Take top 3 or all if fewer
            top_refs = reference_candidates[:min(3, len(reference_candidates))]

            # Weighted average
            total_weight = sum(r['confidence'] for r in top_refs)
            if total_weight > 0:
                scale_factor = sum(r['scale'] * r['confidence'] for r in top_refs) / total_weight
                logger.debug(f"Depth calibrated using {len(top_refs)} references, scale={scale_factor:.4f}")
                return scale_factor

        # Fallback: use default scale (rough estimate)
        default_scale = 0.01  # Rough conversion factor
        logger.debug(f"No reference objects found, using default scale={default_scale}")
        return default_scale

    def _compute_distance(
        self,
        detection: Dict,
        depth_map: np.ndarray,
        scale_factor: float,
        frame_height: int
    ) -> str:
        """Compute distance bucket for a detection using object size heuristic.

        Uses resolution-independent thresholds based on object size as percentage
        of frame height.

        Args:
            detection: Detection dictionary
            depth_map: Depth map (currently unused - using size heuristic instead)
            scale_factor: Calibrated scale factor (currently unused)
            frame_height: Height of the frame in pixels (for resolution-independent thresholds)

        Returns:
            Distance bucket string
        """
        # Get bbox dimensions
        y1, y2 = int(detection['y_min']), int(detection['y_max'])
        x1, x2 = int(detection['x_min']), int(detection['x_max'])

        if y2 <= y1 or x2 <= x1:
            return "n/a"

        # Use object size in frame as proxy for distance
        # Larger object in frame = closer to camera
        height_px = detection['height']
        width_px = detection['width']

        # Heuristic: Use max(height, width) as primary distance indicator
        # This works well for vehicles, pedestrians, etc.
        size_indicator = max(height_px, width_px)

        # Calculate resolution-independent thresholds as percentages of frame height
        # These percentages are tuned based on empirical testing with 720p dashcam footage
        threshold_dangerously_close = frame_height * 0.555  # ~55% of frame height
        threshold_very_close = frame_height * 0.277        # ~28% of frame height
        threshold_close = frame_height * 0.138             # ~14% of frame height
        threshold_moderate = frame_height * 0.069          # ~7% of frame height
        threshold_far = frame_height * 0.034               # ~3.5% of frame height

        # Map size to distance buckets (resolution-independent)
        # Larger size = closer distance
        if size_indicator > threshold_dangerously_close:
            bucket = "dangerously_close"  # Very large in frame (>55% height)
        elif size_indicator > threshold_very_close:
            bucket = "very_close"  # Large in frame (>28% height)
        elif size_indicator > threshold_close:
            bucket = "close"  # Medium-large in frame (>14% height)
        elif size_indicator > threshold_moderate:
            bucket = "moderate"  # Medium in frame (>7% height)
        elif size_indicator > threshold_far:
            bucket = "far"  # Small in frame (>3.5% height)
        else:
            bucket = "very_far"  # Very small in frame (≤3.5% height)

        return bucket

    def _detect_road_geometry(
        self,
        frame_rgb: np.ndarray,
        frame_bgr: np.ndarray,
        timestamp_ms: float
    ) -> List[ObjectLabel]:
        """Detect road geometry (lanes, markings, crosswalks).

        Enhanced with clustering, merging, and crosswalk detection.

        Args:
            frame_rgb: RGB frame
            frame_bgr: BGR frame (for OpenCV operations)
            timestamp_ms: Frame timestamp

        Returns:
            List of road geometry ObjectLabel instances
        """
        geometry_objects = []

        # Detect lane markings (yellow and white) with improvements
        lane_markings = self._detect_lane_markings(frame_bgr)

        # Convert lane markings to ObjectLabel instances
        for marking in lane_markings:
            obj = ObjectLabel(
                description=marking['type'],
                start_time_ms=timestamp_ms,
                distance="n/a",
                priority="none",
                center=marking['center'],
                polygon=marking['polygon'],
                x_min=marking['x_min'],
                y_min=marking['y_min'],
                x_max=marking['x_max'],
                y_max=marking['y_max'],
                width=marking['width'],
                height=marking['height']
            )
            geometry_objects.append(obj)

        # Detect crosswalks using Hough Line Transform
        crosswalk_objects = self._detect_crosswalks(frame_bgr, timestamp_ms)
        geometry_objects.extend(crosswalk_objects)

        # Apply smart filtering to reduce redundancy
        geometry_objects = self._filter_geometry_objects(geometry_objects)

        return geometry_objects

    def _detect_lane_markings(self, frame_bgr: np.ndarray) -> List[Dict]:
        """Detect lane markings using color-based approach.

        Args:
            frame_bgr: BGR frame

        Returns:
            List of lane marking dictionaries
        """
        markings = []

        # Convert to HSV for color detection
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

        # Yellow lane marking detection (double yellow)
        # Use camera-specific HSV thresholds
        lower_yellow = np.array(self.profile["YELLOW_HSV_LOWER"])
        upper_yellow = np.array(self.profile["YELLOW_HSV_UPPER"])
        yellow_mask = cv2.inRange(hsv, lower_yellow, upper_yellow)

        # White lane marking detection
        # Use camera-specific HSV thresholds
        lower_white = np.array(self.profile["WHITE_HSV_LOWER"])
        upper_white = np.array(self.profile["WHITE_HSV_UPPER"])
        white_mask = cv2.inRange(hsv, lower_white, upper_white)

        # Focus on lower half of image (where road markings typically are)
        h = frame_bgr.shape[0]
        roi_start = h // 2

        yellow_mask[:roi_start, :] = 0
        white_mask[:roi_start, :] = 0

        # Process yellow markings (double_yellow)
        yellow_contours, _ = cv2.findContours(
            yellow_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        # Phase 1: Filter by area, aspect ratio, and validation checks
        yellow_contours_filtered = []
        min_area = self.profile.get("MIN_MASK_AREA_PX", MIN_MASK_AREA_PX)
        for contour in yellow_contours:
            area = cv2.contourArea(contour)
            if area > min_area:
                x, y, w, h = cv2.boundingRect(contour)

                # Dimension-based noise filtering (reject tiny specks)
                min_width = self.profile.get("MIN_LANE_WIDTH_PX", 0)
                min_height = self.profile.get("MIN_LANE_HEIGHT_PX", 0)
                if w < min_width or h < min_height:
                    continue

                aspect_ratio = max(w, h) / max(min(w, h), 1)  # Avoid division by zero

                # Keep only elongated shapes (lanes are thin and long)
                if MIN_LANE_ASPECT_RATIO < aspect_ratio < MAX_LANE_ASPECT_RATIO:
                    # Apply validation checks to reduce false positives
                    frame_h, frame_w = frame_bgr.shape[:2]
                    center_x = x + w / 2

                    # Check 1: Linearity (if enabled)
                    if self.profile["ENABLE_LINEARITY_VALIDATION"]:
                        if not self._validate_contour_linearity(contour):
                            continue

                    # Check 2: Spatial context (if enabled)
                    if self.profile["ENABLE_SPATIAL_VALIDATION"]:
                        if not self._validate_spatial_context(center_x, y, frame_w, frame_h):
                            continue

                    # Check 3: Orientation (if enabled)
                    if self.profile["ENABLE_ORIENTATION_VALIDATION"]:
                        if not self._validate_lane_orientation(contour):
                            continue

                    # All checks passed
                    yellow_contours_filtered.append(contour)

        # Phase 2: Cluster and merge nearby yellow markings
        if yellow_contours_filtered:
            yellow_clusters = self._cluster_contours_by_proximity(
                yellow_contours_filtered,
                LANE_CLUSTERING_THRESHOLD_PX
            )

            for cluster in yellow_clusters:
                merged_contour = self._merge_contour_cluster(cluster)
                x, y, w, h = cv2.boundingRect(merged_contour)

                # Double yellow size validation (reduce false positives)
                area = cv2.contourArea(merged_contour)
                min_width = self.profile.get("MIN_DOUBLE_YELLOW_WIDTH", 60)
                max_width = self.profile.get("MAX_DOUBLE_YELLOW_WIDTH", 9999)
                min_height = self.profile.get("MIN_DOUBLE_YELLOW_HEIGHT", 100)
                min_area = self.profile.get("MIN_DOUBLE_YELLOW_AREA_PX", 3500)
                max_area = self.profile.get("MAX_DOUBLE_YELLOW_AREA_PX", 999999)
                min_aspect = self.profile.get("MIN_DOUBLE_YELLOW_ASPECT_RATIO", 0.0)
                aspect_ratio = h / max(w, 1)

                if w < min_width:
                    logger.debug(f"Rejected double_yellow: width {w} < {min_width}")
                    continue
                if w > max_width:
                    logger.debug(f"Rejected double_yellow: width {w} > {max_width}")
                    continue
                if h < min_height:
                    logger.debug(f"Rejected double_yellow: height {h} < {min_height}")
                    continue
                if area < min_area:
                    logger.debug(f"Rejected double_yellow: area {area} < {min_area}")
                    continue
                if area > max_area:
                    logger.debug(f"Rejected double_yellow: area {area} > {max_area}")
                    continue
                if aspect_ratio < min_aspect:
                    logger.debug(f"Rejected double_yellow: aspect_ratio {aspect_ratio:.2f} < {min_aspect}")
                    continue

                # Simplify polygon
                epsilon = 0.01 * cv2.arcLength(merged_contour, True)
                approx = cv2.approxPolyDP(merged_contour, epsilon, True)

                # Convert to PolygonPoint list
                polygon = [
                    PolygonPoint(x=float(pt[0][0]), y=float(pt[0][1]))
                    for pt in approx
                ]

                marking = {
                    'type': 'double_yellow',
                    'center': Center(x=int(x + w/2), y=int(y + h/2)),
                    'polygon': polygon,
                    'x_min': float(x),
                    'y_min': float(y),
                    'x_max': float(x + w),
                    'y_max': float(y + h),
                    'width': float(w),
                    'height': float(h),
                    'area': float(w * h)
                }
                markings.append(marking)

        # Process white markings (lane)
        white_contours, _ = cv2.findContours(
            white_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        # Phase 1: Filter by area, aspect ratio, and validation checks
        white_contours_filtered = []
        min_area = self.profile.get("MIN_MASK_AREA_PX", MIN_MASK_AREA_PX)
        for contour in white_contours:
            area = cv2.contourArea(contour)

            if area > min_area:
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = max(w, h) / max(min(w, h), 1)  # Avoid division by zero

                # Keep only elongated shapes (lanes are thin and long)
                if MIN_LANE_ASPECT_RATIO < aspect_ratio < MAX_LANE_ASPECT_RATIO:
                    # Apply validation checks to reduce false positives
                    frame_h, frame_w = frame_bgr.shape[:2]
                    center_x = x + w / 2

                    # Check 1: Linearity (if enabled)
                    if self.profile["ENABLE_LINEARITY_VALIDATION"]:
                        if not self._validate_contour_linearity(contour):
                            continue

                    # Check 2: Spatial context (if enabled)
                    if self.profile["ENABLE_SPATIAL_VALIDATION"]:
                        if not self._validate_spatial_context(center_x, y, frame_w, frame_h):
                            continue

                    # Check 3: Orientation (if enabled)
                    if self.profile["ENABLE_ORIENTATION_VALIDATION"]:
                        if not self._validate_lane_orientation(contour):
                            continue

                    # All checks passed
                    white_contours_filtered.append(contour)

        # Phase 2: Cluster and merge nearby white markings
        if white_contours_filtered:
            white_clusters = self._cluster_contours_by_proximity(
                white_contours_filtered,
                LANE_CLUSTERING_THRESHOLD_PX
            )

            for cluster in white_clusters:
                merged_contour = self._merge_contour_cluster(cluster)
                x, y, w, h = cv2.boundingRect(merged_contour)

                # Simplify polygon
                epsilon = 0.01 * cv2.arcLength(merged_contour, True)
                approx = cv2.approxPolyDP(merged_contour, epsilon, True)

                # Convert to PolygonPoint list
                polygon = [
                    PolygonPoint(x=float(pt[0][0]), y=float(pt[0][1]))
                    for pt in approx
                ]

                # Determine if this is current lane or adjacent lane
                # Simple heuristic: if in center of image, it's current lane
                frame_center_x = frame_bgr.shape[1] / 2
                marking_center_x = x + w/2

                if abs(marking_center_x - frame_center_x) < frame_bgr.shape[1] * 0.2:
                    lane_type = 'lane(current)'
                else:
                    lane_type = 'lane'

                marking = {
                    'type': lane_type,
                    'center': Center(x=int(x + w/2), y=int(y + h/2)),
                    'polygon': polygon,
                    'x_min': float(x),
                    'y_min': float(y),
                    'x_max': float(x + w),
                    'y_max': float(y + h),
                    'width': float(w),
                    'height': float(h),
                    'area': float(w * h)
                }
                markings.append(marking)

        return markings

    def _cluster_contours_by_proximity(
        self,
        contours: List[np.ndarray],
        threshold_px: float
    ) -> List[List[np.ndarray]]:
        """Cluster contours that are spatially close to each other.

        Args:
            contours: List of OpenCV contours
            threshold_px: Distance threshold in pixels for clustering

        Returns:
            List of contour clusters (each cluster is a list of contours)
        """
        if len(contours) == 0:
            return []

        # Get centroids of all contours
        centroids = []
        for cnt in contours:
            M = cv2.moments(cnt)
            if M['m00'] > 0:
                cx = int(M['m10'] / M['m00'])
                cy = int(M['m01'] / M['m00'])
                centroids.append(np.array([cx, cy]))
            else:
                # Fallback: use bounding box center
                x, y, w, h = cv2.boundingRect(cnt)
                centroids.append(np.array([x + w//2, y + h//2]))

        # Simple clustering: group contours within threshold distance
        clusters = []
        used = set()

        for i, c1 in enumerate(centroids):
            if i in used:
                continue

            cluster = [contours[i]]
            used.add(i)

            for j in range(i + 1, len(centroids)):
                if j in used:
                    continue

                c2 = centroids[j]
                distance = np.linalg.norm(c1 - c2)

                if distance < threshold_px:
                    cluster.append(contours[j])
                    used.add(j)

            clusters.append(cluster)

        logger.debug(f"Clustered {len(contours)} contours into {len(clusters)} groups")
        return clusters

    def _merge_contour_cluster(self, cluster: List[np.ndarray]) -> np.ndarray:
        """Merge multiple contours into a single contour.

        Args:
            cluster: List of contours to merge

        Returns:
            Merged contour (convex hull of all points)
        """
        if len(cluster) == 1:
            return cluster[0]

        # Combine all points from all contours
        all_points = np.vstack(cluster)

        # Compute convex hull of all points
        hull = cv2.convexHull(all_points)

        return hull

    def _cluster_by_orientation(self, contours: List[np.ndarray], angle_tolerance: float = 15) -> List[List[np.ndarray]]:
        """Group contours that have similar orientation (same lane direction).

        This can be used as an additional clustering step after proximity clustering
        to further group lane fragments that belong to the same logical lane.

        Args:
            contours: List of OpenCV contours to cluster
            angle_tolerance: Maximum angle difference in degrees for grouping

        Returns:
            List of contour clusters grouped by similar orientation
        """
        if len(contours) < 2:
            return [[c] for c in contours]

        # Get orientation for each contour
        orientations = []
        for cnt in contours:
            if len(cnt) >= 5:
                rect = cv2.minAreaRect(cnt)
                orientations.append(rect[2])
            else:
                # Not enough points for minAreaRect, skip
                orientations.append(None)

        # Cluster by orientation (within tolerance degrees)
        clusters = []
        used = set()

        for i, angle1 in enumerate(orientations):
            if i in used or angle1 is None:
                continue

            cluster = [contours[i]]
            used.add(i)

            for j, angle2 in enumerate(orientations):
                if j in used or j <= i or angle2 is None:
                    continue

                # Check angle similarity
                # Handle angle wrap-around (angles are in [-90, 0] range)
                angle_diff = abs(angle1 - angle2)
                # Also check 90-degree difference (perpendicular axes can represent same line)
                if angle_diff < angle_tolerance or abs(angle_diff - 90) < angle_tolerance:
                    cluster.append(contours[j])
                    used.add(j)

            clusters.append(cluster)

        # Add any unused contours as singleton clusters
        for i, cnt in enumerate(contours):
            if i not in used:
                clusters.append([cnt])

        logger.debug(f"Orientation clustering: {len(contours)} contours → {len(clusters)} orientation groups")
        return clusters

    def _validate_contour_linearity(self, contour: np.ndarray, threshold: float = None) -> bool:
        """Check if contour is approximately linear (fits a line well).

        Args:
            contour: OpenCV contour to validate
            threshold: Minimum linearity score (0-1), defaults to config value

        Returns:
            True if contour is sufficiently linear, False otherwise
        """
        if threshold is None:
            threshold = self.profile["MIN_LANE_LINEARITY_SCORE"]

        if len(contour) < 5:
            return False

        # Fit a line using cv2.fitLine
        [vx, vy, x, y] = cv2.fitLine(contour, cv2.DIST_L2, 0, 0.01, 0.01)

        # Calculate how well points fit the line (R-squared analog)
        # Project all points onto line, calculate mean distance
        points = contour.reshape(-1, 2)
        line_point = np.array([x[0], y[0]])
        line_dir = np.array([vx[0], vy[0]])

        # Distance from each point to line
        distances = np.abs(np.cross(points - line_point, line_dir))

        # Normalize by contour span
        span = np.max(np.linalg.norm(points - points[0], axis=1))
        linearity_score = 1 - (np.mean(distances) / max(span, 1))

        return linearity_score > threshold

    def _validate_spatial_context(self, x_center: float, y_min: float,
                                   frame_width: int, frame_height: int) -> bool:
        """Reject detections at extreme edges or too high in frame.

        Args:
            x_center: X coordinate of contour center
            y_min: Minimum Y coordinate of contour (top edge)
            frame_width: Width of the frame in pixels
            frame_height: Height of the frame in pixels

        Returns:
            True if location is valid for a lane, False otherwise
        """
        # Reject if in outer X% of frame width (likely curbs/buildings)
        edge_margin = frame_width * self.profile["LANE_EDGE_MARGIN_PERCENT"]
        if x_center < edge_margin or x_center > frame_width - edge_margin:
            return False

        # Reject if detection starts above road region (top X%)
        if y_min < frame_height * self.profile["LANE_MIN_Y_PERCENT"]:
            return False

        return True

    def _validate_lane_orientation(self, contour: np.ndarray, tolerance_deg: float = None) -> bool:
        """Check if contour orientation is consistent with lane direction.

        Note: cv2.minAreaRect returns angle in range [-90, 0] degrees.
        - Angle near 0 means the long axis is horizontal
        - Angle near -90 means the long axis is vertical

        For lanes, we want the long axis to be roughly vertical (angle near -90 or near 0
        for very elongated shapes where OpenCV may flip the interpretation).

        Args:
            contour: OpenCV contour to validate
            tolerance_deg: Maximum angle deviation from vertical, defaults to config value

        Returns:
            True if orientation is consistent with lanes, False otherwise
        """
        if tolerance_deg is None:
            tolerance_deg = self.profile["LANE_ORIENTATION_TOLERANCE_DEG"]

        if len(contour) < 5:
            return True  # Too few points to validate, allow it

        rect = cv2.minAreaRect(contour)
        width, height = rect[1]
        angle = rect[2]

        # Determine which axis is the "long" axis
        if width < height:
            # Height is the long axis - check if it's roughly vertical
            # Angle should be near -90 (vertical) or near 0 (also vertical for tall rect)
            is_vertical = abs(angle + 90) < tolerance_deg or abs(angle) < tolerance_deg
        else:
            # Width is the long axis - should be near -45 to +45 from vertical
            # This means angle near -90 or 0
            is_vertical = abs(angle + 90) < tolerance_deg or abs(angle) < tolerance_deg

        return is_vertical

    def _compute_iou(self, obj1: ObjectLabel, obj2: ObjectLabel) -> float:
        """Compute Intersection over Union (IoU) between two objects' bounding boxes.

        Args:
            obj1: First object
            obj2: Second object

        Returns:
            IoU value between 0 and 1
        """
        # Get coordinates
        x1_min, y1_min = obj1.x_min, obj1.y_min
        x1_max, y1_max = obj1.x_max, obj1.y_max
        x2_min, y2_min = obj2.x_min, obj2.y_min
        x2_max, y2_max = obj2.x_max, obj2.y_max

        # Compute intersection
        inter_x_min = max(x1_min, x2_min)
        inter_y_min = max(y1_min, y2_min)
        inter_x_max = min(x1_max, x2_max)
        inter_y_max = min(y1_max, y2_max)

        if inter_x_max < inter_x_min or inter_y_max < inter_y_min:
            return 0.0  # No intersection

        inter_area = (inter_x_max - inter_x_min) * (inter_y_max - inter_y_min)

        # Compute union
        area1 = (x1_max - x1_min) * (y1_max - y1_min)
        area2 = (x2_max - x2_min) * (y2_max - y2_min)
        union_area = area1 + area2 - inter_area

        if union_area == 0:
            return 0.0

        return inter_area / union_area

    def _apply_nms_to_geometry(self, objects: List[ObjectLabel], iou_threshold: float = None) -> List[ObjectLabel]:
        """Remove overlapping geometry detections using Non-Maximum Suppression.

        Args:
            objects: List of geometry objects to filter
            iou_threshold: IoU threshold for suppression, defaults to config value

        Returns:
            Filtered list with overlapping detections removed
        """
        if iou_threshold is None:
            iou_threshold = GEOMETRY_NMS_IOU_THRESHOLD

        if len(objects) < 2:
            return objects

        # Sort by area (keep larger/more prominent)
        objects = sorted(objects, key=lambda x: x.width * x.height, reverse=True)

        keep = []
        for obj in objects:
            overlap = False
            for kept in keep:
                iou = self._compute_iou(obj, kept)
                if iou > iou_threshold:
                    overlap = True
                    break
            if not overlap:
                keep.append(obj)

        if len(keep) < len(objects):
            logger.debug(f"NMS removed {len(objects) - len(keep)} overlapping geometry objects")

        return keep

    def _filter_geometry_objects(self, geometry_objects: List[ObjectLabel]) -> List[ObjectLabel]:
        """Apply smart filtering to reduce redundant geometry objects.

        Phase 1: Keeps only the most important/prominent geometry objects per type.

        Args:
            geometry_objects: List of geometry objects to filter

        Returns:
            Filtered list of geometry objects
        """
        # Separate objects by type
        lanes = []
        double_yellows = []
        crosswalks = []
        other = []

        for obj in geometry_objects:
            if obj.description in ['lane', 'lane(current)']:
                lanes.append(obj)
            elif obj.description == 'double_yellow':
                double_yellows.append(obj)
            elif obj.description == 'crosswalk':
                crosswalks.append(obj)
            else:
                other.append(obj)

        # Sort by area (prominence) and keep top N for each type
        lanes_sorted = sorted(lanes, key=lambda x: x.width * x.height, reverse=True)
        lanes_filtered = lanes_sorted[:MAX_LANES_PER_FRAME]

        double_yellows_sorted = sorted(double_yellows, key=lambda x: x.width * x.height, reverse=True)
        double_yellows_filtered = double_yellows_sorted[:MAX_DOUBLE_YELLOW_PER_FRAME]

        crosswalks_sorted = sorted(crosswalks, key=lambda x: x.width * x.height, reverse=True)
        crosswalks_filtered = crosswalks_sorted[:MAX_CROSSWALKS_PER_FRAME]

        # Apply NMS to each type separately to remove overlapping detections
        lanes_filtered = self._apply_nms_to_geometry(lanes_filtered)
        double_yellows_filtered = self._apply_nms_to_geometry(double_yellows_filtered)
        crosswalks_filtered = self._apply_nms_to_geometry(crosswalks_filtered)

        # Combine filtered results
        filtered = lanes_filtered + double_yellows_filtered + crosswalks_filtered + other

        if len(geometry_objects) > len(filtered):
            logger.debug(
                f"Filtered geometry objects: {len(geometry_objects)} → {len(filtered)} "
                f"(lanes: {len(lanes)}→{len(lanes_filtered)}, "
                f"double_yellow: {len(double_yellows)}→{len(double_yellows_filtered)}, "
                f"crosswalks: {len(crosswalks)}→{len(crosswalks_filtered)})"
            )

        return filtered

    def _validate_crosswalk_span(self, crosswalk_width: float, frame_width: int) -> bool:
        """Validate crosswalk spans reasonable road width.

        Args:
            crosswalk_width: Width of detected crosswalk in pixels
            frame_width: Frame width in pixels

        Returns:
            True if crosswalk width is valid, False otherwise
        """
        # Crosswalks should span at least 30% of frame width
        min_span = frame_width * 0.3
        return crosswalk_width >= min_span

    def _detect_crosswalks(self, frame_bgr: np.ndarray, timestamp_ms: float) -> List[ObjectLabel]:
        """Detect crosswalks using Hough Line Transform and parallel stripe detection.

        Phase 3: Implements industry-standard crosswalk detection using:
        1. ROI selection (bottom portion of frame where road is)
        2. White pixel detection (crosswalks are white)
        3. Hough Line Transform to find straight lines
        4. Parallel horizontal line clustering
        5. Validation (need ≥3 parallel stripes)
        6. Span validation (must span ≥30% of frame width)

        Args:
            frame_bgr: BGR frame
            timestamp_ms: Frame timestamp

        Returns:
            List of crosswalk ObjectLabel instances
        """
        crosswalks = []

        # Convert to grayscale
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        # Focus on road region (use camera profile parameter)
        h, w = frame_bgr.shape[:2]
        roi_start = int(h * self.profile["CROSSWALK_MIN_Y_PERCENT"])
        gray_roi = gray[roi_start:, :]

        # Detect white regions (crosswalks are bright)
        _, thresh = cv2.threshold(gray_roi, 200, 255, cv2.THRESH_BINARY)

        # Apply edge detection
        edges = cv2.Canny(thresh, 50, 150)

        # Detect lines using Hough Line Transform
        lines = cv2.HoughLinesP(
            edges,
            rho=1,
            theta=np.pi / 180,
            threshold=self.profile["CROSSWALK_HOUGH_THRESHOLD"],
            minLineLength=self.profile["CROSSWALK_MIN_LINE_LENGTH"],
            maxLineGap=self.profile["CROSSWALK_MAX_LINE_GAP"]
        )

        if lines is None or len(lines) < self.profile["CROSSWALK_MIN_STRIPES"]:
            return []

        # Filter for horizontal lines (crosswalk stripes are perpendicular to traffic)
        horizontal_lines = []
        for line in lines:
            x1, y1, x2, y2 = line[0]

            # Calculate angle from horizontal
            if x2 - x1 != 0:
                angle = np.abs(np.arctan2(y2 - y1, x2 - x1) * 180 / np.pi)
            else:
                angle = 90  # Vertical line

            # Keep nearly horizontal lines
            if angle < self.profile["CROSSWALK_STRIPE_ANGLE_TOLERANCE"]:
                # Adjust y-coordinates back to full frame
                y1_adjusted = y1 + roi_start
                y2_adjusted = y2 + roi_start
                horizontal_lines.append((x1, y1_adjusted, x2, y2_adjusted, y1_adjusted))

        if len(horizontal_lines) < self.profile["CROSSWALK_MIN_STRIPES"]:
            logger.debug(f"Found {len(horizontal_lines)} horizontal lines, need {self.profile['CROSSWALK_MIN_STRIPES']}")
            return []

        # Sort lines by y-coordinate
        horizontal_lines.sort(key=lambda l: l[4])

        # Cluster parallel lines that are evenly spaced
        stripe_clusters = []
        current_cluster = [horizontal_lines[0]]

        for i in range(1, len(horizontal_lines)):
            prev_line = horizontal_lines[i - 1]
            curr_line = horizontal_lines[i]

            # Check vertical spacing between lines
            spacing = curr_line[1] - prev_line[1]

            if self.profile["CROSSWALK_STRIPE_SPACING_MIN"] < spacing < self.profile["CROSSWALK_STRIPE_SPACING_MAX"]:
                current_cluster.append(curr_line)
            else:
                if len(current_cluster) >= self.profile["CROSSWALK_MIN_STRIPES"]:
                    stripe_clusters.append(current_cluster)
                current_cluster = [curr_line]

        # Check last cluster
        if len(current_cluster) >= self.profile["CROSSWALK_MIN_STRIPES"]:
            stripe_clusters.append(current_cluster)

        # Create crosswalk objects from clusters
        for cluster in stripe_clusters:
            # Get bounding box of all lines in cluster
            min_x = min(min(line[0], line[2]) for line in cluster)
            max_x = max(max(line[0], line[2]) for line in cluster)
            min_y = min(line[1] for line in cluster)
            max_y = max(line[1] for line in cluster)

            # Validate crosswalk spans reasonable width
            crosswalk_width = max_x - min_x
            if not self._validate_crosswalk_span(crosswalk_width, w):
                logger.debug(f"Rejected crosswalk: width {crosswalk_width:.0f}px ({crosswalk_width/w*100:.1f}%) < 30% of frame")
                continue

            # Create polygon for crosswalk region
            polygon = [
                PolygonPoint(x=float(min_x), y=float(min_y)),
                PolygonPoint(x=float(max_x), y=float(min_y)),
                PolygonPoint(x=float(max_x), y=float(max_y)),
                PolygonPoint(x=float(min_x), y=float(max_y))
            ]

            crosswalk = ObjectLabel(
                description='crosswalk',
                start_time_ms=timestamp_ms,
                distance="n/a",
                priority="none",
                center=Center(x=int((min_x + max_x) / 2), y=int((min_y + max_y) / 2)),
                polygon=polygon,
                x_min=float(min_x),
                y_min=float(min_y),
                x_max=float(max_x),
                y_max=float(max_y),
                width=float(max_x - min_x),
                height=float(max_y - min_y)
            )
            crosswalks.append(crosswalk)

        if crosswalks:
            logger.debug(f"Detected {len(crosswalks)} crosswalk(s) with {len(horizontal_lines)} horizontal stripes")

        return crosswalks

