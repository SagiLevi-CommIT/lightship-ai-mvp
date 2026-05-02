"""ECS Fargate inference worker entry point.

Supports two modes:

* ``run_task``: one job comes from Step Functions container overrides.
* ``sqs_consumer``: a warm ECS service polls the processing SQS queue and
  reuses the same process/model cache across jobs.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make lambda-be/src importable from the worker image.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE / "src"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("inference_worker")
_PROCESS_STARTED_MS = int(time.time() * 1000)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "lightship_jobs")
PROCESSING_BUCKET = os.environ["PROCESSING_BUCKET"]
PROCESSING_QUEUE_URL = os.getenv("PROCESSING_QUEUE_URL", "")
WORKER_MODE = os.getenv("WORKER_MODE", "run_task").strip().lower()

DEFAULT_DETECTOR_BACKEND = os.getenv("DETECTOR_BACKEND", "florence2")
DEFAULT_LANE_BACKEND = os.getenv("LANE_BACKEND", "ufldv2")
DEFAULT_MAX_SNAPSHOTS = int(os.getenv("MAX_SNAPSHOTS", "5"))
DEFAULT_SNAPSHOT_STRATEGY = os.getenv("SNAPSHOT_STRATEGY", "clustering")
DEFAULT_NATIVE_SAMPLING_MODE = (
    os.getenv("NATIVE_SAMPLING_MODE", "count").strip().lower() or "count"
)


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


DEFAULT_NATIVE_FPS = _parse_optional_float(os.getenv("NATIVE_FPS", ""))


@dataclass(frozen=True)
class WorkerJob:
    job_id: str
    s3_input_key: str
    s3_output_prefix: str
    detector_backend: str
    lane_backend: str
    max_snapshots: int
    snapshot_strategy: str
    native_fps: float | None
    native_sampling_mode: str
    dispatched_at_epoch_ms: str = ""


import boto3

_s3 = boto3.client("s3", region_name=AWS_REGION)
_ddb = boto3.client("dynamodb", region_name=AWS_REGION)
_sqs = boto3.client("sqs", region_name=AWS_REGION)
_pipeline_cache: dict[tuple[str, str], Any] = {}


def _log_timing(stage: str, elapsed_ms: float, **fields: object) -> None:
    suffix = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.info(
        "TIMING stage=%s elapsed_ms=%.1f%s",
        stage,
        float(elapsed_ms),
        f" {suffix}" if suffix else "",
    )


def _ddb_update(job_id: str, status: str, progress: float = 0.0, error: str = "") -> None:
    try:
        expr = "SET #s = :s, progress = :p, updated_at = :t"
        names = {"#s": "status"}
        vals: dict[str, dict[str, str]] = {
            ":s": {"S": status},
            ":p": {"N": str(round(progress, 3))},
            ":t": {"S": __import__("datetime").datetime.utcnow().isoformat() + "Z"},
        }
        if error:
            expr += ", error_message = :e"
            vals[":e"] = {"S": error[:2000]}
        _ddb.update_item(
            TableName=DYNAMODB_TABLE,
            Key={"job_id": {"S": job_id}},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=vals,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DynamoDB update failed for job=%s: %s", job_id, exc)


def _ddb_complete(job: WorkerJob, output_json_key: str) -> None:
    try:
        _ddb.update_item(
            TableName=DYNAMODB_TABLE,
            Key={"job_id": {"S": job.job_id}},
            UpdateExpression=(
                "SET #s = :s, progress = :p, updated_at = :t, "
                "output_s3_key = :ok, result_prefix = :rp"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": {"S": "COMPLETED"},
                ":p": {"N": "1.0"},
                ":t": {"S": __import__("datetime").datetime.utcnow().isoformat() + "Z"},
                ":ok": {"S": output_json_key},
                ":rp": {"S": job.s3_output_prefix},
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DynamoDB COMPLETED update failed for job=%s: %s", job.job_id, exc)


_S3_EXTRA_JSON = {
    "ServerSideEncryption": "aws:kms",
    "ContentType": "application/json",
}
_S3_EXTRA_PNG = {
    "ServerSideEncryption": "aws:kms",
    "ContentType": "image/png",
}


def _upload_dir(
    local_dir: Path,
    prefix: str,
    *,
    exclude: set[Path] | None = None,
) -> list[str]:
    """Upload all files in local_dir to S3 under prefix. Returns S3 keys."""
    keys: list[str] = []
    excluded = {p.resolve() for p in (exclude or set())}
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.resolve() in excluded:
            logger.info("Skipping upload of input artefact %s", path.name)
            continue
        rel = path.relative_to(local_dir)
        key = prefix + str(rel).replace("\\", "/")
        lower = path.suffix.lower()
        if lower == ".json":
            extra = dict(_S3_EXTRA_JSON)
        elif lower in (".png", ".jpg", ".jpeg", ".webp"):
            extra = (
                dict(_S3_EXTRA_PNG)
                if lower == ".png"
                else {
                    "ServerSideEncryption": "aws:kms",
                    "ContentType": "image/jpeg" if lower in (".jpg", ".jpeg") else "image/webp",
                }
            )
        else:
            extra = {"ServerSideEncryption": "aws:kms"}
        _s3.upload_file(str(path), PROCESSING_BUCKET, key, ExtraArgs=extra)
        keys.append(key)
        logger.info("Uploaded %s -> s3://%s/%s", rel, PROCESSING_BUCKET, key)
    return keys


def _job_from_env() -> WorkerJob:
    job_id = os.environ.get("JOB_ID", "").strip()
    s3_key = os.environ.get("S3_INPUT_KEY", "").strip()
    if not job_id or not s3_key:
        raise RuntimeError("JOB_ID and S3_INPUT_KEY are required in run_task mode")
    return WorkerJob(
        job_id=job_id,
        s3_input_key=s3_key,
        s3_output_prefix=(
            os.environ.get("S3_OUTPUT_PREFIX", f"results/{job_id}/").rstrip("/") + "/"
        ),
        detector_backend=DEFAULT_DETECTOR_BACKEND,
        lane_backend=DEFAULT_LANE_BACKEND,
        max_snapshots=DEFAULT_MAX_SNAPSHOTS,
        snapshot_strategy=DEFAULT_SNAPSHOT_STRATEGY,
        native_fps=DEFAULT_NATIVE_FPS,
        native_sampling_mode=DEFAULT_NATIVE_SAMPLING_MODE,
        dispatched_at_epoch_ms=os.getenv("DISPATCHED_AT_EPOCH_MS", "").strip(),
    )


def _job_from_sqs_payload(payload: dict[str, Any]) -> WorkerJob:
    job_id = str(payload["job_id"])
    ecs_env = payload.get("ecs_env") or {}
    config = payload.get("config") or {}
    native_fps = _parse_optional_float(ecs_env.get("NATIVE_FPS", config.get("native_fps")))
    return WorkerJob(
        job_id=job_id,
        s3_input_key=str(payload["s3_key"]),
        s3_output_prefix=f"results/{job_id}/",
        detector_backend=str(
            ecs_env.get("DETECTOR_BACKEND")
            or config.get("detector_backend")
            or DEFAULT_DETECTOR_BACKEND
        ),
        lane_backend=str(
            ecs_env.get("LANE_BACKEND")
            or config.get("lane_backend")
            or DEFAULT_LANE_BACKEND
        ),
        max_snapshots=int(
            ecs_env.get("MAX_SNAPSHOTS")
            or config.get("max_snapshots")
            or DEFAULT_MAX_SNAPSHOTS
        ),
        snapshot_strategy=str(
            ecs_env.get("SNAPSHOT_STRATEGY")
            or config.get("snapshot_strategy")
            or DEFAULT_SNAPSHOT_STRATEGY
        ),
        native_fps=native_fps,
        native_sampling_mode=str(
            ecs_env.get("NATIVE_SAMPLING_MODE")
            or config.get("native_sampling_mode")
            or DEFAULT_NATIVE_SAMPLING_MODE
        ).lower(),
        dispatched_at_epoch_ms=str(ecs_env.get("DISPATCHED_AT_EPOCH_MS") or ""),
    )


def _get_pipeline(job: WorkerJob, temp_dir: str):
    """Return a warm Pipeline, reusing detector model instances by backend."""
    from src.pipeline import Pipeline

    cache_key = (job.detector_backend.lower(), job.lane_backend.lower())
    pipeline = _pipeline_cache.get(cache_key)
    if pipeline is None:
        started = time.monotonic()
        pipeline = Pipeline(
            snapshot_strategy=job.snapshot_strategy,
            max_snapshots=job.max_snapshots,
            cleanup_frames=False,
            use_cv_labeler=True,
            native_fps=job.native_fps,
            native_sampling_mode=job.native_sampling_mode,
            detector_backend=job.detector_backend,
            lane_backend=job.lane_backend,
        )
        _pipeline_cache[cache_key] = pipeline
        _log_timing(
            "pipeline_init",
            (time.monotonic() - started) * 1000.0,
            backend=job.detector_backend,
            mode="cold_pipeline_object",
        )
    else:
        pipeline.snapshot_strategy = job.snapshot_strategy
        pipeline.max_snapshots = job.max_snapshots
        pipeline.native_fps = job.native_fps
        pipeline.native_sampling_mode = job.native_sampling_mode
        pipeline.snapshot_selector.strategy = job.snapshot_strategy
        pipeline.snapshot_selector.max_snapshots = job.max_snapshots
        _log_timing("pipeline_init", 0.0, backend=job.detector_backend, mode="warm_reuse")

    pipeline.merger.output_dir = temp_dir
    return pipeline


def _run_job(job: WorkerJob, temp_dir: str) -> None:
    from src.result_persistence import persist_frame_artefacts, put_frames_manifest_json

    logger.info(
        "Worker processing job=%s input=%s output_prefix=%s backend=%s strategy=%s max=%s native_mode=%s native_fps=%s",
        job.job_id,
        job.s3_input_key,
        job.s3_output_prefix,
        job.detector_backend,
        job.snapshot_strategy,
        job.max_snapshots,
        job.native_sampling_mode,
        job.native_fps,
    )
    if job.dispatched_at_epoch_ms:
        try:
            dispatched_ms = int(job.dispatched_at_epoch_ms)
            if WORKER_MODE == "sqs_consumer":
                _log_timing("ecs_startup", 0.0, source="warm_worker_already_running")
                _log_timing(
                    "queue_wait",
                    max(0, int(time.time() * 1000) - dispatched_ms),
                    source="sqs_to_warm_worker",
                )
            else:
                _log_timing(
                    "ecs_startup",
                    max(0, _PROCESS_STARTED_MS - dispatched_ms),
                    source="dispatch_to_worker_process",
                )
        except ValueError:
            logger.warning("Invalid dispatched_at_epoch_ms=%s", job.dispatched_at_epoch_ms)

    _ddb_update(job.job_id, "PROCESSING", 0.02)

    filename = job.s3_input_key.split("/")[-1]
    video_path = os.path.join(temp_dir, filename)
    logger.info("Downloading s3://%s/%s ...", PROCESSING_BUCKET, job.s3_input_key)
    download_started = time.monotonic()
    _s3.download_file(PROCESSING_BUCKET, job.s3_input_key, video_path)
    _log_timing("download", (time.monotonic() - download_started) * 1000.0)
    _ddb_update(job.job_id, "PROCESSING", 0.08)

    pipeline = _get_pipeline(job, temp_dir)

    def _progress(p: float, step: str, msg: str) -> None:
        pct = 0.10 + p * 0.80
        _ddb_update(job.job_id, "PROCESSING", pct)
        logger.info("Progress %.0f%%: %s - %s", pct * 100, step, msg)

    pipeline_started = time.monotonic()
    output_path = pipeline.process_video(video_path, progress_cb=_progress)
    _log_timing("pipeline_total", (time.monotonic() - pipeline_started) * 1000.0)
    if not output_path:
        raise RuntimeError("Pipeline returned no output; check logs for errors")

    _ddb_update(job.job_id, "PROCESSING", 0.92)

    try:
        persist_started = time.monotonic()
        with open(output_path, encoding="utf-8") as fp:
            output_doc = json.load(fp)
        all_obj_dicts = list(output_doc.get("objects") or [])
        frames_manifest = persist_frame_artefacts(
            _s3,
            PROCESSING_BUCKET,
            job.job_id,
            getattr(pipeline, "last_selected_frames", {}) or {},
            getattr(pipeline, "last_annotated_frames", {}) or {},
            getattr(pipeline, "last_frame_timestamps", {}) or {},
            all_obj_dicts,
            getattr(pipeline, "last_extraction_manifest", []) or [],
        )
        put_frames_manifest_json(_s3, PROCESSING_BUCKET, job.job_id, frames_manifest)
        logger.info(
            "Persisted %d frame artefact entries under results/%s/frames/",
            len(frames_manifest),
            job.job_id,
        )
        _log_timing("persistence", (time.monotonic() - persist_started) * 1000.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Frame artefact / manifest persistence failed: %s", exc)

    upload_started = time.monotonic()
    uploaded_keys = _upload_dir(
        Path(temp_dir),
        job.s3_output_prefix,
        exclude={Path(video_path)},
    )
    _log_timing(
        "s3_upload",
        (time.monotonic() - upload_started) * 1000.0,
        files=len(uploaded_keys),
    )

    output_json_key = ""
    if output_path and os.path.exists(output_path):
        normalized_key = job.s3_output_prefix + "output.json"
        try:
            _s3.upload_file(
                str(output_path),
                PROCESSING_BUCKET,
                normalized_key,
                ExtraArgs=dict(_S3_EXTRA_JSON),
            )
            output_json_key = normalized_key
            logger.info("Uploaded output.json -> s3://%s/%s", PROCESSING_BUCKET, normalized_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("output.json upload failed: %s", exc)
            output_json_key = next(
                (k for k in uploaded_keys if k.endswith(".json") and "/client_configs/" not in k),
                "",
            )
    else:
        output_json_key = next(
            (k for k in uploaded_keys if k.endswith(".json") and "/client_configs/" not in k),
            "",
        )

    _ddb_complete(job, output_json_key)
    logger.info(
        "Worker completed: job=%s output_prefix=%s files=%d",
        job.job_id,
        job.s3_output_prefix,
        len(uploaded_keys),
    )


def _run_task_main() -> None:
    job = _job_from_env()
    temp_dir = tempfile.mkdtemp(prefix="lightship_worker_")
    try:
        _run_job(job, temp_dir)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Worker failed: %s", exc)
        _ddb_update(job.job_id, "FAILED", error=str(exc))
        sys.exit(1)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _sqs_consumer_main() -> None:
    if not PROCESSING_QUEUE_URL:
        raise RuntimeError("PROCESSING_QUEUE_URL is required for sqs_consumer mode")
    logger.info("Warm SQS worker started: queue=%s", PROCESSING_QUEUE_URL)
    _log_timing("warm_worker_ready", int(time.time() * 1000) - _PROCESS_STARTED_MS)

    while True:
        resp = _sqs.receive_message(
            QueueUrl=PROCESSING_QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            VisibilityTimeout=1800,
            MessageAttributeNames=["All"],
            AttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages") or []
        if not messages:
            continue

        message = messages[0]
        receipt = message["ReceiptHandle"]
        job_id = "unknown"
        temp_dir = tempfile.mkdtemp(prefix="lightship_worker_")
        try:
            payload = json.loads(message["Body"])
            job = _job_from_sqs_payload(payload)
            job_id = job.job_id
            _run_job(job, temp_dir)
            _sqs.delete_message(QueueUrl=PROCESSING_QUEUE_URL, ReceiptHandle=receipt)
            logger.info("Deleted SQS message for completed job=%s", job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("SQS job failed: job=%s error=%s", job_id, exc)
            if job_id != "unknown":
                _ddb_update(job_id, "FAILED", error=str(exc))
            # Delete after marking failed so a deterministic pipeline error does
            # not loop forever. The processing DLQ still catches delivery-level
            # failures before a worker receives the message.
            try:
                _sqs.delete_message(QueueUrl=PROCESSING_QUEUE_URL, ReceiptHandle=receipt)
            except Exception as delete_exc:  # noqa: BLE001
                logger.warning("Failed to delete failed SQS message: %s", delete_exc)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> None:
    logger.info("Inference worker process started mode=%s", WORKER_MODE)
    if WORKER_MODE == "sqs_consumer":
        _sqs_consumer_main()
    else:
        _run_task_main()


if __name__ == "__main__":
    main()
