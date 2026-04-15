# Lightship MVP — Dashcam Video Annotation & Classification

Automated dashcam video annotation and classification system for Lightship Neuroscience.
Uses Amazon Rekognition for object detection and Bedrock Claude for video classification
and hazard assessment. Generates client-ready config JSONs for 4 training types.

## Quick Start

### Deploy to AWS

```bash
./deploy.sh
```

One command deploys everything: Lambda backend, ECS frontend, ALB routing.

### Access

- **Frontend:** `http://lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com/`
- **API:** `http://lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com/health`

## Architecture

```
Internet ──→ ALB
              ├── / (default)          ──→ ECS Fargate (Streamlit UI)
              └── /health, /process-video,
                  /status/*, /results/*,
                  /download/*, /presign-upload,
                  /jobs                 ──→ Lambda (FastAPI + Mangum)

Lambda Pipeline:
  1. Frame Extraction (OpenCV)
  2. Object Detection (Amazon Rekognition)
  3. Hazard Assessment (Bedrock Claude)
  4. Video Classification (Bedrock Claude → 4 types)
  5. Config JSON Generation (client format)
  6. Frame Annotation (OpenCV)
  7. S3 Persistence + DynamoDB Tracking
```

## Project Structure

```
├── lambda-be/               # Backend (Lambda container)
│   ├── src/
│   │   ├── api_server.py          # FastAPI REST API
│   │   ├── lambda_function.py     # Lambda entry point (API + worker)
│   │   ├── pipeline.py            # Pipeline orchestrator
│   │   ├── rekognition_labeler.py # Rekognition DetectLabels
│   │   ├── video_classifier.py    # Bedrock video classification
│   │   ├── config_generator.py    # Client config JSON generator
│   │   ├── hazard_assessor.py     # Bedrock hazard assessment
│   │   ├── frame_extractor.py     # Frame extraction (OpenCV)
│   │   ├── frame_annotator.py     # Bounding box annotation
│   │   ├── merger.py              # Output merging + detection_summary
│   │   ├── schemas.py             # Pydantic models
│   │   └── config.py              # Configuration
│   ├── Dockerfile
│   └── requirements.txt
├── ui-fe/                   # Frontend (Streamlit ECS)
│   ├── src/
│   │   ├── streamlit_app.py
│   │   ├── api_client.py
│   │   └── visualization.py
│   └── Dockerfile
├── infrastructure/          # CloudFormation IaC
│   ├── vpc-stack.yaml
│   ├── app-stack.yaml
│   ├── backend-lambda-stack.yaml
│   └── frontend-service-stack.yaml
├── deploy.sh                # One-command deploy script
├── tests/                   # Integration tests
└── cicd/                    # CI/CD (CodeBuild)
```

## Video Classification Types

| Type | Config Format | Description |
|------|--------------|-------------|
| `reactivity_braking` | Reactions config | Quick driver reaction required |
| `qa_educational` | Decisions config | Educational Q&A scenario |
| `hazard_detection` | Detection config | Hazard monitoring scenario |
| `job_site_detection` | Jobsite config | Construction site (placeholder) |

## S3 Output Layout

```
results/{job_id}/
  ├── config.json              # Client-format config
  ├── detection_summary.json   # Detection statistics
  ├── annotated_frames/        # Annotated frame images
  └── *_pipeline.json          # Full pipeline output
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/jobs` | List recent jobs |
| GET | `/presign-upload?filename=...` | Get S3 presigned upload URL |
| POST | `/process-video` | Start video processing |
| GET | `/status/{job_id}` | Get job status |
| GET | `/results/{job_id}` | Get job results |
| GET | `/download/json/{job_id}` | Download output JSON |

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for full deployment guide.

## Implementation Status

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for current status.

---

**Account:** 336090301206 | **Region:** us-east-1 | **Timeline:** Mar 5 – Apr 30, 2026
