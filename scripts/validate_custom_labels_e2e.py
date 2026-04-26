"""Start Rekognition Custom Labels endpoint, run one pipeline job on Lambda, verify audit, stop endpoint.

Requires:
  - ``AWS_PROFILE`` (or ``--profile``) with rights to Rekognition, Lambda, S3
  - Lambda env ``REKOGNITION_CUSTOM_MODEL_ARN`` set to the trained ProjectVersionArn
  - Local path to a short MP4 (e.g. a video not used heavily in training)

Example::

    py scripts/validate_custom_labels_e2e.py \\
        --project-version-arn arn:aws:rekognition:us-east-1:336090301206:project/lightship-mvp-objects/version/v2/... \\
        --video-path docs_data_mterials/data/driving/videos/gr_20231019-161559-C.mp4 \\
        --profile lightship
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

import boto3
from botocore.config import Config

logger = logging.getLogger("validate_custom_labels_e2e")


def _resolve_project_arn(rek, project_version_arn: str) -> Tuple[str, str]:
    m = re.search(r":project/([^/]+)/version/([^/]+)/", project_version_arn)
    if not m:
        raise ValueError(f"Unrecognised ProjectVersionArn: {project_version_arn}")
    project_name, version_name = m.group(1), m.group(2)
    token: Optional[str] = None
    while True:
        kwargs = {}
        if token:
            kwargs["NextToken"] = token
        resp = rek.describe_projects(**kwargs)
        for p in resp.get("ProjectDescriptions", []):
            arn = p.get("ProjectArn", "")
            if f":project/{project_name}/" in arn:
                return arn, version_name
        token = resp.get("NextToken")
        if not token:
            break
    raise RuntimeError(f"Project named {project_name!r} not found")


def _wait_endpoint(rek, project_arn: str, version_name: str, want: str, max_wait_s: int) -> str:
    deadline = time.time() + max_wait_s
    last = ""
    while time.time() < deadline:
        resp = rek.describe_project_versions(
            ProjectArn=project_arn,
            VersionNames=[version_name],
        )
        descs = resp.get("ProjectVersionDescriptions") or []
        if descs:
            last = descs[0].get("Status", "")
            logger.info("Project version status: %s", last)
            if last == want:
                return last
            if last in {"FAILED", "TRAINING_FAILED", "DELETING"}:
                raise RuntimeError(f"Bad status: {last}")
        time.sleep(15)
    raise TimeoutError(f"Timed out waiting for {want}; last={last!r}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-version-arn", required=True)
    p.add_argument("--video-path", type=Path, required=True)
    p.add_argument("--profile", default="lightship")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--function-name", default="lightship-mvp-backend")
    p.add_argument("--bucket", default="lightship-mvp-processing-336090301206")
    p.add_argument("--output-dir", type=Path, default=Path("output/validation"))
    p.add_argument("--max-wait-running-min", type=int, default=25)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.video_path.is_file():
        logger.error("Video not found: %s", args.video_path)
        return 2

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    rek = session.client("rekognition")
    s3 = session.client("s3")
    lam = session.client(
        "lambda",
        config=Config(read_timeout=900, connect_timeout=90),
    )

    project_arn, version_name = _resolve_project_arn(rek, args.project_version_arn)
    job_id = str(uuid.uuid4())
    filename = args.video_path.name
    s3_key = f"validation/custom-labels/{job_id}/{filename}"
    out_key = f"results/{job_id}/output.json"
    max_run = args.max_wait_running_min * 60

    report: dict = {"job_id": job_id, "s3_key": s3_key, "out_key": out_key}
    report_path: Optional[Path] = None

    try:
        logger.info("Starting project version endpoint…")
        rek.start_project_version(
            ProjectVersionArn=args.project_version_arn,
            MinInferenceUnits=1,
        )
        _wait_endpoint(rek, project_arn, version_name, "RUNNING", max_run)
        report["endpoint_started"] = True

        logger.info("Uploading %s → s3://%s/%s", args.video_path, args.bucket, s3_key)
        s3.upload_file(str(args.video_path), args.bucket, s3_key)

        payload = {
            "action": "pipeline_stage",
            "job_id": job_id,
            "s3_key": s3_key,
            "filename": filename,
            "config": {
                "snapshot_strategy": "naive",
                "max_snapshots": 3,
                "cleanup_frames": True,
                "use_cv_labeler": True,
                "native_fps": 0.25,
            },
        }
        logger.info("Invoking Lambda %s (sync, up to 15m)…", args.function_name)
        resp = lam.invoke(
            FunctionName=args.function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        raw = resp["Payload"].read().decode("utf-8")
        report["lambda_raw_response"] = raw[:8000]
        if resp.get("FunctionError"):
            logger.error("Lambda error: %s", raw)
            return 3

        logger.info("Waiting for output.json …")
        data = None
        for _ in range(120):
            try:
                obj = s3.get_object(Bucket=args.bucket, Key=out_key)
                data = json.loads(obj["Body"].read())
                break
            except Exception as e:  # noqa: BLE001
                logger.debug("poll: %s", e)
                time.sleep(5)
        if data is None:
            logger.error("Timed out waiting for s3://%s/%s", args.bucket, out_key)
            return 4

        audit = data.get("rekognition_audit") or {}
        frames = audit.get("per_frame") or []
        if not frames:
            raise AssertionError("rekognition_audit.per_frame missing or empty")
        first = frames[0]
        if "custom_labels_invoked" not in first:
            raise AssertionError(
                "rekognition_audit lacks custom_labels_invoked — the deployed "
                "Lambda image is almost certainly older than the Rekognition "
                "custom-labels code. Build/push lambda-be to ECR and "
                "update-function-configuration (ImageUri), then re-run."
            )
        for fr in frames:
            if not fr.get("custom_labels_invoked"):
                raise AssertionError("custom_labels_invoked not true for all frames")
        if not any((fr.get("custom_raw_labels") or []) for fr in frames):
            raise AssertionError("custom_raw_labels empty on every frame")

        args.output_dir.mkdir(parents=True, exist_ok=True)
        report_path = args.output_dir / f"custom_labels_{job_id}.json"
        report["rekognition_audit"] = audit
        report["assertions"] = "passed"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info("Report saved: %s", report_path)
    finally:
        logger.info("Stopping project version endpoint…")
        try:
            rek.stop_project_version(ProjectVersionArn=args.project_version_arn)
        except Exception as e:  # noqa: BLE001
            logger.error("stop_project_version failed: %s", e)

        for _ in range(48):
            try:
                resp = rek.describe_project_versions(
                    ProjectArn=project_arn,
                    VersionNames=[version_name],
                )
                descs = resp.get("ProjectVersionDescriptions") or []
                st = descs[0].get("Status", "") if descs else ""
                logger.info("Post-stop status: %s", st)
                if st in ("STOPPED", "STOPPING"):
                    report["endpoint_final_status"] = st
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning("describe after stop: %s", e)
            time.sleep(10)

    print(json.dumps({"ok": True, "report": str(report_path) if report_path else None}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        logger.error("%s", e)
        sys.exit(5)
    except Exception as e:  # noqa: BLE001
        logger.exception("%s", e)
        sys.exit(1)
