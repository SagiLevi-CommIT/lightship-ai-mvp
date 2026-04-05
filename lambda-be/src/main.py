"""Main entry point for Lightship MVP pipeline.

Command-line interface for processing videos.
"""
import argparse
import glob
import logging
import os
import sys
from pathlib import Path

from src.utils.logging_setup import setup_logging
from src.pipeline import Pipeline
from src.config import (
    TRAIN_DIR,
    TEST_DIR,
    SNAPSHOT_STRATEGY,
    LOG_LEVEL,
    LOG_DIR,
    LOG_FILE
)

logger = logging.getLogger(__name__)


def get_video_files(directory: str) -> list:
    """Get all video files in a directory.

    Args:
        directory: Directory path

    Returns:
        List of video file paths
    """
    video_extensions = ['*.mp4', '*.MP4', '*.avi', '*.AVI', '*.mov', '*.MOV']
    video_files = []

    for ext in video_extensions:
        pattern = os.path.join(directory, ext)
        video_files.extend(glob.glob(pattern))

    return sorted(video_files)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Lightship MVP - Snapshot-based Object and Priority-Hazard Labeling',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--mode',
        type=str,
        choices=['train', 'test', 'single'],
        required=True,
        help='Processing mode: train (process train videos), test (process test videos), '
             'or single (process a single video)'
    )

    parser.add_argument(
        '--video',
        type=str,
        help='Path to single video file (required for --mode single)'
    )

    parser.add_argument(
        '--strategy',
        type=str,
        choices=['naive', 'scene_change'],
        default=SNAPSHOT_STRATEGY,
        help=f'Snapshot selection strategy (default: {SNAPSHOT_STRATEGY})'
    )

    parser.add_argument(
        '--no-cleanup',
        action='store_true',
        help='Do not delete temporary frame files after processing'
    )

    parser.add_argument(
        '--use-v1',
        action='store_true',
        help='Use V1 pipeline (LLM image analysis) instead of V2 (CV + Temporal LLM)'
    )

    parser.add_argument(
        '--log-level',
        type=str,
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        default=LOG_LEVEL,
        help=f'Logging level (default: {LOG_LEVEL})'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(log_level=args.log_level, log_dir=LOG_DIR, log_file=LOG_FILE)
    logger.info("Lightship MVP Pipeline Started")
    logger.info(f"Arguments: {vars(args)}")

    # Validate arguments
    if args.mode == 'single' and not args.video:
        logger.error("--video is required when --mode is 'single'")
        parser.print_help()
        return 1

    # Initialize pipeline
    pipeline = Pipeline(
        snapshot_strategy=args.strategy,
        cleanup_frames=not args.no_cleanup,
        use_cv_labeler=not args.use_v1  # V2 by default unless --use-v1 is specified
    )

    pipeline_version = "V1 (LLM)" if args.use_v1 else "V2 (CV)"
    logger.info(f"Using {pipeline_version} pipeline")

    # Process based on mode
    if args.mode == 'train':
        logger.info(f"Processing TRAIN videos from: {TRAIN_DIR}")
        video_files = get_video_files(TRAIN_DIR)

        if not video_files:
            logger.error(f"No video files found in {TRAIN_DIR}")
            return 1

        logger.info(f"Found {len(video_files)} train videos")
        results = pipeline.process_batch(video_files, is_train=True)

        successful = sum(1 for r in results if r is not None)
        logger.info(f"Train processing complete: {successful}/{len(results)} successful")

    elif args.mode == 'test':
        logger.info(f"Processing TEST videos from: {TEST_DIR}")
        video_files = get_video_files(TEST_DIR)

        if not video_files:
            logger.error(f"No video files found in {TEST_DIR}")
            return 1

        logger.info(f"Found {len(video_files)} test videos")
        results = pipeline.process_batch(video_files, is_train=False)

        successful = sum(1 for r in results if r is not None)
        logger.info(f"Test processing complete: {successful}/{len(results)} successful")

    elif args.mode == 'single':
        if not os.path.exists(args.video):
            logger.error(f"Video file not found: {args.video}")
            return 1

        logger.info(f"Processing single video: {args.video}")

        # Determine if train video based on path
        is_train = TRAIN_DIR in os.path.abspath(args.video)

        output_path = pipeline.process_video(args.video, is_train=is_train)

        if output_path:
            logger.info(f"Single video processing complete: {output_path}")
        else:
            logger.error("Single video processing failed")
            return 1

    logger.info("Pipeline execution complete")
    return 0


if __name__ == '__main__':
    sys.exit(main())

