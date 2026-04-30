"""ECS Fargate inference worker entry-point.

Reads job parameters from environment variables injected by Step Functions
``ecs:runTask`` containerOverrides, processes the video end-to-end using the
full VisionLabeler (Florence-2 + UFLDv2) pipeline, and writes results to S3
and DynamoDB.

Environment variables (required)
---------------------------------
  JOB_ID              UUID of the job row in DynamoDB
  S3_INPUT_KEY        S3 key of the uploaded video (in PROCESSING_BUCKET)
  S3_OUTPUT_PREFIX    S3 prefix for results (e.g. results/<job_id>/)
  PROCESSING_BUCKET   S3 bucket name

Environment variables (optional, with defaults)
------------------------------------------------
  AWS_REGION          us-east-1
  DETECTOR_BACKEND    florence2
  LANE_BACKEND        ufldv2
  MAX_SNAPSHOTS       5
  SNAPSHOT_STRATEGY   clustering
  NATIVE_FPS          optional; dense sampling rate (Hz) for naive strategy
  DYNAMODB_TABLE      lightship_jobs
  LOG_LEVEL           INFO
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Make lambda-be/src importable from the worker image
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE / "src"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("inference_worker")

# ---------------------------------------------------------------------------
# Required env vars
# ---------------------------------------------------------------------------
JOB_ID = os.environ["JOB_ID"]
S3_INPUT_KEY = os.environ["S3_INPUT_KEY"]
S3_OUTPUT_PREFIX = os.environ.get(
    "S3_OUTPUT_PREFIX", f"results/{JOB_ID}/"
).rstrip("/") + "/"
PROCESSING_BUCKET = os.environ["PROCESSING_BUCKET"]

# Optional
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "lightship_jobs")
DETECTOR_BACKEND = os.getenv("DETECTOR_BACKEND", "florence2")
LANE_BACKEND = os.getenv("LANE_BACKEND", "ufldv2")
MAX_SNAPSHOTS = int(os.getenv("MAX_SNAPSHOTS", "5"))
SNAPSHOT_STRATEGY = os.getenv("SNAPSHOT_STRATEGY", "clustering")
_NATIVE_FPS_RAW = os.getenv("NATIVE_FPS", "").strip()
NATIVE_FPS: float | None
try:
    NATIVE_FPS = float(_NATIVE_FPS_RAW) if _NATIVE_FPS_RAW else None
except ValueError:
    NATIVE_FPS = None


# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------
import boto3

_s3 = boto3.client("s3", region_name=AWS_REGION)
_ddb = boto3.client("dynamodb", region_name=AWS_REGION)


def _ddb_update(status: str, progress: float = 0.0, error: str = "") -> None:
    try:
        expr = "SET #s = :s, progress = :p, updated_at = :t"
        names = {"#s": "status"}
        vals: dict = {
            ":s": {"S": status},
            ":p": {"N": str(round(progress, 3))},
            ":t": {"S": __import__("datetime").datetime.utcnow().isoformat() + "Z"},
        }
        if error:
            expr += ", error_message = :e"
            vals[":e"] = {"S": error[:2000]}
        _ddb.update_item(
            TableName=DYNAMODB_TABLE,
            Key={"job_id": {"S": JOB_ID}},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=vals,
        )
    except Exception as exc:
        logger.warning("DynamoDB update failed: %s", exc)


_S3_EXTRA_JSON = {
    "ServerSideEncryption": "aws:kms",
    "ContentType": "application/json",
}
_S3_EXTRA_PNG = {
    "ServerSideEncryption": "aws:kms",
    "ContentType": "image/png",
}


def _upload_dir(local_dir: Path, prefix: str) -> list[str]:
    """Upload all files in local_dir to S3 under prefix. Returns S3 keys."""
    keys = []
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir)
        key = prefix + str(rel).replace("\\", "/")
        lower = path.suffix.lower()
        if lower == ".json":
            extra = dict(_S3_EXTRA_JSON)
        elif lower in (".png", ".jpg", ".jpeg", ".webp"):
            extra = dict(_S3_EXTRA_PNG) if lower == ".png" else {
                "ServerSideEncryption": "aws:kms",
                "ContentType": "image/jpeg" if lower in (".jpg", ".jpeg") else "image/webp",
            }
        else:
            extra = {"ServerSideEncryption": "aws:kms"}
        _s3.upload_file(str(path), PROCESSING_BUCKET, key, ExtraArgs=extra)
        keys.append(key)
        logger.info("Uploaded %s → s3://%s/%s", rel, PROCESSING_BUCKET, key)
    return keys


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info(
        "Inference worker started: job=%s input=%s output_prefix=%s",
        JOB_ID, S3_INPUT_KEY, S3_OUTPUT_PREFIX,
    )
    _ddb_update("PROCESSING", 0.02)

    temp_dir = tempfile.mkdtemp(prefix="lightship_worker_")
    try:
        _run(temp_dir)
    except Exception as exc:
        logger.exception("Worker failed: %s", exc)
        _ddb_update("FAILED", error=str(exc))
        sys.exit(1)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run(temp_dir: str) -> None:
    from src.pipeline import Pipeline
    from src.result_persistence import persist_frame_artefacts, put_frames_manifest_json

    # 1. Download video
    filename = S3_INPUT_KEY.split("/")[-1]
    video_path = os.path.join(temp_dir, filename)
    logger.info("Downloading s3://%s/%s ...", PROCESSING_BUCKET, S3_INPUT_KEY)
    _s3.download_file(PROCESSING_BUCKET, S3_INPUT_KEY, video_path)
    _ddb_update("PROCESSING", 0.08)

    # 2. Run pipeline
    pipeline = Pipeline(
        snapshot_strategy=SNAPSHOT_STRATEGY,
        max_snapshots=MAX_SNAPSHOTS,
        cleanup_frames=False,
        use_cv_labeler=True,
        native_fps=NATIVE_FPS,
        detector_backend=DETECTOR_BACKEND,
        lane_backend=LANE_BACKEND,
    )
    # Override output dir to temp so we can upload
    pipeline.merger.output_dir = temp_dir

    def _progress(p: float, step: str, msg: str) -> None:
        # Scale pipeline progress [0,1] to DynamoDB [0.10, 0.90]
        pct = 0.10 + p * 0.80
        _ddb_update("PROCESSING", pct)
        logger.info("Progress %.0f%%: %s — %s", pct * 100, step, msg)

    output_path = pipeline.process_video(video_path, progress_cb=_progress)
    if not output_path:
        raise RuntimeError("Pipeline returned no output — check logs for errors")

    _ddb_update("PROCESSING", 0.92)

    # 2b. Per-frame artefacts + frames_manifest (same contract as Lambda API)
    try:
        with open(output_path, encoding="utf-8") as fp:
            output_doc = json.load(fp)
        all_obj_dicts = list(output_doc.get("objects") or [])
        frames_manifest = persist_frame_artefacts(
            _s3,
            PROCESSING_BUCKET,
            JOB_ID,
            getattr(pipeline, "last_selected_frames", {}) or {},
            getattr(pipeline, "last_annotated_frames", {}) or {},
            getattr(pipeline, "last_frame_timestamps", {}) or {},
            all_obj_dicts,
            getattr(pipeline, "last_extraction_manifest", []) or [],
        )
        put_frames_manifest_json(_s3, PROCESSING_BUCKET, JOB_ID, frames_manifest)
        logger.info(
            "Persisted %d frame artefact entries under results/%s/frames/",
            len(frames_manifest),
            JOB_ID,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Frame artefact / manifest persistence failed: %s", exc)

    # 3. Upload results to S3
    local_output_root = Path(temp_dir)
    uploaded_keys = _upload_dir(local_output_root, S3_OUTPUT_PREFIX)

    # The merger names the output file after the video (e.g. "20251107-153701-C.json"),
    # but the Lambda API always fetches results/{job_id}/output.json.  Upload an
    # additional copy under the canonical "output.json" name so the API can find it.
    output_json_key: str = ""
    if output_path and os.path.exists(output_path):
        normalized_key = S3_OUTPUT_PREFIX + "output.json"
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
            # Fall back to the video-named key if already uploaded
            output_json_key = next(
                (k for k in uploaded_keys if k.endswith(".json") and "/client_configs/" not in k),
                "",
            )
    else:
        # Fallback: scan uploaded keys for any top-level JSON
        output_json_key = next(
            (k for k in uploaded_keys if k.endswith(".json") and "/client_configs/" not in k),
            "",
        )

    # 4. Mark COMPLETED in DynamoDB with S3 pointers
    try:
        _ddb.update_item(
            TableName=DYNAMODB_TABLE,
            Key={"job_id": {"S": JOB_ID}},
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
                ":rp": {"S": S3_OUTPUT_PREFIX},
            },
        )
    except Exception as exc:
        logger.warning("DynamoDB COMPLETED update failed: %s", exc)

    logger.info(
        "Worker completed: job=%s  output_prefix=%s  files=%d",
        JOB_ID, S3_OUTPUT_PREFIX, len(uploaded_keys),
    )


if __name__ == "__main__":
    main()
