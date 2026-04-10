"""FastAPI server wrapper for the Lightship MVP pipeline.

Provides REST API endpoints for video processing.
Persists job status to DynamoDB (lightship_jobs table).
"""
import os
import tempfile
import shutil
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import FastAPI, File, UploadFile, BackgroundTasks, HTTPException, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import boto3
from botocore.exceptions import ClientError

from src.pipeline import Pipeline
from src.config import SNAPSHOT_STRATEGY, MAX_SNAPSHOTS_PER_VIDEO, TEMP_FRAMES_DIR

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Lightship MVP API",
    description="Object detection and hazard labeling for dashcam videos",
    version="1.0.0"
)

# Configure uvicorn logger to reduce clutter
import logging as uvicorn_logging
uvicorn_logging.getLogger("uvicorn.access").setLevel(uvicorn_logging.WARNING)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for processing status (warm-cache; DynamoDB is authoritative)
processing_status: Dict[str, Dict[str, Any]] = {}
processing_results: Dict[str, Dict[str, Any]] = {}

# ─── DynamoDB job tracking ──────────────────────────────────────────────────
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "lightship_jobs")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

try:
    _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    _jobs_table = _dynamodb.Table(DYNAMODB_TABLE)
    logger.info(f"DynamoDB table connected: {DYNAMODB_TABLE}")
except Exception as e:
    logger.warning(f"DynamoDB init failed (will use in-memory only): {e}")
    _jobs_table = None


def _dynamo_put_job(job_id: str, status: str, **extra) -> None:
    """Create or update a job record in DynamoDB."""
    if _jobs_table is None:
        logger.warning(f"DynamoDB table ref is None – skipping put_item for {job_id}")
        return
    try:
        item = {
            "job_id": job_id,
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **{k: v for k, v in extra.items() if v is not None},
        }
        _jobs_table.put_item(Item=item)
        logger.info(f"DynamoDB put_item OK job={job_id} status={status}")
    except Exception as e:
        logger.warning(f"DynamoDB put_item failed for {job_id}: {type(e).__name__}: {e}")


def _dynamo_update_status(job_id: str, status: str, **extra) -> None:
    """Update just the status (and optional extra attrs) of an existing job."""
    if _jobs_table is None:
        return
    try:
        update_expr = "SET #s = :s, updated_at = :u"
        expr_values: Dict[str, Any] = {
            ":s": status,
            ":u": datetime.now(timezone.utc).isoformat(),
        }
        expr_names = {"#s": "status"}  # status is a DynamoDB reserved word
        for k, v in extra.items():
            if v is not None:
                update_expr += f", {k} = :{k}"
                expr_values[f":{k}"] = v
        _jobs_table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
            ExpressionAttributeNames=expr_names,
        )
        logger.info(f"DynamoDB update OK job={job_id} status={status}")
    except Exception as e:
        logger.warning(f"DynamoDB update failed for {job_id}: {type(e).__name__}: {e}")


def _dynamo_get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a job record from DynamoDB. Returns None if not found."""
    if _jobs_table is None:
        return None
    try:
        resp = _jobs_table.get_item(Key={"job_id": job_id})
        return resp.get("Item")
    except Exception as e:
        logger.warning(f"DynamoDB get_item failed for {job_id}: {type(e).__name__}: {e}")
        return None


class ProcessingConfig(BaseModel):
    """Configuration for video processing."""
    snapshot_strategy: str = "naive"
    max_snapshots: int = 3
    cleanup_frames: bool = False  # Keep frames for UI display
    use_cv_labeler: bool = True   # V2 pipeline by default
    hazard_mode: str = "sliding_window"
    window_size: int = 3
    window_overlap: int = 1


class ProcessingStatus(BaseModel):
    """Status response model."""
    status: str
    progress: float
    message: str
    current_step: Optional[str] = None


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "message": "Lightship MVP API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/jobs")
def list_jobs(limit: int = 50):
    """List recent jobs from DynamoDB, sorted by created_at descending."""
    if _jobs_table is None:
        return {"jobs": []}
    try:
        resp = _jobs_table.scan(Limit=limit)
        jobs = resp.get("Items", [])
        jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"jobs": jobs}
    except Exception as e:
        logger.warning(f"DynamoDB scan failed for /jobs: {e}")
        return {"jobs": []}


@app.post("/process-video")
async def process_video(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    config: Optional[str] = Form(None)
):
    """Process uploaded video.

    Args:
        video: Uploaded video file
        config: Optional JSON string with ProcessingConfig

    Returns:
        job_id for status tracking
    """
    # Generate job ID
    import uuid
    job_id = str(uuid.uuid4())

    # Parse config
    if config:
        import json
        config_dict = json.loads(config)
        proc_config = ProcessingConfig(**config_dict)
        logger.info(f"Received config: max_snapshots={proc_config.max_snapshots}, strategy={proc_config.snapshot_strategy}")
    else:
        proc_config = ProcessingConfig()
        logger.info(f"Using default config: max_snapshots={proc_config.max_snapshots}")

    # Save uploaded video to temp file
    temp_dir = tempfile.mkdtemp()
    video_path = os.path.join(temp_dir, video.filename)

    with open(video_path, "wb") as f:
        content = await video.read()
        f.write(content)

    # Initialize status (in-memory + DynamoDB)
    processing_status[job_id] = {
        "status": "QUEUED",
        "progress": 0.0,
        "message": "Video uploaded, queued for processing",
        "video_path": video_path,
        "temp_dir": temp_dir
    }
    _dynamo_put_job(
        job_id,
        status="QUEUED",
        filename=video.filename,
        input_type="video",
        snapshot_strategy=proc_config.snapshot_strategy,
        max_snapshots=proc_config.max_snapshots,
    )

    # Add processing task to background
    background_tasks.add_task(
        process_video_task,
        job_id,
        video_path,
        temp_dir,
        proc_config
    )

    logger.info(f"Job {job_id} queued for video: {video.filename}")

    return {"job_id": job_id, "status": "QUEUED"}


def process_video_task(
    job_id: str,
    video_path: str,
    temp_dir: str,
    config: ProcessingConfig
):
    """Background task for video processing."""
    try:
        # Update status
        processing_status[job_id].update({
            "status": "PROCESSING",
            "progress": 0.1,
            "message": "Initializing pipeline",
            "current_step": "init"
        })
        _dynamo_update_status(job_id, "PROCESSING", current_step="init")

        # Initialize pipeline with config
        pipeline = Pipeline(
            snapshot_strategy=config.snapshot_strategy,
            max_snapshots=config.max_snapshots,
            cleanup_frames=config.cleanup_frames,
            use_cv_labeler=config.use_cv_labeler
        )

        # Update progress for processing
        processing_status[job_id].update({
            "progress": 0.3,
            "message": "Processing video with pipeline",
            "current_step": "processing"
        })

        # Process video using pipeline (handles both V1 and V2)
        # Temporarily change merger output dir to temp_dir
        original_output_dir = pipeline.merger.output_dir
        pipeline.merger.output_dir = temp_dir

        try:
            output_json_path = pipeline.process_video(video_path, is_train=False)

            if output_json_path is None:
                raise ValueError("Pipeline returned None - processing failed")

            # Rename to output.json for consistency
            final_output_path = os.path.join(temp_dir, "output.json")
            if output_json_path != final_output_path:
                shutil.move(output_json_path, final_output_path)
            output_json_path = final_output_path

        finally:
            # Restore original output dir
            pipeline.merger.output_dir = original_output_dir

        # Update progress
        processing_status[job_id].update({
            "progress": 0.9,
            "message": "Loading results",
            "current_step": "finalize"
        })

        # Load video metadata for results
        video_metadata = pipeline.video_loader.load_video_metadata(video_path)

        # Load output for summary
        from src.schemas import VideoOutput
        with open(output_json_path, 'r') as f:
            import json
            data = json.load(f)
            video_output = VideoOutput(**data)

        summary = pipeline.merger.get_summary_stats(video_output)

        # Get frame info
        # Since we don't have direct access to snapshots, extract from output
        timestamps = sorted(set(obj.start_time_ms for obj in video_output.objects))

        # Map timestamps to frame files in temp_frames
        import glob
        temp_frames_pattern = os.path.join(TEMP_FRAMES_DIR, f"{os.path.splitext(video_metadata.filename)[0]}_frame_*.png")
        frame_files = glob.glob(temp_frames_pattern)

        extracted_frames = {}
        snapshots_info = []

        for frame_file in frame_files:
            # Extract frame_idx and timestamp from filename
            # Format: videoname_frame_N_TIMEms.png
            basename = os.path.basename(frame_file)
            parts = basename.split('_')
            try:
                frame_idx = int(parts[parts.index('frame') + 1])
                time_str = parts[-1].replace('ms.png', '')
                timestamp_ms = float(time_str)

                extracted_frames[frame_idx] = frame_file
                snapshots_info.append({
                    "frame_idx": frame_idx,
                    "timestamp_ms": timestamp_ms,
                    "frame_path": frame_file
                })
            except:
                continue

        # Sort snapshots by timestamp
        snapshots_info.sort(key=lambda x: x['timestamp_ms'])

        # Store results
        processing_results[job_id] = {
            "output_json": output_json_path,
            "extracted_frames": extracted_frames,
            "snapshots": snapshots_info,
            "video_metadata": {
                "filename": video_metadata.filename,
                "camera": video_metadata.camera,
                "fps": video_metadata.fps,
                "duration_ms": video_metadata.duration_ms,
                "width": video_metadata.width,
                "height": video_metadata.height
            },
            "summary": summary,
            "temp_dir": temp_dir
        }

        # Update status
        processing_status[job_id].update({
            "status": "COMPLETED",
            "progress": 1.0,
            "message": "Processing completed successfully",
            "current_step": "completed"
        })
        _dynamo_update_status(
            job_id, "COMPLETED",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(f"Job {job_id} completed successfully")

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        processing_status[job_id].update({
            "status": "FAILED",
            "progress": 0.0,
            "message": f"Processing failed: {str(e)}",
            "current_step": "error"
        })
        _dynamo_update_status(
            job_id, "FAILED",
            error_message=str(e),
        )


@app.get("/status/{job_id}")
def get_status(job_id: str):
    """Get processing status for a job (in-memory first, DynamoDB fallback)."""
    if job_id in processing_status:
        return processing_status[job_id]

    # Fallback to DynamoDB (handles cold-start / different Lambda instance)
    dynamo_item = _dynamo_get_job(job_id)
    if dynamo_item:
        return {
            "status": dynamo_item.get("status", "UNKNOWN"),
            "progress": float(dynamo_item.get("progress", 0)),
            "message": dynamo_item.get("message", ""),
            "current_step": dynamo_item.get("current_step"),
        }

    raise HTTPException(status_code=404, detail="Job not found")


@app.get("/results/{job_id}")
def get_results(job_id: str):
    """Get processing results for a completed job."""
    if job_id not in processing_status:
        raise HTTPException(status_code=404, detail="Job not found")

    if processing_status[job_id]["status"].upper() != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed. Current status: {processing_status[job_id]['status']}"
        )

    if job_id not in processing_results:
        raise HTTPException(status_code=404, detail="Results not found")

    return processing_results[job_id]


@app.get("/download/json/{job_id}")
def download_json(job_id: str):
    """Download output JSON file."""
    if job_id not in processing_results:
        raise HTTPException(status_code=404, detail="Results not found")

    json_path = processing_results[job_id]["output_json"]

    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="JSON file not found")

    return FileResponse(
        json_path,
        media_type="application/json",
        filename="output.json"
    )


@app.get("/download/frame/{job_id}/{frame_idx}")
def download_frame(job_id: str, frame_idx: int):
    """Download specific frame image."""
    if job_id not in processing_results:
        raise HTTPException(status_code=404, detail="Results not found")

    frame_path = processing_results[job_id]["extracted_frames"].get(frame_idx)

    if not frame_path or not os.path.exists(frame_path):
        raise HTTPException(status_code=404, detail="Frame not found")

    return FileResponse(
        frame_path,
        media_type="image/png",
        filename=f"frame_{frame_idx}.png"
    )


@app.delete("/cleanup/{job_id}")
def cleanup_job(job_id: str):
    """Cleanup temporary files for a job."""
    if job_id in processing_results:
        temp_dir = processing_results[job_id].get("temp_dir")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        del processing_results[job_id]

    if job_id in processing_status:
        del processing_status[job_id]

    return {"message": "Cleanup successful"}


if __name__ == "__main__":
    uvicorn.run(
        "src.api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )

