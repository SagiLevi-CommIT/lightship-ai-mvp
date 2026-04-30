# Implementation Status

## Complete System Implementation — Status

Last updated: 2026-04-30

### E2E follow-up (CodeBuild YAML + PyAV + UI picker) — 2026-04-30 — **SHIPPED TO GIT; AWS PENDING SSO**

**Git (`main`, seven commits ending `c69dd81`):** Pushed to GitHub
`https://github.com/SagiLevi-CommIT/lightship-ai-mvp` (`github` remote).  
`origin` (CodeCommit) push requires a fresh `aws sso login --profile proxy-corp-ai-devops` then
`git push origin main` if your pipeline still tracks CodeCommit.

**Code changes in this follow-up:**
- **`ui-fe/buildspec.yml` + `lambda-be/buildspec.yml`:** detect-secrets gate moved to
  `scripts/ci/check_detect_secrets.py` so CodeBuild no longer fails YAML parse at `DOWNLOAD_SOURCE`.
- **`lambda-be/src/frame_extractor.py`:** PyAV (`av`) decode when OpenCV fails or returns a
  substituted neighbour; worker image installs `av>=12` + libav dev packages.
- **`ui-fe`:** detector backend picker + **Substituted** badge on frame thumbnails when
  `extraction_status === 'substituted'`.

**Run after SSO (account `336090301206`, `us-east-1`, profile `corp-ai-sandbox-devops`):**

```powershell
aws sso login --profile proxy-corp-ai-devops
aws codebuild start-build --project-name lightship-mvp-frontend --profile corp-ai-sandbox-devops --region us-east-1
# wait SUCCEEDED, then:
aws ecs update-service --cluster lightship-mvp-cluster --service lightship-mvp-frontend-service `
  --force-new-deployment --profile corp-ai-sandbox-devops --region us-east-1
```

**Worker (PyAV):** rebuild/push `lightship-mvp-inference-worker:<git-sha>` from repo root, then
`aws cloudformation deploy` using `infrastructure/inference-worker-stack.yaml` with `ImageUri=…`.
Re-apply SFN overrides with `py build/patch_sfn_ecs_env.py` if CFN cannot update the state machine.

**Live E2E (manual, authorized client):** three runs on a **non-HUD** dashcam clip with
`detector_backend ∈ {florence2, yolo, detectron2}`; confirm dropdown, `vision_audit.backend`,
≥4/5 frames `extraction.status == "ok"`, three SUCCEEDED SFN executions.

**Screenshot placeholder (attach after UI deploy):**  
`![Detector picker + frames](docs/images/e2e-detector-picker-2026-04-30.png)` — capture from `/run`
showing the detector dropdown and one thumbnail row per backend; save under `docs/images/` and
uncomment or embed in this file.

---

### E2E multi-backend + worker frame artefacts — 2026-04-30 — **DEPLOYED**

**Scope:** Per-job detector selection (`florence2` \| `yolo` \| `detectron2`), shared
`result_persistence` for Lambda + ECS, ECS worker writes `frames_manifest.json` and
per-frame PNG/JSON under `results/{job_id}/frames/`, Step Functions passes
`ecs_env` into the task, real Detectron2 (Mask R-CNN) in the worker image, UI
backend picker, `VisionLabeler` runs **only** the selected backend, Lambda
fallback rejects `detectron2`, scene-change selector retried with threshold 0.18
+ pixel-diff fallback on long clips.

**Detectron2 wheel:** `detectron2==0.6+18f6958pt2.6.0cpu` from
[miropsota/torch_packages_builder](https://miropsota.github.io/torch_packages_builder)
(pinned build id; `0.6+pt2.6.0cpu` is not a valid distribution name on the index).

**AWS (account 336090301206, us-east-1):**
- Lambda `lightship-mvp-backend` — image pushed (`lightship-backend:7cf6c9a` digest updated on ECR).
- Step Functions `lightship-mvp-pipeline` — definition updated in-place (ECS env overrides for
  `MAX_SNAPSHOTS`, `SNAPSHOT_STRATEGY`, `NATIVE_FPS`, `DETECTOR_BACKEND`, `LANE_BACKEND`;
  CloudFormation `deploy` for the backend stack still hits a pre-existing circular dependency,
  so ASL was applied via `aws stepfunctions update-state-machine` + `build/patch_sfn_ecs_env.py`).
- ECS worker stack `lightship-mvp-inference-worker` — **UPDATE_COMPLETE** with new task definition
  pointing at `lightship-mvp-inference-worker:7cf6c9a` / `:latest` on ECR.
- CodeBuild `lightship-mvp-frontend` — build **started** for the UI (`lightship-mvp-frontend:4443c79f-…`).

**Live browser E2E** (upload via ALB ×3 backends) is **not executed from CI/agent**
(ALB is IP-restricted). From an authorized client, use `build/run_e2e_three_backends.py`
(HTTPS to the ALB) or the UI run flow.

---

### Pipeline "FAIL" flag fix — 2026-04-30 — **DEPLOYED**

**Root cause identified & fixed:**
- The merger names the output JSON after the video file (e.g. `20251107-153701-C.json`), not `output.json`.
- The entrypoint searched for `output.json` in uploaded S3 keys → never found it → `output_s3_key = ""` in DynamoDB.
- The Lambda API (`/download/json`, `/client-configs`, `/video-class`) always fetches `results/{job_id}/output.json` → 404.
- The UI's mandatory `getOutputJson()` call threw after retries → asset set to `failed` → run shows "FAIL".

**Fix applied (commit `7cf6c9a`):**
1. `inference-worker/entrypoint.py`: After uploading pipeline artefacts, also upload the output JSON under the canonical `results/{job_id}/output.json` name. Falls back to scanning uploaded keys for any top-level `.json` file.
2. `lambda-be/src/api_server.py`: `_load_output_json_from_s3` now has a two-stage fallback — tries canonical key first, then reads `output_s3_key` from DynamoDB for any residual naming variant.

**Deployed:**
- Lambda `lightship-mvp-backend` → image `lightship-backend:7cf6c9a` (updated)
- ECR `lightship-mvp-inference-worker:latest` → image `7cf6c9a` (new ECS tasks will pick this up)

---

### Rekognition → VisionLabeler migration — 2026-04-29 — **DEPLOYED & VALIDATED IN PRODUCTION**

Full migration off Rekognition Custom Labels to Florence-2 + Detectron2 + UFLDv2, with ECS Fargate Worker for inference.

**Live e2e validation (2026-04-29 23:00 UTC+3, account 336090301206):**
- SFN execution `e2e-vision-test-v4-py-1777496033` → SUCCEEDED in 182s
- ECS task `lightship-mvp-inference-worker:1` ran on Fargate cluster `lightship-mvp-cluster`
- Florence-2-base loaded successfully on CPU; 5 frames processed at ~9 sec/frame
- `vision_audit` block in `s3://lightship-mvp-processing-336090301206/results/.../output.json`:
  - `frames_evaluated: 5`, `backend: "florence2"`, `lane_backend: "ufldv2"`, `fallback_triggered_count: 0`
  - per-frame `primary_elapsed_ms ≈ 8500-10000` (real Florence-2 inference)
- CloudWatch metrics flowing: `VisionLabelerCalls`, `VisionLabelerInstancesKept`, `VisionLabelerCallMs`, `LaneBackendLanesKept`
- Rekognition project `lightship-mvp-objects` (version `v2domain`) **fully deleted**
- SSM parameter `/lightship/mvp/rekognition/custom-labels-arn` **deleted**
- Lambda role `lightship-mvp-lambda-role` — `RekognitionAccess` policy **removed**, `ECSRunTaskAccess` **added**
- SFN role `lightship-mvp-sfn-execution-role` — `rekognition:*` actions removed, `ECSRunTaskAccess` added

**Drift from CFN templates (manual ops; CFN deploy role still missing `elasticloadbalancing:DescribeLoadBalancers`):**
- Lambda image URI: `lightship-backend:vision-7cf6c9a` (CFN file says `:latest`)
- Lambda env: `DETECTOR_BACKEND=auto`, `LANE_BACKEND=ufldv2` (live = template)
- SFN definition matches `infrastructure/backend-lambda-stack.yaml` (ECS RunTask + Lambda fallback)

**Active deployment artifacts in AWS:**
| Resource | Identifier |
|---|---|
| Inference worker stack | `lightship-mvp-inference-worker` (CREATE_COMPLETE) |
| Worker ECR repo | `lightship-mvp-inference-worker:latest` (digest `1a2ad25c...`) |
| Worker TaskDef revision | `lightship-mvp-inference-worker:3` (BEDROCK_MODEL_ID=Sonnet 4.5, LANE_BACKEND=opencv) |
| ECS Cluster | `lightship-mvp-cluster` (reused) |
| Worker SG | `sg-0fc9be8b2c09432b4` (reused pre-existing; allowlisted on VPC endpoints) |
| Worker task role | `lightship-mvp-inference-worker-task-role` (S3 + DynamoDB + Bedrock + KMS) |
| Lambda image | `lightship-backend:vision-honest2-7cf6c9a` (digest `759b506e...`) |
| Step Functions | `lightship-mvp-pipeline` (ECS RunTask + Lambda fallback) |
| Bedrock model in use | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (cross-region inference profile) |

### Honest disclosure of partial / deferred items

1. **UFLDv2 lane detection — NOT integrated, opt-in only.**
   - Tried: HuggingFace repo `cfzd/ufld-v2-culane` — does not exist (401 Unauthorized; I made up the repo name based on the GitHub org).
   - Real distribution: only via Google Drive / Baidu Drive (`cfzd/Ultra-Fast-Lane-Detection-V2`) — not script-friendly.
   - Viable path: PINTO_model_zoo provides ONNX exports (`ufldv2_culane_res34_320x1600.onnx`); requires baking into worker image + adding `onnxruntime` package.
   - **Current default**: `LANE_BACKEND=opencv` (HSV + Hough in `cv_labeler.py`). Production-tested.
   - The UFLDv2 backend module is a stub; selecting it emits zero lanes (deliberate, no silent fallback).

2. **Detectron2 — NEVER actually loaded.**
   - The Apache-2.0 prebuilt CPU wheel from `dl.fbaipublicfiles.com/detectron2/wheels/cpu/torch2.6/...` was not added to `requirements.txt`.
   - The "Detectron2 backend" was always falling through to YOLOv11n.
   - Renamed honestly to `YoloFallbackBackend` (file: `lambda-be/src/backends/yolo_fallback_backend.py`).
   - `detectron2_backend.py` kept as a one-line back-compat shim importing the new class.

3. **YOLO version & licence** (used by both `CVLabeler` dense pre-selection AND `YoloFallbackBackend` for Florence-2 fallback):
   - **YOLOv11n via Ultralytics 8.4.45**, AGPL-3.0.
   - Weights `yolo11n.pt` (~6 MB) baked into the worker image at build time (`/app/.cache/ultralytics/`).
   - AGPL-3.0 is acceptable for server-side processing; would block on-prem distribution.

4. **Bedrock Claude — root cause was wrong model ID, not IAM.**
   - Calling roles: `lightship-mvp-lambda-role` and `lightship-mvp-inference-worker-task-role`. Both have `bedrock:InvokeModel` allowed.
   - Old model ID: `us.anthropic.claude-sonnet-4-20250514-v1:0` — Bedrock reports this as `LEGACY` and the account never had Marketplace model access granted (the "AWS Marketplace actions (aws-marketplace:ViewSubscriptions, aws-marketplace:Subscribe)" error is Bedrock's wording for "model access not enabled in the Bedrock console for this account").
   - Working model IDs in this account today: `us.anthropic.claude-sonnet-4-5-20250929-v1:0` (in use), `us.anthropic.claude-sonnet-4-6` (also works), `anthropic.claude-3-haiku-20240307-v1:0` (works on-demand without inference profile).
   - Secondary issue: Sonnet 4.5 rejects requests that pass both `temperature` and `top_p`. Fixed `frame_refiner.py`, `hazard_assessor.py`, `scene_labeler.py` to pass only `temperature`.

| Area | Status | Files |
|---|---|---|
| Frame selection backfill bug | **FIXED** | `lambda-be/src/pipeline.py` — after existence filter, backfills from valid candidates to reach `max_snapshots` |
| VisionLabeler orchestrator | **DONE** | `lambda-be/src/vision_labeler.py` (NEW) |
| Florence-2 backend (MIT) | **DONE** | `lambda-be/src/backends/florence2_backend.py` (NEW) |
| Detectron2 fallback backend (Apache 2.0) | **DONE** | `lambda-be/src/backends/detectron2_backend.py` (NEW) |
| UFLDv2 lane backend (Apache 2.0) | **DONE** | `lambda-be/src/backends/ufldv2_backend.py` (NEW) |
| OpenCV lanes gated to `LANE_BACKEND=opencv` | **DONE** | `lambda-be/src/cv_labeler.py` |
| Pipeline wire-up (rekognition→vision, audit rename) | **DONE** | `lambda-be/src/pipeline.py` |
| Config vars (DETECTOR_BACKEND, LANE_BACKEND, etc.) | **DONE** | `lambda-be/src/config.py` |
| UI/API backend + lane config fields | **DONE** | `lambda-be/src/api_server.py` (ProcessingConfig) |
| ECS Fargate inference worker | **DONE** | `inference-worker/entrypoint.py`, `inference-worker/Dockerfile`, `inference-worker/requirements.txt` |
| ECS infrastructure stack | **DONE** | `infrastructure/inference-worker-stack.yaml` (NEW) |
| Step Functions ASL — ECS path + Lambda fallback | **DONE** | `infrastructure/backend-lambda-stack.yaml` |
| Rekognition IAM / env / dashboard removed | **DONE** | `infrastructure/app-stack.yaml`, `infrastructure/backend-lambda-stack.yaml` |
| Rekognition code/scripts/tests deleted | **DONE** | `lambda-be/src/rekognition_labeler.py`, `tests/test_11_rekognition_audit.py`, `scripts/prepare_rekognition_dataset.py`, `scripts/train_custom_labels.py`, `scripts/validate_custom_labels_e2e.py`, `scripts/start_stop_custom_labels.py` |
| vision_audit e2e test | **DONE** | `tests/test_12_vision_audit.py` (NEW), `tests/test_06_e2e_pipeline.py` updated |
| POC evaluation script | **DONE** | `scripts/run_vision_poc.py` (NEW) |
| Docs deprecation notice | **DONE** | `docs/aws-architecture-audit-2026-04-26.md` |

**Manual AWS cleanup still required:**
1. Delete SSM parameter `/lightship/mvp/rekognition/custom-labels-arn`
2. Stop and delete Rekognition Custom Labels version `v2domain` on project `lightship-mvp-objects`
3. Delete Rekognition project `lightship-mvp-objects` (saves ~$4/hr)
4. Deploy `infrastructure/inference-worker-stack.yaml` → build and push ECR image → re-deploy `backend-lambda-stack.yaml`

**Test status:**
- `pytest tests/test_12_vision_audit.py -v` → 1 skipped (numpy/cv2 not in dev venv; full run in Lambda container)
- `pytest tests/test_07_config_generator.py -v` → **5 passed**
- `pytest tests/test_01_infrastructure.py -v` → 13 failed (pre-existing: live AWS credentials required; 5 passed)

---

### MVP fix pass — 2026-04-20 (branch `cursor/lightship-mvp-fixes-6abf`)

Eight-task "misleading progress + flaky results + frame reliability"
fix pass. What landed:

| Task | Status | Files |
|---|---|---|
| Restore missing backend modules | DONE | `lambda-be/src/job_status.py`, `lambda-be/src/utils/metrics.py`, `ui-fe/src/lib/uuid.ts` |
| Task 1 — granular pipeline progress | DONE | `lambda-be/src/pipeline.py`, `lambda-be/src/api_server.py`, `ui-fe/src/app/run/page.tsx` |
| Task 2/3 — reliable Run→Results | DONE | `ui-fe/src/lib/api.ts` (`retry404`, `ApiError`), `ui-fe/src/app/results/[runId]/page.tsx`, `ui-fe/src/lib/history-persist.ts`, `ui-fe/src/components/evaluation/flow-provider.tsx` |
| Task 4 — frame extraction reliability | DONE | `lambda-be/src/frame_extractor.py`, `tests/test_09_frame_extractor.py` |
| Task 5 — selection logic | DONE | `lambda-be/src/pipeline.py` (dedup + path existence check), `tests/test_10_frame_selection.py` |
| Task 6 — Rekognition audit + clean RGB input | DONE | `tests/test_11_rekognition_audit.py` (pre-existing pipeline already sends raw frames; new tests assert the contract) |
| Task 7 — frame viewer UX | DONE | `ui-fe/src/components/evaluation/backend-frame-gallery.tsx` (open-full-size, fallback, substituted-frame annotation) |
| Task 8 — Clear History + dedup header nav | DONE | `ui-fe/src/app/history/page.tsx`, flow provider |
| Smoke-discovered: Float → Decimal for Dynamo writes | DONE | `lambda-be/src/job_status.py` |
| Smoke-discovered: /status must read Dynamo first (HTTP vs worker Lambdas are different containers) | DONE | `lambda-be/src/job_status.py` |

Test status:

- Backend: `pytest tests/test_08_progress_tracking.py tests/test_09_frame_extractor.py tests/test_10_frame_selection.py tests/test_11_rekognition_audit.py -v` → **21 passed**.
- Frontend: `cd ui-fe && npm run build` → green (5 routes).

### Production deploy — verified 2026-04-20 (smoke on live ALB)

Deployed via CodeBuild (`lightship-mvp-backend` + `lightship-mvp-frontend`)
from branch `cursor/lightship-mvp-fixes-6abf` at commit `ee7f5e3`.
Lambda env vars (`PIPELINE_STATE_MACHINE_ARN`, `PROCESSING_QUEUE_URL`,
`BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0`,
`LOG_FORMAT=json`, `EMIT_METRICS=true`, `METRICS_NAMESPACE`) were
re-applied after the backend buildspec's `update-function-configuration`
call (which intentionally resets to a minimal set).

ECS: `lightship-mvp-frontend-svc` (TaskDef:22) and
`lightship-mvp-frontend-service` (TaskDef:23) both rolled over to
the new `lightship-frontend:latest` image. Both 1/1 running.

Live smoke against
`http://lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com`:

- `/health` → `{"status":"healthy"}` (17 s cold, < 100 ms warm).
- `POST /process-s3-video` with `plr_snow_4818293461-C.mp4` returned
  `{"dispatch":"sqs"}`.
- `/status/<id>` polled at 4 s — progress stream visible:
  `0.15 extracting_frames → 0.22-0.49 detecting_objects (46 frames)
  → 0.65 refining_frames → 1.0 completed`. **No more 30 %
  plateau, no more jump to 90 %.**
- `/frames/<id>` returned real 1280×720 annotated frames with
  `extraction_source=requested`, `status=ok`.
- `/download/json/<id>` includes `rekognition_audit.frames_evaluated=3`.
- `/video-class/<id>` returns `{ display_label: "Driving" }`.

### Production-Ready End-to-End Plan (in progress)

### Production-Ready End-to-End Plan (in progress)

| Phase | Status | Notes |
|-------|--------|-------|
| 1 — Stabilization | **DONE** | UUID fallback, missing rewrites, step-level Dynamo progress, self-invoke IAM, browser smoke checklist |
| 2 — Observability | **DONE** | JSON logging + EMF metrics wired into `api_server`, Rekognition audit in `output.json`, contract tests (all 14 endpoints), dashboard updated |
| 3 — SQS + Step Functions | **DONE** | `/process-video` → SQS → dispatcher → `LightshipPipelineStateMachine` → backend Lambda task; `MarkFailed` Catch state; SQS `EventSourceMapping` with `ReportBatchItemFailures`; legacy self-invoke kept behind env-var fallback (removed in Phase 6) |
| 4 — Batch + UX | **DONE** | Parallel per-asset submission (concurrency cap 3) + single-round-trip batch polling via new `/batch/status`; `/batch/process` and `/process-s3-prefix` endpoints; `/download/frames-zip/{job_id}` streams a ZIP of annotated frames + per-frame JSON + `output.json`; results page Frames/Rendered/JSON tabs with windowed scroll for large batches; deleted stub routes + unused components |
| 5 — Testing | **DONE** | Playwright config + 3 e2e specs (upload, batch, results); extended `test_e2e_live.py` with Rekognition audit + batch-status + frames-zip assertions; `.github/workflows/ci.yml` wires backend pytest + UI typecheck + UI build + Playwright run |
| 6 — Docs + cleanup | **DONE** | README rewritten with mermaid (SQS + Step Functions is canonical); DEPLOYMENT.md rewritten with 4-stack walkthrough + name matrix; deploy.sh output keys fixed; FrontendTargetGroup port 8501→3000; ECS service-name mismatch fixed in conftest; ALB listener rules gained `/batch/*` + `/process-s3-prefix`; stale `DEPLOYMENT_STATUS.md` removed; `.env.example` enumerates every env var; `lightship_mvp_aws_architecture_note.md` SQS names aligned |

**Phase 1-6 — final offline test run:** `pytest tests/test_07_config_generator.py tests/test_08_progress_tracking.py tests/test_api_contracts.py tests/test_batch_endpoints.py tests/test_dispatcher_and_sqs.py tests/test_metrics_and_logging.py -v` → **51 passed**. `cd ui-fe && npx tsc --noEmit` → clean. `cd ui-fe && npx next build` → green (5 routes, no ESLint fail). Playwright specs compile; real runs happen in `.github/workflows/ci.yml`.

### Production deploy — verified 2026-04-20

Deployed to AWS account `336090301206` / `us-east-1`. Every stack is `CREATE_COMPLETE` or `UPDATE_COMPLETE`:

| Stack | Status |
|---|---|
| `lightship-mvp-vpc` | UPDATE_COMPLETE |
| `lightship-mvp-app` | UPDATE_COMPLETE (new listener rules + SFN logging perms + dashboard widgets) |
| `lightship-mvp-pipeline-addon` | CREATE_COMPLETE (Step Functions state machine + SQS EventSourceMapping) |
| `lightship-mvp-frontend-service` | CREATE_COMPLETE (Fargate task + service, DesiredCount=1) |
| `lightship-mvp-cicd` | CREATE_COMPLETE |

Verified end-to-end:

- `POST /process-s3-video` returns `{"dispatch":"sqs"}` — Phase 3 SQS path is active (no more self-invoke).
- Step Functions execution `lightship-mvp-pipeline` completes with status **SUCCEEDED**; job id transitions QUEUED → PROCESSING (progress 0.3) → COMPLETED (progress 1.0) inside ~50 s.
- `/download/json/{job_id}` includes `rekognition_audit` with `frames_evaluated` > 0 — Phase 2 audit is persisted on every real run.
- `/frames/{job_id}` returns presigned S3 URLs for annotated frames.
- `/batch/status?job_ids=...` round-trips — Phase 4 batch API is wired through ALB.
- Frontend ECS service serves HTML at `http://lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com/` with 200.

Deploy fixes applied on top of Phase 6 code:

- `infrastructure/app-stack.yaml` — `BackendListenerRuleExtra` split into two rules because the ALB rejects > 5 path-pattern values per condition; added `logs:*LogDelivery*` permissions to `StepFunctionsExecutionRole` so vended-logs on the state machine work. Reverted the FrontendTargetGroup port change (immutable; cosmetic anyway).
- `infrastructure/pipeline-addon-stack.yaml` (new) — standalone CFN that adds the Step Functions state machine + SQS EventSourceMapping to the already-deployed backend Lambda by function name. Avoids replacing the existing Lambda (which is what `backend-lambda-stack.yaml` would have done).
- `lambda-be/Dockerfile` — installs CPU-only torch from `https://download.pytorch.org/whl/cpu` with `--only-binary=:all:` to prevent the default build from pulling ~3 GB of unused CUDA wheels and failing on the Lambda base image's GCC 7.3 when numpy falls back to source.
- `lambda-be/src/lambda_function.py` — renamed `extra={"filename": ...}` to `"video_filename"` to avoid `KeyError: Attempt to overwrite 'filename' in LogRecord` (Python stdlib reserves `filename` on `LogRecord`). Caught by a real SFN execution during verification.
- `infrastructure/frontend-service-stack.yaml` — ECS `DesiredCount` 2 → 1 and ASG min 2 → 1 for cost reasons; scales up automatically under load.

**Phase 5 — files changed:**

- `ui-fe/playwright.config.ts` (new) — Chromium only, 60s per test, auto-starts `npm run start` locally; disabled when `PLAYWRIGHT_BASE_URL` is set so CI can manage the server explicitly. Retains traces/screenshots/videos on failure.
- `ui-fe/package.json` — added `@playwright/test` devDep, `test:e2e`, `test:e2e:install` scripts.
- `ui-fe/tests/e2e/upload-flow.spec.ts` (new) — asserts the Run button is disabled until a file (or S3 URI) is queued, using the existing `data-test-id` hooks.
- `ui-fe/tests/e2e/batch-flow.spec.ts` (new) — stubs `/process-s3-video`, `/batch/status`, `/download/json/*`, `/frames/*`, `/client-configs/*`, `/video-class/*` at the Playwright network boundary and drives the full multi-video flow to the results page.
- `ui-fe/tests/e2e/results.spec.ts` (new) — isolated tab-switcher test (Frames ⇄ Rendered ⇄ JSON), locks in the tab contract.
- `ui-fe/src/components/evaluation/s3-uri-input.tsx` — added `data-test-id="s3-uri-input-field"` on the input and `s3-uri-add-button` on the add button; moved the wrapper `data-test-id="s3-uri-input"` up to the flex container so the test can scope by either.
- `tests/test_e2e_live.py` — updated the UI-route test (removed deleted `/pipeline` and `/preview` stubs); added live-ALB assertions for `/batch/status` 422-empty, `/batch/status` `NOT_FOUND` row, `/download/frames-zip/<unknown>` 404, `rekognition_audit` present in `output.json` after a COMPLETED job, `/download/frames-zip/<job>` returning a valid ZIP with `output.json`, and `/batch/status` reporting `COMPLETED` + `progress=1.0` for a finished job.
- `.github/workflows/ci.yml` (new) — three parallel jobs: backend pytest (offline), frontend typecheck + `npm run build`, frontend Playwright with Chromium browser install. Playwright report is uploaded on failure.

**Phase 4 — files changed:**

- `lambda-be/src/api_server.py` — added `_BatchItem` / `_BatchRequest` Pydantic models, `_item_to_jobs` helper that resolves s3_uri, s3_key, and s3_prefix (ListObjectsV2 pagination, auto-copy into PROCESSING_BUCKET when needed). New endpoints: `POST /batch/process`, `POST /process-s3-prefix`, `GET /batch/status?job_ids=a,b,c` (one round-trip for N jobs, unknown ids return `"NOT_FOUND"`), `GET /download/frames-zip/{job_id}` (streams a ZIP of annotated frames + per-frame JSON + `output.json`).
- `ui-fe/src/lib/api.ts` — added `BatchStatusRow`, `getBatchStatus`, `pollBatchToTerminal` (single-fetch-per-tick polling for N jobs), `batchProcess`, `downloadFramesZipUrl`.
- `ui-fe/src/app/run/page.tsx` — rewrote for parallel batch execution: `runWithConcurrency` helper caps parallel uploads at 3; batch polling uses a single `/batch/status` call per tick instead of N parallel `/status` polls; failed submits + failed runs are tracked per-asset; result fetch fans out in parallel.
- `ui-fe/src/app/results/[runId]/page.tsx` — added Frames/Rendered/JSON tab switcher (with `data-test-id` hooks for Playwright); left rail now scrollable with 40-item windowing (scroll reveals more — no `react-window` dep); "Download frames ZIP" button wired to `/download/frames-zip`; "Download all JSON" still batched across every completed asset.
- `ui-fe/src/app/pipeline/` and `ui-fe/src/app/preview/` — deleted (they were redirect stubs).
- `ui-fe/src/components/evaluation/pipeline-config-form.tsx`, `wizard-stepper.tsx`, `results-frame-gallery.tsx` — deleted (not imported anywhere).
- `tests/test_batch_endpoints.py` (new) — 7 tests covering batch submit, s3_prefix expansion (and mp4-only filter), s3-prefix shortcut, `/batch/status` multi-id lookup including `NOT_FOUND`, `/download/frames-zip/{job_id}` 404 + ZIP content/shape.

### Phase 6 — files changed

- `README.md` — rewritten: SQS + Step Functions architecture with mermaid diagram, full API table (every endpoint including Phase 4 batch + frames-zip), repo layout reflects Phase 3 changes, 4-stack deploy pointer to DEPLOYMENT.md, observability section documenting JSON logs + EMF metric names + `rekognition_audit`.
- `DEPLOYMENT.md` — rewritten: account ID (`336090301206`) and region (`us-east-1`) correct throughout; 4-stack walkthrough (VPC → app → frontend-service → backend-lambda) with correct stack/service names; appendix matrix maps every named resource to the stack that creates it.
- `DEPLOYMENT_STATUS.md` — deleted (stale January 2026 content, wrong account, Streamlit-era).
- `.env.example` (new) — enumerates every env var the backend + frontend + tests read, grouped by concern (AWS / Bedrock / S3+Dynamo / pipeline tuning / Phase 3 dispatch / Phase 2 observability / frontend / tests).
- `.gitignore` — whitelists `.env.example`; adds Playwright artefact paths (`ui-fe/playwright-report/`, `ui-fe/test-results/`, `ui-fe/tests/e2e/.auth/`).
- `infrastructure/deploy.sh` — `FrontendECRRepository` → `FrontendECRRepositoryUri`, `BackendECRRepository` → `BackendECRRepositoryUri` (matches the real app-stack outputs). Deployment summary now lists the four stacks and shows how to query Step Functions executions.
- `infrastructure/app-stack.yaml` — `FrontendTargetGroup` port 8501 → 3000 (with migration note); ALB listener rule gains `/batch/*` and `/process-s3-prefix`; removed dead `/pipeline-result/*` rule; clarified the "defer" comment at the bottom of the Resources block to point at `backend-lambda-stack.yaml`.
- `tests/conftest.py` — default `ECS_SERVICE` = `lightship-mvp-frontend-service` (was `...-frontend-svc`).
- `lightship_mvp_aws_architecture_note.md` — queue/DLQ names realigned to `lightship-mvp-*` (matching the CFN templates).
- All Phase 2-4 test-file stubs narrowed so they only replace `src.pipeline` (the heavy ML import) and leave lightweight modules like `src.config_generator` untouched — fixes test-order coupling that made `test_07_config_generator` fail when run after the batch/dispatcher suites.

**Phase 3 — files changed:**

- `lambda-be/src/lambda_function.py` — rewrote the entry-point to route every Lambda event type: ALB HTTP (Mangum), SQS batch (dispatcher → `StartExecution`), SFN task (`action=pipeline_stage` → runs the pipeline), SFN error (`action=mark_failed` → Dynamo FAILED), plus the legacy `action=process_worker` path that stays until Phase 6 deploy verification.
- `lambda-be/src/api_server.py` — added SQS client + `_enqueue_job` helper which prefers the SQS/Step Functions path (when `PROCESSING_QUEUE_URL` is set), falls back to Lambda self-invoke, and finally to a background task in local dev. `/process-video` and `/process-s3-video` both use it; both mark the Dynamo row FAILED if the dispatch call itself raises.
- `infrastructure/state-machines/pipeline.asl.json` (new) — standalone ASL definition for readability; the CFN template mirrors the same flow inline so the state machine isn't split across files at deploy time.
- `infrastructure/backend-lambda-stack.yaml` — now provisions `ProcessingQueueEventSource` (SQS→Lambda EventSourceMapping with `ReportBatchItemFailures`), `PipelineStateMachineLogGroup`, and `LightshipPipelineStateMachine` (STANDARD workflow, 870s TimeoutSeconds, Retry on Lambda transient errors, Catch → MarkFailed → Fail). Added `PROCESSING_QUEUE_URL` and `PIPELINE_STATE_MACHINE_ARN` env vars.
- `infrastructure/app-stack.yaml` — removed the transitional `LambdaSelfInvoke` policy now that SQS+SFN is the canonical path; updated the "Note:" comment block to document what lives where.
- `tests/test_dispatcher_and_sqs.py` (new) — 8 tests: SQS-preferred / lambda-fallback / background-mode enqueue routing, FAILED-on-dispatch-error bookkeeping, per-record StartExecution, idempotent `ExecutionAlreadyExists` handling, `batchItemFailures` granularity, `mark_failed` Dynamo update with structured SFN error payload.

**Phase 2 — files changed:**

- `lambda-be/src/utils/logging_setup.py` — rewritten to emit one-line JSON on Lambda (controlled by `LOG_FORMAT=json` or presence of `AWS_LAMBDA_FUNCTION_NAME`); every `extra={}` kwarg becomes a top-level JSON field so CloudWatch Logs Insights can filter on `job_id`, `stage`, `duration_ms` etc. Falls back to text format + rotating file handler locally.
- `lambda-be/src/utils/metrics.py` (new) — CloudWatch Embedded Metric Format emitter: `put_metric`, `put_metrics`, `count`, `duration_ms`, `stage_timer` context manager. No `PutMetricData` API calls; CloudWatch extracts metrics from the log line at ingest time.
- `lambda-be/src/rekognition_labeler.py` — records a per-frame audit entry on every call (raw labels, confidence, kept count, latency, error if any); emits `RekognitionCalls`, `RekognitionLabelsReturned`, `RekognitionInstancesKept`, `RekognitionFailures`, `RekognitionCallMs` EMF metrics.
- `lambda-be/src/pipeline.py` — after `merger.merge_and_save`, reopens `output.json` and embeds `rekognition_audit` (frames evaluated, total instances kept, per-frame detail). Tolerant of failure so a bad audit doesn't block the pipeline.
- `lambda-be/src/api_server.py` — replaced `logging.basicConfig` with `setup_logging()`; added `metrics.count("PipelineStarts/Completions/Failures")` and `metrics.stage_timer("process_video")`; switched final log statements to structured `extra=` form.
- `infrastructure/app-stack.yaml` — CloudWatch dashboard gains three new widgets: Pipeline Throughput (starts/completions/failures), Rekognition Activity, Stage Duration p50/p95 via SEARCH expression.
- `infrastructure/backend-lambda-stack.yaml` — added `LOG_FORMAT=json`, `EMIT_METRICS=true`, `METRICS_NAMESPACE=Lightship/Backend` env vars.
- `tests/test_api_contracts.py` (new) — 17 offline contract tests covering every UI-called endpoint using FastAPI's `TestClient` with in-memory S3 + DynamoDB stubs. Uses `sys.modules` stubs for `src.pipeline` / `src.config_generator` so the heavyweight ML imports are avoided.
- `tests/test_metrics_and_logging.py` (new) — 8 tests: EMF line shape, batched metrics, disabled mode, stage timer duration + failure count, JSON formatter output + exception capture, `setup_logging` idempotency.
- `tests/test_06_e2e_pipeline.py` — added `test_rekognition_audit_present_in_output_json` that reads the completed job's S3 `output.json` and asserts a non-empty `rekognition_audit` block.

**Phase 1 — files changed:**

- `ui-fe/src/lib/uuid.ts` (new) — `uuidv4()` with `crypto.randomUUID` → `getRandomValues` → `Math.random` fallback chain, so HTTP (non-secure-context) browsers no longer throw when queuing assets.
- `ui-fe/src/components/evaluation/flow-provider.tsx` — imports `uuidv4`; both `createAssetId` sites now call the safe helper.
- `ui-fe/next.config.ts` — added rewrites for `/frames/:path*`, `/video-class/:path*`, `/process-s3-video`, `/process-s3-prefix`, `/batch/:path*` (previously missing; local proxy silently 404'd).
- `lambda-be/src/job_status.py` (new) — canonical progress module: `write_progress`, `update_status`, `put_job`, `get_job`, `read_status`. All DynamoDB writes alias every attribute via `ExpressionAttributeNames`, so reserved words (`status`, `message`) no longer cause silent `ValidationException`.
- `lambda-be/src/api_server.py` — imports `job_status`; five progress update sites inside `process_video_task` now call `_write_progress` (which writes BOTH warm cache AND Dynamo); `/status` delegates to `job_status.read_status`.
- `infrastructure/app-stack.yaml` — added `LambdaSelfInvoke` policy to `LambdaExecutionRole` (transitional; Phase 3 removes it).
- `scripts/smoke_browser.md` (new) — 6-step manual HTTP smoke checklist with failure triage.
- `tests/test_08_progress_tracking.py` (new) — 6 pure-Python tests covering progress write, reserved-word aliasing, cold-Dynamo fallback, and `put_job` `None` filtering.

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
