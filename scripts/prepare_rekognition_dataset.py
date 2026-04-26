"""Prepare a Rekognition Custom Labels training dataset from Lightship ground-truth.

Walks all ground-truth folders under ``docs_data_mterials/data/driving/ground_truth``,
normalises the two annotation schemas (per-frame and per-video), dedupes overlapping
annotations across iterations, extracts the corresponding video frames as JPEG, and
writes an Augmented Manifest (JSONL) ready for Rekognition Custom Labels training.

Usage (from repo root):

    python scripts/prepare_rekognition_dataset.py \\
        --gt-root docs_data_mterials/data/driving/ground_truth \\
        --videos-root docs_data_mterials/data/driving \\
        --out-dir build/rekognition_dataset \\
        [--s3-prefix s3://bucket/rekognition-finetune/v2] \\
        [--upload-s3] \\
        [--min-instances 10] \\
        [--allowed-labels crosswalk,pedestrian,...]

``--allowed-labels`` (when set) restricts training to exactly those labels that
appear in the ground truth with at least one instance; ``--min-instances`` is
ignored in that mode.

The script does NOT upload to S3 by default; pass ``--upload-s3`` with ``--s3-prefix``.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import cv2

logger = logging.getLogger("prepare_rekognition_dataset")

# Folder priority — higher index wins on dedupe collisions (newest last).
FOLDER_PRIORITY = [
    "ground_truth",
    "26-29-3",
    "json_12-22-25",
    "json_12-25-25",
    "4-23-26",
]

JOB_NAME = "lightship-finetune"


@dataclass
class Annotation:
    label: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass
class FramePack:
    """All annotations belonging to a single (video, timestamp_ms) frame."""
    video_filename: str
    timestamp_ms: float
    source_folder: str
    annotations: List[Annotation] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, int]:
        return (self.video_filename, int(round(self.timestamp_ms)))


def _detect_format(payload: dict) -> str:
    """Return 'A' (per-frame) or 'B' (per-video) or 'unknown'."""
    if isinstance(payload.get("frames"), list):
        return "B"
    if isinstance(payload.get("objects"), list):
        return "A"
    return "unknown"


def _parse_format_a(payload: dict, source_folder: str) -> List[FramePack]:
    """One JSON file = one frame, all objects share start_time_ms."""
    video = payload.get("filename")
    objects = payload.get("objects") or []
    if not video or not objects:
        return []

    by_ts: Dict[float, FramePack] = {}
    for obj in objects:
        ts_raw = obj.get("start_time_ms")
        if ts_raw is None:
            continue
        try:
            ts_ms = float(ts_raw)
        except (TypeError, ValueError):
            continue
        label = (obj.get("description") or "").strip()
        if not label:
            continue
        try:
            ann = Annotation(
                label=label,
                x_min=float(obj["x_min"]),
                y_min=float(obj["y_min"]),
                x_max=float(obj["x_max"]),
                y_max=float(obj["y_max"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        pack = by_ts.setdefault(
            ts_ms, FramePack(video, ts_ms, source_folder)
        )
        pack.annotations.append(ann)
    return list(by_ts.values())


def _parse_format_b(payload: dict, source_folder: str) -> List[FramePack]:
    """One JSON file = one video, ``frames[]`` lists labelled frames."""
    video = payload.get("filename")
    frames = payload.get("frames") or []
    if not video or not frames:
        return []

    packs: List[FramePack] = []
    for frame in frames:
        ts_sec = frame.get("timestamp_sec")
        if ts_sec is None:
            continue
        try:
            ts_ms = float(ts_sec) * 1000.0
        except (TypeError, ValueError):
            continue
        pack = FramePack(video, ts_ms, source_folder)
        for obj in frame.get("objects") or []:
            label = (obj.get("class") or "").strip()
            if not label:
                continue
            bbox = obj.get("bbox") or {}
            try:
                ann = Annotation(
                    label=label,
                    x_min=float(bbox["x_min"]),
                    y_min=float(bbox["y_min"]),
                    x_max=float(bbox["x_max"]),
                    y_max=float(bbox["y_max"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            pack.annotations.append(ann)
        if pack.annotations:
            packs.append(pack)
    return packs


def load_all_ground_truth(gt_root: Path) -> Tuple[List[FramePack], int]:
    """Walk all GT subfolders and return (FramePacks, number of JSON files read)."""
    folders = [d for d in gt_root.iterdir() if d.is_dir()]
    folders.sort(key=lambda d: FOLDER_PRIORITY.index(d.name)
                 if d.name in FOLDER_PRIORITY else -1)

    packs: List[FramePack] = []
    json_count = 0
    for folder in folders:
        json_files = sorted(folder.glob("*.json"))
        logger.info("Folder %s: %d JSON files", folder.name, len(json_files))
        for jf in json_files:
            json_count += 1
            try:
                payload = json.loads(jf.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Skipping %s: %s", jf, e)
                continue
            fmt = _detect_format(payload)
            if fmt == "A":
                packs.extend(_parse_format_a(payload, folder.name))
            elif fmt == "B":
                packs.extend(_parse_format_b(payload, folder.name))
            else:
                logger.warning("Unknown format in %s — skipping", jf.name)
    return packs, json_count


def dedupe_packs(packs: List[FramePack]) -> List[FramePack]:
    """Newer source folder wins on (video, timestamp_ms) collisions."""

    def folder_rank(name: str) -> int:
        try:
            return FOLDER_PRIORITY.index(name)
        except ValueError:
            return -1

    by_key: Dict[Tuple[str, int], FramePack] = {}
    for pack in packs:
        existing = by_key.get(pack.key)
        if existing is None or folder_rank(pack.source_folder) > folder_rank(existing.source_folder):
            by_key[pack.key] = pack
    return sorted(by_key.values(), key=lambda p: (p.video_filename, p.timestamp_ms))


def find_video(videos_root: Path, filename: str) -> Optional[Path]:
    direct = videos_root / "videos" / filename
    if direct.exists():
        return direct
    direct2 = videos_root / filename
    if direct2.exists():
        return direct2
    matches = list(videos_root.rglob(filename))
    return matches[0] if matches else None


def extract_frame(
    video_path: Path,
    timestamp_ms: float,
) -> Optional[Tuple[bytes, int, int]]:
    """Return (jpeg_bytes, width, height) for the frame at timestamp_ms."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_ms))
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        h, w = frame.shape[:2]
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            return None
        return bytes(buf), w, h
    finally:
        cap.release()


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(name: str) -> str:
    return _SAFE.sub("_", Path(name).stem)


def build_manifest_line(
    source_ref: str,
    width: int,
    height: int,
    annotations: Iterable[Annotation],
    class_map: Dict[str, int],
    creation_iso: str,
) -> Optional[str]:
    bbox_anns = []
    for ann in annotations:
        if ann.label not in class_map:
            continue
        left = max(0, int(round(ann.x_min)))
        top = max(0, int(round(ann.y_min)))
        right = min(width, int(round(ann.x_max)))
        bottom = min(height, int(round(ann.y_max)))
        w = right - left
        h = bottom - top
        if w <= 1 or h <= 1:
            continue
        bbox_anns.append({
            "class_id": class_map[ann.label],
            "top": top,
            "left": left,
            "width": w,
            "height": h,
        })
    if not bbox_anns:
        return None
    inverse = {v: k for k, v in class_map.items()}
    record = {
        "source-ref": source_ref,
        "bounding-box": {
            "image_size": [{"width": width, "height": height, "depth": 3}],
            "annotations": bbox_anns,
        },
        "bounding-box-metadata": {
            "objects": [{"confidence": 1} for _ in bbox_anns],
            "class-map": {str(k): inverse[k] for k in sorted(inverse)},
            "type": "groundtruth/object-detection",
            "human-annotated": "yes",
            "creation-date": creation_iso,
            "job-name": JOB_NAME,
        },
    }
    return json.dumps(record, separators=(",", ":"))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--gt-root", type=Path,
                   default=repo_root / "docs_data_mterials" / "data" / "driving" / "ground_truth")
    p.add_argument("--videos-root", type=Path,
                   default=repo_root / "docs_data_mterials" / "data" / "driving")
    p.add_argument("--out-dir", type=Path,
                   default=repo_root / "build" / "rekognition_dataset")
    p.add_argument("--s3-prefix", type=str, default="",
                   help="S3 URI prefix for source-ref in manifest, e.g. s3://bucket/path/v2")
    p.add_argument("--upload-s3", action="store_true",
                   help="Also upload images + manifest to --s3-prefix")
    p.add_argument("--min-instances", type=int, default=10,
                   help="Drop labels with fewer than this many instances (ignored if --allowed-labels set)")
    p.add_argument("--allowed-labels", type=str, default="",
                   help="Comma-separated allowlist; only labels present in GT are kept")
    p.add_argument("--dry-run", action="store_true",
                   help="Print histogram and stop without extracting frames")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not args.gt_root.exists():
        logger.error("Ground-truth root not found: %s", args.gt_root)
        return 2

    packs, gt_json_files = load_all_ground_truth(args.gt_root)
    normalized_objects = sum(len(p.annotations) for p in packs)
    logger.info("Loaded %d frame packs (pre-dedupe), %d GT JSON files", len(packs), gt_json_files)
    packs = dedupe_packs(packs)
    logger.info("After dedupe: %d unique (video, timestamp) frames", len(packs))

    label_counter: Counter[str] = Counter()
    for pack in packs:
        for ann in pack.annotations:
            label_counter[ann.label] += 1

    print("\n=== Label histogram (instances) ===")
    for label, count in label_counter.most_common():
        print(f"  {label:<35} {count}")
    print()

    allowed = {s.strip() for s in args.allowed_labels.split(",") if s.strip()}
    if allowed:
        missing_in_gt = sorted(allowed - set(label_counter.keys()))
        if missing_in_gt:
            logger.warning(
                "Allowed labels with zero instances in GT (skipped): %s",
                ", ".join(missing_in_gt),
            )
        kept_labels = allowed & set(label_counter.keys())
    else:
        kept_labels = {l for l, c in label_counter.items() if c >= args.min_instances}

    dropped = sorted(set(label_counter) - kept_labels)
    if dropped:
        logger.warning(
            "Dropping %d labels (below min or not in allowlist): %s",
            len(dropped), ", ".join(dropped[:30]) + ("..." if len(dropped) > 30 else ""),
        )

    if not kept_labels:
        logger.error("No labels survived filtering — adjust --min-instances or --allowed-labels")
        return 3

    class_map: Dict[str, int] = OrderedDict(
        (l, i) for i, l in enumerate(sorted(kept_labels))
    )
    logger.info("Final class map (%d labels): %s",
                len(class_map), list(class_map.keys()))

    if args.dry_run:
        print("\n=== Dry run summary ===")
        print(f"  GT JSON files parsed:     {gt_json_files}")
        print(f"  Normalized annotations: {normalized_objects}")
        print(f"  Unique frames (deduped): {len(packs)}")
        print(f"  Labels kept:              {sorted(kept_labels)}")
        return 0

    images_dir = args.out_dir / "images"
    manifests_dir = args.out_dir / "manifests"
    images_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir.mkdir(parents=True, exist_ok=True)

    s3_prefix = args.s3_prefix.rstrip("/") if args.s3_prefix else ""
    creation_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    manifest_path = manifests_dir / "train.manifest"

    written = 0
    skipped_no_video = 0
    skipped_no_frame = 0
    skipped_no_ann = 0
    video_cache: Dict[str, Optional[Path]] = {}

    with manifest_path.open("w", encoding="utf-8") as mf:
        for pack in packs:
            relevant = [a for a in pack.annotations if a.label in class_map]
            if not relevant:
                skipped_no_ann += 1
                continue

            if pack.video_filename not in video_cache:
                video_cache[pack.video_filename] = find_video(
                    args.videos_root, pack.video_filename
                )
            video_path = video_cache[pack.video_filename]
            if video_path is None:
                logger.warning("Video not found: %s", pack.video_filename)
                skipped_no_video += 1
                continue

            extracted = extract_frame(video_path, pack.timestamp_ms)
            if extracted is None:
                logger.warning(
                    "Frame read failed: %s @ %.0f ms",
                    pack.video_filename, pack.timestamp_ms,
                )
                skipped_no_frame += 1
                continue
            jpeg_bytes, width, height = extracted

            stem = f"{safe_stem(pack.video_filename)}_{int(round(pack.timestamp_ms))}"
            img_name = f"{stem}.jpg"
            img_path = images_dir / img_name
            img_path.write_bytes(jpeg_bytes)

            source_ref = (
                f"{s3_prefix}/images/{img_name}" if s3_prefix
                else str(img_path.resolve()).replace("\\", "/")
            )
            line = build_manifest_line(
                source_ref=source_ref,
                width=width,
                height=height,
                annotations=relevant,
                class_map=class_map,
                creation_iso=creation_iso,
            )
            if line is None:
                skipped_no_ann += 1
                img_path.unlink(missing_ok=True)
                continue
            mf.write(line + "\n")
            written += 1

    logger.info("Manifest written: %s (%d entries)", manifest_path, written)
    logger.info(
        "Skipped — no video: %d, no frame: %d, no annotations after filter: %d",
        skipped_no_video, skipped_no_frame, skipped_no_ann,
    )

    class_map_path = manifests_dir / "class_map.json"
    class_map_path.write_text(json.dumps(class_map, indent=2), encoding="utf-8")
    logger.info("Class map written: %s", class_map_path)

    manifest_s3_uri = ""
    if args.upload_s3:
        if not s3_prefix:
            logger.error("--upload-s3 requires --s3-prefix")
            return 4
        upload_to_s3(images_dir, manifest_path, s3_prefix)
        up = urlparse(s3_prefix)
        base = up.path.lstrip("/").rstrip("/")
        manifest_s3_uri = f"s3://{up.netloc}/{base}/manifests/train.manifest"

    print("\n=== Final summary ===")
    print(f"  GT JSON files parsed:       {gt_json_files}")
    print(f"  Normalized annotations:   {normalized_objects}")
    print(f"  Unique frames (deduped):    {len(packs)}")
    print(f"  Images / manifest lines:    {written}")
    print(f"  Labels included:            {sorted(kept_labels)}")
    print(f"  Labels excluded:            {dropped[:50]}{'...' if len(dropped) > 50 else ''}")
    print(f"  Local manifest:             {manifest_path}")
    if manifest_s3_uri:
        print(f"  S3 manifest URI:            {manifest_s3_uri}")
    return 0


def upload_to_s3(images_dir: Path, manifest_path: Path, s3_prefix: str) -> None:
    import boto3
    from urllib.parse import urlparse

    parsed = urlparse(s3_prefix)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {s3_prefix}")
    bucket = parsed.netloc
    base_key = parsed.path.lstrip("/").rstrip("/")
    s3 = boto3.client("s3")

    for img in sorted(images_dir.glob("*.jpg")):
        key = f"{base_key}/images/{img.name}"
        logger.info("Uploading %s → s3://%s/%s", img.name, bucket, key)
        s3.upload_file(str(img), bucket, key, ExtraArgs={"ContentType": "image/jpeg"})

    manifest_key = f"{base_key}/manifests/train.manifest"
    logger.info("Uploading manifest → s3://%s/%s", bucket, manifest_key)
    s3.upload_file(
        str(manifest_path), bucket, manifest_key,
        ExtraArgs={"ContentType": "application/x-jsonlines"},
    )


if __name__ == "__main__":
    sys.exit(main())
