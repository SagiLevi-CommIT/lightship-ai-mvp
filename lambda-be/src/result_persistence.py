"""S3 persistence for pipeline results and per-frame artefacts.

Shared by the FastAPI Lambda worker (`api_server.process_video_task`) and the
ECS inference worker (`inference-worker/entrypoint.py`) so both paths emit the
same S3 key layout expected by ``GET /frames/{job_id}``.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RESULTS_PREFIX = "results"
OUTPUT_JSON_KEY = "output.json"


def s3_result_key(job_id: str, name: str) -> str:
    return f"{RESULTS_PREFIX}/{job_id}/{name}"


def persist_frame_artefacts(
    s3_client: Any,
    bucket: str,
    job_id: str,
    selected_frames: Dict[int, str],
    annotated_frames: Dict[int, str],
    timestamps: Dict[int, float],
    all_objects: list,
    extraction_manifest: Optional[list] = None,
) -> List[Dict[str, Any]]:
    """Upload selected + annotated frame images and per-frame JSON to S3.

    ``all_objects`` must be a list of dicts with ``start_time_ms`` (as produced
    by ``ObjectLabel.model_dump()`` or loaded from ``output.json``).

    Returns a list of per-frame manifest entries (with S3 keys) for
    ``frames_manifest.json``.
    """
    if s3_client is None:
        return []
    manifest: List[Dict[str, Any]] = []
    extraction_by_idx: Dict[int, Dict[str, Any]] = {}
    for entry in extraction_manifest or []:
        idx = entry.get("frame_idx")
        if idx is not None:
            extraction_by_idx[int(idx)] = entry

    objs_by_ms: Dict[int, list] = defaultdict(list)
    for obj in all_objects:
        key = int(round(obj.get("start_time_ms", 0)))
        objs_by_ms[key].append(obj)

    for frame_idx, frame_path in sorted(annotated_frames.items()):
        if not os.path.exists(frame_path):
            continue
        ts_ms = timestamps.get(frame_idx, 0.0)
        frame_key = s3_result_key(job_id, f"frames/frame_{frame_idx:04d}_annotated.png")
        raw_key = None
        raw_path = selected_frames.get(frame_idx)
        if raw_path and os.path.exists(raw_path):
            raw_key = s3_result_key(job_id, f"frames/frame_{frame_idx:04d}_raw.png")
            try:
                s3_client.upload_file(
                    raw_path, bucket, raw_key,
                    ExtraArgs={
                        "ServerSideEncryption": "aws:kms",
                        "ContentType": "image/png",
                    },
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Raw frame upload failed (frame %d): %s", frame_idx, e)
                raw_key = None
        try:
            s3_client.upload_file(
                frame_path, bucket, frame_key,
                ExtraArgs={
                    "ServerSideEncryption": "aws:kms",
                    "ContentType": "image/png",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Annotated frame upload failed (frame %d): %s", frame_idx, e)
            continue

        matched_ms = [ms for ms in objs_by_ms if abs(ms - ts_ms) <= 100]
        frame_objs: list = []
        for ms in matched_ms:
            frame_objs.extend(objs_by_ms[ms])

        extraction_meta = extraction_by_idx.get(int(frame_idx), {})
        per_frame_json_key = s3_result_key(job_id, f"frames/frame_{frame_idx:04d}.json")
        per_frame_doc = {
            "job_id": job_id,
            "frame_idx": frame_idx,
            "timestamp_ms": ts_ms,
            "num_objects": len(frame_objs),
            "objects": frame_objs,
            "extraction": {
                "source": extraction_meta.get("source"),
                "status": extraction_meta.get("status"),
                "decoded_idx": extraction_meta.get("decoded_idx"),
                "width": extraction_meta.get("width"),
                "height": extraction_meta.get("height"),
                "error": extraction_meta.get("error"),
            },
        }
        try:
            s3_client.put_object(
                Bucket=bucket,
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
            "extraction_source": extraction_meta.get("source"),
            "extraction_status": extraction_meta.get("status"),
            "width": extraction_meta.get("width"),
            "height": extraction_meta.get("height"),
        })
    return manifest


def put_frames_manifest_json(
    s3_client: Any,
    bucket: str,
    job_id: str,
    manifest: List[Dict[str, Any]],
) -> None:
    """Write ``results/{job_id}/frames_manifest.json``."""
    if s3_client is None or not manifest:
        return
    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=s3_result_key(job_id, "frames_manifest.json"),
            Body=json.dumps(manifest, indent=2).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="aws:kms",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("frames_manifest.json upload failed: %s", e)
