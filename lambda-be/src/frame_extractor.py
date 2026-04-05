"""Frame extractor module for extracting frames from videos.

Extracts specific frames at selected timestamps and saves as image files.
"""
import logging
import os
from pathlib import Path
from typing import List, Dict
import cv2
from src.schemas import SnapshotInfo, VideoMetadata
from src.config import TEMP_FRAMES_DIR, FRAME_FORMAT, FRAME_QUALITY

logger = logging.getLogger(__name__)


class FrameExtractor:
    """Extracts frames from videos at specified timestamps."""
    
    def __init__(self, output_dir: str = TEMP_FRAMES_DIR):
        """Initialize FrameExtractor.
        
        Args:
            output_dir: Directory to save extracted frames
        """
        self.output_dir = output_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"FrameExtractor initialized. Output dir: {output_dir}")
    
    def extract_frames(
        self,
        video_metadata: VideoMetadata,
        snapshots: List[SnapshotInfo]
    ) -> Dict[int, str]:
        """Extract frames at specified snapshots.
        
        Args:
            video_metadata: Video metadata
            snapshots: List of snapshots to extract
            
        Returns:
            Dictionary mapping frame_idx to extracted image filepath
            
        Raises:
            ValueError: If video cannot be opened or frames cannot be extracted
        """
        cap = cv2.VideoCapture(video_metadata.filepath)
        if not cap.isOpened():
            raise ValueError(f"Cannot open video: {video_metadata.filepath}")
        
        extracted_frames = {}
        video_name = os.path.splitext(video_metadata.filename)[0]
        
        try:
            for snapshot in snapshots:
                frame_idx = snapshot.frame_idx
                
                # Set video position to frame
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                ret, frame = cap.read()
                
                if not ret:
                    logger.error(
                        f"Failed to read frame {frame_idx} from {video_metadata.filename}"
                    )
                    continue
                
                # Generate output filename
                output_filename = (
                    f"{video_name}_frame_{frame_idx}_"
                    f"{snapshot.timestamp_ms:.0f}ms.{FRAME_FORMAT}"
                )
                output_path = os.path.join(self.output_dir, output_filename)
                
                # Save frame
                if FRAME_FORMAT.lower() in ['jpg', 'jpeg']:
                    cv2.imwrite(
                        output_path,
                        frame,
                        [cv2.IMWRITE_JPEG_QUALITY, FRAME_QUALITY]
                    )
                elif FRAME_FORMAT.lower() == 'png':
                    # PNG quality is compression level (0-9, higher = more compression)
                    compression = 9 - int(FRAME_QUALITY / 10)
                    cv2.imwrite(
                        output_path,
                        frame,
                        [cv2.IMWRITE_PNG_COMPRESSION, compression]
                    )
                else:
                    cv2.imwrite(output_path, frame)
                
                extracted_frames[frame_idx] = output_path
                logger.info(
                    f"Extracted frame {frame_idx} ({snapshot.timestamp_ms:.2f}ms) "
                    f"to {output_filename}"
                )
        
        finally:
            cap.release()
        
        logger.info(
            f"Extracted {len(extracted_frames)}/{len(snapshots)} frames "
            f"from {video_metadata.filename}"
        )
        
        return extracted_frames
    
    def cleanup_frames(self, frame_paths: List[str]) -> None:
        """Delete extracted frame files.
        
        Args:
            frame_paths: List of frame file paths to delete
        """
        deleted = 0
        for path in frame_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    deleted += 1
            except Exception as e:
                logger.warning(f"Failed to delete frame {path}: {e}")
        
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} temporary frame files")

