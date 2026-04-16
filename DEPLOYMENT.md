# Lightship MVP — Deployment Guide

Target AWS account: **336090301206** (`role-commit-lightship-devops`).
Region: **us-east-1**.  Authorized operator IP: **87.70.177.112/32**.

This guide covers the four deployment phases referenced in the plan:

1. VPC + networking
2. Application stack (IAM, ECR, S3, DynamoDB, SQS, SNS, ALB, KMS, Secrets Manager)
3. Frontend (Next.js on ECS Fargate) via CodeBuild
4. Backend (FastAPI on Lambda container) via CodeBuild

---

## 0. Prerequisites

```bash
aws --version                     # v2
aws sts get-caller-identity       # should show role-commit-lightship-devops
export AWS_REGION=us-east-1
export PROJECT_NAME=lightship
export ENVIRONMENT=mvp
```

## 1. VPC stack

```bash
aws cloudformation deploy \
  --template-file infrastructure/vpc-stack.yaml \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-vpc \
  --parameter-overrides \
      ProjectName=${PROJECT_NAME} Environment=${ENVIRONMENT} \
      VpcCidr=10.145.16.0/20 \
      AvailabilityZone1=us-east-1a AvailabilityZone2=us-east-1b \
  --capabilities CAPABILITY_NAMED_IAM
```

Produces VPC `vpc-mvp-lightship` with six /24 subnets:

| Subnet | CIDR | AZ |
|---|---|---|
| `net-dev-private-az1` | 10.145.16.0/24 | us-east-1a |
| `net-dev-private-az2` | 10.145.17.0/24 | us-east-1b |
| `net-dev-public-az1`  | 10.145.18.0/24 | us-east-1a |
| `net-dev-public-az2`  | 10.145.19.0/24 | us-east-1b |
| `net-dev-data-az1`    | 10.145.20.0/24 | us-east-1a |
| `net-dev-data-az2`    | 10.145.21.0/24 | us-east-1b |

Includes one NAT gateway, shared private route table, S3/DynamoDB
gateway endpoints, and interface endpoints for ECR API/DKR and
CloudWatch Logs.

## 2. App stack

```bash
aws cloudformation deploy \
  --template-file infrastructure/app-stack.yaml \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-app \
  --parameter-overrides \
      ProjectName=${PROJECT_NAME} Environment=${ENVIRONMENT} \
      VPCStackName=${PROJECT_NAME}-${ENVIRONMENT}-vpc \
  --capabilities CAPABILITY_NAMED_IAM
```

Creates:

- ECR repos: `lightship-frontend`, `lightship-backend`.
- S3 buckets (KMS SSE via the `alias/lightship-mvp` CMK):
  - `lightship-mvp-processing-<account>` (video ingest + results)
  - `lightship-mvp-lancedb-<account>`
  - `lightship-mvp-conversations-<account>`
  - External: `s3-lightship-custom-datasources-us-east-1` (referenced by IAM).
- DynamoDB `lightship_jobs` (`job_id` PK, `user_id`+`created_at` GSI).
- SQS `lightship-mvp-processing-queue` + DLQ `lightship-mvp-processing-dlq`.
- SNS topic `lightship-mvp-notifications` + alarms on DLQ depth, ALB 5xx, ECS CPU.
- ALB `lightship-mvp-alb` (internet-facing, authorized IP only) with listener
  rules for core + upload + image/client-config routes.
- IAM roles: `lightship-mvp-ecs-execution-role`, `lightship-mvp-ecs-task-role`,
  `lightship-mvp-lambda-role`, `lightship-mvp-sfn-execution-role`.
- Secrets Manager `lightship/mvp/config` (detection thresholds, Bedrock model ID).
- CloudWatch log groups: `/ecs/lightship-mvp-frontend`, `/aws/lambda/lightship-mvp-backend`,
  `/ecs/lightship-mvp-worker`, `/aws/states/lightship-mvp`.
- CloudWatch dashboard `lightship-mvp-dashboard`.

## 3. CI/CD (CodeCommit + CodeBuild)

```bash
aws cloudformation deploy \
  --template-file cicd/cicd-stack.yaml \
  --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-cicd \
  --capabilities CAPABILITY_NAMED_IAM
```

Creates the CodeCommit repo `lightship-ai-mvp` and two CodeBuild
projects — `lightship-mvp-frontend` (uses `ui-fe/buildspec.yml`) and
`lightship-mvp-backend` (uses `lambda-be/buildspec.yml`).

Push code to the CodeCommit remote:

```bash
git remote add codecommit \
  https://git-codecommit.us-east-1.amazonaws.com/v1/repos/lightship-ai-mvp
git push codecommit HEAD:refs/heads/main
```

## 4. Frontend build + deploy

```bash
aws codebuild start-build --project-name lightship-mvp-frontend
```

The buildspec:

1. Reads stack outputs (target group ARN, subnets, security groups, roles, ALB DNS).
2. Builds `ui-fe/Dockerfile` (Next.js standalone, port 3000).
3. Pushes `latest` + commit SHA tags to ECR.
4. Registers a new ECS task definition and creates or updates
   `lightship-mvp-frontend-svc` in `lightship-mvp-cluster`.

Verify:

```bash
aws ecs describe-services --cluster lightship-mvp-cluster \
  --services lightship-mvp-frontend-svc --query 'services[0].{R:runningCount,D:desiredCount}'
curl -s http://<alb-dns>/ | head -20
```

## 5. Backend build + deploy

```bash
aws codebuild start-build --project-name lightship-mvp-backend
```

The buildspec:

1. Reads stack outputs (bucket names, Lambda role ARN, backend TG ARN).
2. Builds `lambda-be/Dockerfile` (AWS Lambda Python 3.11 base).
3. Pushes to ECR in Docker manifest format (required for Lambda).
4. Creates or updates the `lightship-mvp-backend` Lambda (memory=3008,
   timeout=900s) and attaches it to the ALB backend target group.

Verify:

```bash
curl -s http://<alb-dns>/health
aws logs tail /aws/lambda/lightship-mvp-backend --since 10m
```

## 6. End-to-end smoke

```bash
pytest tests/ -v                  # infra + API + DynamoDB + S3 + CloudWatch
```

Or manually via the ALB URL — upload a dashcam video, watch `/status/{id}`
transition `QUEUED → PROCESSING → COMPLETED`, then check:

- `GET /download/json/{id}` — core output JSON
- `GET /client-configs/{id}` — four client config families
- `/ecs/lightship-mvp-frontend` and `/aws/lambda/lightship-mvp-backend` logs

## 7. Updates

Re-run the frontend or backend CodeBuild project:

```bash
aws codebuild start-build --project-name lightship-mvp-frontend
aws codebuild start-build --project-name lightship-mvp-backend
```

Both projects are idempotent — they create or update the Lambda / ECS
service based on whether it already exists.
