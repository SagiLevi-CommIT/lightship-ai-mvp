# Lightship MVP — Project Overview

_A consolidated, engineer-oriented onboarding document synthesized from SOW, kickoff decks, architecture notes, product requirements, UI code, client emails, and evaluation data._

---

## 1. Project Goal & Business Context

Lightship Neuroscience builds driver-safety training content derived from fleet dashcam video (Lytx, Netradyne, Samsara, Verizon). Today the pipeline from raw video → annotated training asset is **largely manual**: human reviewers scrub footage, pick key frames, describe hazards, and hand-author the JSON configuration that drives the downstream learning product.

The Lightship MVP (delivered by Commit Software) replaces that manual pipeline with an **automated, AWS-hosted video annotation and classification system**. The system ingests dashcam video, extracts and filters frames, detects road/construction objects, classifies the video into one of four training categories, and emits a client-ready JSON config together with annotated frames.

**Stakeholders**

- **Client:** Lightship Neuroscience (CEO: Zechari Tempesta)
- **Vendor:** Commit Software (Comm-IT)
- **Core team:** Daniel Nahman (PM), Ido Garbi (AI Lead), Sagi Levi & Asaf Lavi (AI Dev), Adi Ben David (DevOps), Niv Spector (PM support)

**Engagement shape**

- Follows a completed POC that validated core detection with YOLO11x
- MVP window: **Mar 5 – Apr 30, 2026** (≈6 weeks, 3 sprints)
- Fixed price: **$55K**, plus AWS cost (funding source TBD)
- MVP is deployed into the **client's own AWS account**; POC ran in Commit's account

**Success is measured by KPIs against a golden dataset**

- Motorcycle detection recall ≥ 80%
- Lane detection accuracy (IoU) ≥ 80%
- Road-sign detection precision ≥ 80%
- Construction object precision ≥ 80%
- 4-class video classification accuracy ≥ 80%
- Detection performance maintained or improved under rain / low-visibility vs. POC baseline

---

## 2. Main Use Cases & User Flows

The product exposes a small, focused Next.js web app. Four primary flows:

1. **Run New Pipeline — Batch mode.** User uploads one or more dashcam clips (MP4/AVI/WebM), optionally tunes the frame-selection method (Native FPS vs. Scene Change) and output S3 path, and submits. The job is queued and processed asynchronously; the UI shows per-video status (`QUEUED → PROCESSING → COMPLETED/FAILED`) and surfaces results when ready.
2. **Run New Pipeline — Evaluation mode.** Same intake surface, but the run is executed against the internal golden dataset and produces a benchmark report (per-KPI pass/fail, per-video metrics, radar chart).
3. **Single-image job-site detection.** A user uploads a single still (PNG/JPG) from a construction site and receives an annotated image with detected objects (workers, equipment, barriers, cones). No classification step.
4. **Historical runs.** A sidebar lists previous pipeline runs (batch or eval) and lets the user re-open their results without re-running.

Out-of-scope for MVP: multi-tenant access, role-based nav, per-file JSON editing, synchronous / real-time processing, custom domain, dedicated mobile UI.

---

## 3. Data Inputs & Outputs

### Inputs

- Dashcam video clips (MP4, AVI, WebM), typically 5–20 minutes, from fleet cameras.
- Optional: single PNG/JPG for job-site detection.
- Optional config: frame-selection method, target FPS, custom S3 output prefix.

### Intermediate / processing artifacts (short retention)

- Raw extracted frames at 5 FPS (14-day retention)
- Motion/scene-change filtered frames — typically 300–400 per 5-minute video (30-day retention)
- Per-frame detection JSONs (objects, signs, lanes, confidences)
- Cross-frame tracking output (trajectory / IoU match)

### Outputs (90–180 day retention)

- Annotated frames (bounding boxes, lane overlays, labels)
- `config.json` — client-ready configuration shaped per video type
- `detection_summary.json` — aggregated per-video metadata
- For `qa_educational` videos: auto-generated Q&A questions

**Example output (hazard_detection):**

```json
{
  "job_id": "uuid",
  "video_class": "hazard_detection",
  "road_type": "highway",
  "weather": "rain",
  "traffic": "moderate",
  "speed": ">60 mph",
  "hazards": [
    {
      "timestamp_sec": 45.5,
      "severity": "high",
      "description": "Motorcycle entering driver's lane from left",
      "object_id": "obj_003"
    }
  ],
  "config": { "training_type": "hazard_detection", "risk_levels": ["high"] }
}
```

### Golden dataset

Two snapshots exist (v1 `golden_dataset-3-23-26`, v2 `golden_dataset_3-29-26`). Contents:

- ~31–39 driving clips, labeled with `video_class`, `road_type`, `weather`, hazard events, per-frame objects, lanes, road signs, traffic signals.
- 50+ job-site clips, labeled with `job_site_detection` class and construction-specific objects.
- 4 JSON config templates — one per video type (reactivity, qa, hazard, jobsite).

The POC evaluation corpus contained 147 videos (48 hazard, 39 reactivity, 27 qa, 33 jobsite) and was used to drive initial KPI measurements.

---

## 4. System Architecture

The MVP is an **asynchronous, event-driven batch pipeline on AWS**, orchestrated by Step Functions and deployed via CloudFormation (nested stacks) into **us-east-1**.

### Component responsibilities

| Layer | Service | Purpose |
|---|---|---|
| Frontend + API | ECS Fargate (Next.js + API) behind ALB | Serves UI, accepts uploads, writes to S3, creates DynamoDB job, enqueues SQS message |
| Job dispatch | SQS + Lambda dispatcher | Consumes queue, kicks off a Step Functions execution per job; DLQ after 3 retries |
| Orchestration | Step Functions | 10-state machine: extract frames → filter → detect → track → (expand event window) → classify → generate config → render annotations → finalize |
| Frame processing | ECS Fargate worker (FFmpeg, Pillow) | Extracts/filters frames, renders annotated outputs |
| Detection — primary | **Amazon Rekognition** | Vehicles, motorcycles, pedestrians, road signs, traffic signals |
| Detection — fallback | Detectron2 on ECS | Construction objects, lanes (panoptic segmentation) — used only where Rekognition underperforms |
| Classification + config | Amazon Bedrock (Claude Sonnet 4.5) | 4-class video typing, config-JSON generation, Q&A synthesis |
| Storage | S3 (single bucket, prefix-separated) | `input/`, `processing/`, `results/` with lifecycle rules |
| Metadata | DynamoDB | Job record: id, status, timestamps, per-stage metrics, error messages |
| Networking | VPC, ALB, public + private subnets, NAT GW, VPC endpoints | S3 (gateway), ECR (required for Fargate), optional Bedrock/Rekognition/SQS/Step Functions |
| Security | IAM (least-privilege per role), KMS CMK, Secrets Manager, WAF on ALB | Threshold config, prompts, schemas stored as secrets; encryption at rest end-to-end |
| Observability | CloudWatch (logs, custom metrics, alarms, dashboard) | Per-stage latency, error rates, detection cost |
| IaC | CloudFormation nested stacks | `network`, `security`, `compute`, `processing`, `storage`, `monitoring` — with `dev` and `mvp` environment flavors |

### End-to-end data flow

```
User uploads video ──► Web App stores to S3 (input/videos/{job_id}/)
                      │
                      └─► DynamoDB record (status=QUEUED) + SQS message
                                       │
                                       ▼
                        Lambda dispatcher ─► Step Functions execution
                                       │
              ┌────────────────────────┼─────────────────────────────────────┐
              ▼                        ▼                                     ▼
  ExtractAndSelectFrames   Detect (Rekognition / Detectron2)      Classify + GenerateConfig
  (FFmpeg 30→5 FPS,        per selected frame → S3               (Bedrock Claude)
   temporal + SSIM filter)                                             │
              │                        │                               ▼
              └──────► CrossFrameTracking (IoU, trajectories) ──► optional EventWindowExpansion
                                                                       │
                                                                       ▼
                                                   GenerateAnnotatedFrames (Pillow)
                                                                       │
                                                                       ▼
                                     Results to S3 (results/{output_path}/)
                                                                       │
                                                DynamoDB COMPLETED + SNS notification
```

### Frame-selection pipeline (cost control)

Because Rekognition is billed per image, aggressive frame reduction is critical:

1. **FFmpeg 30 → 5 FPS** (~83% reduction; 5-min video: 9,000 → 1,500 frames)
2. **Temporal sampling** 1 frame / 0.5 s (~60% further reduction; ~600 frames)
3. **SSIM-based motion / scene-change filter** drops static frames (~30–50% further reduction; ~300–400 frames land at the detector)
4. **Event window expansion**: on hazard detection, re-extract ±2 s at 5 FPS around the event for denser coverage

Net effect: ≈$0.40/video in Rekognition cost vs. ≈$9.00 without filtering (~95% savings).

### Deployment environments

- **dev** — loose guardrails for fast iteration in the vendor account
- **mvp** — locked-down deployment into Lightship's AWS account; multi-account org left as future work

### Cost envelope (≈50 videos/month)

Expected monthly spend is **~$145–$190**, dominated by fixed infra (NAT GW ≈ $32, ALB ≈ $22, VPC endpoints ≈ $7–$50) rather than inference (Rekognition ≈ $20, Bedrock ≈ $1–$2). Fargate workloads, S3, DynamoDB, and SQS all stay under ~$30 combined at this scale.

---

## 5. Product Perspective (UI, Features, User Flow)

The front-end is a **Next.js 14 + React + TypeScript** app with a dark, Lightship-branded theme. Key screens map 1:1 to the flows above:

- `/` — home, "Run New Pipeline" vs. "Pipeline Historical Runs"
- `/pipeline` — workspace: mode toggle (Batch / Evaluation), upload dropzone, queue, left-sidebar config (frame method, FPS, S3 path)
- `/run` — live per-stage progress; per-asset status in batch mode, benchmark progress in eval mode
- `/preview` — annotated frame viewer
- `/results/[runId]` — annotated-frame gallery + structured properties panel + "Download all JSON"
- `/history` — historical runs browser, re-open past results

Notable components in `src/`: `SiteBrand`, `ModeToggle`, `UploadDropzone`, `UploadQueue`, `PipelineConfigForm`, `RunProgress`, `ResultsFrameGallery`, `ResultsPropertiesPanel`, `EvalReport`. Application state is held in a `FlowProvider` context; mock evaluation results live under `evaluation/` for UI-driven testing before the backend is wired.

Features explicitly in scope for MVP: batch and single-video upload, single-image job-site detection, configurable frame selection, custom S3 output, async processing with notifications, per-video status, annotated frame gallery, JSON download, evaluation benchmark, historical runs.

---

## 6. Key Technical Decisions & Constraints

### Model selection — aligned on Amazon Rekognition

The team has converged on **Amazon Rekognition as the primary detection model** for MVP, with the flexibility to revisit this decision later if KPI results or use cases demand it.

Rationale:

- Managed service — no GPU infra, no model-serving burden
- Pay-per-image pricing (~$0.001/image) fits MVP volumes after aggressive frame filtering
- No licensing risk (contrast YOLOv11/12, which are AGPL and flagged a legal concern even though the POC used YOLO11x)
- Automatic scaling

**Detectron2 is retained as a fallback** for cases where Rekognition is weak (panoptic lane segmentation, construction-specific objects). It is Apache 2.0 licensed, which removes the AGPL concern, and it will only be deployed if benchmarking justifies it.

**YOLOv11/v12 is explicitly out of scope for MVP** due to AGPL licensing, despite successful POC usage.

### Orchestration and compute

- **Step Functions** over raw SQS polling: explicit state transitions, native retry/catch, DLQ integration.
- **ECS Fargate** for both the web app and workers: no GPU required in MVP (CPU inference only); auto-scales; fits the serverless cost profile.
- **Lambda** only for thin glue (SQS → Step Functions dispatch, per-frame Rekognition orchestration, lightweight per-frame logic).

### Storage, retention, security

- Single S3 bucket, partitioned by prefix, with lifecycle rules: inputs 60 days, raw frames 14 days, selected/detection artifacts 30 days, final results 90–180 days.
- DynamoDB holds all job state; no relational DB in MVP.
- KMS CMK encrypts S3, SQS, DynamoDB, and CloudWatch Logs.
- Secrets Manager holds model thresholds, LLM prompts, schemas, API keys.
- IAM roles are scoped per component.
- WAF is attached to the ALB.

### Constraints

- Fixed $55K scope; 6-week calendar.
- Deployment target is client's AWS account → tighter blast-radius controls than POC.
- Client-owned data in S3 → encryption, least privilege, and retention policy are hard requirements.
- Per-image Rekognition pricing drives the entire frame-reduction design; without it the MVP cost envelope breaks.

---

## 7. Assumptions, Open Questions, and Gaps

The following items should be resolved early; several were still in flight in client emails through late March:

1. **Rekognition detection quality on motorcycles and signs.** POC data flagged motorcycle under-detection. Must be benchmarked against the golden dataset before locking the hybrid model decision. If < 80% recall, fall back to Detectron2.
2. **Lane detection algorithm.** Rekognition does not produce lane polygons. The architecture note leans on Detectron2 panoptic segmentation, but there is no explicit design for how lane type and geometry are extracted, scored, or rendered.
3. **Weather-aware performance.** KPI asks for maintained or improved performance in rain / low-visibility, but the approach (augmentation, separate model, confidence calibration) is undefined.
4. **Job-site dataset readiness.** The driving golden set was finalized by 3/29/26; the job-site portfolio is lagging and its final volume is unclear. Construction detection is a KPI and a UI surface, but may be data-starved at evaluation time.
5. **Golden dataset schema stability.** Emails on 3/18, 3/23, and 3/29 show ongoing iteration on JSON schema and labeled-frame counts. Any code consuming golden-dataset labels must tolerate version skew.
6. **Bedrock / Claude model version.** Architecture assumes Claude Sonnet 4.5; no fallback prompt engineering for model deprecation.
7. **AWS account provisioning.** Client AWS account needs pre-granted access to Bedrock, Rekognition, ECS, Lambda, Step Functions, S3, and SQS before CloudFormation apply. Not yet confirmed as ready.
8. **Event-window expansion scope.** Currently triggered only on hazard detections; unclear whether it should also fire for jobsite anomalies.

### Contradictions resolved

- **Model selection.** Earlier weekly notes (Jan 20) still debated YOLO vs. Rekognition on cost and licensing. The latest architecture note and project analysis both align on Rekognition primary + Detectron2 fallback. **This document treats Rekognition as the aligned decision, with explicit room to revisit.**
- **Lane handling.** One note says "YOLO 11 is not designed for geometric detection such as lanes"; the architecture points to Detectron2. Treated as confirmation that lanes are Detectron2's responsibility, not Rekognition's.
- **Runtime mode.** Early drafts hinted at synchronous previews; all later docs (SoW, kickoff, PRD) lock the MVP as asynchronous batch. Sync processing is explicitly out of scope.

---

## 8. Summary

| Aspect | Value |
|---|---|
| Project | Lightship MVP — dashcam video annotation & classification |
| Client / Vendor | Lightship Neuroscience / Commit Software |
| Timeline | Mar 5 – Apr 30, 2026 (3 sprints) |
| Budget | $55K fixed + AWS (TBD) |
| Primary detection model | **Amazon Rekognition** (flexibility to revisit) |
| Fallback detection model | Detectron2 (Apache 2.0) |
| Rejected | YOLOv11 / v12 (AGPL licensing risk) |
| Classification / config gen | Amazon Bedrock — Claude Sonnet 4.5 |
| Orchestration | AWS Step Functions (10-state machine) |
| Compute | ECS Fargate workers + Lambda glue, no GPU |
| Storage | S3 (single bucket, lifecycle-managed) + DynamoDB |
| IaC / Region | CloudFormation nested stacks, us-east-1 |
| UI | Next.js 14 + React + TypeScript, dark theme |
| KPI bar | ≥ 80% across 5 detection / classification metrics + weather robustness |
| Est. monthly cost (50 videos) | ~$145–$190 |
| Top risks | Rekognition recall on motorcycles; lane-detection design; jobsite dataset readiness |
