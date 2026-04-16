"""Canonical evaluation harness (Phase 5 of the MVP plan).

Builds a dataset manifest over a GT folder tree and produces KPI-style
metrics per category / per video, plus a PoC-vs-MVP comparison table.

This module is intentionally dependency-light (only pydantic + stdlib) so
it can run inside CodeBuild, inside the Lambda image, or on a laptop.

Usage
-----

    python -m src.evaluation_harness \
        --predictions output/enhanced_format \
        --ground-truth docs_data_mterials/data/driving \
        --report      output/reports/mvp_baseline.json

The report JSON is machine readable; use ``--markdown`` to also emit a
human readable summary.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Manifest / adapters
# ---------------------------------------------------------------------------


@dataclass
class Sample:
    video_name: str
    gt_path: Path
    prediction_path: Optional[Path]
    meta: Dict[str, str] = field(default_factory=dict)


def build_manifest(predictions_dir: Path, gt_dir: Path) -> List[Sample]:
    """Build a list of aligned (gt, prediction) samples.

    Ground truth files are expected to be ``*.json`` anywhere under
    ``gt_dir``.  Predictions are matched by filename stem.
    """
    predictions = {p.stem: p for p in predictions_dir.rglob("*.json")}
    samples: List[Sample] = []
    for gt_file in sorted(gt_dir.rglob("*.json")):
        pred_path = predictions.get(gt_file.stem)
        samples.append(Sample(
            video_name=gt_file.stem,
            gt_path=gt_file,
            prediction_path=pred_path,
        ))
    logger.info(
        "Manifest built: %d GT files, %d matched predictions",
        len(samples),
        sum(1 for s in samples if s.prediction_path),
    )
    return samples


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------


_CATEGORY_KEYWORDS = {
    "motorcycle": ("motorcycle", "motor bike", "moto"),
    "lane":       ("lane",),
    "sign":       ("sign", "stop sign", "speed limit"),
    "construction": (
        "cone", "barrier", "barricade", "construction", "worker", "excavator",
        "crane", "scaffold",
    ),
}


def _category_of(description: str) -> Optional[str]:
    t = (description or "").lower()
    for cat, keys in _CATEGORY_KEYWORDS.items():
        if any(k in t for k in keys):
            return cat
    return None


def _iou_bbox(a: Tuple[float, float, float, float],
              b: Tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    ub = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = ua + ub - inter
    return inter / union if union > 0 else 0.0


def _bbox_of(obj: Dict) -> Optional[Tuple[float, float, float, float]]:
    if all(k in obj and obj[k] is not None for k in ("x_min", "y_min", "x_max", "y_max")):
        return float(obj["x_min"]), float(obj["y_min"]), float(obj["x_max"]), float(obj["y_max"])
    c = obj.get("center")
    if c and "x" in c and "y" in c:
        x, y = float(c["x"]), float(c["y"])
        # Synthesize a tiny anchor box so IoU is non-trivial but localized.
        return x - 10.0, y - 10.0, x + 10.0, y + 10.0
    return None


def _score_category(gt_objs: List[Dict], pred_objs: List[Dict], iou_thr: float = 0.3):
    """Greedy matching precision/recall for objects in one category."""
    gt_boxes = [b for b in (_bbox_of(o) for o in gt_objs) if b]
    pd_boxes = [b for b in (_bbox_of(o) for o in pred_objs) if b]
    if not gt_boxes and not pd_boxes:
        return {"precision": 1.0, "recall": 1.0, "tp": 0, "fp": 0, "fn": 0, "gt": 0, "pred": 0}

    matched_gt = set()
    tp = 0
    for pb in pd_boxes:
        best_iou, best_i = 0.0, -1
        for i, gb in enumerate(gt_boxes):
            if i in matched_gt:
                continue
            iou = _iou_bbox(pb, gb)
            if iou > best_iou:
                best_iou, best_i = iou, i
        if best_iou >= iou_thr and best_i >= 0:
            tp += 1
            matched_gt.add(best_i)

    fp = len(pd_boxes) - tp
    fn = len(gt_boxes) - tp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "precision": precision, "recall": recall,
        "tp": tp, "fp": fp, "fn": fn,
        "gt": len(gt_boxes), "pred": len(pd_boxes),
    }


def score_sample(sample: Sample) -> Dict:
    with sample.gt_path.open("r", encoding="utf-8") as fp:
        gt = json.load(fp)
    if not sample.prediction_path:
        return {"video": sample.video_name, "status": "no_prediction"}
    with sample.prediction_path.open("r", encoding="utf-8") as fp:
        pred = json.load(fp)

    gt_objs = gt.get("objects", []) or []
    pred_objs = pred.get("objects", []) or []

    per_category: Dict[str, Dict] = {}
    for cat in _CATEGORY_KEYWORDS:
        gts = [o for o in gt_objs if _category_of(o.get("description", "")) == cat]
        pds = [o for o in pred_objs if _category_of(o.get("description", "")) == cat]
        per_category[cat] = _score_category(gts, pds)

    # Video-classification agreement.
    video_match = {
        "weather_match": int(gt.get("weather") == pred.get("weather")),
        "lighting_match": int(gt.get("lighting") == pred.get("lighting")),
        "traffic_match": int(gt.get("traffic") == pred.get("traffic")),
    }

    return {
        "video": sample.video_name,
        "status": "scored",
        "per_category": per_category,
        "classification": video_match,
        "gt_objects": len(gt_objs),
        "pred_objects": len(pred_objs),
    }


def aggregate(results: List[Dict]) -> Dict:
    scored = [r for r in results if r.get("status") == "scored"]
    if not scored:
        return {"num_scored": 0, "num_skipped": len(results)}

    agg_cat: Dict[str, Dict[str, float]] = {}
    for cat in _CATEGORY_KEYWORDS:
        tp = sum(r["per_category"][cat]["tp"] for r in scored)
        fp = sum(r["per_category"][cat]["fp"] for r in scored)
        fn = sum(r["per_category"][cat]["fn"] for r in scored)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        agg_cat[cat] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn,
        }

    classification = {
        "weather_accuracy": round(
            sum(r["classification"]["weather_match"] for r in scored) / len(scored), 4),
        "lighting_accuracy": round(
            sum(r["classification"]["lighting_match"] for r in scored) / len(scored), 4),
        "traffic_accuracy": round(
            sum(r["classification"]["traffic_match"] for r in scored) / len(scored), 4),
    }
    return {
        "num_scored": len(scored),
        "num_skipped": len(results) - len(scored),
        "per_category": agg_cat,
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_markdown(report: Dict) -> str:
    lines = ["# Lightship MVP Evaluation Report", ""]
    agg = report.get("aggregate", {})
    lines.append(f"- scored videos: **{agg.get('num_scored', 0)}**")
    lines.append(f"- skipped videos (no prediction): **{agg.get('num_skipped', 0)}**")
    lines.append("")
    lines.append("## Per-category KPIs")
    lines.append("| category | precision | recall | f1 | TP | FP | FN |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for cat, m in agg.get("per_category", {}).items():
        lines.append(
            f"| {cat} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | "
            f"{m['tp']} | {m['fp']} | {m['fn']} |"
        )
    cls = agg.get("classification", {})
    lines.append("")
    lines.append("## Classification accuracy")
    lines.append(f"- weather: **{cls.get('weather_accuracy', 0):.3f}**")
    lines.append(f"- lighting: **{cls.get('lighting_accuracy', 0):.3f}**")
    lines.append(f"- traffic: **{cls.get('traffic_accuracy', 0):.3f}**")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lightship MVP evaluation harness — plan Phase 5")
    parser.add_argument("--predictions", required=True, type=Path,
                        help="Directory containing prediction JSON files")
    parser.add_argument("--ground-truth", required=True, type=Path,
                        help="Directory containing ground-truth JSON files")
    parser.add_argument("--report", required=True, type=Path,
                        help="Path to machine-readable report JSON")
    parser.add_argument("--markdown", type=Path,
                        help="Optional Markdown summary path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    samples = build_manifest(args.predictions, args.ground_truth)
    results = [score_sample(s) for s in samples]
    report = {
        "num_samples": len(samples),
        "per_video": results,
        "aggregate": aggregate(results),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as fp:
        json.dump(report, fp, indent=2)
    logger.info("Wrote JSON report to %s", args.report)

    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(_render_markdown(report), encoding="utf-8")
        logger.info("Wrote Markdown summary to %s", args.markdown)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
