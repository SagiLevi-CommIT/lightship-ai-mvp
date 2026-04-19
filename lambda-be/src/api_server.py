"""FastAPI server wrapper for the Lightship MVP pipeline.

Provides REST API endpoints for video processing.
Persists job status to DynamoDB (lightship_jobs table).
"""
import os
import json
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
from src.config_generator import generate_client_configs, write_client_configs

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

# ─── S3 video upload bucket ─────────────────────────────────────────────────
PROCESSING_BUCKET = os.environ.get("PROCESSING_BUCKET", "lightship-mvp-processing-336090301206")
try:
    from botocore.config import Config as _BotocoreConfig
    # s3v4 is required for buckets with KMS-SSE — SigV2 presigned URLs are
    # rejected by S3 when the bucket policy enforces aws:kms encryption.
    _s3_client = boto3.client(
        "s3",
        region_name=AWS_REGION,
        config=_BotocoreConfig(signature_version="s3v4"),
    )
    logger.info(f"S3 client initialised (SigV4), PROCESSING_BUCKET={PROCESSING_BUCKET}")
except Exception as e:
    logger.warning(f"S3 client init failed: {e}")
    _s3_client = None

try:
    _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    _jobs_table = _dynamodb.Table(DYNAMODB_TABLE)
    logger.info(f"DynamoDB table connected: {DYNAMODB_TABLE}")
except Exception as e:
    logger.warning(f"DynamoDB init failed (will use in-memory only): {e}")
    _jobs_table = None

# ─── Lambda self-invocation client (async worker dispatch) ───────────────────
# AWS_LAMBDA_FUNCTION_NAME is set automatically by the Lambda runtime.
# When running locally (uvicorn), it is absent so we fall back to background_tasks.
LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
_lambda_client = None
if LAMBDA_FUNCTION_NAME:
    try:
        _lambda_client = boto3.client("lambda", region_name=AWS_REGION)
        logger.info(f"Lambda client initialised for async self-invocation: {LAMBDA_FUNCTION_NAME}")
    except Exception as e:
        logger.warning(f"Lambda client init failed: {e}")


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


# ---------------------------------------------------------------------------
# S3-backed result storage (survives cross-invocation Lambda cold starts)
# ---------------------------------------------------------------------------
_RESULTS_PREFIX = "results"
_OUTPUT_JSON_KEY = "output.json"


def _s3_result_key(job_id: str, name: str) -> str:
    return f"{_RESULTS_PREFIX}/{job_id}/{name}"


def _persist_result_to_s3(job_id: str, output_json_path: str,
                          summary: Dict[str, Any],
                          video_metadata: Dict[str, Any],
                          snapshots: list) -> None:
    """Upload the pipeline output JSON + manifest to S3.

    In-memory `processing_results` is Lambda-instance-local; persisting
    to S3 means `/results`, `/download/json`, `/client-configs` all keep
    working after a cold start.
    """
    if _s3_client is None:
        return
    try:
        _s3_client.upload_file(
            output_json_path, PROCESSING_BUCKET,
            _s3_result_key(job_id, _OUTPUT_JSON_KEY),
            ExtraArgs={"ServerSideEncryption": "aws:kms",
                       "ContentType": "application/json"},
        )
        manifest = {
            "job_id": job_id,
            "summary": summary,
            "video_metadata": video_metadata,
            "snapshots": snapshots,
        }
        _s3_client.put_object(
            Bucket=PROCESSING_BUCKET,
            Key=_s3_result_key(job_id, "manifest.json"),
            Body=json.dumps(manifest).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
        )
        logger.info(f"Results persisted to s3://{PROCESSING_BUCKET}/{_RESULTS_PREFIX}/{job_id}/")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Result S3 persist failed for {job_id}: {e}")


def _load_result_manifest_from_s3(job_id: str) -> Optional[Dict[str, Any]]:
    if _s3_client is None:
        return None
    try:
        resp = _s3_client.get_object(
            Bucket=PROCESSING_BUCKET,
            Key=_s3_result_key(job_id, "manifest.json"),
        )
        return json.loads(resp["Body"].read())
    except Exception as e:  # noqa: BLE001
        logger.debug(f"No manifest in S3 for {job_id}: {e}")
        return None


def _load_output_json_from_s3(job_id: str, dest_dir: Optional[str] = None) -> Optional[str]:
    """Download the pipeline output.json for job_id to a temp path. Returns path or None."""
    if _s3_client is None:
        return None
    try:
        dest_dir = dest_dir or tempfile.mkdtemp()
        local_path = os.path.join(dest_dir, f"{job_id}-output.json")
        _s3_client.download_file(
            PROCESSING_BUCKET, _s3_result_key(job_id, _OUTPUT_JSON_KEY), local_path,
        )
        return local_path
    except Exception as e:  # noqa: BLE001
        logger.debug(f"No output.json in S3 for {job_id}: {e}")
        return None


def _persist_frame_artefacts_to_s3(
    job_id: str,
    selected_frames: Dict[int, str],
    annotated_frames: Dict[int, str],
    timestamps: Dict[int, float],
    all_objects: list,
) -> list:
    """Persist selected + annotated frame images and per-frame JSON to S3.

    Returns a list of per-frame manifest entries (with S3 keys) that the
    API can surface to the UI via presigned GETs.
    """
    if _s3_client is None:
        return []
    manifest: list = []
    # Group objects by integer millisecond timestamp so we can slice them
    # per frame for per-frame JSON files.
    from collections import defaultdict
    objs_by_ms: Dict[int, list] = defaultdict(list)
    for obj in all_objects:
        key = int(round(obj.get("start_time_ms", 0)))
        objs_by_ms[key].append(obj)

    for frame_idx, frame_path in sorted(annotated_frames.items()):
        if not os.path.exists(frame_path):
            continue
        ts_ms = timestamps.get(frame_idx, 0.0)
        frame_key = _s3_result_key(job_id, f"frames/frame_{frame_idx:04d}_annotated.png")
        raw_key = None
        raw_path = selected_frames.get(frame_idx)
        if raw_path and os.path.exists(raw_path):
            raw_key = _s3_result_key(job_id, f"frames/frame_{frame_idx:04d}_raw.png")
            try:
                _s3_client.upload_file(
                    raw_path, PROCESSING_BUCKET, raw_key,
                    ExtraArgs={
                        "ServerSideEncryption": "aws:kms",
                        "ContentType": "image/png",
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Raw frame upload failed (frame %d): %s", frame_idx, e)
                raw_key = None
        try:
            _s3_client.upload_file(
                frame_path, PROCESSING_BUCKET, frame_key,
                ExtraArgs={
                    "ServerSideEncryption": "aws:kms",
                    "ContentType": "image/png",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Annotated frame upload failed (frame %d): %s", frame_idx, e)
            continue

        # Find objects whose timestamp is within 100ms of this frame
        matched_ms = [ms for ms in objs_by_ms if abs(ms - ts_ms) <= 100]
        frame_objs: list = []
        for ms in matched_ms:
            frame_objs.extend(objs_by_ms[ms])

        per_frame_json_key = _s3_result_key(job_id, f"frames/frame_{frame_idx:04d}.json")
        per_frame_doc = {
            "job_id": job_id,
            "frame_idx": frame_idx,
            "timestamp_ms": ts_ms,
            "num_objects": len(frame_objs),
            "objects": frame_objs,
        }
        try:
            _s3_client.put_object(
                Bucket=PROCESSING_BUCKET,
                Key=per_frame_json_key,
                Body=json.dumps(per_frame_doc, indent=2).encode("utf-8"),
                ContentType="application/json",
                ServerSideEncryption="aws:kms",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Per-frame JSON upload failed (frame %d): %s", frame_idx, e)
            per_frame_json_key = None

        manifest.append({
            "frame_idx": frame_idx,
            "timestamp_ms": ts_ms,
            "num_objects": len(frame_objs),
            "annotated_key": frame_key,
            "raw_key": raw_key,
            "json_key": per_frame_json_key,
        })
    return manifest


def _presign_get(key: str, expires_in: int = 3600) -> Optional[str]:
    if _s3_client is None or not key:
        return None
    try:
        return _s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": PROCESSING_BUCKET, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Presign GET failed for %s: %s", key, e)
        return None


def _load_per_frame_manifest_from_s3(job_id: str) -> list:
    """Read the per-frame manifest list persisted by the worker, if any."""
    if _s3_client is None:
        return []
    try:
        resp = _s3_client.get_object(
            Bucket=PROCESSING_BUCKET,
            Key=_s3_result_key(job_id, "frames_manifest.json"),
        )
        return json.loads(resp["Body"].read())
    except Exception as e:  # noqa: BLE001
        logger.debug("No frames_manifest.json in S3 for %s: %s", job_id, e)
        return []


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


@app.get("/presign-upload")
def presign_upload(filename: str, content_type: str = "video/mp4"):
    """Generate a pre-signed S3 PUT URL so the frontend can upload videos
    directly to S3, bypassing the 1 MB ALB→Lambda payload limit.

    Args:
        filename: Original filename (used to name the S3 object)
        content_type: MIME type for the upload (default: video/mp4)

    Returns:
        presign_url: PUT to this URL directly from the client
        s3_key: Pass this key to POST /process-video instead of a file body
    """
    import uuid as _uuid
    if _s3_client is None:
        raise HTTPException(status_code=503, detail="S3 client not available")
    s3_key = f"input/videos/{_uuid.uuid4()}/{filename}"
    try:
        url = _s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": PROCESSING_BUCKET,
                "Key": s3_key,
                "ContentType": content_type,
                "ServerSideEncryption": "aws:kms",  # bucket enforces KMS SSE
            },
            ExpiresIn=900,  # 15 minutes
        )
        logger.info(f"Presigned PUT URL generated for s3://{PROCESSING_BUCKET}/{s3_key}")
        # Client MUST send these headers exactly as signed into the URL
        return {
            "presign_url": url,
            "s3_key": s3_key,
            "required_headers": {
                "Content-Type": content_type,
                "x-amz-server-side-encryption": "aws:kms",
            },
        }
    except Exception as e:
        logger.error(f"Failed to generate presigned URL: {e}")
        raise HTTPException(status_code=500, detail=f"Presign failed: {e}")


@app.post("/process-video")
async def process_video(
    background_tasks: BackgroundTasks,
    video: Optional[UploadFile] = File(None),
    s3_key: Optional[str] = Form(None),
    config: Optional[str] = Form(None)
):
    """Process a dashcam video. Accepts either:
      - an s3_key form field pointing to a video already in S3 via /presign-upload (production), OR
      - a direct multipart upload (local dev / small files).

    In production (Lambda), immediately returns a job_id and dispatches processing
    asynchronously via Lambda self-invocation (Event type) – the ALB is never blocked.
    In local dev (uvicorn), runs processing as a FastAPI background task.
    """
    import uuid as _uuid

    if video is None and not s3_key:
        raise HTTPException(status_code=422, detail="Either 'video' file or 's3_key' must be provided")

    job_id = str(_uuid.uuid4())

    # Parse config
    if config:
        proc_config = ProcessingConfig(**json.loads(config))
        logger.info(f"Received config: max_snapshots={proc_config.max_snapshots}, strategy={proc_config.snapshot_strategy}")
    else:
        proc_config = ProcessingConfig()
        logger.info(f"Using default config: max_snapshots={proc_config.max_snapshots}")

    # ── Resolve filename and ensure video is reachable in S3 ────────────────
    temp_dir = tempfile.mkdtemp()

    if s3_key:
        # Presign-upload flow: video already in S3, worker will download it
        filename = s3_key.split("/")[-1]
    else:
        # Multipart upload: write to /tmp, then push to S3 so worker can access it
        filename = video.filename
        if _s3_client is None:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=503, detail="S3 client not available")
        content = await video.read()
        local_path = os.path.join(temp_dir, filename)
        with open(local_path, "wb") as f:
            f.write(content)
        s3_key = f"input/videos/{job_id}/{filename}"
        try:
            _s3_client.upload_file(local_path, PROCESSING_BUCKET, s3_key,
                                   ExtraArgs={"ServerSideEncryption": "aws:kms"})
            logger.info(f"Uploaded {filename} → s3://{PROCESSING_BUCKET}/{s3_key}")
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.error(f"S3 upload failed for {filename}: {e}")
            raise HTTPException(status_code=500, detail=f"S3 upload failed: {e}")

    # ── Persist job to DynamoDB (QUEUED) ─────────────────────────────────────
    _dynamo_put_job(
        job_id,
        status="QUEUED",
        filename=filename,
        input_type="video",
        snapshot_strategy=proc_config.snapshot_strategy,
        max_snapshots=proc_config.max_snapshots,
    )
    logger.info(f"Job {job_id} queued for video: {filename}")

    # ── Dispatch worker ───────────────────────────────────────────────────────
    if _lambda_client and LAMBDA_FUNCTION_NAME:
        # Production Lambda: self-invoke async (InvocationType=Event → 202, no wait)
        # This returns immediately so the ALB connection is never timed out.
        worker_payload = {
            "action": "process_worker",
            "job_id": job_id,
            "s3_key": s3_key,
            "filename": filename,
            "config": proc_config.model_dump(),
        }
        try:
            _lambda_client.invoke(
                FunctionName=LAMBDA_FUNCTION_NAME,
                InvocationType="Event",  # fire-and-forget, returns 202
                Payload=json.dumps(worker_payload).encode(),
            )
            logger.info(f"✅ Worker Lambda invoked async for job {job_id}")
        except Exception as e:
            logger.error(f"❌ Worker dispatch failed for job {job_id}: {e}")
            _dynamo_update_status(job_id, "FAILED", error=str(e))
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Worker dispatch failed: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        # Local dev (uvicorn): download from S3 now and run in background task
        video_path = os.path.join(temp_dir, filename)
        if not os.path.exists(video_path) and _s3_client:
            logger.info(f"Downloading s3://{PROCESSING_BUCKET}/{s3_key} → {video_path}")
            try:
                _s3_client.download_file(PROCESSING_BUCKET, s3_key, video_path)
            except Exception as e:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.error(f"S3 download failed for {s3_key}: {e}")
                raise HTTPException(status_code=500, detail=f"S3 download failed: {e}")
        processing_status[job_id] = {
            "status": "QUEUED",
            "progress": 0.0,
            "message": "Video uploaded, queued for processing",
            "video_path": video_path,
            "temp_dir": temp_dir,
        }
        background_tasks.add_task(process_video_task, job_id, video_path, temp_dir, proc_config)

    logger.info(f"POST /process-video 200 job_id={job_id}")
    return {"job_id": job_id, "status": "QUEUED"}


def process_video_worker(event: dict) -> dict:
    """Entry point for async Lambda self-invocation (action=process_worker).

    Downloads the video from S3, runs the full pipeline, and persists results
    to S3/DynamoDB.  Never called via HTTP – invoked by Lambda async dispatch.
    """
    job_id = event["job_id"]
    s3_key = event["s3_key"]
    filename = event["filename"]
    proc_config = ProcessingConfig(**event.get("config", {}))

    logger.info(f"🚀 Worker started: job={job_id} file={filename}")

    temp_dir = tempfile.mkdtemp()
    video_path = os.path.join(temp_dir, filename)

    try:
        # Download video from S3
        logger.info(f"Downloading s3://{PROCESSING_BUCKET}/{s3_key} → {video_path}")
        _s3_client.download_file(PROCESSING_BUCKET, s3_key, video_path)

        # Seed in-memory status so process_video_task update() calls don't KeyError
        processing_status[job_id] = {
            "status": "QUEUED",
            "progress": 0.0,
            "message": "Worker started",
            "video_path": video_path,
            "temp_dir": temp_dir,
        }

        # Run the full pipeline (updates DynamoDB at each stage)
        process_video_task(job_id, video_path, temp_dir, proc_config)

        logger.info(f"✅ Worker completed: job={job_id}")
        return {"status": "ok", "job_id": job_id}

    except Exception as e:
        logger.error(f"❌ Worker failed: job={job_id}: {e}", exc_info=True)
        _dynamo_update_status(job_id, "FAILED", error=str(e))
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"status": "error", "job_id": job_id, "error": str(e)}



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

        video_meta_dict = {
            "filename": video_metadata.filename,
            "camera": video_metadata.camera,
            "fps": video_metadata.fps,
            "duration_ms": video_metadata.duration_ms,
            "width": video_metadata.width,
            "height": video_metadata.height,
        }

        # Persist selected + annotated per-frame images and per-frame JSON
        # to S3, and record the resulting manifest so the UI can fetch frame
        # artefacts cross-invocation.
        all_obj_dicts = [o.model_dump() for o in video_output.objects]
        frames_manifest = _persist_frame_artefacts_to_s3(
            job_id=job_id,
            selected_frames=getattr(pipeline, "last_selected_frames", {}) or {},
            annotated_frames=getattr(pipeline, "last_annotated_frames", {}) or {},
            timestamps=getattr(pipeline, "last_frame_timestamps", {}) or {},
            all_objects=all_obj_dicts,
        )
        # Write the manifest to S3 too so /frames/{id} works cross-invocation
        if _s3_client is not None and frames_manifest:
            try:
                _s3_client.put_object(
                    Bucket=PROCESSING_BUCKET,
                    Key=_s3_result_key(job_id, "frames_manifest.json"),
                    Body=json.dumps(frames_manifest, indent=2).encode("utf-8"),
                    ContentType="application/json",
                    ServerSideEncryption="aws:kms",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("frames_manifest.json upload failed: %s", e)

        # Store results in-memory (warm cache) and persist to S3 (survives cold starts).
        processing_results[job_id] = {
            "output_json": output_json_path,
            "extracted_frames": extracted_frames,
            "snapshots": snapshots_info,
            "video_metadata": video_meta_dict,
            "summary": summary,
            "temp_dir": temp_dir,
            "frames_manifest": frames_manifest,
        }
        _persist_result_to_s3(
            job_id, output_json_path, summary, video_meta_dict, snapshots_info,
        )

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
    """Get processing results for a completed job (in-memory → S3 → DynamoDB)."""
    if job_id in processing_results:
        return processing_results[job_id]

    # Cold-start fallback: hydrate from S3 manifest.
    manifest = _load_result_manifest_from_s3(job_id)
    if manifest:
        return manifest

    # Last-resort: check DynamoDB to give a clearer error.
    dynamo_item = _dynamo_get_job(job_id)
    if not dynamo_item:
        raise HTTPException(status_code=404, detail="Job not found")
    status = (dynamo_item.get("status") or "").upper()
    if status != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail=f"Job not completed. Current status: {status}",
        )
    raise HTTPException(status_code=404, detail="Results not available")


@app.get("/download/json/{job_id}")
def download_json(job_id: str):
    """Download output JSON file (in-memory → S3 fallback)."""
    # In-memory fast path
    if job_id in processing_results:
        json_path = processing_results[job_id].get("output_json")
        if json_path and os.path.exists(json_path):
            return FileResponse(
                json_path, media_type="application/json", filename="output.json",
            )

    # S3 fallback
    local_path = _load_output_json_from_s3(job_id)
    if local_path:
        return FileResponse(
            local_path, media_type="application/json", filename="output.json",
        )

    raise HTTPException(status_code=404, detail="Results not found")


@app.get("/download/frame/{job_id}/{frame_idx}")
def download_frame(job_id: str, frame_idx: int):
    """Download a specific frame image.

    Tries in-memory extracted_frames first (warm cache on same Lambda
    container), then falls back to the S3 frames manifest (survives
    cold starts and cross-invocation).
    """
    # In-memory warm path
    if job_id in processing_results:
        frame_path = processing_results[job_id].get("extracted_frames", {}).get(frame_idx)
        if frame_path and os.path.exists(frame_path):
            return FileResponse(
                frame_path, media_type="image/png",
                filename=f"frame_{frame_idx}.png",
            )

    # S3 fallback: look up the manifest and stream the annotated image
    manifest = _load_per_frame_manifest_from_s3(job_id)
    for entry in manifest:
        if entry.get("frame_idx") == frame_idx:
            key = entry.get("annotated_key") or entry.get("raw_key")
            if key and _s3_client is not None:
                try:
                    tmp_dir = tempfile.mkdtemp()
                    local_path = os.path.join(tmp_dir, f"frame_{frame_idx}.png")
                    _s3_client.download_file(PROCESSING_BUCKET, key, local_path)
                    return FileResponse(
                        local_path, media_type="image/png",
                        filename=f"frame_{frame_idx}.png",
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("Frame S3 fetch failed for %d: %s", frame_idx, e)
                    break
    raise HTTPException(status_code=404, detail="Frame not found")


@app.get("/client-configs/{job_id}")
def get_client_configs(job_id: str):
    """Return the four client-config families for a completed job.

    Maps the pipeline `VideoOutput` to the reactivity / educational /
    hazard / jobsite config families requested in the MVP brief.
    Tolerant to cross-invocation state loss by falling back to S3.
    """
    json_path: Optional[str] = None
    if job_id in processing_results:
        candidate = processing_results[job_id].get("output_json")
        if candidate and os.path.exists(candidate):
            json_path = candidate

    if json_path is None:
        json_path = _load_output_json_from_s3(job_id)

    if json_path is None or not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Results not found")

    try:
        from src.schemas import VideoOutput
        with open(json_path, "r", encoding="utf-8") as fp:
            video_output = VideoOutput(**json.load(fp))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to load VideoOutput for {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Results parse failed") from e

    return generate_client_configs(video_output)


@app.post("/process-image")
async def process_image(
    image: Optional[UploadFile] = File(None),
    s3_key: Optional[str] = Form(None),
    config: Optional[str] = Form(None),
):
    """Single-image (job-site) processing mode.

    Accepts either a presigned-uploaded S3 key or a direct multipart image
    and returns detection + classification inline (no DynamoDB job is
    created because the call is synchronous and short-lived).
    """
    if image is None and not s3_key:
        raise HTTPException(status_code=422, detail="Either 'image' or 's3_key' must be provided")

    proc_config = ProcessingConfig(**json.loads(config)) if config else ProcessingConfig()

    temp_dir = tempfile.mkdtemp()
    try:
        if s3_key:
            if _s3_client is None:
                raise HTTPException(status_code=503, detail="S3 client not available")
            filename = s3_key.split("/")[-1]
            local_path = os.path.join(temp_dir, filename)
            _s3_client.download_file(PROCESSING_BUCKET, s3_key, local_path)
        else:
            filename = image.filename
            local_path = os.path.join(temp_dir, filename)
            data = await image.read()
            with open(local_path, "wb") as fp:
                fp.write(data)

        # Lazy import to avoid heavy deps when only video mode is used
        from src.cv_labeler import CVLabeler
        from src.camera_profiles import detect_camera_from_filename, get_camera_profile

        camera_profile = get_camera_profile(detect_camera_from_filename(filename))
        labeler = CVLabeler(camera_profile=camera_profile)

        import cv2 as _cv2
        img = _cv2.imread(local_path)
        if img is None:
            raise HTTPException(status_code=400, detail="Failed to decode image")
        h, w = img.shape[:2]
        objects = labeler.label_frame(local_path, timestamp_ms=0.0, video_width=w, video_height=h)

        return {
            "filename": filename,
            "camera": camera_profile.name,
            "width": w,
            "height": h,
            "objects": [o.model_dump() for o in objects],
            "num_objects": len(objects),
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"/process-image failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/frames/{job_id}")
def list_frames(job_id: str):
    """List annotated + raw selected frames for a completed job.

    Each entry has a presigned HTTP(S) URL for the annotated image, the
    raw image and the per-frame JSON so the UI can render them directly.
    """
    # Try in-memory first (warm cache on the same container)
    manifest: list = []
    if job_id in processing_results:
        manifest = list(processing_results[job_id].get("frames_manifest") or [])

    if not manifest:
        manifest = _load_per_frame_manifest_from_s3(job_id)

    if not manifest:
        dyn = _dynamo_get_job(job_id)
        if not dyn:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=404, detail="No frame manifest for this job yet")

    frames = []
    for entry in manifest:
        frames.append({
            "frame_idx": entry.get("frame_idx"),
            "timestamp_ms": entry.get("timestamp_ms"),
            "num_objects": entry.get("num_objects", 0),
            "annotated_url": _presign_get(entry.get("annotated_key") or ""),
            "raw_url": _presign_get(entry.get("raw_key") or "") if entry.get("raw_key") else None,
            "json_url": _presign_get(entry.get("json_key") or "") if entry.get("json_key") else None,
        })
    return {"job_id": job_id, "num_frames": len(frames), "frames": frames}


@app.get("/video-class/{job_id}")
def get_video_class(job_id: str):
    """Return the explicit Driving vs Job Site video classification.

    This derives from the pipeline's VideoOutput (same source as
    /client-configs), but surfaces only the one-word class so the UI
    can display it prominently.
    """
    json_path: Optional[str] = None
    if job_id in processing_results:
        p = processing_results[job_id].get("output_json")
        if p and os.path.exists(p):
            json_path = p
    if json_path is None:
        json_path = _load_output_json_from_s3(job_id)
    if not json_path or not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail="Results not found")
    try:
        from src.schemas import VideoOutput
        with open(json_path, "r", encoding="utf-8") as fp:
            video_output = VideoOutput(**json.load(fp))
    except Exception as e:  # noqa: BLE001
        logger.error("video-class parse failed: %s", e)
        raise HTTPException(status_code=500, detail="Results parse failed") from e

    bundle = generate_client_configs(video_output)
    raw_class = (bundle.get("video_class") or "").strip().lower()
    # Collapse to the two customer-facing buckets
    if raw_class == "jobsite":
        label = "Job Site"
    else:
        label = "Driving"
    return {
        "job_id": job_id,
        "video_class": raw_class,
        "display_label": label,
        "collision": video_output.collision,
        "weather": video_output.weather,
        "lighting": video_output.lighting,
        "traffic": video_output.traffic,
    }


@app.post("/process-s3-video")
async def process_s3_video(payload: Dict[str, Any]):
    """Start a pipeline job from an S3 object already sitting in the
    custom datasources bucket (or the processing bucket).

    Body:
        {
          "s3_uri": "s3://bucket/key" | { "bucket": "...", "key": "..." },
          "config": { "max_snapshots": 5, "snapshot_strategy": "scene_change" }
        }
    """
    import uuid as _uuid

    bucket: Optional[str] = None
    key: Optional[str] = None
    s3_uri = payload.get("s3_uri") or payload.get("s3Uri")
    if isinstance(s3_uri, str) and s3_uri.startswith("s3://"):
        rest = s3_uri[5:]
        if "/" in rest:
            bucket, key = rest.split("/", 1)
    else:
        bucket = payload.get("bucket")
        key = payload.get("key") or payload.get("s3_key")

    if not bucket or not key:
        raise HTTPException(status_code=422, detail="Provide s3_uri or bucket+key")
    if _s3_client is None:
        raise HTTPException(status_code=503, detail="S3 client not available")

    cfg_raw = payload.get("config") or {}
    proc_config = ProcessingConfig(**cfg_raw)
    job_id = str(_uuid.uuid4())
    filename = key.split("/")[-1]

    # If the video lives in a different bucket (e.g. custom datasources),
    # copy it into the processing bucket so the worker (which only has
    # access to PROCESSING_BUCKET by default) can read it.
    target_key = key
    if bucket != PROCESSING_BUCKET:
        target_key = f"input/videos/{job_id}/{filename}"
        try:
            _s3_client.copy_object(
                Bucket=PROCESSING_BUCKET,
                Key=target_key,
                CopySource={"Bucket": bucket, "Key": key},
                ServerSideEncryption="aws:kms",
                MetadataDirective="REPLACE",
                ContentType="video/mp4",
            )
            logger.info("Copied s3://%s/%s -> s3://%s/%s", bucket, key, PROCESSING_BUCKET, target_key)
        except Exception as e:  # noqa: BLE001
            logger.error("S3 copy failed: %s", e)
            raise HTTPException(status_code=500, detail=f"S3 copy failed: {e}") from e

    _dynamo_put_job(
        job_id,
        status="QUEUED",
        filename=filename,
        input_type="s3",
        source_bucket=bucket,
        source_key=key,
        snapshot_strategy=proc_config.snapshot_strategy,
        max_snapshots=proc_config.max_snapshots,
    )

    worker_payload = {
        "action": "process_worker",
        "job_id": job_id,
        "s3_key": target_key,
        "filename": filename,
        "config": proc_config.model_dump(),
    }

    if _lambda_client and LAMBDA_FUNCTION_NAME:
        try:
            _lambda_client.invoke(
                FunctionName=LAMBDA_FUNCTION_NAME,
                InvocationType="Event",
                Payload=json.dumps(worker_payload).encode(),
            )
            logger.info("Worker invoked async for S3 job %s", job_id)
        except Exception as e:  # noqa: BLE001
            logger.error("Worker dispatch failed: %s", e)
            _dynamo_update_status(job_id, "FAILED", error_message=str(e))
            raise HTTPException(status_code=500, detail=f"Worker dispatch failed: {e}") from e
    else:
        # Local dev: run synchronously via a background task (api_server
        # is imported by uvicorn in dev; we intentionally avoid importing
        # BackgroundTasks here because that requires request context).
        import threading
        threading.Thread(
            target=process_video_worker,
            args=(worker_payload,),
            daemon=True,
        ).start()

    return {"job_id": job_id, "status": "QUEUED", "input_type": "s3"}


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

