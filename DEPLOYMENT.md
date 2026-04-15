# Lightship MVP — Deployment Guide

## Prerequisites

- AWS CLI configured with credentials for account `336090301206`
- Docker installed and running
- The following CloudFormation stacks must exist:
  - `lightship-mvp-vpc` — VPC, subnets, NAT
  - `lightship-mvp-app` — ALB, ECR, S3, DynamoDB, SQS, ECS, IAM

## One-Command Deploy

```bash
./deploy.sh
```

This single script performs all deployment steps:

1. Updates the app stack (ALB listener rules)
2. Logs into ECR
3. Builds and pushes the backend Lambda container image
4. Deploys/updates the backend-lambda CloudFormation stack
5. Updates the Lambda function code and waits for completion
6. Registers the Lambda as an ALB target
7. Builds and pushes the frontend Streamlit container image
8. Registers a new ECS task definition
9. Creates or updates the ECS frontend service
10. Verifies health of both backend and frontend

## Architecture

```
User ─→ ALB ─→ / (default)           ─→ ECS Fargate (Streamlit UI)
              ─→ /health, /process-video,
                 /status/*, /results/*,
                 /download/*, /presign-upload,
                 /jobs, /cleanup/*      ─→ Lambda (FastAPI backend)
```

### Data Flow

```
Upload video ─→ S3 input/videos/{job_id}/
             ─→ Lambda self-invoke (async)
             ─→ Pipeline:
                  Frame extraction (OpenCV)
                  Object detection (Rekognition)
                  Hazard assessment (Bedrock Claude)
                  Video classification (Bedrock Claude)
                  Config JSON generation
                  Frame annotation
             ─→ S3 results/{job_id}/
                  config.json
                  detection_summary.json
                  annotated_frames/
                  {video}_pipeline.json
             ─→ DynamoDB COMPLETED
```

## S3 Bucket Layout

Bucket: `lightship-mvp-processing-336090301206`

| Prefix | Contents | Lifecycle |
|--------|----------|-----------|
| `input/videos/{job_id}/` | Uploaded videos | 60 days |
| `results/{job_id}/config.json` | Client config JSON | 180 days |
| `results/{job_id}/detection_summary.json` | Detection summary | 180 days |
| `results/{job_id}/annotated_frames/` | Annotated frames | 180 days |
| `results/{job_id}/*_pipeline.json` | Internal pipeline output | 180 days |

## DynamoDB Job Lifecycle

Table: `lightship_jobs`

| Status | Trigger |
|--------|---------|
| QUEUED | POST /process-video |
| PROCESSING | Pipeline starts |
| COMPLETED | Pipeline finishes, results in S3 |
| FAILED | Error at any stage |

Fields: `job_id`, `status`, `filename`, `video_class`, `road_type`, `s3_results_uri`, `created_at`, `completed_at`, `error_message`

## Environment Variables (Lambda)

| Variable | Description |
|----------|-------------|
| `BEDROCK_MODEL_ID` | Bedrock model for LLM |
| `PROCESSING_BUCKET` | S3 bucket for input/output |
| `RESULTS_BUCKET` | S3 bucket for results |
| `RESULTS_PREFIX` | S3 prefix for results (default: `results`) |
| `DYNAMODB_TABLE` | DynamoDB job table |
| `REKOGNITION_MIN_CONFIDENCE` | Min confidence for Rekognition |
| `AWS_REGION_NAME` | AWS region |

## Updating

To redeploy after code changes:

```bash
./deploy.sh
```

The script is idempotent — safe to run repeatedly.

## CloudFormation Stacks

| Stack | Template | Resources |
|-------|----------|-----------|
| `lightship-mvp-vpc` | `infrastructure/vpc-stack.yaml` | VPC, subnets, NAT, VPC endpoints |
| `lightship-mvp-app` | `infrastructure/app-stack.yaml` | ALB, ECR, S3, DynamoDB, SQS, SNS, ECS, IAM, KMS, CloudWatch |
| `lightship-mvp-backend-lambda` | `infrastructure/backend-lambda-stack.yaml` | Lambda function |
| `lightship-mvp-cicd` | `cicd/cicd-stack.yaml` | CodeBuild projects |

## Troubleshooting

```bash
# Check Lambda logs
aws logs tail /aws/lambda/lightship-mvp-backend --follow

# Check ECS frontend logs
aws logs tail /ecs/lightship-mvp-frontend --follow

# Check Lambda health
curl http://lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com/health

# Check DynamoDB jobs
aws dynamodb scan --table-name lightship_jobs --max-items 5
```
