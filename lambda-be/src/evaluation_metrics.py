"""Evaluation script to calculate comprehensive metrics.

Metrics:
- Hit rate (recall) per object category
- Precision per object category
- F1 scores per category
- Overall IoU distribution
- Distance category accuracy
"""
import json
import os
import glob
import numpy as np
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def normalize_description(desc: str) -> str:
    """Normalize description for matching."""
    if "traffic_signal" in desc:
        return desc
    if "(" in desc:
        return desc.split("(")[0]
    return desc


def polygon_to_bbox(polygon: List[Dict]) -> Optional[Tuple[float, float, float, float]]:
    """Convert polygon to bounding box."""
    if not polygon:
        return None
    x_coords = [p["x"] for p in polygon]
    y_coords = [p["y"] for p in polygon]
    return (min(x_coords), min(y_coords), max(x_coords), max(y_coords))


def get_bbox(obj: Dict) -> Optional[Tuple[float, float, float, float]]:
    """Extract bounding box from object."""
    if all(k in obj and obj[k] is not None for k in ["x_min", "y_min", "x_max", "y_max"]):
        return (obj["x_min"], obj["y_min"], obj["x_max"], obj["y_max"])
    if "polygon" in obj and obj["polygon"]:
        return polygon_to_bbox(obj["polygon"])
    return None


def calculate_iou(bbox1: Tuple[float, float, float, float], 
                  bbox2: Tuple[float, float, float, float]) -> float:
    """Calculate IoU between two bounding boxes."""
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2
    
    x_inter_min = max(x1_min, x2_min)
    y_inter_min = max(y1_min, y2_min)
    x_inter_max = min(x1_max, x2_max)
    y_inter_max = min(y1_max, y2_max)
    
    if x_inter_max < x_inter_min or y_inter_max < y_inter_min:
        return 0.0
    
    inter_area = (x_inter_max - x_inter_min) * (y_inter_max - y_inter_min)
    bbox1_area = (x1_max - x1_min) * (y1_max - y1_min)
    bbox2_area = (x2_max - x2_min) * (y2_max - y2_min)
    union_area = bbox1_area + bbox2_area - inter_area
    
    if union_area == 0:
        return 0.0
    
    return inter_area / union_area


def load_gt_objects(train_dir: str) -> Dict[str, Dict[float, List[Dict]]]:
    """Load all GT objects organized by video and timestamp."""
    gt_data = defaultdict(lambda: defaultdict(list))
    json_files = glob.glob(os.path.join(train_dir, "*-*.json"))
    
    for json_file in json_files:
        filename = os.path.basename(json_file)
        video_name = filename.rsplit("-", 1)[0]
        
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        if not data.get('objects'):
            continue
        
        timestamp = float(data['objects'][0]['start_time_ms'])
        gt_data[video_name][timestamp] = data['objects']
    
    return gt_data


def load_generated_objects(output_dir: str) -> Dict[str, Dict[float, List[Dict]]]:
    """Load all generated objects organized by video and timestamp."""
    gen_data = defaultdict(lambda: defaultdict(list))
    json_files = glob.glob(os.path.join(output_dir, "*.json"))
    
    for json_file in json_files:
        filename = os.path.basename(json_file)
        video_name = os.path.splitext(filename)[0]
        
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        for obj in data.get('objects', []):
            timestamp = float(obj['start_time_ms'])
            gen_data[video_name][timestamp].append(obj)
    
    return gen_data


def find_matching_timestamp(target_ts: float, available_ts: List[float], 
                           tolerance_ms: float = 100) -> Optional[float]:
    """Find closest matching timestamp within tolerance."""
    closest = None
    min_diff = float('inf')
    
    for ts in available_ts:
        diff = abs(ts - target_ts)
        if diff < min_diff and diff <= tolerance_ms:
            min_diff = diff
            closest = ts
    
    return closest


def match_objects_with_metrics(gt_objects: List[Dict], gen_objects: List[Dict], 
                               iou_threshold: float = 0.3) -> Dict:
    """Match objects and calculate detailed metrics.
    
    Returns:
        Dictionary with matching results and IoU values
    """
    matched_pairs = []
    unmatched_gt = list(range(len(gt_objects)))
    unmatched_gen = list(range(len(gen_objects)))
    iou_values = []
    distance_matches = []
    
    # Try to match each GT object
    for gt_idx, gt_obj in enumerate(gt_objects):
        gt_desc = normalize_description(gt_obj['description'])
        gt_bbox = get_bbox(gt_obj)
        
        if gt_bbox is None:
            continue
        
        best_match = None
        best_iou = iou_threshold
        
        for gen_idx in unmatched_gen:
            gen_obj = gen_objects[gen_idx]
            gen_desc = normalize_description(gen_obj['description'])
            gen_bbox = get_bbox(gen_obj)
            
            if gen_bbox is None:
                continue
            
            if gt_desc != gen_desc:
                continue
            
            iou = calculate_iou(gt_bbox, gen_bbox)
            
            if iou > best_iou:
                best_iou = iou
                best_match = gen_idx
        
        if best_match is not None:
            gen_obj = gen_objects[best_match]
            matched_pairs.append({
                "gt_idx": gt_idx,
                "gen_idx": best_match,
                "iou": best_iou,
                "category": normalize_description(gt_obj['description']),
                "gt_distance": gt_obj.get('distance', 'n/a'),
                "gen_distance": gen_obj.get('distance', 'n/a')
            })
            iou_values.append(best_iou)
            
            # Check distance match
            if gt_obj.get('distance') == gen_obj.get('distance'):
                distance_matches.append(True)
            else:
                distance_matches.append(False)
            
            unmatched_gt.remove(gt_idx)
            unmatched_gen.remove(best_match)
    
    return {
        "matched_pairs": matched_pairs,
        "unmatched_gt_idx": unmatched_gt,
        "unmatched_gen_idx": unmatched_gen,
        "iou_values": iou_values,
        "distance_matches": distance_matches
    }


def calculate_metrics():
    """Calculate comprehensive evaluation metrics."""
    logger.info("="*80)
    logger.info("EVALUATION METRICS")
    logger.info("="*80)
    
    # Load data
    train_dir = "data/train"
    output_dir = "output"
    
    gt_data = load_gt_objects(train_dir)
    gen_data = load_generated_objects(output_dir)
    
    # Initialize metrics storage
    category_metrics = defaultdict(lambda: {
        "tp": 0,  # True Positives (matched)
        "fp": 0,  # False Positives (generated but not in GT)
        "fn": 0,  # False Negatives (in GT but not detected)
        "iou_values": []
    })
    
    all_iou_values = []
    all_distance_matches = []
    distance_confusion = defaultdict(lambda: defaultdict(int))
    
    # Process all videos
    for video_name in sorted(gt_data.keys()):
        gt_timestamps = sorted(gt_data[video_name].keys())
        gen_timestamps = sorted(gen_data[video_name].keys()) if video_name in gen_data else []
        
        for gt_ts in gt_timestamps:
            gen_ts = find_matching_timestamp(gt_ts, gen_timestamps, tolerance_ms=100)
            
            if gen_ts is None:
                continue
            
            gt_objects = gt_data[video_name][gt_ts]
            gen_objects = gen_data[video_name][gen_ts]
            
            # Match objects
            results = match_objects_with_metrics(gt_objects, gen_objects)
            
            # Update category metrics
            for match in results["matched_pairs"]:
                category = match["category"]
                category_metrics[category]["tp"] += 1
                category_metrics[category]["iou_values"].append(match["iou"])
                all_iou_values.append(match["iou"])
                
                # Distance confusion matrix
                distance_confusion[match["gt_distance"]][match["gen_distance"]] += 1
            
            # Count FN (unmatched GT objects)
            for gt_idx in results["unmatched_gt_idx"]:
                category = normalize_description(gt_objects[gt_idx]['description'])
                category_metrics[category]["fn"] += 1
            
            # Count FP (unmatched generated objects)
            for gen_idx in results["unmatched_gen_idx"]:
                category = normalize_description(gen_objects[gen_idx]['description'])
                category_metrics[category]["fp"] += 1
            
            # Distance matches
            all_distance_matches.extend(results["distance_matches"])
    
    # Calculate per-category metrics
    logger.info(f"\n{'='*80}")
    logger.info("PER-CATEGORY METRICS")
    logger.info(f"{'='*80}")
    logger.info(f"{'Category':<25} {'TP':>5} {'FP':>5} {'FN':>5} {'Recall':>8} {'Prec':>8} {'F1':>8} {'mIoU':>8}")
    logger.info(f"{'-'*80}")
    
    category_results = []
    for category in sorted(category_metrics.keys(), 
                          key=lambda x: category_metrics[x]["tp"] + category_metrics[x]["fn"], 
                          reverse=True):
        metrics = category_metrics[category]
        tp = metrics["tp"]
        fp = metrics["fp"]
        fn = metrics["fn"]
        
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        mean_iou = np.mean(metrics["iou_values"]) if metrics["iou_values"] else 0
        
        logger.info(f"{category:<25} {tp:>5} {fp:>5} {fn:>5} "
                   f"{recall*100:>7.1f}% {precision*100:>7.1f}% "
                   f"{f1*100:>7.1f}% {mean_iou:>7.3f}")
        
        category_results.append({
            "category": category,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "recall": recall,
            "precision": precision,
            "f1": f1,
            "mean_iou": mean_iou
        })
    
    # Overall metrics
    total_tp = sum(m["tp"] for m in category_metrics.values())
    total_fp = sum(m["fp"] for m in category_metrics.values())
    total_fn = sum(m["fn"] for m in category_metrics.values())
    
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    overall_f1 = 2 * (overall_precision * overall_recall) / (overall_precision + overall_recall) if (overall_precision + overall_recall) > 0 else 0
    overall_mean_iou = np.mean(all_iou_values) if all_iou_values else 0
    
    logger.info(f"{'-'*80}")
    logger.info(f"{'OVERALL':<25} {total_tp:>5} {total_fp:>5} {total_fn:>5} "
               f"{overall_recall*100:>7.1f}% {overall_precision*100:>7.1f}% "
               f"{overall_f1*100:>7.1f}% {overall_mean_iou:>7.3f}")
    
    # IoU distribution
    logger.info(f"\n{'='*80}")
    logger.info("IoU DISTRIBUTION")
    logger.info(f"{'='*80}")
    
    if all_iou_values:
        iou_array = np.array(all_iou_values)
        logger.info(f"Total matched objects: {len(all_iou_values)}")
        logger.info(f"Mean IoU: {np.mean(iou_array):.3f}")
        logger.info(f"Median IoU: {np.median(iou_array):.3f}")
        logger.info(f"Std IoU: {np.std(iou_array):.3f}")
        logger.info(f"Min IoU: {np.min(iou_array):.3f}")
        logger.info(f"Max IoU: {np.max(iou_array):.3f}")
        
        # IoU bins
        logger.info(f"\nIoU Distribution by bins:")
        bins = [0.0, 0.3, 0.5, 0.7, 0.9, 1.0]
        for i in range(len(bins) - 1):
            count = np.sum((iou_array >= bins[i]) & (iou_array < bins[i+1]))
            if i == len(bins) - 2:  # Last bin includes 1.0
                count = np.sum(iou_array >= bins[i])
            pct = count / len(iou_array) * 100
            logger.info(f"  [{bins[i]:.1f} - {bins[i+1]:.1f}): {count:>4} ({pct:>5.1f}%)")
    
    # Distance accuracy
    logger.info(f"\n{'='*80}")
    logger.info("DISTANCE CATEGORY ACCURACY")
    logger.info(f"{'='*80}")
    
    if all_distance_matches:
        distance_accuracy = sum(all_distance_matches) / len(all_distance_matches)
        logger.info(f"Total matched objects with distance: {len(all_distance_matches)}")
        logger.info(f"Distance matches: {sum(all_distance_matches)}")
        logger.info(f"Distance accuracy: {distance_accuracy*100:.1f}%")
        
        # Distance confusion matrix
        logger.info(f"\nDistance Confusion Matrix:")
        logger.info(f"(Rows: GT, Columns: Generated)")
        
        all_distances = sorted(set(list(distance_confusion.keys()) + 
                                  [d for row in distance_confusion.values() for d in row.keys()]))
        
        # Header
        header = f"{'GT \\ Gen':<15}"
        for dist in all_distances:
            header += f"{dist[:8]:>10}"
        logger.info(header)
        logger.info("-" * len(header))
        
        # Rows
        for gt_dist in all_distances:
            row = f"{gt_dist:<15}"
            for gen_dist in all_distances:
                count = distance_confusion[gt_dist][gen_dist]
                row += f"{count:>10}"
            logger.info(row)
    
    # Save results
    output_file = "evaluation_metrics_results.json"
    results = {
        "overall": {
            "recall": overall_recall,
            "precision": overall_precision,
            "f1": overall_f1,
            "mean_iou": overall_mean_iou,
            "total_tp": total_tp,
            "total_fp": total_fp,
            "total_fn": total_fn
        },
        "per_category": category_results,
        "iou_distribution": {
            "mean": float(np.mean(all_iou_values)) if all_iou_values else 0,
            "median": float(np.median(all_iou_values)) if all_iou_values else 0,
            "std": float(np.std(all_iou_values)) if all_iou_values else 0,
            "min": float(np.min(all_iou_values)) if all_iou_values else 0,
            "max": float(np.max(all_iou_values)) if all_iou_values else 0,
            "values": all_iou_values
        },
        "distance_accuracy": {
            "accuracy": sum(all_distance_matches) / len(all_distance_matches) if all_distance_matches else 0,
            "total": len(all_distance_matches),
            "correct": sum(all_distance_matches),
            "confusion_matrix": {k: dict(v) for k, v in distance_confusion.items()}
        }
    }
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"\nDetailed results saved to: {output_file}")


if __name__ == "__main__":
    calculate_metrics()

