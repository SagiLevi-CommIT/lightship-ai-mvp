# Lightship MVP — Next.js Frontend

Production frontend for the Lightship dashcam video analysis platform. Replaces the previous Streamlit prototype.

## Stack

- **Next.js 14** (App Router, React Server Components)
- **TypeScript**
- **Tailwind CSS**
- **Standalone output** for minimal Docker images

## Pages

| Route | Purpose |
|-------|---------|
| `/` | Upload page — drag & drop dashcam videos, configure max snapshots and frame strategy |
| `/run` | Processing page — uploads to S3, triggers pipeline, polls status, shows real-time progress |
| `/history` | History page — lists past jobs from DynamoDB, view completed results |

## Backend Integration

The frontend calls the FastAPI backend through the ALB. When `NEXT_PUBLIC_API_BASE` is empty (default), requests go to the same origin. The ALB listener rule routes API paths (`/health`, `/process-video`, `/status/*`, `/results/*`, `/download/*`, `/presign-upload`, `/jobs`, `/cleanup/*`) to the backend Lambda target group.

### API Flow

1. **Upload**: `GET /presign-upload` → S3 presigned PUT → `POST /process-video` (form-encoded with `s3_key`)
2. **Poll**: `GET /status/{job_id}` every 3 seconds
3. **Results**: `GET /download/json/{job_id}` for full pipeline output
4. **History**: `GET /jobs` for job list, then load individual results

## Development

```bash
npm install
npm run dev     # http://localhost:3000
```

## Docker Build

```bash
docker build --build-arg NEXT_PUBLIC_API_BASE="" -t lightship-frontend .
docker run -p 3000:3000 lightship-frontend
```

## Deployment

The frontend runs on ECS Fargate behind the ALB on port 3000. See `infrastructure/frontend-service-stack.yaml` and `infrastructure/deploy.sh`.
