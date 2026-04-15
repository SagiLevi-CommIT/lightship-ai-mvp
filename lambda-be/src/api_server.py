"""FastAPI server for the Lightship MVP pipeline.

Provides REST API endpoints for video processing with Rekognition-based pipeline.
Persists job status to DynamoDB and results to S3.
"""
import os
import json
import tempfile
import shutil
import glob
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, File, UploadFile, BackgroundTasks, HTTPException, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import boto3
from botocore.exceptions import ClientError

from src.pipeline import Pipeline, PipelineResult
from src.config import TEMP_FRAMES_DIR, AWS_REGION, OUTPUT_DIR

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Lightship MVP API",
    description="Rekognition-based dashcam video annotation and classification",
    version="3.0.0",
)

import logging as _ulog
_ulog.getLogger("uvicorn.access").setLevel(_ulog.WARNING)

ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

processing_status: Dict[str, Dict[str, Any]] = {}
processing_results: Dict[str, Dict[str, Any]] = {}

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "lightship_jobs")
PROCESSING_BUCKET = os.environ.get("PROCESSING_BUCKET", "")
RESULTS_BUCKET = os.environ.get("RESULTS_BUCKET", PROCESSING_BUCKET)
RESULTS_PREFIX = os.environ.get("RESULTS_PREFIX", "results")

try:
    from botocore.config import Config as _BotocoreConfig
    _s3_client = boto3.client(
        "s3", region_name=AWS_REGION,
        config=_BotocoreConfig(signature_version="s3v4"),
    )
    logger.info("S3 client initialised, PROCESSING_BUCKET=%s", PROCESSING_BUCKET)
except Exception as e:
    logger.warning("S3 client init failed: %s", e)
    _s3_client = None

try:
    _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    _jobs_table = _dynamodb.Table(DYNAMODB_TABLE)
    logger.info("DynamoDB table connected: %s", DYNAMODB_TABLE)
except Exception as e:
    logger.warning("DynamoDB init failed (in-memory only): %s", e)
    _jobs_table = None

LAMBDA_FUNCTION_NAME = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "")
_lambda_client = None
if LAMBDA_FUNCTION_NAME:
    try:
        _lambda_client = boto3.client("lambda", region_name=AWS_REGION)
        logger.info("Lambda client for async: %s", LAMBDA_FUNCTION_NAME)
    except Exception as e:
        logger.warning("Lambda client init failed: %s", e)


# ─── DynamoDB helpers ─────────────────────────────────────────────────────────

def _dynamo_put_job(job_id: str, status: str, **extra) -> None:
    if _jobs_table is None:
        return
    try:
        item = {"job_id": job_id, "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
                **{k: v for k, v in extra.items() if v is not None}}
        _jobs_table.put_item(Item=item)
    except Exception as e:
        logger.warning("DynamoDB put failed for %s: %s", job_id, e)


def _dynamo_update_status(job_id: str, status: str, **extra) -> None:
    if _jobs_table is None:
        return
    try:
        expr = "SET #s = :s, updated_at = :u"
        vals: Dict[str, Any] = {":s": status, ":u": datetime.now(timezone.utc).isoformat()}
        names = {"#s": "status"}
        for k, v in extra.items():
            if v is not None:
                expr += f", {k} = :{k}"
                vals[f":{k}"] = v
        _jobs_table.update_item(Key={"job_id": job_id}, UpdateExpression=expr,
                                ExpressionAttributeValues=vals, ExpressionAttributeNames=names)
    except Exception as e:
        logger.warning("DynamoDB update failed for %s: %s", job_id, e)


def _dynamo_get_job(job_id: str) -> Optional[Dict[str, Any]]:
    if _jobs_table is None:
        return None
    try:
        return _jobs_table.get_item(Key={"job_id": job_id}).get("Item")
    except Exception as e:
        logger.warning("DynamoDB get failed for %s: %s", job_id, e)
        return None


# ─── S3 helpers ───────────────────────────────────────────────────────────────

def _upload_results_to_s3(job_id: str, temp_dir: str) -> Optional[str]:
    if not _s3_client or not RESULTS_BUCKET:
        logger.warning("S3 not configured, skipping results upload")
        return None
    prefix = f"{RESULTS_PREFIX}/{job_id}"
    uploaded = 0
    for root, _dirs, files in os.walk(temp_dir):
        for fname in files:
            local = os.path.join(root, fname)
            rel = os.path.relpath(local, temp_dir)
            s3_key = f"{prefix}/{rel}"
            try:
                _s3_client.upload_file(local, RESULTS_BUCKET, s3_key,
                                       ExtraArgs={"ServerSideEncryption": "aws:kms"})
                uploaded += 1
            except Exception as e:
                logger.error("Failed to upload %s: %s", s3_key, e)
    logger.info("Uploaded %d result files to s3://%s/%s/", uploaded, RESULTS_BUCKET, prefix)
    return f"s3://{RESULTS_BUCKET}/{prefix}/"


def _download_results_from_s3(job_id: str) -> Optional[Dict[str, Any]]:
    if not _s3_client or not RESULTS_BUCKET:
        return None
    prefix = f"{RESULTS_PREFIX}/{job_id}"
    try:
        resp = _s3_client.list_objects_v2(Bucket=RESULTS_BUCKET, Prefix=prefix, MaxKeys=100)
        for obj in resp.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/output.json") or (key.endswith(".json") and "pipeline" not in key):
                body = _s3_client.get_object(Bucket=RESULTS_BUCKET, Key=key)["Body"]
                return json.loads(body.read())
    except Exception as e:
        logger.warning("Failed to fetch results from S3 for %s: %s", job_id, e)
    return None


# ─── Models ───────────────────────────────────────────────────────────────────

class ProcessingConfig(BaseModel):
    snapshot_strategy: str = "naive"
    max_snapshots: int = 5
    cleanup_frames: bool = False
    use_cv_labeler: bool = True
    hazard_mode: str = "sliding_window"
    window_size: int = 3
    window_overlap: int = 1


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "Lightship MVP API", "version": "3.0.0", "status": "running"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/jobs")
def list_jobs(limit: int = 50):
    if _jobs_table is None:
        return {"jobs": []}
    try:
        resp = _jobs_table.scan(Limit=limit)
        jobs = resp.get("Items", [])
        jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"jobs": jobs}
    except Exception as e:
        logger.warning("DynamoDB scan failed: %s", e)
        return {"jobs": []}


@app.get("/presign-upload")
def presign_upload(filename: str, content_type: str = "video/mp4"):
    import uuid as _uuid
    if _s3_client is None:
        raise HTTPException(status_code=503, detail="S3 client not available")
    s3_key = f"input/videos/{_uuid.uuid4()}/{filename}"
    try:
        url = _s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": PROCESSING_BUCKET, "Key": s3_key,
                    "ContentType": content_type, "ServerSideEncryption": "aws:kms"},
            ExpiresIn=900,
        )
        return {"presign_url": url, "s3_key": s3_key,
                "required_headers": {"Content-Type": content_type,
                                     "x-amz-server-side-encryption": "aws:kms"}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Presign failed: {e}")


@app.post("/process-video")
async def process_video(
    background_tasks: BackgroundTasks,
    video: Optional[UploadFile] = File(None),
    s3_key: Optional[str] = Form(None),
    config: Optional[str] = Form(None),
):
    import uuid as _uuid
    if video is None and not s3_key:
        raise HTTPException(status_code=422, detail="Either 'video' or 's3_key' required")

    job_id = str(_uuid.uuid4())
    proc_config = ProcessingConfig(**(json.loads(config) if config else {}))
    temp_dir = tempfile.mkdtemp()

    if s3_key:
        filename = s3_key.split("/")[-1]
    else:
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
        except Exception as e:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"S3 upload failed: {e}")

    _dynamo_put_job(job_id, status="QUEUED", filename=filename, input_type="video",
                    snapshot_strategy=proc_config.snapshot_strategy,
                    max_snapshots=proc_config.max_snapshots)

    if _lambda_client and LAMBDA_FUNCTION_NAME:
        payload = {"action": "process_worker", "job_id": job_id, "s3_key": s3_key,
                   "filename": filename, "config": proc_config.model_dump()}
        try:
            _lambda_client.invoke(FunctionName=LAMBDA_FUNCTION_NAME,
                                  InvocationType="Event", Payload=json.dumps(payload).encode())
        except Exception as e:
            _dynamo_update_status(job_id, "FAILED", error=str(e))
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=f"Worker dispatch failed: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        video_path = os.path.join(temp_dir, filename)
        if not os.path.exists(video_path) and _s3_client:
            try:
                _s3_client.download_file(PROCESSING_BUCKET, s3_key, video_path)
            except Exception as e:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise HTTPException(status_code=500, detail=f"S3 download failed: {e}")
        processing_status[job_id] = {"status": "QUEUED", "progress": 0.0,
                                     "message": "Video uploaded, queued for processing",
                                     "video_path": video_path, "temp_dir": temp_dir}
        background_tasks.add_task(process_video_task, job_id, video_path, temp_dir, proc_config)

    return {"job_id": job_id, "status": "QUEUED"}


def process_video_worker(event: dict) -> dict:
    job_id = event["job_id"]
    s3_key = event["s3_key"]
    filename = event["filename"]
    proc_config = ProcessingConfig(**event.get("config", {}))
    logger.info("Worker started: job=%s file=%s", job_id, filename)
    temp_dir = tempfile.mkdtemp()
    video_path = os.path.join(temp_dir, filename)
    try:
        _s3_client.download_file(PROCESSING_BUCKET, s3_key, video_path)
        processing_status[job_id] = {"status": "QUEUED", "progress": 0.0,
                                     "message": "Worker started",
                                     "video_path": video_path, "temp_dir": temp_dir}
        process_video_task(job_id, video_path, temp_dir, proc_config)
        return {"status": "ok", "job_id": job_id}
    except Exception as e:
        logger.error("Worker failed: job=%s: %s", job_id, e, exc_info=True)
        _dynamo_update_status(job_id, "FAILED", error_message=str(e))
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"status": "error", "job_id": job_id, "error": str(e)}


def process_video_task(job_id: str, video_path: str, temp_dir: str, config: ProcessingConfig):
    try:
        processing_status[job_id].update({"status": "PROCESSING", "progress": 0.1,
                                          "message": "Initializing pipeline", "current_step": "init"})
        _dynamo_update_status(job_id, "PROCESSING", current_step="init")

        pipeline = Pipeline(snapshot_strategy=config.snapshot_strategy,
                            max_snapshots=config.max_snapshots,
                            cleanup_frames=config.cleanup_frames)
        pipeline.merger.output_dir = temp_dir

        processing_status[job_id].update({"progress": 0.3, "message": "Processing video",
                                          "current_step": "processing"})

        pipe_result: Optional[PipelineResult] = pipeline.process_video(video_path, is_train=False)
        if pipe_result is None or pipe_result.output_json_path is None:
            raise ValueError("Pipeline returned None — check logs for details")

        # Ensure output.json exists in temp_dir
        final_output_path = os.path.join(temp_dir, "output.json")
        if not os.path.exists(final_output_path):
            if os.path.exists(pipe_result.output_json_path):
                shutil.copy2(pipe_result.output_json_path, final_output_path)
            else:
                raise ValueError("No output JSON produced")

        processing_status[job_id].update({"progress": 0.8, "message": "Persisting results",
                                          "current_step": "persist"})

        # Copy selected frames into temp_dir so they survive across Lambda invocations
        sel_dest = os.path.join(temp_dir, "selected_frames")
        os.makedirs(sel_dest, exist_ok=True)
        for idx, fpath in pipe_result.selected_frame_paths.items():
            if os.path.exists(fpath):
                dest = os.path.join(sel_dest, os.path.basename(fpath))
                try:
                    shutil.copy2(fpath, dest)
                    pipe_result.selected_frame_paths[idx] = dest
                except Exception as e:
                    logger.warning("Failed to copy selected frame %d: %s", idx, e)

        # Copy annotated frames into temp_dir for S3 upload
        ann_dest = os.path.join(temp_dir, "annotated_frames")
        os.makedirs(ann_dest, exist_ok=True)
        for idx, ann_path in pipe_result.annotated_frame_paths.items():
            if os.path.exists(ann_path):
                dest = os.path.join(ann_dest, os.path.basename(ann_path))
                try:
                    shutil.copy2(ann_path, dest)
                    pipe_result.annotated_frame_paths[idx] = dest
                except Exception as e:
                    logger.warning("Failed to copy annotated frame %d: %s", idx, e)

        s3_results_uri = _upload_results_to_s3(job_id, temp_dir)

        with open(final_output_path, "r") as f:
            output_data = json.load(f)

        video_metadata = pipeline.video_loader.load_video_metadata(video_path)

        # Build frames info from PipelineResult (paths already point to temp_dir copies)
        extracted_frames: Dict[int, str] = dict(pipe_result.selected_frame_paths)
        annotated_frames: Dict[int, str] = {
            idx: p for idx, p in pipe_result.annotated_frame_paths.items() if os.path.exists(p)
        }

        snapshots_info = []
        for idx in sorted(pipe_result.snapshot_timestamps.keys()):
            ts = pipe_result.snapshot_timestamps[idx]
            entry: Dict[str, Any] = {"frame_idx": idx, "timestamp_ms": ts,
                                     "has_annotated": idx in annotated_frames}
            if idx in extracted_frames:
                entry["frame_path"] = extracted_frames[idx]
            if idx in annotated_frames:
                entry["annotated_path"] = annotated_frames[idx]
            snapshots_info.append(entry)

        summary = {
            "filename": output_data.get("filename", ""),
            "video_class": output_data.get("video_class", "unknown"),
            "road_type": output_data.get("road_type", "unknown"),
            "total_objects": len(output_data.get("objects", [])),
            "num_snapshots": len(snapshots_info),
            "num_hazards": len(output_data.get("hazard_events", [])),
        }

        processing_results[job_id] = {
            "output_json": final_output_path,
            "extracted_frames": extracted_frames,
            "annotated_frames": annotated_frames,
            "snapshots": snapshots_info,
            "video_metadata": {
                "filename": video_metadata.filename, "camera": video_metadata.camera,
                "fps": video_metadata.fps, "duration_ms": video_metadata.duration_ms,
                "width": video_metadata.width, "height": video_metadata.height,
            },
            "summary": summary,
            "temp_dir": temp_dir,
            "s3_results_uri": s3_results_uri,
            "client_config": output_data.get("client_config"),
            "detection_summary": output_data.get("detection_summary"),
        }

        processing_status[job_id].update({"status": "COMPLETED", "progress": 1.0,
                                          "message": "Processing completed", "current_step": "completed"})
        _dynamo_update_status(job_id, "COMPLETED",
                              completed_at=datetime.now(timezone.utc).isoformat(),
                              video_class=output_data.get("video_class", "unknown"),
                              road_type=output_data.get("road_type", "unknown"),
                              s3_results_uri=s3_results_uri or "")
        logger.info("Job %s completed", job_id)

    except Exception as e:
        logger.error("Job %s failed: %s", job_id, e, exc_info=True)
        processing_status[job_id].update({"status": "FAILED", "progress": 0.0,
                                          "message": f"Processing failed: {e}", "current_step": "error"})
        _dynamo_update_status(job_id, "FAILED", error_message=str(e))


# ─── Status / Results / Download endpoints ────────────────────────────────────

@app.get("/status/{job_id}")
def get_status(job_id: str):
    if job_id in processing_status:
        return processing_status[job_id]
    dynamo_item = _dynamo_get_job(job_id)
    if dynamo_item:
        return {"status": dynamo_item.get("status", "UNKNOWN"),
                "progress": float(dynamo_item.get("progress", 0)),
                "message": dynamo_item.get("message", ""),
                "current_step": dynamo_item.get("current_step")}
    raise HTTPException(status_code=404, detail="Job not found")


@app.get("/results/{job_id}")
def get_results(job_id: str):
    if job_id in processing_results:
        return processing_results[job_id]
    s3_data = _download_results_from_s3(job_id)
    if s3_data:
        return {"output_data": s3_data, "source": "s3"}
    dynamo_item = _dynamo_get_job(job_id)
    if dynamo_item and dynamo_item.get("status") == "COMPLETED":
        return {"status": "COMPLETED", "message": "Results in S3",
                "s3_results_uri": dynamo_item.get("s3_results_uri", "")}
    raise HTTPException(status_code=404, detail="Results not found")


@app.get("/frames/{job_id}")
def get_frames_list(job_id: str):
    """Return list of available frames for a job."""
    if job_id in processing_results:
        return {"frames": processing_results[job_id].get("snapshots", [])}

    # Fall back to S3: list annotated frames to reconstruct frame list
    if _s3_client and RESULTS_BUCKET:
        prefix = f"{RESULTS_PREFIX}/{job_id}/annotated_frames/"
        try:
            resp = _s3_client.list_objects_v2(Bucket=RESULTS_BUCKET, Prefix=prefix, MaxKeys=50)
            frames = []
            for obj in resp.get("Contents", []):
                basename = obj["Key"].rsplit("/", 1)[-1]
                parts = basename.replace("_annotated.png", "").split("_")
                try:
                    fidx_pos = parts.index("frame") + 1
                    fidx = int(parts[fidx_pos])
                    ts_str = parts[fidx_pos + 1].replace("ms", "")
                    ts = float(ts_str)
                    frames.append({"frame_idx": fidx, "timestamp_ms": ts, "has_annotated": True})
                except (ValueError, IndexError):
                    continue
            frames.sort(key=lambda f: f["timestamp_ms"])
            if frames:
                return {"frames": frames}
        except Exception as e:
            logger.warning("S3 frames list failed for %s: %s", job_id, e)

    raise HTTPException(status_code=404, detail="Results not found")


@app.get("/download/json/{job_id}")
def download_json(job_id: str):
    if job_id in processing_results:
        json_path = processing_results[job_id]["output_json"]
        if os.path.exists(json_path):
            return FileResponse(json_path, media_type="application/json", filename="output.json")
    s3_data = _download_results_from_s3(job_id)
    if s3_data:
        return JSONResponse(content=s3_data)
    raise HTTPException(status_code=404, detail="JSON not found")


def _serve_frame_from_local_or_s3(job_id: str, frame_dict_key: str, frame_idx: int, s3_subdir: str):
    """Try local file first, then redirect to S3 presigned URL to avoid ALB body size limit."""
    # Try in-memory results (same Lambda instance) — only for small files
    if job_id in processing_results:
        fpath = processing_results[job_id].get(frame_dict_key, {}).get(frame_idx)
        if fpath and os.path.exists(fpath):
            fsize = os.path.getsize(fpath)
            if fsize < 500_000:
                return FileResponse(fpath, media_type="image/png",
                                    filename=f"{s3_subdir}_{frame_idx}.png")

    # Serve via S3 presigned URL (works for any size, bypasses ALB limit)
    if _s3_client and RESULTS_BUCKET:
        prefix = f"{RESULTS_PREFIX}/{job_id}/{s3_subdir}/"
        try:
            resp = _s3_client.list_objects_v2(Bucket=RESULTS_BUCKET, Prefix=prefix, MaxKeys=50)
            for obj in resp.get("Contents", []):
                key = obj["Key"]
                basename = key.rsplit("/", 1)[-1]
                if f"_frame_{frame_idx}_" in basename or f"_frame_{frame_idx}ms" in basename:
                    presigned = _s3_client.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": RESULTS_BUCKET, "Key": key},
                        ExpiresIn=3600,
                    )
                    return JSONResponse(content={"url": presigned, "filename": basename})
        except Exception as e:
            logger.warning("S3 frame fetch failed for %s/%s/%d: %s", job_id, s3_subdir, frame_idx, e)

    raise HTTPException(status_code=404, detail="Frame not found")


@app.get("/download/frame/{job_id}/{frame_idx}")
def download_frame(job_id: str, frame_idx: int):
    return _serve_frame_from_local_or_s3(job_id, "extracted_frames", frame_idx, "selected_frames")


@app.get("/download/annotated-frame/{job_id}/{frame_idx}")
def download_annotated_frame(job_id: str, frame_idx: int):
    return _serve_frame_from_local_or_s3(job_id, "annotated_frames", frame_idx, "annotated_frames")


@app.delete("/cleanup/{job_id}")
def cleanup_job(job_id: str):
    if job_id in processing_results:
        td = processing_results[job_id].get("temp_dir")
        if td and os.path.exists(td):
            shutil.rmtree(td)
        del processing_results[job_id]
    if job_id in processing_status:
        del processing_status[job_id]
    return {"message": "Cleanup successful"}


if __name__ == "__main__":
    uvicorn.run("src.api_server:app", host="0.0.0.0", port=8000, reload=True)
