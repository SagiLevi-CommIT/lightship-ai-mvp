# Lightship MVP — Implementation Status

**Last Updated:** 2026-04-15
**Pipeline:** Rekognition-only (YOLO removed)
**Region:** us-east-1

---

## Architecture Overview

The Lightship MVP is a Rekognition-based dashcam video annotation and classification system.

### Pipeline Flow

```
Upload Video → Extract Frames → Rekognition Detection → Hazard Assessment (LLM)
  → Video Classification (LLM) → Client Config JSON Generation → Annotated Frames
  → S3 Persistence → DynamoDB Status Update → SNS Notification (planned)
```

### Core Components

| Component | Status | Technology |
|-----------|--------|------------|
| Object Detection | ✅ Implemented | Amazon Rekognition DetectLabels |
| Depth/Distance | ✅ Implemented | Size-heuristic (bbox/frame ratio) |
| Hazard Assessment | ✅ Implemented | Amazon Bedrock (Claude) |
| Video Classification | ✅ Implemented | Amazon Bedrock (Claude) — 4 types |
| Config Generation | ✅ Implemented | detection, decisions, reactions formats |
| Frame Annotation | ✅ Implemented | OpenCV bounding boxes + labels |
| S3 Persistence | ✅ Implemented | Results uploaded to S3 after processing |
| DynamoDB Tracking | ✅ Implemented | Job status, video_class, road_type |
| Streamlit UI | ✅ Updated | Upload, processing, results, history |
| Jobsite Config | 🔲 Placeholder | Awaiting client config template |
| Step Functions | 🔲 Not started | Architecture planned |
| ECS Workers | 🔲 Not started | Architecture planned |
| SQS/SNS | 🔲 Not started | Architecture planned |
| Cross-frame Tracking | 🔲 Not started | IoU-based matching planned |
| Lane Detection | 🔲 Not started | Dedicated model needed |
| Single Image Mode | 🔲 Not started | API endpoint planned |
| WAF/CloudTrail | 🔲 Not started | Security hardening planned |

---

## Video Classification Types

| Type | Config Format | Status |
|------|---------------|--------|
| `reactivity_braking` | ReactionsConfigOutput | ✅ Implemented |
| `qa_educational` | DecisionsConfigOutput | ✅ Implemented |
| `hazard_detection` | DetectionConfigOutput | ✅ Implemented |
| `job_site_detection` | JobsiteConfigOutput | 🔲 Placeholder (no client template) |

---

## Taxonomy (Interim — Aligned to Client Emails)

### Distance Categories (5)
`danger_close`, `near`, `mid`, `far`, `very_far` (+ `n/a`)

### Road Types
`highway`, `city`, `town`, `rural`

### Speed (Road Speed Limit)
`<15_mph`, `15-25_mph`, `25-40_mph`, `40-55_mph`, `55-70_mph`, `>70_mph`

### Object Classes (GT-aligned)
`car`, `truck`, `bus`, `motorcycle`, `bicycle`, `pedestrian`, `construction_worker`, `cone`, `barrier`, `heavy_equipment`, `construction_sign`, `fencing`, `debris`, `animal`, `other`

### Traffic Signal Labels
`red`, `yellow`, `green`, `flashing_red`, `flashing_yellow`, `off`, `other`

### Sign Labels
`speed_limit`, `stop`, `yield`, `warning`, `construction`, `info`

---

## Config Output Format

Generated configs match the client's application schema:

- **detection_config**: `hazard_x`, `hazard_y`, `hazard_size`, `hazard_desc`, `trial_start_prompt`, `hazard_view_duration`, `road`, `speed`, `traffic`, `collision`, `space`
- **decisions_config**: `questions[]` with Q&A, `trial_start_prompt`
- **reactions_config**: `reaction_time_window`, hazard coordinates, `trial_start_prompt`
- **jobsite_config**: `objects_detected[]`, `hazards[]` (placeholder)

---

## Known Gaps / Client Dependencies

1. **Jobsite config template** — client TODO (blocks full jobsite pipeline)
2. **Distance categories confirmation** — 3 (GT) vs 5 (email) — using 5 per email
3. **Road type taxonomy** — GT uses "urban/city"; code uses "city" per spec
4. **YOLO licensing decision** — resolved by switching to Rekognition
5. **Step Functions orchestration** — deferred to Phase 2
6. **ECS Fargate workers** — deferred to Phase 2
7. **Cross-frame tracking** — deferred to Phase 4
8. **Lane detection model** — deferred to Phase 3
9. **WAF, CloudTrail, VPC Flow Logs** — deferred to Phase 5

---

## Environment Variables

See `.env.example` for the complete list.

Key variables:
- `AWS_REGION` — must be `us-east-1`
- `PROCESSING_BUCKET` — S3 bucket for video input/output
- `RESULTS_BUCKET` — S3 bucket for persisted results
- `DYNAMODB_TABLE` — DynamoDB table name
- `BEDROCK_MODEL_ID` — Bedrock model for classification/hazard assessment
- `BACKEND_API_URL` — Backend URL for frontend (was previously misnamed `BACKEND_URL`)
