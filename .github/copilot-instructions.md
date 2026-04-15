# Lightship MVP — Copilot Instructions

AI coding assistant guide for the Lightship dashcam video analysis system.

## Architecture Overview

AWS-based dashcam video processing with Rekognition and Bedrock Claude:

```
Internet → ALB → {
  /           → ECS Fargate (Streamlit UI, ui-fe/)
  /api/*      → Lambda container (FastAPI backend, lambda-be/)
}
Backend → Amazon Rekognition + AWS Bedrock Claude
```

**Key components:**
- **Backend (`lambda-be/src/`):** FastAPI server with Rekognition pipeline
- **Frontend (`ui-fe/src/`):** Streamlit UI for upload, progress, results
- **Pipeline:** Rekognition detection → Bedrock hazard assessment → Bedrock classification → Config generation
- **Detection:** Amazon Rekognition DetectLabels (managed, no AGPL risk)
- **LLM:** AWS Bedrock Claude for classification and hazard assessment

## Pipeline Stages

1. `video_loader.py` — Extract video metadata
2. `snapshot_selector.py` — Select keyframes
3. `frame_extractor.py` — Extract frames as images
4. `rekognition_labeler.py` — Amazon Rekognition object detection
5. `hazard_assessor.py` — LLM-based temporal hazard assessment
6. `video_classifier.py` — Classify video into 4 training types
7. `config_generator.py` — Generate client-format config JSON
8. `frame_annotator.py` — Annotate frames with bounding boxes
9. `merger.py` — Save pipeline + client config JSON

## Video Types & Config Formats

| Type | Config Class |
|------|-------------|
| reactivity_braking | ReactionsConfigOutput |
| qa_educational | DecisionsConfigOutput |
| hazard_detection | DetectionConfigOutput |
| job_site_detection | JobsiteConfigOutput (placeholder) |

## Configuration

All settings in `lambda-be/src/config.py`:
- `AWS_REGION` = us-east-1
- Distance taxonomy: danger_close, near, mid, far, very_far (+ n/a)
- Road types: highway, city, town, rural
- Speed: road speed limit categories

## Conventions

- **Never hardcode AWS credentials** — use IAM roles
- **S3 bucket from env var** `PROCESSING_BUCKET` — never hardcode
- **Frontend env var** `BACKEND_API_URL` (not `BACKEND_URL`)
- Use `logger.info()` / `.warning()` / `.error()` for logging
- All outputs persisted to S3 under `results/{job_id}/`
