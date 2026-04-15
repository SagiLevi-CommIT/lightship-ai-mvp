# Lightship MVP — Implementation Status

**Last Updated:** 2026-04-15
**Pipeline:** Rekognition-only (YOLO removed)
**Region:** us-east-1 | **Account:** 336090301206

---

## Deployment

| Component | Status | Details |
|-----------|--------|---------|
| VPC Stack | ✅ Deployed | `lightship-mvp-vpc` |
| App Stack | ✅ Deployed | `lightship-mvp-app` (ALB, ECR, S3, DynamoDB, SQS, SNS, ECS, IAM, KMS) |
| Backend Lambda Stack | ✅ Deployed | `lightship-mvp-backend-lambda` |
| CI/CD Stack | ✅ Deployed | `lightship-mvp-cicd` |
| One-Command Deploy | ✅ Implemented | `./deploy.sh` |

## Pipeline

| Stage | Status | Technology |
|-------|--------|------------|
| Frame Extraction | ✅ | OpenCV |
| Object Detection | ✅ | Amazon Rekognition DetectLabels |
| Hazard Assessment | ✅ | Amazon Bedrock Claude (graceful degradation) |
| Video Classification | ✅ | Amazon Bedrock Claude — 4 types |
| Config Generation | ✅ | detection, decisions, reactions, jobsite formats |
| Frame Annotation | ✅ | OpenCV bounding boxes + labels |
| S3 Persistence | ✅ | `results/{job_id}/config.json`, `detection_summary.json`, `annotated_frames/` |
| DynamoDB Tracking | ✅ | QUEUED → PROCESSING → COMPLETED/FAILED |

## Infrastructure

| Resource | Status | Name |
|----------|--------|------|
| ALB | ✅ Active | `lightship-mvp-alb` |
| Lambda | ✅ Active | `lightship-mvp-backend` (3008 MB, 900s timeout) |
| ECS Cluster | ✅ Active | `lightship-mvp-cluster` |
| ECS Service | ✅ Active | `lightship-mvp-frontend-service` |
| S3 Bucket | ✅ Active | `lightship-mvp-processing-336090301206` |
| DynamoDB | ✅ Active | `lightship_jobs` |
| SQS | ✅ Created | `lightship-mvp-processing-queue` + DLQ |
| SNS | ✅ Created | `lightship-mvp-notifications` |
| KMS | ✅ Active | `alias/lightship-mvp` |
| CloudWatch | ✅ Active | Log groups, alarms, dashboard |

## UI (Streamlit)

| Feature | Status |
|---------|--------|
| Upload single video | ✅ |
| Upload batch videos | ✅ |
| Processing progress | ✅ |
| Results display | ✅ |
| Client config download | ✅ |
| Job history | ✅ |
| API health indicator | ✅ |

## Taxonomy (Client-Aligned)

- **Distance:** `danger_close`, `near`, `mid`, `far`, `very_far` (+ `n/a`)
- **Road Types:** `highway`, `city`, `town`, `rural`
- **Speed:** Road speed limit categories
- **Object Classes:** car, truck, bus, motorcycle, bicycle, pedestrian, cone, barrier, etc.

## Known Gaps

1. **Jobsite config template** — placeholder (client dependency)
2. **Step Functions orchestration** — IAM role ready, state machine deferred
3. **ECS Workers** — architecture ready, not yet needed at MVP volume
4. **Cross-frame tracking** — deferred to Phase 4
5. **Lane detection model** — deferred to Phase 3
6. **WAF, CloudTrail** — deferred to Phase 5
