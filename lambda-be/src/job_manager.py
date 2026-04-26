"""DynamoDB job lifecycle management.

Centralizes all job state operations so neither the API nor the worker
need to know the DynamoDB schema details. Designed to be the single
source of truth for job status — in-memory dicts in api_server are
supplementary caches, not authoritative.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import boto3
from botocore.exceptions import ClientError

from src.config import AWS_REGION

logger = logging.getLogger(__name__)

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "lightship_jobs")

PIPELINE_STAGES = [
    "QUEUED",
    "EXTRACTING_FRAMES",
    "DETECTING_OBJECTS",
    "ASSESSING_HAZARDS",
    "CLASSIFYING_VIDEO",
    "GENERATING_CONFIG",
    "ANNOTATING_FRAMES",
    "SAVING_RESULTS",
    "COMPLETED",
]

STAGE_INDEX = {stage: i for i, stage in enumerate(PIPELINE_STAGES)}
TOTAL_STAGES = len(PIPELINE_STAGES) - 1  # COMPLETED doesn't count as a processing stage


class JobManager:
    """Manages job records in DynamoDB."""

    def __init__(self):
        try:
            self._dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
            self._table = self._dynamodb.Table(DYNAMODB_TABLE)
            logger.info("JobManager connected to %s", DYNAMODB_TABLE)
        except Exception as e:
            logger.warning("JobManager DynamoDB init failed: %s", e)
            self._table = None

    @property
    def available(self) -> bool:
        return self._table is not None

    def create_job(
        self,
        job_id: str,
        filename: str,
        s3_input_uri: str = "",
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._table:
            return
        try:
            item = {
                "job_id": job_id,
                "status": "QUEUED",
                "current_stage": "QUEUED",
                "stages_completed": 0,
                "total_stages": TOTAL_STAGES,
                "filename": filename,
                "s3_input_uri": s3_input_uri,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            if config:
                item["config"] = config
            self._table.put_item(Item=item)
        except Exception as e:
            logger.warning("create_job failed for %s: %s", job_id, e)

    def update_stage(self, job_id: str, stage: str) -> None:
        """Update the current processing stage with real progress."""
        if not self._table:
            return
        idx = STAGE_INDEX.get(stage, 0)
        progress = round(idx / TOTAL_STAGES, 2) if TOTAL_STAGES > 0 else 0.0
        try:
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=(
                    "SET #s = :s, current_stage = :cs, "
                    "stages_completed = :sc, updated_at = :u, "
                    "progress = :p"
                ),
                ExpressionAttributeValues={
                    ":s": "PROCESSING" if stage != "COMPLETED" else "COMPLETED",
                    ":cs": stage,
                    ":sc": idx,
                    ":u": _now_iso(),
                    ":p": str(progress),
                },
                ExpressionAttributeNames={"#s": "status"},
            )
        except Exception as e:
            logger.warning("update_stage failed for %s/%s: %s", job_id, stage, e)

    def complete_job(
        self,
        job_id: str,
        video_class: str = "",
        road_type: str = "",
        s3_results_uri: str = "",
    ) -> None:
        if not self._table:
            return
        try:
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=(
                    "SET #s = :s, current_stage = :cs, "
                    "stages_completed = :sc, progress = :p, "
                    "updated_at = :u, completed_at = :ca, "
                    "video_class = :vc, road_type = :rt, "
                    "s3_results_uri = :uri"
                ),
                ExpressionAttributeValues={
                    ":s": "COMPLETED",
                    ":cs": "COMPLETED",
                    ":sc": TOTAL_STAGES,
                    ":p": "1.0",
                    ":u": _now_iso(),
                    ":ca": _now_iso(),
                    ":vc": video_class,
                    ":rt": road_type,
                    ":uri": s3_results_uri,
                },
                ExpressionAttributeNames={"#s": "status"},
            )
        except Exception as e:
            logger.warning("complete_job failed for %s: %s", job_id, e)

    def fail_job(self, job_id: str, error_message: str) -> None:
        if not self._table:
            return
        try:
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=(
                    "SET #s = :s, current_stage = :cs, "
                    "updated_at = :u, error_message = :em"
                ),
                ExpressionAttributeValues={
                    ":s": "FAILED",
                    ":cs": "FAILED",
                    ":u": _now_iso(),
                    ":em": error_message[:500],
                },
                ExpressionAttributeNames={"#s": "status"},
            )
        except Exception as e:
            logger.warning("fail_job failed for %s: %s", job_id, e)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if not self._table:
            return None
        try:
            resp = self._table.get_item(Key={"job_id": job_id})
            return resp.get("Item")
        except Exception as e:
            logger.warning("get_job failed for %s: %s", job_id, e)
            return None

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return status info suitable for the /status API endpoint."""
        item = self.get_job(job_id)
        if not item:
            return None
        return {
            "status": item.get("status", "UNKNOWN"),
            "progress": float(item.get("progress", 0)),
            "current_stage": item.get("current_stage", ""),
            "stages_completed": int(item.get("stages_completed", 0)),
            "total_stages": int(item.get("total_stages", TOTAL_STAGES)),
            "message": _stage_to_message(item.get("current_stage", "")),
        }

    def list_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self._table:
            return []
        try:
            resp = self._table.scan(Limit=limit)
            items = resp.get("Items", [])
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return items
        except Exception as e:
            logger.warning("list_jobs failed: %s", e)
            return []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_to_message(stage: str) -> str:
    messages = {
        "QUEUED": "Waiting in queue",
        "EXTRACTING_FRAMES": "Extracting video frames",
        "DETECTING_OBJECTS": "Running object detection",
        "ASSESSING_HAZARDS": "Assessing hazards via LLM",
        "CLASSIFYING_VIDEO": "Classifying video type",
        "GENERATING_CONFIG": "Generating client config",
        "ANNOTATING_FRAMES": "Annotating frames with detections",
        "SAVING_RESULTS": "Saving results to S3",
        "COMPLETED": "Processing completed",
        "FAILED": "Processing failed",
    }
    return messages.get(stage, f"Processing: {stage}")
