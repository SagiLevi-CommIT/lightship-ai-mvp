"""Video loader module for extracting video metadata.

Uses OpenCV to load videos and extract fps, duration, frame counts, etc.
"""
import logging
import os
from pathlib import Path
import cv2
from src.schemas import VideoMetadata

logger = logging.getLogger(__name__)


class VideoLoader:
    """Loads videos and extracts metadata."""
    
    def __init__(self):
        """Initialize VideoLoader."""
        pass
    
    def load_video_metadata(self, video_path: str) -> VideoMetadata:
        """Load video and extract metadata.
        
        Args:
            video_path: Path to video file
            
        Returns:
            VideoMetadata object with extracted information
            
        Raises:
            FileNotFoundError: If video file doesn't exist
            ValueError: If video cannot be opened or metadata cannot be extracted
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")
        
        # Open video
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video file: {video_path}")
        
        try:
            # Extract metadata
            fps = cap.get(cv2.CAP_PROP_FPS)
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            # Calculate duration in milliseconds
            if fps > 0:
                duration_ms = (total_frames / fps) * 1000
            else:
                raise ValueError(f"Invalid FPS ({fps}) for video: {video_path}")
            
            # Extract filename and infer camera vendor
            filename = os.path.basename(video_path)
            camera = self._infer_camera_vendor(filename)
            
            metadata = VideoMetadata(
                filename=filename,
                filepath=video_path,
                camera=camera,
                fps=fps,
                duration_ms=duration_ms,
                total_frames=total_frames,
                width=width,
                height=height
            )
            
            logger.info(
                f"Loaded video: {filename} | "
                f"FPS: {fps:.2f} | Duration: {duration_ms:.2f}ms | "
                f"Frames: {total_frames} | Resolution: {width}x{height}"
            )
            
            return metadata
            
        finally:
            cap.release()
    
    def _infer_camera_vendor(self, filename: str) -> str:
        """Infer camera vendor from filename.
        
        Args:
            filename: Video filename
            
        Returns:
            Camera vendor string (lytx, netradyne, samsara, verizon)
        """
        filename_lower = filename.lower()
        
        if "lytx" in filename_lower:
            return "lytx"
        elif "netradyne" in filename_lower:
            return "netradyne"
        elif "samsara" in filename_lower:
            return "samsara"
        elif "verizon" in filename_lower:
            return "verizon"
        else:
            logger.warning(f"Cannot infer camera vendor from filename: {filename}")
            return "unknown"
    
    def frame_idx_to_timestamp(self, frame_idx: int, fps: float) -> float:
        """Convert frame index to timestamp in milliseconds.
        
        Args:
            frame_idx: Frame index (0-based)
            fps: Frames per second
            
        Returns:
            Timestamp in milliseconds
        """
        return (frame_idx / fps) * 1000
    
    def timestamp_to_frame_idx(self, timestamp_ms: float, fps: float) -> int:
        """Convert timestamp to frame index.
        
        Args:
            timestamp_ms: Timestamp in milliseconds
            fps: Frames per second
            
        Returns:
            Frame index (0-based, rounded)
        """
        return round((timestamp_ms / 1000) * fps)

