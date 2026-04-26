"""Start or stop a Rekognition Custom Labels project version (inference endpoint).

Examples::

    AWS_PROFILE=lightship python scripts/start_stop_custom_labels.py start \\
        --project-version-arn arn:aws:rekognition:us-east-1:ACCOUNT:project/.../version/v2/... \\
        --wait-running --max-wait-min 20

    AWS_PROFILE=lightship python scripts/start_stop_custom_labels.py stop \\
        --project-version-arn arn:aws:rekognition:us-east-1:ACCOUNT:project/.../version/v2/...
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from typing import Optional, Tuple

import boto3

logger = logging.getLogger("start_stop_custom_labels")


def _resolve_project_arn(rek, project_version_arn: str) -> Tuple[str, str]:
    """Return (project_arn, version_name) for DescribeProjectVersions."""
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


def wait_status(
    rek,
    project_version_arn: str,
    want: str,
    max_wait_s: int,
    poll_s: int = 15,
) -> str:
    deadline = time.time() + max_wait_s
    last = ""
    project_arn, version_name = _resolve_project_arn(rek, project_version_arn)
    while time.time() < deadline:
        resp = rek.describe_project_versions(
            ProjectArn=project_arn,
            VersionNames=[version_name],
        )
        descs = resp.get("ProjectVersionDescriptions") or []
        if not descs:
            time.sleep(poll_s)
            continue
        last = descs[0].get("Status", "")
        logger.info("Status: %s", last)
        if last == want:
            return last
        if last in {"FAILED", "TRAINING_FAILED", "DELETING"}:
            raise RuntimeError(f"Terminal failure status: {last}")
        time.sleep(poll_s)
    raise TimeoutError(f"Timed out after {max_wait_s}s; last status={last!r}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["start", "stop"])
    p.add_argument("--project-version-arn", required=True)
    p.add_argument("--profile", default="lightship", help="AWS named profile (default: lightship)")
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--min-inference-units", type=int, default=1)
    p.add_argument("--wait-running", action="store_true",
                   help="After start, block until status is RUNNING")
    p.add_argument("--max-wait-min", type=int, default=25)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    rek = session.client("rekognition")
    arn = args.project_version_arn
    max_wait_s = max(60, args.max_wait_min * 60)

    if args.command == "stop":
        rek.stop_project_version(ProjectVersionArn=arn)
        logger.info("Stop requested for %s", arn)
        return 0

    rek.start_project_version(
        ProjectVersionArn=arn,
        MinInferenceUnits=args.min_inference_units,
    )
    logger.info("Start requested for %s", arn)
    if args.wait_running:
        wait_status(rek, arn, "RUNNING", max_wait_s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
