"""Train an Amazon Rekognition Custom Labels model and store the resulting
ProjectVersionArn in SSM Parameter Store.

Usage (from repo root):

    AWS_PROFILE=lightship python scripts/train_custom_labels.py \\
        --project-name lightship-mvp-objects \\
        --version-name v2 \\
        --manifest-s3 s3://lightship-mvp-processing-336090301206/rekognition-finetune/v2/manifests/train.manifest \\
        --output-s3 s3://lightship-mvp-processing-336090301206/rekognition-finetune/v2/training-output/ \\
        --ssm-name /lightship/mvp/rekognition/custom-labels-arn \\
        --region us-east-1

Behaviour:

- Creates the Rekognition project if it does not exist (idempotent).
- Starts a project version using ``TrainingData`` / ``TestingData`` from S3 when
  AWS accepts the manifest (best for **fresh** projects).

**Dataset-first training (recommended when inline manifests fail):**
``CreateProjectVersion`` sometimes rejects the same SageMaker augmented manifest
that ``CreateDataset`` accepts. In that case: run ``create-dataset`` for TRAIN
and TEST from ``train_split.manifest`` / ``test_split.manifest`` (real JSONL
newlines), then call ``create_project_version`` with only ``ProjectArn``,
``VersionName``, and ``OutputConfig``. Use ``prepare_rekognition_dataset.py`` for
the base manifest and an 80/20 split, upload to S3, then poll status here or in
the console.

Polls every 60s until a terminal status. On ``TRAINING_COMPLETED``, writes the
ProjectVersionArn to SSM (unless ``--skip-ssm``) and saves a JSON report
(default ``build/rekognition_training_report.json``).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("train_custom_labels")

TERMINAL_STATUSES = {
    "TRAINING_COMPLETED",
    "TRAINING_FAILED",
    "DELETING",
    "FAILED",
}


def parse_s3(uri: str) -> Tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def get_or_create_project(rek, name: str) -> str:
    """Find project by human-readable name embedded in ProjectArn."""
    token: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {}
        if token:
            kwargs["NextToken"] = token
        resp = rek.describe_projects(**kwargs)
        for p in resp.get("ProjectDescriptions", []):
            arn = p.get("ProjectArn", "")
            # e.g. arn:...:project/my-project-name/1234567890
            if f":project/{name}/" in arn:
                logger.info("Reusing existing project: %s", arn)
                return arn
        token = resp.get("NextToken")
        if not token:
            break
    arn = rek.create_project(ProjectName=name)["ProjectArn"]
    logger.info("Created project: %s", arn)
    return arn


def write_report(
    path: Path,
    *,
    project_arn: str,
    version_name: str,
    version_arn: str,
    status: str,
    status_message: str,
    evaluation: Dict[str, Any],
    ssm_name: Optional[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "project_arn": project_arn,
        "version_name": version_name,
        "project_version_arn": version_arn,
        "status": status,
        "status_message": status_message,
        "evaluation_result": evaluation,
        "ssm_parameter": ssm_name,
        "written_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Report written: %s", path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--project-name", required=True)
    p.add_argument("--version-name", required=True)
    p.add_argument("--manifest-s3", required=True)
    p.add_argument("--output-s3", required=True)
    p.add_argument("--ssm-name", required=True)
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--no-wait", action="store_true",
                   help="Start training and exit; do not poll for completion")
    p.add_argument("--skip-ssm", action="store_true",
                   help="Do not write SSM even after TRAINING_COMPLETED")
    repo_root = Path(__file__).resolve().parents[1]
    p.add_argument(
        "--report-path",
        type=Path,
        default=repo_root / "build" / "rekognition_training_report.json",
        help="Local JSON report path",
    )
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    rek = boto3.client("rekognition", region_name=args.region)
    ssm = boto3.client("ssm", region_name=args.region)

    project_arn = get_or_create_project(rek, args.project_name)

    train_bucket, train_key = parse_s3(args.manifest_s3)
    out_bucket, out_prefix = parse_s3(args.output_s3)
    if not out_prefix.endswith("/"):
        out_prefix = out_prefix + "/"

    try:
        version_arn = rek.create_project_version(
            ProjectArn=project_arn,
            VersionName=args.version_name,
            OutputConfig={"S3Bucket": out_bucket, "S3KeyPrefix": out_prefix},
            TrainingData={
                "Assets": [{
                    "GroundTruthManifest": {
                        "S3Object": {"Bucket": train_bucket, "Name": train_key},
                    },
                }],
            },
            TestingData={"AutoCreate": True},
        )["ProjectVersionArn"]
        logger.info("Started training: %s", version_arn)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            versions = rek.describe_project_versions(
                ProjectArn=project_arn,
                VersionNames=[args.version_name],
            )["ProjectVersionDescriptions"]
            if not versions:
                raise
            version_arn = versions[0]["ProjectVersionArn"]
            logger.info("Reusing in-progress version: %s", version_arn)
        else:
            raise

    if args.no_wait:
        write_report(
            args.report_path,
            project_arn=project_arn,
            version_name=args.version_name,
            version_arn=version_arn,
            status="STARTED",
            status_message="no-wait",
            evaluation={},
            ssm_name=None if args.skip_ssm else args.ssm_name,
        )
        print(version_arn)
        return 0

    started = time.time()
    descs: list = []
    status = "UNKNOWN"
    while True:
        descs = rek.describe_project_versions(
            ProjectArn=project_arn,
            VersionNames=[args.version_name],
        )["ProjectVersionDescriptions"]
        status = descs[0]["Status"] if descs else "UNKNOWN"
        elapsed_min = int((time.time() - started) / 60)
        logger.info("[t+%dm] Status: %s", elapsed_min, status)
        if status in TERMINAL_STATUSES:
            break
        time.sleep(60)

    status_message = descs[0].get("StatusMessage", "") if descs else ""
    evaluation: Dict[str, Any] = {}
    if descs:
        evaluation = dict(descs[0].get("EvaluationResult") or {})

    write_report(
        args.report_path,
        project_arn=project_arn,
        version_name=args.version_name,
        version_arn=version_arn,
        status=status,
        status_message=status_message,
        evaluation=evaluation,
        ssm_name=None if args.skip_ssm else args.ssm_name,
    )

    if status != "TRAINING_COMPLETED":
        logger.error("Training did not complete: %s — %s", status, status_message)
        return 2

    f1 = evaluation.get("F1Score")
    summary = evaluation.get("Summary")
    logger.info("Training complete. F1=%s, Summary=%s", f1, summary)

    if not args.skip_ssm:
        ssm.put_parameter(
            Name=args.ssm_name,
            Value=version_arn,
            Type="String",
            Overwrite=True,
            Description="Rekognition Custom Labels ProjectVersionArn for Lightship",
        )
        logger.info("Saved ARN to SSM %s", args.ssm_name)

    print(version_arn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
