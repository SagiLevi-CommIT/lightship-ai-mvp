# Lightship MVP — Dashcam Video Annotation & Classification

Automated dashcam video annotation and classification system for Lightship Neuroscience.
Ingests fleet dashcam video, detects road objects via Amazon Rekognition, classifies videos
into 4 training types via Bedrock Claude, and generates client-ready JSON configs.

## Architecture

```
User → Streamlit UI (ECS Fargate)
         ↓
       ALB → Lambda (FastAPI backend)
         ↓
       Pipeline:
         1. Frame Extraction (OpenCV)
         2. Object Detection (Amazon Rekognition)
         3. Hazard Assessment (Bedrock Claude)
         4. Video Classification (Bedrock Claude)
         5. Config JSON Generation
         6. Frame Annotation (OpenCV)
         7. S3 Persistence + DynamoDB Tracking
```

## Project Structure

```
├── lambda-be/               # Backend (Lambda container)
│   ├── src/
│   │   ├── api_server.py          # FastAPI REST API
│   │   ├── lambda_function.py     # Lambda entry point
│   │   ├── pipeline.py            # Pipeline orchestrator
│   │   ├── rekognition_labeler.py # Rekognition detection
│   │   ├── video_classifier.py    # Bedrock video classification
│   │   ├── config_generator.py    # Client config JSON generator
│   │   ├── hazard_assessor.py     # Bedrock hazard assessment
│   │   ├── frame_extractor.py     # Frame extraction
│   │   ├── frame_annotator.py     # Bounding box annotation
│   │   ├── merger.py              # Output merging
│   │   ├── video_loader.py        # Video metadata
│   │   ├── snapshot_selector.py   # Frame selection
│   │   ├── schemas.py             # Pydantic models
│   │   └── config.py              # Configuration
│   ├── Dockerfile
│   └── requirements.txt
├── ui-fe/                   # Frontend (Streamlit)
│   ├── src/
│   │   ├── streamlit_app.py       # Main Streamlit app
│   │   ├── api_client.py          # Backend API client
│   │   └── visualization.py       # Frame visualization
│   ├── Dockerfile
│   └── requirements.txt
├── infrastructure/          # CloudFormation IaC
│   ├── vpc-stack.yaml
│   ├── app-stack.yaml
│   ├── frontend-service-stack.yaml
│   ├── backend-lambda-stack.yaml
│   └── deploy.sh
├── tests/                   # Integration tests
└── cicd/                    # CI/CD (CodeBuild)
```

## Quick Start

### Prerequisites
- Python 3.11+
- AWS account with Rekognition, Bedrock, S3, DynamoDB access
- AWS CLI configured with appropriate IAM role

### Local Development

```bash
# Clone and install
cd lambda-be
pip install -r requirements.txt

# Set environment
cp ../.env.example .env
# Edit .env with your AWS settings

# Run backend
python -m src.api_server

# In another terminal, run frontend
cd ../ui-fe
pip install -r requirements.txt
streamlit run src/streamlit_app.py
```

### Environment Variables

See `.env.example` for all settings. Key variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `AWS_REGION` | AWS region | `us-east-1` |
| `PROCESSING_BUCKET` | S3 bucket for videos/results | (required) |
| `DYNAMODB_TABLE` | DynamoDB job table | `lightship_jobs` |
| `BEDROCK_MODEL_ID` | Bedrock model for LLM | Claude Sonnet 4 |
| `BACKEND_API_URL` | Backend URL for frontend | `http://localhost:8000` |

## Pipeline Output

The system generates per-video config JSONs in the client's application format:

- **Detection config** (`hazard_detection`): hazard coordinates, descriptions, risk levels
- **Decisions config** (`qa_educational`): Q&A questions with answer options
- **Reactions config** (`reactivity_braking`): reaction time windows, hazard positions
- **Jobsite config** (`job_site_detection`): placeholder (awaiting client template)

## Deployment

See `DEPLOYMENT.md` for CloudFormation deployment instructions.

## Status

See `IMPLEMENTATION_STATUS.md` for current implementation status and known gaps.

---

**Timeline:** Mar 5 – Apr 30, 2026 | **Region:** us-east-1
