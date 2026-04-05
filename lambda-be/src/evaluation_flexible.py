"""Flexible evaluation script with adjustable matching criteria.

Allows testing different IoU thresholds and matching strategies.
"""
import sys
from evaluation_metrics import *

def calculate_metrics_flexible(iou_threshold: float = 0.3,
                              use_center_distance: bool = False,
                              center_distance_threshold: int = 50,
                              silent: bool = False):
    """Calculate metrics with flexible matching criteria.

    Args:
        iou_threshold: IoU threshold for matching (default 0.3)
        use_center_distance: Also accept matches based on center distance
        center_distance_threshold: Max center distance in pixels (default 50)
        silent: If True, suppress some log output
    """
    if not silent:
        logger.info("="*80)
        logger.info(f"FLEXIBLE EVALUATION METRICS")
        logger.info(f"IoU Threshold: {iou_threshold}")
        logger.info(f"Center Distance Matching: {use_center_distance}")
        if use_center_distance:
            logger.info(f"Center Distance Threshold: {center_distance_threshold}px")
        logger.info("="*80)

    # Load data
    train_dir = "data/train"
    output_dir = "output"

    gt_data = load_gt_objects(train_dir)
    gen_data = load_generated_objects(output_dir)

    # Initialize metrics storage
    category_metrics = defaultdict(lambda: {
        "tp": 0,
        "fp": 0,
        "fn": 0,
        "iou_values": []
    })

    all_iou_values = []
    all_distance_matches = []

    # Modified matching function with center distance option
    def match_with_center_distance(gt_objects, gen_objects):
        """Match objects with optional center distance fallback."""
        matched_pairs = []
        unmatched_gt = list(range(len(gt_objects)))
        unmatched_gen = list(range(len(gen_objects)))

        for gt_idx, gt_obj in enumerate(gt_objects):
            gt_desc = normalize_description(gt_obj['description'])
            gt_bbox = get_bbox(gt_obj)

            if gt_bbox is None:
                continue

            gt_center = gt_obj.get('center', {})
            gt_cx = gt_center.get('x') if isinstance(gt_center, dict) else None
            gt_cy = gt_center.get('y') if isinstance(gt_center, dict) else None

            # Calculate GT center from bbox if not provided
            if gt_cx is None or gt_cy is None:
                gt_cx = (gt_bbox[0] + gt_bbox[2]) / 2
                gt_cy = (gt_bbox[1] + gt_bbox[3]) / 2

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

                # Calculate IoU
                iou = calculate_iou(gt_bbox, gen_bbox)

                # Check if IoU match
                if iou > best_iou:
                    best_iou = iou
                    best_match = gen_idx

                # If using center distance and IoU didn't match, check center distance
                if use_center_distance and iou < iou_threshold:
                    gen_center = gen_obj.get('center', {})
                    gen_cx = gen_center.get('x') if isinstance(gen_center, dict) else None
                    gen_cy = gen_center.get('y') if isinstance(gen_center, dict) else None

                    if gen_cx is not None and gen_cy is not None:
                        center_dist = np.sqrt((gt_cx - gen_cx)**2 + (gt_cy - gen_cy)**2)

                        if center_dist < center_distance_threshold:
                            # Accept as match via center distance
                            best_iou = iou  # Keep actual IoU for reporting
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
                unmatched_gt.remove(gt_idx)
                unmatched_gen.remove(best_match)

        return {
            "matched_pairs": matched_pairs,
            "unmatched_gt_idx": unmatched_gt,
            "unmatched_gen_idx": unmatched_gen
        }

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
            results = match_with_center_distance(gt_objects, gen_objects)

            # Update category metrics
            for match in results["matched_pairs"]:
                category = match["category"]
                category_metrics[category]["tp"] += 1
                category_metrics[category]["iou_values"].append(match["iou"])
                all_iou_values.append(match["iou"])

                # Distance matches
                if match["gt_distance"] == match["gen_distance"]:
                    all_distance_matches.append(True)
                else:
                    all_distance_matches.append(False)

            # Count FN
            for gt_idx in results["unmatched_gt_idx"]:
                category = normalize_description(gt_objects[gt_idx]['description'])
                category_metrics[category]["fn"] += 1

            # Count FP
            for gen_idx in results["unmatched_gen_idx"]:
                category = normalize_description(gen_objects[gen_idx]['description'])
                category_metrics[category]["fp"] += 1

    # Calculate per-category metrics
    logger.info(f"\n{'='*80}")
    logger.info("PER-CATEGORY METRICS")
    logger.info(f"{'='*80}")
    logger.info(f"{'Category':<25} {'TP':>5} {'FP':>5} {'FN':>5} {'Recall':>8} {'Prec':>8} {'F1':>8}")
    logger.info(f"{'-'*80}")

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

        logger.info(f"{category:<25} {tp:>5} {fp:>5} {fn:>5} "
                   f"{recall*100:>7.1f}% {precision*100:>7.1f}% {f1*100:>7.1f}%")

    # Overall metrics
    total_tp = sum(m["tp"] for m in category_metrics.values())
    total_fp = sum(m["fp"] for m in category_metrics.values())
    total_fn = sum(m["fn"] for m in category_metrics.values())

    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0
    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0
    overall_f1 = 2 * (overall_precision * overall_recall) / (overall_precision + overall_recall) if (overall_precision + overall_recall) > 0 else 0

    logger.info(f"{'-'*80}")
    logger.info(f"{'OVERALL':<25} {total_tp:>5} {total_fp:>5} {total_fn:>5} "
               f"{overall_recall*100:>7.1f}% {overall_precision*100:>7.1f}% {overall_f1*100:>7.1f}%")

    logger.info(f"\n{'='*80}")
    logger.info("SUMMARY")
    logger.info(f"{'='*80}")
    logger.info(f"Total GT objects: {total_tp + total_fn}")
    logger.info(f"Total matches: {total_tp}")
    logger.info(f"Overall Recall: {overall_recall*100:.1f}%")
    logger.info(f"Overall Precision: {overall_precision*100:.1f}%")
    logger.info(f"Overall F1: {overall_f1*100:.1f}%")

    if all_distance_matches:
        distance_accuracy = sum(all_distance_matches) / len(all_distance_matches)
        logger.info(f"Distance Accuracy: {distance_accuracy*100:.1f}%")


if __name__ == "__main__":
    # Test with different thresholds
    logger.info("\n\n")
    logger.info("#"*80)
    logger.info("# COMPARISON: Testing Different Matching Strategies")
    logger.info("#"*80)

    logger.info("\n" + "="*80)
    logger.info("BASELINE: IoU = 0.3 (strict)")
    logger.info("="*80)
    calculate_metrics_flexible(iou_threshold=0.3, use_center_distance=False)

    logger.info("\n" + "="*80)
    logger.info("LENIENT: IoU = 0.2")
    logger.info("="*80)
    calculate_metrics_flexible(iou_threshold=0.2, use_center_distance=False)

    logger.info("\n" + "="*80)
    logger.info("VERY LENIENT: IoU = 0.15")
    logger.info("="*80)
    calculate_metrics_flexible(iou_threshold=0.15, use_center_distance=False)

    logger.info("\n" + "="*80)
    logger.info("HYBRID: IoU = 0.2 OR Center Distance < 50px")
    logger.info("="*80)
    calculate_metrics_flexible(iou_threshold=0.2, use_center_distance=True, center_distance_threshold=50)

