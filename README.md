# Lightship MVP — Dashcam Video Analysis Platform

Production-ready AWS MVP for dashcam video ingestion, frame selection,
object / hazard detection, video classification and client-ready config
generation.  Implements the phased plan in
[`LIGHTSHIP_MVP_EXECUTION_PLAN.md`](LIGHTSHIP_MVP_EXECUTION_PLAN.md).

---

## 1. High-level architecture

```
Internet (authorized IP allow-list)
        │
        ▼
  Application Load Balancer (internet-facing, HTTP 80)
        │
        ├─── path: /                               → ECS Fargate  (Next.js UI, port 3000)
        │                                              └── /image, /run, /history pages
        │
        └─── path: /health, /process-video, /results/*, /status/*, /download/*,
             /presign-upload, /jobs, /cleanup/*, /frames/*, /pipeline-result/*,
             /process-image, /client-configs/*     → Lambda (FastAPI / container image)

Backend pipeline:
  Lambda API → DynamoDB (job tracking)
             → S3 presigned PUT (video upload, bypasses 1 MB ALB limit)
             → self-invoke async Lambda worker
                  └── Frame extraction + preprocessing (CLAHE / unsharp)
                  └── Smart frame selection (HOG+PCA+KMeans clustering)
                  └── CV detection (YOLO11 + optional Depth-Anything)
                  └── Rekognition-primary label enrichment (general objects)
                  └── Per-frame LLM refinement (Bedrock Claude Sonnet 4)
                  └── Temporal hazard assessment (Bedrock)
                  └── Output JSON + client-config families (reactivity /
                      educational / hazard / jobsite)
```

Region: **us-east-1**. VPC CIDR: **10.145.16.0/20**. Naming follows
[`AWS-NAMING-CONVENTION.md`](AWS-NAMING-CONVENTION.md).

---

## 2. Repository layout

```
├── infrastructure/               # CloudFormation — deploy in order:
│   ├── vpc-stack.yaml            #   1) VPC, subnets, NAT, VPC endpoints, flow logs
│   ├── app-stack.yaml            #   2) IAM, ECR, S3, DynamoDB, SQS, SNS, ALB, KMS
│   ├── frontend-service-stack.yaml  # 3) ECS task + service for UI (optional)
│   ├── backend-lambda-stack.yaml    # 4) Lambda container function (optional)
│   └── deploy.sh                 # One-shot deployment helper
│
├── cicd/
│   └── cicd-stack.yaml           # CodeCommit + CodeBuild (frontend + backend)
│
├── ui-fe/                        # Next.js 14 app (Streamlit was replaced)
│   ├── src/app/                  # /, /image, /run, /history routes
│   ├── src/components/           # nav + evaluation/results views
│   ├── src/lib/api-client.ts     # Typed ALB client
│   ├── Dockerfile                # Standalone Next.js image on port 3000
│   └── buildspec.yml             # CodeBuild → ECR → ECS
│
├── lambda-be/                    # FastAPI Lambda container
│   ├── src/
│   │   ├── api_server.py         # REST API (+/process-image, +/client-configs/{id})
│   │   ├── pipeline.py           # V3 orchestrator (also writes client configs)
│   │   ├── config_generator.py   # 4 client-config families (Phase 4)
│   │   ├── evaluation_harness.py # Canonical KPI harness (Phase 5)
│   │   ├── frame_selector.py     # HOG+PCA+KMeans diversity selection
│   │   ├── frame_preprocessor.py # CLAHE + unsharp-mask profile
│   │   ├── cv_labeler.py, frame_refiner.py, hazard_assessor.py, merger.py …
│   │   └── schemas.py
│   ├── Dockerfile                # Lambda Python 3.11 base image
│   └── buildspec.yml             # CodeBuild → ECR → Lambda update
│
├── tests/                        # pytest suite (infra, API, DynamoDB, S3, E2E)
└── docs_data_mterials/           # project brief, kickoff deck, GT samples
```

---

## 3. API contract (frozen)

All routes are served behind the ALB at the URL exposed by the
`lightship-mvp-alb` stack output.

| Method | Path                           | Purpose                                          |
|--------|--------------------------------|--------------------------------------------------|
| GET    | `/health`                      | Liveness check                                   |
| GET    | `/jobs?limit=50`               | List recent jobs from DynamoDB                   |
| GET    | `/presign-upload?filename=…`   | Returns `presign_url`, `s3_key`, required headers|
| POST   | `/process-video`               | Form data (`s3_key` or `video`, `config`)        |
| POST   | `/process-image`               | Synchronous single-image detection               |
| GET    | `/status/{job_id}`             | Job status + progress                            |
| GET    | `/results/{job_id}`            | Full in-memory summary                           |
| GET    | `/download/json/{job_id}`      | Download core output JSON                        |
| GET    | `/download/frame/{job_id}/{n}` | Download annotated frame PNG                     |
| GET    | `/client-configs/{job_id}`     | Four client config families                      |
| DELETE | `/cleanup/{job_id}`            | Release temp artifacts                           |

The presigned upload flow is required in production because the ALB →
Lambda integration has a hard 1 MB payload cap — video uploads go
directly to S3.

---

## 4. Deploying from scratch

Prerequisites: AWS credentials with the role
`arn:aws:iam::<account>:role/role-commit-lightship-devops`, AWS CLI v2,
and Docker (for local builds; CodeBuild handles the cloud path).

```bash
export AWS_REGION=us-east-1
export PROJECT_NAME=lightship
export ENVIRONMENT=mvp

# 1) Networking
aws cloudformation deploy \
  --template-file infrastructure/vpc-stack.yaml \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-vpc \
  --capabilities CAPABILITY_NAMED_IAM

# 2) App (ECR, S3, DynamoDB, SQS, SNS, ALB, KMS, IAM)
aws cloudformation deploy \
  --template-file infrastructure/app-stack.yaml \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-app \
  --parameter-overrides VPCStackName=${PROJECT_NAME}-${ENVIRONMENT}-vpc \
  --capabilities CAPABILITY_NAMED_IAM

# 3) CI/CD (CodeCommit + CodeBuild, frontend and backend)
aws cloudformation deploy \
  --template-file cicd/cicd-stack.yaml \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-cicd \
  --capabilities CAPABILITY_NAMED_IAM

# 4) Trigger initial backend + frontend builds
aws codebuild start-build --project-name lightship-mvp-backend
aws codebuild start-build --project-name lightship-mvp-frontend
```

The backend buildspec creates or updates the Lambda function and
registers it with the ALB target group on first run.  The frontend
buildspec creates or updates the ECS service in `lightship-mvp-cluster`.

---

## 5. Local development

```bash
# Backend
cd lambda-be
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api_server:app --host 0.0.0.0 --port 8000 --reload

# Frontend
cd ui-fe
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```

---

## 6. Evaluation harness

A canonical, dependency-light Phase-5 harness lives at
`lambda-be/src/evaluation_harness.py`:

```bash
python -m src.evaluation_harness \
  --predictions  output/enhanced_format \
  --ground-truth docs_data_mterials/data/driving \
  --report       output/reports/mvp_baseline.json \
  --markdown     output/reports/mvp_baseline.md
```

Outputs per-category precision / recall / F1 plus weather / lighting /
traffic classification accuracy, matched against the plan's KPIs.

---

## 7. Client-config generation

`lambda-be/src/config_generator.py` produces the four client families:

- `reactivity`  — braking / reaction / collision-avoidance clips
- `educational` — Q&A scaffold for training footage
- `hazard`      — hazard-annotated clips for model review
- `jobsite`     — construction / job-site detections

These files are written alongside the main output JSON in
`output/<video>.<family>.json` and exposed through
`GET /client-configs/{job_id}`.

---

## 8. Operational notes

- IAM uses the `lightship-mvp-lambda-role` and `lightship-mvp-ecs-task-role`
  least-privilege roles created by the app stack.
- All S3 buckets enforce KMS SSE via the `lightship-mvp` CMK.
- ALB is internet-facing but gated on the authorized IP `87.70.177.112/32`.
- CloudWatch dashboard: `lightship-mvp-dashboard`. Alarms on ALB 5xx,
  DLQ depth and ECS CPU feed the `lightship-mvp-notifications` SNS topic.
- Logs:
  - Lambda: `/aws/lambda/lightship-mvp-backend`
  - Frontend ECS: `/ecs/lightship-mvp-frontend`
  - VPC flow logs: `/aws/vpc/flowlogs/lightship-mvp`

For the deeper execution plan, KPI targets and open decisions, see
[`LIGHTSHIP_MVP_EXECUTION_PLAN.md`](LIGHTSHIP_MVP_EXECUTION_PLAN.md).
