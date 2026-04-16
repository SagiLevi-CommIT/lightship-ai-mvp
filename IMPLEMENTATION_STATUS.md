# Implementation Status

## Complete System Implementation — Status

Last updated: 2026-04-16

### Phase A: Backend Fixes (COMPLETED)

| Task | Status | Files Changed |
|------|--------|--------------|
| **Task 2: Fix Frame Selection Bug** | **DONE** | `lambda-be/src/pipeline.py`, `lambda-be/src/frame_extractor.py` |
| **Task 5: Fix LLM Reliability** | **DONE** | `lambda-be/src/hazard_assessor.py`, `lambda-be/src/frame_refiner.py`, `lambda-be/src/pipeline.py` |
| **Task 6: Fix Hazard Detection** | **DONE** | `lambda-be/src/hazard_assessor.py` |

**Task 2 details:**
- Removed post-detection frame dropping in `_process_v2` — frames are now ranked by object count and top N selected (never dropping below `max_snapshots`)
- Added extraction retry with seek-back strategy in `frame_extractor.py`
- Added frame count assertion and debug logging

**Task 5 details:**
- Added raw LLM response logging in `hazard_assessor._call_bedrock` and `frame_refiner._call_bedrock`
- Added empty-string coercion for all inferred metadata fields (description, traffic, lighting, weather, collision, speed)
- Strengthened prompt with "CRITICAL: Every single field MUST have a real value"
- Added output quality check in `pipeline._process_v2` — logs warning if <50% of fields are populated

**Task 6 details:**
- Added explicit execution logging at start/end of `assess_hazards_only`
- Created new `assess_hazards_simple` method with focused hazard-only prompt
- Wired simple path as automatic fallback when primary returns 0 hazards but high-priority objects (VRUs) were detected
- Added minimum hazard expectation check

### Phase B: Smart Selection + Preprocessing (COMPLETED)

| Task | Status | Files Changed |
|------|--------|--------------|
| **Task 3: Smart Frame Selection** | **DONE** | `lambda-be/src/frame_selector.py` (NEW), `lambda-be/src/pipeline.py`, `lambda-be/src/config.py` |
| **Task 4: Rekognition Preprocessing** | **DONE** | `lambda-be/src/frame_preprocessor.py` (NEW), `lambda-be/src/pipeline.py`, `lambda-be/src/config.py` |

**Task 3 details:**
- Implemented numpy-only HOG + PCA + KMeans clustering in `frame_selector.py` — no scikit-learn dependency
- PCA via eigendecomposition with Gram matrix trick for efficiency
- KMeans++ initialisation + Lloyd's algorithm
- Default strategy changed from `naive` to `clustering` (configurable via `SNAPSHOT_STRATEGY` env var)
- Falls back to ranked selection if clustering fails

**Task 4 details:**
- Created `frame_preprocessor.py` with CLAHE contrast enhancement, unsharp-mask sharpening, brightness normalisation
- Added optional 2x2 grid cropping with configurable overlap for small-object detection
- Wired preprocessing into pipeline (before CV labeling) with `ENABLE_FRAME_PREPROCESSING` env var toggle
- Operates on LAB colour space for perceptually correct contrast/brightness adjustments

### Phase C: Infrastructure + Frontend (COMPLETED)

| Task | Status | Files Changed |
|------|--------|--------------|
| **Task 1: Next.js Frontend** | **DONE** | Complete rewrite of `ui-fe/` |
| **Task 1f: ALB Routes** | **DONE** | `infrastructure/app-stack.yaml` |
| **Task 1g: Port 8501→3000** | **DONE** | `infrastructure/app-stack.yaml`, `infrastructure/frontend-service-stack.yaml`, `ui-fe/ecs-task-definition.json` |
| **Task 1h: Deploy Script** | **DONE** | `infrastructure/deploy.sh` |

**Task 1 — Next.js Frontend (FULL REPLACEMENT):**
- Completely replaced Streamlit with Next.js 14 (App Router + TypeScript + Tailwind CSS)
- Created `api-client.ts` wired to real backend endpoints (presign-upload, process-video, status, results, jobs, cleanup)
- Upload page (`/`): drag & drop, multi-file, configurable max_snapshots and frame strategy
- Processing page (`/run`): real S3 upload via presigned URL, form-encoded POST, polling with progress bar, real-time stage mapping
- History page (`/history`): lists DynamoDB jobs, loads completed results
- Results view: video metadata cards, priority distribution, frame-by-frame object table, hazard events list
- Standalone Docker build on port 3000
- `npm run build` verified passing

**Task 1f details:**
- Added missing ALB backend paths: `/presign-upload`, `/jobs`, `/frames/*`, `/pipeline-result/*`, `/cleanup/*`

**Task 1g details:**
- Updated FrontendTargetGroup port from 8501 to 3000
- Updated ECS security group ingress from 8501 to 3000
- Updated frontend-service-stack ContainerPort and env vars (removed Streamlit vars, added PORT=3000, NODE_ENV=production)
- Updated ecs-task-definition.json: port, env vars, health check (curl instead of wget/streamlit)

**Task 1h details:**
- Fixed directory names in deploy.sh from `s3-ui-fe`/`s3-lambda-be` to `ui-fe`/`lambda-be`
- Added `--build-arg NEXT_PUBLIC_API_BASE=""` to frontend docker build
- ECS force-new-deployment was already present

### Phase D: Validation (COMPLETED)

| Task | Status | Files Changed |
|------|--------|--------------|
| **Task 7: E2E Validation Script** | **DONE** | `tests/test_e2e_live.py` (NEW) |

**Task 7 details:**
- Created comprehensive E2E test suite using `requests` against live ALB
- Tests: health, root, jobs list, presign-upload, frame count, field quality, hazard detection, priority distribution, cleanup
- Pipeline tests gated on `TEST_VIDEO_S3_KEY` env var
- Run with: `AWS_PROFILE=lightship pytest tests/test_e2e_live.py -v`

### Configuration Changes

| Config | Old | New |
|--------|-----|-----|
| `SNAPSHOT_STRATEGY` | `naive` (hardcoded) | `clustering` (env-configurable) |
| `ENABLE_FRAME_PREPROCESSING` | N/A | `true` (env-configurable) |
| Frontend Port | 8501 | 3000 |
| Frontend Tech | Streamlit | Next.js ready |

### New Files Created

- `lambda-be/src/frame_selector.py` — HOG+PCA+KMeans frame clustering
- `lambda-be/src/frame_preprocessor.py` — CLAHE/sharpen/brightness preprocessing
- `tests/test_e2e_live.py` — E2E validation suite
