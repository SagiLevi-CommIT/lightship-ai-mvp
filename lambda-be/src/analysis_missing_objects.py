"""Analysis script to identify missing objects in generated outputs vs GT.

Analyzes the ~50% object detection gap by comparing GT and generated outputs.
"""
import json
import os
import glob
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def normalize_description(desc: str) -> str:
    """Normalize description for matching.
    
    Treats variants as same base type:
    - vehicle/vehicle(parked) -> vehicle
    - pedestrian/pedestrian(group) -> pedestrian
    - lane/lane(current)/lane(right_turn) -> lane
    
    BUT keeps traffic signal colors separate.
    """
    # Keep traffic signal colors distinct
    if "traffic_signal" in desc:
        return desc
    
    # Remove parenthetical modifiers
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
    """Extract bounding box from object (from bbox fields or polygon)."""
    # Try direct bbox fields
    if all(k in obj and obj[k] is not None for k in ["x_min", "y_min", "x_max", "y_max"]):
        return (obj["x_min"], obj["y_min"], obj["x_max"], obj["y_max"])
    
    # Try polygon
    if "polygon" in obj and obj["polygon"]:
        return polygon_to_bbox(obj["polygon"])
    
    return None


def calculate_iou(bbox1: Tuple[float, float, float, float], 
                  bbox2: Tuple[float, float, float, float]) -> float:
    """Calculate IoU between two bounding boxes."""
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2
    
    # Calculate intersection
    x_inter_min = max(x1_min, x2_min)
    y_inter_min = max(y1_min, y2_min)
    x_inter_max = min(x1_max, x2_max)
    y_inter_max = min(y1_max, y2_max)
    
    if x_inter_max < x_inter_min or y_inter_max < y_inter_min:
        return 0.0
    
    inter_area = (x_inter_max - x_inter_min) * (y_inter_max - y_inter_min)
    
    # Calculate union
    bbox1_area = (x1_max - x1_min) * (y1_max - y1_min)
    bbox2_area = (x2_max - x2_min) * (y2_max - y2_min)
    union_area = bbox1_area + bbox2_area - inter_area
    
    if union_area == 0:
        return 0.0
    
    return inter_area / union_area


def load_gt_objects(train_dir: str) -> Dict[str, Dict[float, List[Dict]]]:
    """Load all GT objects organized by video and timestamp.
    
    Returns:
        {video_name: {timestamp: [objects]}}
    """
    gt_data = defaultdict(lambda: defaultdict(list))
    
    json_files = glob.glob(os.path.join(train_dir, "*-*.json"))
    
    for json_file in json_files:
        filename = os.path.basename(json_file)
        # Extract video name (e.g., "lytx_1" from "lytx_1-1.json")
        video_name = filename.rsplit("-", 1)[0]
        
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        if not data.get('objects'):
            continue
        
        # Extract timestamp from first object
        timestamp = float(data['objects'][0]['start_time_ms'])
        
        # Store all objects at this timestamp
        gt_data[video_name][timestamp] = data['objects']
    
    return gt_data


def load_generated_objects(output_dir: str) -> Dict[str, Dict[float, List[Dict]]]:
    """Load all generated objects organized by video and timestamp.
    
    Returns:
        {video_name: {timestamp: [objects]}}
    """
    gen_data = defaultdict(lambda: defaultdict(list))
    
    json_files = glob.glob(os.path.join(output_dir, "*.json"))
    
    for json_file in json_files:
        filename = os.path.basename(json_file)
        video_name = os.path.splitext(filename)[0]
        
        with open(json_file, 'r') as f:
            data = json.load(f)
        
        # Group objects by timestamp
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


def match_objects(gt_objects: List[Dict], gen_objects: List[Dict], 
                 iou_threshold: float = 0.3) -> Tuple[List, List, List]:
    """Match GT objects with generated objects.
    
    Matching criteria (Option D):
    1. Description must match (normalized)
    2. IoU must be above threshold
    
    Returns:
        (matched_pairs, unmatched_gt, unmatched_gen)
    """
    matched_pairs = []
    unmatched_gt = list(range(len(gt_objects)))
    unmatched_gen = list(range(len(gen_objects)))
    
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
            
            # Check description match
            if gt_desc != gen_desc:
                continue
            
            # Calculate IoU
            iou = calculate_iou(gt_bbox, gen_bbox)
            
            if iou > best_iou:
                best_iou = iou
                best_match = gen_idx
        
        if best_match is not None:
            matched_pairs.append((gt_idx, best_match, best_iou))
            unmatched_gt.remove(gt_idx)
            unmatched_gen.remove(best_match)
    
    return matched_pairs, unmatched_gt, unmatched_gen


def analyze_all():
    """Main analysis function."""
    logger.info("="*80)
    logger.info("MISSING OBJECTS ANALYSIS")
    logger.info("="*80)
    
    # Load data
    train_dir = "data/train"
    output_dir = "output"
    
    logger.info(f"\nLoading GT data from: {train_dir}")
    gt_data = load_gt_objects(train_dir)
    
    logger.info(f"Loading generated data from: {output_dir}")
    gen_data = load_generated_objects(output_dir)
    
    logger.info(f"\nVideos found: {len(gt_data)}")
    
    # Statistics
    total_gt_objects = 0
    total_gen_objects = 0
    total_matched = 0
    total_unmatched_gt = 0
    total_unmatched_gen = 0
    
    category_stats = defaultdict(lambda: {"gt": 0, "gen": 0, "matched": 0})
    missing_by_category = defaultdict(list)
    
    # Analyze each video
    for video_name in sorted(gt_data.keys()):
        logger.info(f"\n{'-'*80}")
        logger.info(f"VIDEO: {video_name}")
        logger.info(f"{'-'*80}")
        
        gt_timestamps = sorted(gt_data[video_name].keys())
        gen_timestamps = sorted(gen_data[video_name].keys()) if video_name in gen_data else []
        
        logger.info(f"GT timestamps: {len(gt_timestamps)}")
        logger.info(f"Generated timestamps: {len(gen_timestamps)}")
        
        # Match timestamps
        for gt_ts in gt_timestamps:
            gen_ts = find_matching_timestamp(gt_ts, gen_timestamps, tolerance_ms=100)
            
            if gen_ts is None:
                logger.warning(f"  No matching timestamp for GT {gt_ts:.2f}ms")
                continue
            
            logger.info(f"\nTimestamp: GT={gt_ts:.2f}ms, Gen={gen_ts:.2f}ms")
            
            gt_objects = gt_data[video_name][gt_ts]
            gen_objects = gen_data[video_name][gen_ts]
            
            logger.info(f"  GT objects: {len(gt_objects)}")
            logger.info(f"  Generated objects: {len(gen_objects)}")
            
            # Match objects
            matched_pairs, unmatched_gt_idx, unmatched_gen_idx = match_objects(
                gt_objects, gen_objects
            )
            
            logger.info(f"  Matched: {len(matched_pairs)}")
            logger.info(f"  Unmatched GT: {len(unmatched_gt_idx)}")
            logger.info(f"  Unmatched Gen: {len(unmatched_gen_idx)}")
            
            # Update totals
            total_gt_objects += len(gt_objects)
            total_gen_objects += len(gen_objects)
            total_matched += len(matched_pairs)
            total_unmatched_gt += len(unmatched_gt_idx)
            total_unmatched_gen += len(unmatched_gen_idx)
            
            # Category analysis
            for gt_idx, gt_obj in enumerate(gt_objects):
                desc = normalize_description(gt_obj['description'])
                category_stats[desc]["gt"] += 1
                
                if gt_idx not in unmatched_gt_idx:
                    category_stats[desc]["matched"] += 1
                else:
                    missing_by_category[desc].append({
                        "video": video_name,
                        "timestamp": gt_ts,
                        "description": gt_obj['description'],
                        "distance": gt_obj.get('distance', 'n/a')
                    })
            
            for gen_obj in gen_objects:
                desc = normalize_description(gen_obj['description'])
                category_stats[desc]["gen"] += 1
    
    # Print summary
    logger.info(f"\n{'='*80}")
    logger.info("OVERALL STATISTICS")
    logger.info(f"{'='*80}")
    logger.info(f"Total GT objects: {total_gt_objects}")
    logger.info(f"Total Generated objects: {total_gen_objects}")
    logger.info(f"Total Matched: {total_matched}")
    logger.info(f"Total Unmatched GT: {total_unmatched_gt}")
    logger.info(f"Total Unmatched Gen: {total_unmatched_gen}")
    logger.info(f"\nRecall (Hit Rate): {total_matched / total_gt_objects * 100:.1f}%")
    logger.info(f"Precision: {total_matched / total_gen_objects * 100:.1f}%")
    
    # Category breakdown
    logger.info(f"\n{'='*80}")
    logger.info("CATEGORY BREAKDOWN")
    logger.info(f"{'='*80}")
    logger.info(f"{'Category':<25} {'GT':>6} {'Gen':>6} {'Match':>6} {'Recall':>8} {'Miss':>6}")
    logger.info(f"{'-'*80}")
    
    for category in sorted(category_stats.keys(), 
                          key=lambda x: category_stats[x]["gt"], 
                          reverse=True):
        stats = category_stats[category]
        recall = stats["matched"] / stats["gt"] * 100 if stats["gt"] > 0 else 0
        missed = stats["gt"] - stats["matched"]
        
        logger.info(f"{category:<25} {stats['gt']:>6} {stats['gen']:>6} "
                   f"{stats['matched']:>6} {recall:>7.1f}% {missed:>6}")
    
    # Most missed categories
    logger.info(f"\n{'='*80}")
    logger.info("TOP 10 MOST MISSED CATEGORIES")
    logger.info(f"{'='*80}")
    
    missed_counts = {cat: len(objs) for cat, objs in missing_by_category.items()}
    top_missed = sorted(missed_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    
    for i, (category, count) in enumerate(top_missed, 1):
        logger.info(f"{i}. {category}: {count} missed objects")
    
    # Save detailed results
    output_file = "analysis_missing_objects_results.json"
    results = {
        "summary": {
            "total_gt_objects": total_gt_objects,
            "total_gen_objects": total_gen_objects,
            "total_matched": total_matched,
            "total_unmatched_gt": total_unmatched_gt,
            "total_unmatched_gen": total_unmatched_gen,
            "recall_percent": total_matched / total_gt_objects * 100,
            "precision_percent": total_matched / total_gen_objects * 100
        },
        "category_stats": dict(category_stats),
        "missing_by_category": {k: v for k, v in missing_by_category.items()}
    }
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"\nDetailed results saved to: {output_file}")


if __name__ == "__main__":
    analyze_all()

