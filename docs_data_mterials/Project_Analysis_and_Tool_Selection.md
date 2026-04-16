# Project Analysis & Technical Tool-Selection Report

**Date:** March 31, 2026
**Scope:** Full review of BWI and LightShip project folders — documentation, architecture, schemas, sample data, POC code, meeting notes, and email threads.

---

## A. Project Summary

The folders contain materials for **two distinct but related projects**, both built by Commit (Comm-IT) for external clients, deployed on AWS.

### Project 1 — LightShip (Lightship Neuroscience)

**What it does:** An AI-powered dashcam video annotation and classification system. The system ingests dashcam videos from fleet cameras (Lytx, Netradyne, Samsara, Verizon), extracts key frames, runs multi-model object detection (vehicles, motorcycles, pedestrians, lanes, road signs, traffic signals, construction objects), classifies each video into one of four training types, and auto-generates structured JSON configuration files that feed directly into Lightship's driver-training application.

The four video/training types are: reactivity/braking, Q&A/educational, hazard detection, and job site detection.

The system also handles job-site still images for construction-hazard detection.

**Business purpose:** Lightship sells driver safety training to fleet operators. Today, building training content from dashcam footage is manual. This system automates the annotation and classification pipeline so Lightship can scale content production.

**Project phase:** Transitioning from POC (completed) to MVP. The POC validated a pipeline using YOLO11x + Depth-Anything-V2 + Claude Sonnet 4.5. The MVP must improve detection accuracy (especially motorcycles, lanes, signs), add LLM-powered classification, and produce production-ready infrastructure.

### Project 2 — BWI (Black Widow Imaging)

**What it does:** An AI image-processing pipeline for vehicle photography. BWI captures high-resolution vehicle images (7 exterior angles) at dealership lots. The system performs vehicle detection (GroundingDINO), segmentation (SAM2), mask refinement (OpenCV morphological operations), and background replacement (centering + shadow generation + custom backgrounds).

**Business purpose:** BWI provides automated vehicle imaging for dealerships. The AI pipeline replaces manual photo editing — isolating vehicles from lot backgrounds and compositing them onto clean, professional backgrounds.

**Project phase:** POC completed, MVP architecture designed. The POC validated GroundingDINO + SAM2 with bbox IoU of 0.95 and mask recall above 80%. The MVP adds batch processing, per-image status tracking, a human edit mode for mask/shadow corrections, and REST API integration.

---

## B. Source Materials Reviewed

### LightShip folder

| Category | Files |
|---|---|
| Architecture documents | `lightship_mvp_aws_architecture_note (1).md` — 662-line implementation-ready architecture spec |
| Kickoff presentations | `Lightship MVP - Kickoff.pdf` — SOW3 MVP kickoff slides (March 2026) |
| Task management | `LightShip_MVP_Task_Management.pdf` / `.xlsx` — detailed requirements matrix |
| Architecture diagram | `Blank diagram (1).pdf` — AWS high-level architecture diagram |
| Email threads | `emails.txt` — golden dataset preparation correspondence (Daniel Nahman ↔ Zechari Tempesta) |
| Meeting notes | `weekley_20_1_26.txt` — AI-generated meeting summary (Jan 20, 2026) |
| Draft architecture | `draft_from_gpt.txt` — early architecture draft |
| Requirements spec | `golden_dataset-3-23-26/docs/Comm-IT Requirements.txt` — golden dataset JSON schema |
| Golden dataset (v1) | `golden_dataset-3-23-26/` — 29 driving + 48 jobsite ground-truth JSONs, 3 config examples, video info spreadsheet |
| Golden dataset (v2) | `golden_dataset_3-29-26/` — expanded: 31 driving + 50+ jobsite ground-truth JSONs |
| POC evaluation results | `evaluation/evaluation/test/` — 15 test video outputs with SUMMARY.json, per-video JSONs, extracted frames |
| Processing log | `processing_20260112_203553.log` — POC V3 pipeline execution log |
| Training data (early) | `train/json_12-22-25/`, `train/json_12-25-25/` — earlier annotation format iterations |
| Config examples | 3 config JSONs: `decisions_config_example.json`, `detection_config_example.json`, `reactions_config_example.json` |

### BWI folder

| Category | Files |
|---|---|
| Architecture document | `BWI_MVP_AWS_Architecture.md` — serverless + managed ML architecture spec |
| POC-to-MVP analysis | `BWI_POC_to_MVP_Analysis.docx` |
| Kickoff presentations | POC kickoff `.pdf`/`.pptx`, MVP kickoff `.pptx` |
| Delivery trackers | POC and MVP delivery tracker `.pptx` files |
| Statement of work | SOW1 (POC, fully executed) `.pdf`, SOW3 (MVP) `.docx` |
| MVP scoping | `BWI MVP Scoping.xlsx` |
| POC codebase | `bwi-ai-poc/` — full Python repo with pipeline (detector, segmenter, refiner, composer), Lambda backend, Streamlit frontend, CloudFormation infra |
| Ground truth comparison | `gt_comparison/` — comparison_summary.json (mean bbox IoU 0.9526), CSVs, visualization PNGs |
| Sample images | `Samples/` — vehicle exterior photos (original + background-replaced) |
| Architecture diagram | `BWI AI Image Processing Preliminary Architecture.png` |

---

## C. Functional/Task Breakdown

### LightShip — Required Capabilities

1. **Frame extraction from video** — Extract frames from dashcam .mp4 files at configurable rates
2. **Frame selection / key-frame filtering** — Reduce 9,000 frames (5-min video at 30fps) to ~300-400 through temporal sampling, motion detection, and event window expansion
3. **Object detection** — Detect vehicles, motorcycles, pedestrians, bicycles, cones, barriers, heavy equipment, construction workers, debris, animals across frames
4. **Lane detection** — Identify ego lane and adjacent lanes as polygons; detect lane markings (double yellow, white dashed, solid)
5. **Road sign detection** — Detect and classify 50+ road sign types (speed limit, stop, yield, warning, construction)
6. **Traffic signal detection** — Detect and classify traffic signal states (red, yellow, green, flashing)
7. **Construction/jobsite object detection** — Detect heavy equipment, fencing, construction signs, workers, barriers, overhead hazards, PPE
8. **Cross-frame object tracking** — Track objects across consecutive frames, compute trajectories, flag hazard events when objects enter the driver's lane
9. **Depth/distance estimation** — Estimate object distance categories (danger_close, near, mid, far, very_far)
10. **Weather/environment detection** — Classify weather conditions (clear, rain, snow, fog, reduced visibility) and lighting (daylight, dusk, night)
11. **Road type classification** — Classify road as highway, freeway, city/intersection, residential, rural, interchange
12. **Video classification (LLM)** — Classify each video into one of 4 training types: reactivity_braking, qa_educational, hazard_detection, job_site_detection
13. **Config JSON generation (LLM)** — Auto-generate type-specific configuration JSONs matching Lightship's application schema
14. **Hazard assessment** — Identify and score hazard events with severity (low/medium/high/critical) and descriptions
15. **Batch async processing** — Process multiple videos concurrently via job queue
16. **Single image mode** — Process individual construction-site images
17. **Web UI** — Upload, status tracking, result viewing/download
18. **Evaluation framework** — Automated comparison against golden dataset with per-category metrics

### BWI — Required Capabilities

1. **Vehicle detection** — Detect the vehicle in dealer-lot photos (single vehicle per image, 7 angles)
2. **Vehicle segmentation** — Generate pixel-level masks separating vehicle from background
3. **Mask refinement** — Clean up segmentation masks using morphological operations
4. **Background replacement / composition** — Center vehicle, generate shadow, composite onto custom backgrounds
5. **Per-image status tracking** — QUEUED → PROCESSING → COMPLETED / FAILED state machine
6. **Human edit mode** — UI for manual mask/shadow adjustments
7. **REST API** — Integration endpoint for BWI's back-office system
8. **Batch ingestion** — Process ~1,000 images/day

---

## D. Inputs / Outputs / Data Understanding

### LightShip

**Inputs:**
- Dashcam video files (.mp4) from Lytx (10fps), Netradyne (30fps), Samsara (30fps), Verizon cameras
- Single still images for jobsite detection
- Camera type metadata (affects processing profile)

**Golden Dataset / Ground Truth JSON Schema (per video):**
```
{
  filename, video_description, video_class, road_type,
  weather, traffic, speed, collision, hazards[],
  frames[{
    frame_number, timestamp_sec,
    objects[{ class, description, distance, center{x,y}, bbox{x_min,x_max,y_min,y_max,width,height} }],
    lanes[{ lane_id, type, polygon[[x,y]...] }],
    road_signs[{ label, bbox }],
    traffic_signals[{ label, bbox }]
  }]
}
```

**Pipeline Output JSON Schema (per video):**
```
{
  filename, fps, camera, description, traffic, lighting,
  weather, collision, speed, video_duration_ms,
  objects[{ description, start_time_ms, distance, priority,
            location_description, center{x,y}, polygon[],
            x_min, y_min, x_max, y_max, width, height }],
  hazard_events[{ timestamp_ms, description, severity, objects_involved[] }]
}
```

**Config Output JSONs (3 types confirmed):**
- **Decisions config** — following_distance domain: questions, sliders, correct answers, video references, road/speed/traffic metadata
- **Detection config** — detection domain: hazard coordinates (x,y,size), descriptions, view durations, trial prompts
- **Reactions config** — reactivity domain: hazard stimulus start times, road/collision/space metadata

**Object classes defined:** car, truck, bus, motorcycle, bicycle, pedestrian, construction_worker, cone, barrier, heavy_equipment, construction_sign, fencing, debris, animal, other

**Distance categories:** danger_close, very_close, near, mid, far, very_far (evolved from 3 to 5+ during project)

**Target Metrics (from kickoff):**
- Motorcycle detection recall ≥ 80%
- Lane detection IoU ≥ 80%
- Road sign detection precision ≥ 80%
- Construction object precision ≥ 80%
- Video classification accuracy ≥ 80%

### BWI

**Inputs:** JPG/PNG vehicle images (7 exterior angles per vehicle), custom background images
**Outputs:** Background-replaced images with vehicle centered, shadow generated
**Metrics achieved (POC):** Bbox IoU 0.9526, Dice 0.975, mask features IoU 0.373 (lower due to fine-edge challenges)

---

## E. Architecture Understanding

### LightShip MVP Architecture

The architecture is a fully AWS-managed, async batch-processing pipeline:

**Flow:** User uploads video via Web UI → S3 → SQS job queue → Lambda dispatcher → Step Functions orchestration → ECS Fargate workers (frame extraction) → Rekognition / custom models (detection) → Lambda (cross-frame tracking) → Bedrock LLM (classification + config generation) → Lambda (annotated frame generation) → S3 results → DynamoDB status update → SNS notification → User views results

**Key architectural decisions already made:**
- AWS Step Functions for pipeline orchestration (10 states)
- ECS Fargate for compute (no GPU for MVP — CPU inference only)
- Amazon Rekognition as primary detection service
- Amazon Bedrock (Claude) for LLM classification and config generation
- Single S3 bucket with prefix-separated layout
- DynamoDB for job status tracking
- CloudFormation nested stacks for IaC
- VPC with public/private subnets, NAT Gateway, VPC endpoints
- Target: ~50 videos/month, ~$145-190/month AWS cost

### BWI MVP Architecture

**Flow:** BWI back-office sends S3 URIs → API Gateway → Step Functions → Lambda/ECS (detection via GroundingDINO → segmentation via SAM2 → refinement via OpenCV → composition) → S3 outputs → DynamoDB status → SNS notification

**Key decisions:** GPU required (SAM2 needs ~14GB VRAM), ≤30s per image, ~1,000 images/day, 5 concurrent users.

---

## F. Required Technical Tools/Capabilities

### F1. Object Detection (Vehicles, Motorcycles, Pedestrians, General Objects)

**Why needed:** Core capability for both projects. LightShip must detect 15+ object classes in dashcam video frames. BWI must detect vehicles in lot photos. The POC revealed weaknesses in motorcycle and traffic signal detection that the MVP must fix.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Amazon Rekognition** | AWS managed object detection API | No infrastructure management, no model hosting, pay-per-image ($0.001/image), auto-scales, no licensing concerns, GPU-free | Limited customization, cannot fine-tune, may lack domain-specific classes (construction_worker, heavy_equipment), black-box confidence calibration, dependent on AWS pricing |
| **YOLOv11/v12 (Ultralytics)** | State-of-the-art real-time object detector, used in POC (YOLO11x) | Excellent speed-accuracy tradeoff, huge community, strong motorcycle/vehicle detection, configurable confidence thresholds, proven in POC (1,315 objects across 15 test videos) | **AGPL license** — requires code to be public if deployed as a service; internal-only use may be permissible but requires legal review; Ultralytics commercial license costs ~$1,500+/year |
| **Detectron2 (Meta)** | Research-grade detection framework with Faster R-CNN, panoptic segmentation | Apache 2.0 license (fully permissive), strong panoptic segmentation (useful for lanes), Mask R-CNN variant available, highly configurable | Heavier/slower than YOLO, more complex deployment, less active community than Ultralytics, requires more GPU memory |
| **RT-DETR / DINO** | Transformer-based detectors | End-to-end (no NMS), strong on small objects, good for signs | Newer ecosystem, fewer pre-trained checkpoints for dashcam domain, higher compute requirements |

**Recommendation for LightShip:** Use **Amazon Rekognition as the primary service** for standard object classes (vehicles, pedestrians, motorcycles), complemented by **Detectron2** for specialized detection (construction objects, heavy equipment) where Rekognition coverage is thin. This avoids YOLO's AGPL licensing risk while staying within the AWS-managed service ecosystem. If benchmarking shows Rekognition underperforms on motorcycles (the known POC weakness), Detectron2 can serve as the fallback. The architecture already accommodates parallel model execution.

**Recommendation for BWI:** Continue with **GroundingDINO** (open-set detection, proven in POC with 0.95 bbox IoU). No change needed.

**Confidence:** Medium-High. Rekognition must be benchmarked against the golden dataset before finalizing. The YOLO licensing question is the key blocker for YOLO adoption.

---

### F2. Lane Detection

**Why needed:** The system must detect ego lane and adjacent lanes as polygons, plus identify lane marking types (double yellow, white dashed, solid). The POC meeting notes explicitly flag lane detection as inconsistent, and the team acknowledged YOLO is not designed for geometric/lane detection.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Ultra-Fast-Lane-Detection-v2** | Specialized lane detection model (row-anchor based) | Purpose-built for lanes, fast inference (~200fps on GPU), handles curved lanes, pre-trained on CULane/TuSimple, outputs lane line points | Outputs lane lines (not polygons) — needs post-processing to create lane area polygons; may struggle with unusual markings |
| **CLRNet / CLRerNet** | Cross-layer refinement lane detection | High accuracy on CULane benchmark, handles occlusion well | More complex, less community tooling |
| **Detectron2 Panoptic Segmentation** | Segment road surface and lane markings as semantic classes | Can output pixel-level lane masks directly, reuse same framework as object detection | Not lane-specific — may conflate lane boundaries; slower; requires training data |
| **LaneATT** | Anchor-based attention lane detection | Good speed-accuracy tradeoff, handles variable lane counts | Fewer pre-trained checkpoints, older architecture |
| **Amazon Rekognition** | Managed detection | No deployment complexity | Does NOT detect lane markings — not a viable option for this task |
| **LLM-based refinement (current POC approach)** | YOLO detects lane-adjacent geometry, LLM (Claude) refines into lane polygons | Already working in POC V3, leverages LLM contextual understanding | Expensive per-frame LLM calls, non-deterministic, hard to evaluate systematically |

**Recommendation:** Use **Ultra-Fast-Lane-Detection-v2** as a dedicated lane detection model, run in the same ECS Fargate worker container alongside the general object detector. Post-process the lane-line points into polygon regions. Supplement with LLM refinement for edge cases (merging lanes, construction zones with temporary markings). This is the most proven, fastest, and most purpose-built approach.

**Confidence:** Medium. Lane detection in dashcam footage is inherently harder than object detection. The 80% IoU target is achievable but will require careful tuning. No single model excels across all road types (highway lanes vs. residential vs. construction zones).

**Open question:** The ground truth defines lanes as polygons, but most lane detection models output lane lines. A polygon-construction step is needed — this is engineering work, not a model choice.

---

### F3. Road Sign and Traffic Signal Detection

**Why needed:** The system must detect and classify 50+ road sign types and traffic signal states. The POC had trouble with traffic signals specifically.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Amazon Rekognition** | Managed detection with built-in traffic sign labels | Zero-deployment, supports "Traffic Light", "Stop Sign" labels out of the box | Limited sign taxonomy (may not cover all 50+ types), no signal state detection (red/yellow/green) |
| **YOLO11/v12 with traffic-sign fine-tuning** | YOLO model fine-tuned on GTSDB/MTSD datasets | Strong small-object detection, sign-specific models available on Hugging Face, can be trained for signal state | AGPL licensing concern; fine-tuning is out of MVP scope |
| **Detectron2 with traffic sign classes** | Detectron2 trained/fine-tuned on sign datasets | Apache 2.0 license, good small-object detection with FPN | Requires fine-tuning (out of scope for MVP); heavier inference |
| **Mapillary Traffic Sign Detection (MTSD)** | Open dataset + pre-trained models for 400+ sign classes | Comprehensive taxonomy, purpose-built | Model integration complexity, may require custom hosting |
| **Hybrid: Rekognition + LLM post-classification** | Rekognition detects sign regions, LLM classifies the specific sign type from the cropped image | Leverages both managed services, LLM handles the long tail of sign types | Additional latency and cost for LLM calls per sign |

**Recommendation:** **Hybrid approach — Amazon Rekognition for initial sign/signal region detection + Amazon Bedrock (Claude) for detailed classification.** Rekognition identifies bounding boxes for signs and signals; cropped regions are sent to Claude (vision) for fine-grained classification (sign type, signal state). This stays within the managed-service architecture, avoids YOLO licensing, and handles the long tail of sign types that no single pre-trained model covers well. Traffic signal state detection (red/yellow/green) is well within Claude's vision capabilities.

**Confidence:** Medium. The hybrid approach adds latency and cost per sign. If sign volume per frame is high, costs could grow. Benchmarking is essential.

---

### F4. Construction / Jobsite Object Detection

**Why needed:** The system must detect construction-specific objects (heavy equipment, barriers, fencing, construction signs, workers with PPE, overhead hazards, wires/cables). This is a distinct domain from general road detection. The golden dataset includes 48-50+ jobsite ground-truth files.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Amazon Rekognition** | Managed detection | Has some construction labels (Person, Helmet, Machinery) | Limited construction-specific taxonomy; may miss specialized categories like overhead_obstruction_hazard, fencing, ppe_detail |
| **YOLO + construction fine-tuning** | YOLO model fine-tuned on construction datasets | Good detection speed, construction-safety models exist on HuggingFace | AGPL licensing; fine-tuning is out of MVP scope |
| **Detectron2 + COCO pre-trained** | Detectron2 with COCO classes | Covers person, truck, some equipment | Missing specialized construction classes |
| **Hybrid: Rekognition + Bedrock vision** | Rekognition detects general objects, Bedrock classifies construction-specific categories | Handles open-ended categories, leverages LLM visual understanding | Higher per-image cost, latency |
| **GroundingDINO (open-set detection)** | Text-prompted zero-shot object detection | Can detect ANY described object without training ("construction worker with hard hat", "overhead power line") | Requires GPU (~4GB VRAM), slower, text prompts must be engineered carefully, MIT license (permissive) |

**Recommendation:** **GroundingDINO for jobsite detection.** It is already proven in the BWI project (0.95 bbox IoU). Its zero-shot, text-prompted detection is ideal for the diverse, specialized jobsite categories (overhead hazards, PPE, wires, fencing) that no pre-trained COCO model covers. GroundingDINO is MIT-licensed, eliminating legal concerns. For the MVP, it can run in an ECS Fargate task with GPU or on a g5.xlarge spot instance. Alternatively, if GPU cost is prohibitive at MVP scale, fall back to Rekognition + Bedrock vision hybrid.

**Confidence:** Medium-High. GroundingDINO's open-vocabulary capability is uniquely suited to the diverse jobsite taxonomy. The risk is GPU cost and latency.

---

### F5. Frame Selection / Key-Frame Extraction

**Why needed:** A 5-minute dashcam video at 30fps produces 9,000 frames. Processing every frame is wasteful. The architecture spec calls for reducing this to ~300-400 frames (93-97% reduction) while preserving all frames of interest.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **FFmpeg temporal sampling + SSIM motion filter** (specified in architecture) | Extract at 5fps, sample at 0.5s intervals, drop near-duplicate frames via SSIM | Simple, proven, fast (FFmpeg is highly optimized), no model needed, 93-97% frame reduction | May miss brief events between samples; SSIM threshold tuning required per camera type |
| **Optical flow (OpenCV)** | Dense or sparse optical flow to detect significant motion | Detects actual motion rather than appearance change | Computationally heavier than SSIM, prone to false positives from camera shake |
| **Scene change detection (PySceneDetect)** | Detect scene boundaries using content-aware analysis | Purpose-built, handles cuts/transitions | Designed for edited video, not continuous dashcam footage; may over-segment |
| **Histogram delta** | Compare frame histograms for significant changes | Very fast, simple to implement | Poor at detecting important objects that don't change overall histogram (e.g., a pedestrian entering frame) |
| **Event window expansion (post-detection)** | After initial detection pass, extract ±2 seconds around detected events at higher frame rate | Recovers temporal context around important moments | Requires two-pass processing (detection → expansion → re-detection); adds latency |

**Recommendation:** **FFmpeg extraction at 5fps + SSIM-based motion filtering + event window expansion** — exactly as specified in the architecture document. This is the right approach. FFmpeg is mature, fast, and well-integrated. SSIM captures meaningful visual changes. The event-window expansion is a smart two-pass strategy that preserves temporal context without processing everything.

**Important implementation detail:** The POC already uses 500ms-interval frame extraction (from processing log). Camera-specific profiles are enabled (Lytx at 10fps, Netradyne/Samsara at 30fps), which means the extraction rate should be normalized.

**Confidence:** High. This is well-understood engineering. The main tuning parameter is the SSIM threshold, which should be calibrated per camera type using the golden dataset.

---

### F6. Weather / Environment / Scene Classification

**Why needed:** The ground truth schema includes weather (clear, rain, snow, fog, reduced visibility), lighting (daylight, dusk, night), road_type (highway, freeway, city, residential, rural, interchange), and traffic density (low, moderate, high). These classifications feed into the output config JSONs and affect training content selection.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Amazon Bedrock (Claude vision)** | Send representative frames to Claude with classification prompt | Handles all categories in one call, understands context, can classify weather + road + traffic simultaneously, already in the architecture | Non-deterministic, cost per call, requires prompt engineering |
| **Amazon Rekognition scene labels** | Rekognition returns scene labels (Outdoor, Road, Highway, Rain) | Zero-deployment, fast | Labels are generic, may not match the required taxonomy precisely |
| **Custom CNN classifier (ResNet/EfficientNet)** | Train a small classifier on weather/road/lighting categories | Fast inference, deterministic, cheap at scale | Requires training data (out of scope for MVP), needs labeled training set |
| **Hybrid: Rekognition labels → Bedrock refinement** | Rekognition provides base labels, Bedrock maps to exact taxonomy | Combines speed with precision | Two-stage adds complexity |

**Recommendation:** **Amazon Bedrock (Claude vision)** as the primary classification service. The architecture already designates Bedrock for video classification and config generation. Weather, road type, lighting, and traffic density can all be classified in the same LLM call that classifies the video type. This is the simplest, most flexible approach. Rekognition scene labels can provide supporting signals as input to the prompt.

**Confidence:** High. LLMs are excellent at scene-level classification from images. The categories are well-defined and unambiguous for a vision-capable model.

---

### F7. Video Classification (4 Training Types)

**Why needed:** Each video must be classified as one of: reactivity_braking, qa_educational, hazard_detection, or job_site_detection. This classification drives which config JSON template is generated. The kickoff specifies ≥80% classification accuracy.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Amazon Bedrock (Claude)** | Provide aggregated detection metadata + representative frames → classify video type | Already in architecture, excellent at multi-factor reasoning, can explain its classification, handles edge cases | Cost per video (~$0.02-0.05), non-deterministic, prompt sensitivity |
| **Rule-based classifier** | Heuristic rules: if construction objects → job_site; if braking event → reactivity; etc. | Deterministic, fast, free, explainable | Brittle for ambiguous videos, doesn't handle edge cases, hard to maintain as rules grow |
| **Fine-tuned text classifier** | Train a small model on video metadata to predict class | Fast, cheap, deterministic | Requires training data, can't handle novel scenarios |
| **Hybrid: Rules → LLM for ambiguous cases** | Apply simple rules first, route uncertain cases to LLM | Reduces LLM costs, fast for clear cases | Two-path complexity |

**Recommendation:** **Amazon Bedrock (Claude) with structured prompting.** The four categories have nuanced boundaries (e.g., a video with both a braking event and a construction zone). An LLM can weigh multiple factors — detected objects, hazard events, road context, traffic patterns — to make a holistic classification. For MVP scale (~50 videos/month), cost is negligible (~$1-2/month). Include few-shot examples from the golden dataset config files (decisions, detection, reactions configs) in the prompt to ground the classification.

**Confidence:** High. The categories are well-defined by the config examples. LLM classification at this granularity should easily exceed 80%.

---

### F8. Cross-Frame Object Tracking

**Why needed:** Objects must be tracked across consecutive frames to detect trajectories and flag hazards (e.g., a motorcycle entering the ego lane). The architecture specifies IoU-based object matching across frames.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Simple IoU tracking** | Match objects between frames by bbox IoU overlap | Simple, fast, no additional model needed, works well at low fps | Fails when objects move quickly between sparse frames, can't handle occlusion |
| **ByteTrack** | Multi-object tracker using detection confidences | State-of-the-art MOT performance, handles low-confidence detections, no ReID model needed | Designed for sequential video frames — may struggle with 0.5s intervals |
| **SORT / DeepSORT** | Kalman filter + optional ReID for multi-object tracking | Well-established, handles prediction between frames | ReID adds complexity, Kalman filter needs tuning for sparse frame rates |
| **BoT-SORT** | Combines motion (Kalman) + appearance (ReID) + camera motion compensation | Handles camera shake (common in dashcam), robust tracking | More complex, heavier |

**Recommendation:** **Simple IoU-based tracking** for MVP, with the option to upgrade to **ByteTrack** if accuracy is insufficient. At 2fps effective frame rate (after selection), objects don't move dramatically between frames, making IoU overlap sufficient. The architecture already specifies this approach. Hazard detection (object entering ego lane) can be implemented as a geometric check: if a tracked object's bbox intersects the ego lane polygon, flag it.

**Confidence:** Medium. The sparse frame rate (0.5s intervals) is the main risk. If objects appear in very different positions between frames, IoU matching will break. ByteTrack would be the fallback.

---

### F9. Depth / Distance Estimation

**Why needed:** The output schema requires distance estimates per object (danger_close, very_close, near, mid, far, very_far). The POC used Depth-Anything-V2 for this.

**Candidate approaches:**

| Option | Description | Advantages | Disadvantages |
|---|---|---|---|
| **Depth-Anything-V2** (used in POC) | Monocular depth estimation model | Strong zero-shot performance, already proven in POC, MIT license, multiple sizes (Small to Giant) | Requires GPU for good speed, outputs relative depth (not absolute meters), needs calibration per camera |
| **MiDaS** | Intel monocular depth estimation | Well-established, multiple model sizes | Older, lower accuracy than Depth-Anything-V2 |
| **Bbox-based heuristic** | Estimate distance from bbox size + position in frame | No model needed, fast, simple | Inaccurate for non-standard object sizes, doesn't account for camera parameters |
| **LLM-based estimation** | Claude estimates distance from visual context | Contextual understanding, handles ambiguity | Non-deterministic, expensive per frame, unreliable for precise categories |

**Recommendation:** **Depth-Anything-V2-Small** (used in POC). Continue with what works. The Small variant (25M params) runs reasonably on CPU and fast on GPU. Map relative depth values to distance categories using calibrated thresholds per camera type. The POC already established these mappings.

**Confidence:** Medium-High. Monocular depth is inherently approximate, but the required output is categorical (near/mid/far), not metric distance. The POC demonstrated adequate results.

---

### F10. Config JSON Generation (LLM)

**Why needed:** The system must auto-generate type-specific JSON configuration files matching Lightship's application schemas. There are three distinct config structures (decisions, detection, reactions), each with different fields, question formats, and hazard annotation patterns.

**Candidate approach:** This is exclusively an **LLM task (Amazon Bedrock / Claude)**. The config schemas are complex, domain-specific, and require contextual understanding of the video content. There is no viable non-LLM approach.

**Recommendation:** **Amazon Bedrock (Claude) with few-shot prompting** using the actual config examples from the golden dataset as templates. Provide the video's detection results, classification, and metadata as context. Use structured output (JSON mode) to ensure schema compliance. Validate the output against the expected schema before saving.

**Confidence:** High. Claude excels at structured data generation when given clear schemas and examples.

---

### F11. Hazard Assessment

**Why needed:** The system must identify hazardous events with timestamps, descriptions, and severity ratings. Hazards are defined as: objects entering the driver's lane, vehicles braking suddenly, pedestrians/cyclists in dangerous proximity, construction zones, etc.

**Recommendation:** **Two-stage approach**: (1) Rule-based hazard triggers from cross-frame tracking data (object enters ego lane polygon, object distance changes from far to near rapidly), combined with (2) LLM-based hazard refinement and description generation via Bedrock. The POC V3 pipeline already uses this pattern (processing log shows "Hazard Assessor" as a pipeline stage using Claude).

**Confidence:** Medium-High. The POC demonstrated 118 hazard events across 15 test videos (7.9 per video average). Refinement of severity calibration is needed.

---

## G. Decision Table

| Capability | Option A | Option B | Option C | Recommended | Confidence | Unresolved Questions |
|---|---|---|---|---|---|---|
| General object detection | Rekognition | Detectron2 | YOLO11/12 | **Rekognition + Detectron2 fallback** | Medium-High | Benchmark Rekognition motorcycle recall against golden dataset; YOLO licensing decision |
| Lane detection | Ultra-Fast-Lane-v2 | Detectron2 panoptic | LLM refinement | **Ultra-Fast-Lane-v2 + LLM** | Medium | Polygon construction from lane lines; performance on non-highway roads |
| Road sign detection | Rekognition | YOLO fine-tuned | Hybrid Rekognition+LLM | **Rekognition + Bedrock vision** | Medium | Rekognition sign taxonomy coverage; per-sign LLM cost at scale |
| Traffic signal state | Rekognition | YOLO | Bedrock vision | **Bedrock vision (crop + classify)** | Medium-High | Latency of per-signal LLM calls |
| Construction/jobsite detection | Rekognition | GroundingDINO | Detectron2 | **GroundingDINO** | Medium-High | GPU cost for MVP; prompt engineering for open-vocab detection |
| Frame selection | FFmpeg + SSIM | Optical flow | PySceneDetect | **FFmpeg + SSIM + event expansion** | High | SSIM threshold per camera type |
| Weather/scene classification | Bedrock (Claude) | Rekognition labels | Custom CNN | **Bedrock (Claude vision)** | High | Prompt design for consistent taxonomy mapping |
| Video classification (4 types) | Bedrock (Claude) | Rule-based | Fine-tuned classifier | **Bedrock (Claude)** | High | Prompt few-shot design; handling ambiguous videos |
| Cross-frame tracking | IoU-based | ByteTrack | DeepSORT | **IoU-based (upgrade to ByteTrack if needed)** | Medium | Performance at 0.5s frame intervals |
| Depth/distance estimation | Depth-Anything-V2 | MiDaS | Bbox heuristic | **Depth-Anything-V2-Small** | Medium-High | Camera-specific calibration thresholds |
| Config JSON generation | Bedrock (Claude) | Template engine | N/A | **Bedrock (Claude)** | High | Schema validation strategy |
| Hazard assessment | Rules + LLM | Pure LLM | Pure rules | **Rules + LLM hybrid** | Medium-High | Severity calibration; false-positive rate |
| Vehicle detection (BWI) | GroundingDINO | Rekognition | YOLO | **GroundingDINO** (proven) | High | None — POC validated |
| Vehicle segmentation (BWI) | SAM2 | Mask R-CNN | U-Net | **SAM2** (proven) | High | None — POC validated |

---

## H. Open Design Decisions

### Critical (must resolve before implementation)

1. **YOLO licensing decision.** The AGPL license is a genuine risk if the system is deployed as a service to Lightship. The team discussed this in the Jan 20 meeting but no resolution was documented. If YOLO is used, either: (a) obtain an Ultralytics commercial license, (b) ensure deployment is internal-only (which may still trigger AGPL), or (c) switch to Detectron2/Rekognition. This affects the primary detection model choice.

2. **GPU vs. CPU inference for MVP.** The architecture says "GPU not required at MVP volume" and specifies Fargate (CPU) for workers. However, Depth-Anything-V2, Ultra-Fast-Lane-Detection, and GroundingDINO all benefit heavily from GPU. Running these on CPU will be slow (potentially 10-30x slower). Decision needed: accept slower processing, use GPU spot instances (g5.xlarge at ~$0.50/hr), or limit scope of models that require GPU.

3. **Rekognition benchmarking.** Rekognition is designated as the primary detection service, but it has never been benchmarked against the golden dataset. If it underperforms on motorcycles or construction objects (likely), the fallback plan (Detectron2 or hybrid) must be ready. This benchmark should happen in Sprint 1.

4. **Lane polygon construction method.** The ground truth defines lanes as polygons, but lane detection models output lane lines (left boundary, right boundary). The engineering to convert lane lines → filled polygon regions → IoU comparison is non-trivial and needs design.

5. **Config JSON schema validation.** Three config types are defined, but the schemas evolved between early training JSONs (12/22/25, 12/25/25) and the golden dataset (3/23/26, 3/29/26). The "final" schema must be locked down with Lightship before the LLM generates against it.

### Important (should resolve during Sprint 1)

6. **Distance category harmonization.** The emails show disagreement about 3 vs 5 distance categories. The golden dataset uses variable categories (danger_close through very_far). Daniel agreed to 5 categories, but the Comm-IT Requirements doc says 3 (near/mid/far). Need final confirmation.

7. **Jobsite config type.** Three config examples exist (decisions, detection, reactions), but the jobsite config is noted as "pending" in the readme. Lightship must provide the jobsite config example before the system can auto-generate it.

8. **Speed metadata interpretation.** The emails discuss whether speed is max speed in the video or road speed limit. Daniel clarified it should be "the max speed the vehicle should go" (road limit), not actual vehicle speed. This affects how the LLM interprets speed metadata.

9. **Frame count per video in golden dataset.** Daniel suggested 5 frames per video is sufficient (not 10-50). The golden dataset v2 (3-29-26) has 5 frames per driving video. Confirm this is final.

10. **Camera-specific detection profiles.** The POC uses camera-specific profiles (Lytx vs Netradyne vs Samsara have different resolutions and frame rates). How many camera types must the MVP support?

### Nice to resolve

11. **Evaluation framework design.** The MVP requires an automated evaluation report comparing against the golden dataset with per-category metrics. The exact metrics, visualization format, and comparison methodology need design.

12. **Multi-model orchestration.** If multiple models run per frame (Rekognition for objects + Ultra-Fast-Lane for lanes + Depth-Anything for distance + Bedrock for classification), how are results merged? Need a unified detection-result schema.

---

## I. Final Recommendation — Overall Tool Stack

### LightShip MVP Recommended Stack

| Pipeline Stage | Recommended Tool | Rationale |
|---|---|---|
| **Frame extraction** | FFmpeg (via ECS Fargate Worker) | Industry standard, fast, handles all codec/fps variations |
| **Frame selection** | SSIM motion filter + temporal sampling (Python/OpenCV) | Simple, proven, tunable per camera type |
| **General object detection** | Amazon Rekognition (primary) + Detectron2 (benchmark/fallback) | Managed service for core classes; Apache 2.0 fallback avoids YOLO licensing |
| **Motorcycle detection** | Benchmark Rekognition vs Detectron2 on golden dataset | POC showed YOLO11x weakness here; must verify Rekognition performance |
| **Lane detection** | Ultra-Fast-Lane-Detection-v2 (dedicated model) | Purpose-built, fast, handles curved lanes; supplement with LLM for edge cases |
| **Road sign detection** | Rekognition (region detection) + Bedrock Claude vision (classification) | Hybrid covers the long tail of 50+ sign types |
| **Traffic signal detection** | Bedrock Claude vision (crop + classify state) | LLM vision handles state classification (red/yellow/green) reliably |
| **Construction/jobsite objects** | GroundingDINO (open-vocabulary detection) | Zero-shot detection covers diverse construction categories; MIT license |
| **Cross-frame tracking** | IoU-based matching (Python) | Simple, sufficient at 2fps; upgrade to ByteTrack if needed |
| **Depth/distance estimation** | Depth-Anything-V2-Small | Proven in POC, MIT license, good zero-shot |
| **Weather/road/scene classification** | Amazon Bedrock (Claude vision) | Single LLM call classifies all scene-level attributes |
| **Video classification (4 types)** | Amazon Bedrock (Claude) with few-shot prompting | LLM reasoning over aggregated detection results |
| **Config JSON generation** | Amazon Bedrock (Claude) with schema-guided generation | Complex structured output matching client schemas |
| **Hazard assessment** | Rule-based triggers + Bedrock refinement | Geometric triggers (lane entry) + LLM severity/description |
| **Orchestration** | AWS Step Functions | Multi-step pipeline with error handling and state tracking |
| **Compute** | ECS Fargate (Web + CPU workers) + optional GPU spot instances | Fargate for scale; GPU for GroundingDINO/Depth/Lane if benchmarks require it |
| **Storage** | S3 (single bucket, prefix-separated) | Cost-effective, lifecycle-managed |
| **Job tracking** | DynamoDB | Serverless, free at MVP volume |
| **IaC** | CloudFormation (nested stacks) | Consistent with existing POC infrastructure |

### BWI MVP Recommended Stack (no changes from current design)

| Stage | Tool |
|---|---|
| Vehicle detection | GroundingDINO |
| Segmentation | SAM2 |
| Mask refinement | OpenCV morphological operations |
| Composition | Custom Python (centering + shadow + background) |
| Infrastructure | Same AWS pattern (Step Functions, ECS, S3, DynamoDB) |

### Key Risk Mitigation Actions

1. **Sprint 1, Week 1:** Benchmark Rekognition against golden dataset for motorcycle, sign, and construction categories. Results determine whether Detectron2 is needed as primary or just fallback.
2. **Sprint 1, Week 1:** Get legal/licensing decision on YOLO AGPL usage. If cleared, YOLO11x remains viable as the strongest single-model option.
3. **Sprint 1:** Lock down config JSON schemas with Lightship, including the missing jobsite config.
4. **Sprint 1:** Design and prototype the lane-line-to-polygon conversion pipeline.
5. **Sprint 2:** Run end-to-end evaluation on all 15+ test videos, comparing MVP pipeline accuracy against POC baseline per category.

---

*This analysis is based solely on the materials found in the BWI and LightShip project folders. No implementation has been started. All recommendations should be validated through benchmarking against the golden dataset before finalizing tool choices.*
