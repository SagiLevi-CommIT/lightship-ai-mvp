# Lightship Dashcam Video Analysis - Copilot Instructions# Lightship AI - Copilot Instructions



AI coding assistant guide for the Lightship dashcam video analysis system. Follow these patterns for consistency.AI coding assistant guide for the Lightship Dashcam Video Analysis project. Follow these patterns to maintain consistency and avoid common pitfalls.



## Architecture Overview## Architecture Overview



AWS-based dashcam video processing with computer vision and LLM analysis:Dashcam video analysis system with AWS Bedrock Claude Sonnet for object detection and hazard labeling:



``````

Internet → ALB → {Internet → ALB → {

  /           → ECS Fargate (Streamlit UI)  /           → ECS Fargate (Streamlit UI in s3-ui-fe/)

  /api/*      → Lambda (FastAPI backend)  /api/*      → Lambda container (FastAPI backend in s3-lambda-be/)

}}

Backend → YOLO11 + Depth-Anything-V2 + AWS Bedrock Claude Sonnet 4Lambda → AWS Bedrock Claude Sonnet 4 + Computer Vision (YOLO11 + Depth-Anything-V2)

``````



**Key components:****Critical architectural facts:**

- **Backend (`s3-lambda-be/src/`):** FastAPI server with V3 pipeline for video processing- **Backend (s3-lambda-be/):** FastAPI server (`api_server.py`) orchestrates V3 pipeline: CV detection (YOLO11) → per-frame LLM refinement → temporal hazard assessment

- **Frontend (`s3-ui-fe/src/`):** Streamlit UI for upload, progress tracking, results viewing- **Frontend (s3-ui-fe/):** Streamlit UI (`streamlit_app.py`) for video upload, real-time progress, annotated frame viewer

- **Pipeline:** 6-stage workflow (video_loader → snapshot_selector → frame_extractor → cv_labeler → frame_refiner → hazard_assessor → merger)- **Pipeline (pipeline.py):** 6-stage workflow: video_loader → snapshot_selector → frame_extractor → cv_labeler → frame_refiner → hazard_assessor → merger

- **CV Models:** YOLO11 (traffic objects), Depth-Anything-V2 (geometry/distance)- **CV Models:** YOLO11 for traffic objects, Depth-Anything-V2 for geometry, custom confidence tuning per object class

- **LLM:** AWS Bedrock Claude Sonnet 4 for threat assessment and false positive filtering- **LLM Integration:** AWS Bedrock Claude Sonnet 4 for threat assessment, false positive filtering, priority hazard identification



## V3 Pipeline Stages## Multi-Agent Pipeline



**Stage 1-3: Frame Preparation****SystemManager** (`s3-lambda-be/src/lightship/cli/system_manager.py`) orchestrates:

- `video_loader.py` - Extract video metadata (FPS, resolution, duration)1. **SupervisorAgent** (`agents/supervisor.py`) coordinates all agents

- `snapshot_selector.py` - Select keyframes (scene change detection or uniform sampling)2. **ClarifierAgent** (`agents/clarifier.py`) → structured QuestionSpec with intent/period/entities

- `frame_extractor.py` - Extract selected frames as images3. **DecomposerAgent** (`agents/decomposer.py`) → breaks complex queries into sub-questions

4. **SqlGeneratorAgent** (`agents/sql_generator.py`) → generates SQL per sub-question

**Stage 4: Computer Vision Detection**5. **RunnerAgent** (`agents/runner.py`) → executes SQL against DuckDB

- `cv_labeler.py` - YOLO11 object detection + Depth-Anything-V2 distance estimation6. **SynthesizerAgent** (`agents/synthesizer.py`) → assembles final natural language answer

- Custom confidence thresholds per object class7. Optional: **ForecasterAgent** (`agents/forecaster.py`) → ETS-based financial forecasting

- Returns bounding boxes, labels, confidence scores, distances

**Agent contract:** All agents return structured output (dataclasses/JSON), use `_parse_json_response()` fallback pattern for LLM responses, and log with emoji prefixes (`🚀`, `✅`, `❌`, `⚠️`, `🔍`).

**Stage 5-6: LLM Refinement**

- `frame_refiner.py` - Per-frame LLM analysis to filter false positives and assess threats## Configuration & Settings

- `hazard_assessor.py` - Temporal analysis across frames to identify priority hazards

- `merger.py` - Generate final JSON output per specification**s3-lambda-be/src/lightship/config/settings.py:**

- Singleton `settings = load_settings()` - **never instantiate directly**

## Configuration- `PROJECT_DEFAULTS` dict applies automatically before imports to override stale env vars

- Critical settings: `AWS_REGION=us-east-1`, `BEDROCK_MODEL_ID` (ARN), S3 bucket names (`s3_bucket_name`, `s3_schemas_prefix`)

**Backend config (`s3-lambda-be/src/config.py`):**- Local dev: `.env` file searched in project root, backend root, cwd (in that order)

- `SNAPSHOT_STRATEGY` - "scene_change" or "naive" (uniform)- Lambda: Uses IAM role (never explicit credentials), checks `AWS_LAMBDA_FUNCTION_NAME` env var

- `MAX_SNAPSHOTS_PER_VIDEO` - Frame extraction limit (default: 10)

- Per-class confidence thresholds for YOLO11**Bootstrap pattern in frontend (`s3-ui-fe/src/cli/streamlit_app.py`):**

- Bedrock model settings (Claude Sonnet 4)```python

- Camera profiles with FOV and mounting specifications# _bootstrap() runs BEFORE imports to fix sys.path and apply PROJECT_DEFAULTS

# Ensures backend modules importable and correct inference profile ARN set

**AWS Credentials:**```

- **ALWAYS use IAM roles** - Never hardcode AWS credentials

- Boto3 clients initialize without explicit keys: `boto3.client('bedrock-runtime', region_name='us-east-1')`## Infrastructure & Deployment

- Lambda automatically uses execution role

**Multi-stack CloudFormation pattern** (order matters):

## Key Conventions1. `infrastructure/vpc-stack.yaml` - VPC, subnets (public/private), NAT, security groups

2. `infrastructure/app-stack.yaml` - ALB, ECS, Lambda, ECR, S3 buckets, IAM roles, CloudWatch logs

**Project structure:**

- Backend source: `s3-lambda-be/src/*.py` (flat structure, no nested packages)**Deploy commands** (see `infrastructure/deploy.sh` for automation):

- Frontend source: `s3-ui-fe/src/*.py` (streamlit_app, api_client, visualization)```bash

- Data: `data/train/` (10 videos with ground truth), `data/test/` (15 test videos)# Phase 1: VPC

- Output: `output/*.json` (detection results per video)aws cloudformation create-stack --stack-name lightship-mvp-vpc --template-body file://vpc-stack.yaml --region us-east-1



**API endpoints (`api_server.py`):**# Phase 2: App resources

- `POST /process` - Upload video and start processingaws cloudformation create-stack --stack-name lightship-mvp-app --template-body file://app-stack.yaml --capabilities CAPABILITY_NAMED_IAM --region us-east-1

- `GET /status/{job_id}` - Check processing status

- `GET /result/{job_id}` - Retrieve results JSON# Phase 3: Build & push images (frontend and backend to ECR)

- `GET /frames/{job_id}` - Get annotated frames ZIP# Phase 4: Update ECS task & Lambda function with new image URIs

```

**Testing & validation:**

- `test_pipeline_v3.py` - Full pipeline test with ground truth comparison**Parameter-driven templates:** All stacks use `ProjectName` and `Environment` parameters. Cross-stack refs via `Fn::ImportValue` (e.g., VPC exports used by app stack).

- `test_frame_refiner.py` - Per-frame LLM refiner validation

- `evaluation_metrics.py` - Calculate precision/recall/F1## Key Conventions

- `analysis_missing_objects.py` - Analyze detection gaps

**AWS resource naming** (see `AWS-NAMING-CONVENTION.md`):

## Docker & Deployment- Format: `<object-type>-<env>-<name>` (all lowercase, hyphens)

- Examples: `alb-mvp-lightship`, `lambda-mvp-backend`, `s3-lightship-mvp-lancedb-<AccountId>`

**Backend Dockerfile (`s3-lambda-be/Dockerfile`):**- **Do not invent new patterns** - follow existing conventions

- Base: `public.ecr.aws/lambda/python:3.11`

- Installs requirements.txt (OpenCV, PyTorch, Ultralytics, Transformers, FastAPI)**Logging style:**

- Copies `src/` to Lambda task root- Use structured logging: `logger.info()`, `.debug()`, `.warning()`, `.exception()`

- CMD: Lambda handler- Emoji prefixes for visual scanning: `🚀` (start), `✅` (success), `❌` (error), `⚠️` (warning), `🔍` (processing)

- Example: `logger.info("🚀 SystemManager processing query [%s]: %s", session_id, question)`

**Frontend Dockerfile (`s3-ui-fe/Dockerfile`):**

- Base: `python:3.11-slim`**Guardrails:**

- Installs `requirements_streamlit.txt`- `guardrails/sql_policy.py` - validates SQL (no DROP/DELETE/UPDATE, table/column allowlists, requires LIMIT)

- Copies `src/` to `/app`- `guardrails/prompt_policies.py` - scans for prompt injection patterns

- CMD: `streamlit run streamlit_app.py`- Both applied before execution, violations logged with `⚠️`



**Build & push images:****Testing & validation:**

```bash- Smoke tests: `python s3-lambda-be/src/lightship/cli/smoke.py` (basic imports, schema loading, agent init)

# Get ECR login- Golden tests: `s3-lambda-be/src/lightship/eval/golden_tests.py` (predefined test cases with expected outputs)

aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com- CLI demos: `s3-lambda-be/src/lightship/cli/demo.py` (full pipeline demonstrations)



# Backend## Common Pitfalls to Avoid

cd s3-lambda-be

docker build -t lightship-backend .1. **Don't hardcode Bedrock model IDs** - use `settings.bedrock_model_id` (ARN format required for this account)

docker tag lightship-backend:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/lightship-backend:latest2. **Don't create new CloudFormation templates** - extend existing `vpc-stack.yaml` or `app-stack.yaml`

docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/lightship-backend:latest3. **Don't use blocking I/O in agents** - agents should be async-first (even though not all are currently async)

4. **Don't bypass guardrails** - SQL and prompt policies must validate before execution

# Frontend5. **Don't modify global state in Lambda handler** - use warm-start singleton pattern (`get_system_manager()`)

cd s3-ui-fe6. **Don't assume local file paths** - check for S3 cache first (`ensure_s3_cache_initialized()`)

docker build -t lightship-frontend .

docker tag lightship-frontend:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/lightship-frontend:latest## Key Files Reference

docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/lightship-frontend:latest

```| Component | Location | Purpose |

|-----------|----------|---------|

## Common Pitfalls| Lambda entry | `s3-lambda-be/src/lightship/lambda_handler.py` | API Gateway handler, warm-start singleton |

| Orchestrator | `s3-lambda-be/src/lightship/cli/system_manager.py` | Main pipeline coordinator |

1. **Don't hardcode AWS credentials** - Use IAM roles (already in environment)| Supervisor | `s3-lambda-be/src/lightship/agents/supervisor.py` | Agent coordination, schema loading |

2. **Don't modify pipeline stages** - V3 is production-ready, tune thresholds in config.py instead| Config | `s3-lambda-be/src/lightship/config/settings.py` | Settings singleton, PROJECT_DEFAULTS |

3. **Check requirements versions** - PyTorch/YOLO11 have specific version dependencies| Frontend | `s3-ui-fe/src/cli/streamlit_app.py` | Streamlit UI with bootstrap pattern |

4. **Dockerfile paths matter** - Backend expects `src/` structure, frontend needs proper PYTHONPATH| VPC infra | `infrastructure/vpc-stack.yaml` | Network foundation |

5. **Model downloads** - YOLO11/Depth-Anything models auto-download on first run (ensure /tmp space in Lambda)| App infra | `infrastructure/app-stack.yaml` | ALB, ECS, Lambda, ECR, S3, IAM |

| Deploy script | `infrastructure/deploy.sh` | Automated phased deployment |

## Key Files Reference

## Making Changes

| Component | File | Purpose |

|-----------|------|---------|**Before editing:**

| Pipeline orchestrator | `s3-lambda-be/src/pipeline.py` | V3 end-to-end workflow |1. Check `settings` in `config/settings.py` for existing env var names

| API server | `s3-lambda-be/src/api_server.py` | FastAPI REST endpoints |2. Search `s3-lambda-be/src/agents/` for similar implementations

| CV detection | `s3-lambda-be/src/cv_labeler.py` | YOLO11 + depth estimation |3. Verify CloudFormation changes preserve parameter-driven design (`!Sub`, `!Ref`, `Fn::ImportValue`)

| LLM refiner | `s3-lambda-be/src/frame_refiner.py` | Per-frame Bedrock analysis |4. Test locally with `python s3-lambda-be/src/lightship/cli/smoke.py`

| Hazard assessor | `s3-lambda-be/src/hazard_assessor.py` | Temporal threat analysis |

| Config | `s3-lambda-be/src/config.py` | All tunable parameters |**Agent pattern example:**

| Streamlit UI | `s3-ui-fe/src/streamlit_app.py` | Web interface |```python

| Backend Dockerfile | `s3-lambda-be/Dockerfile` | Lambda container |class NewAgent:

| Frontend Dockerfile | `s3-ui-fe/Dockerfile` | ECS container |    def __init__(self):

        self.settings = load_settings()  # Never instantiate LightshipSettings()

## Making Changes        self.bedrock_client = boto3.client("bedrock-runtime", region_name=self.settings.aws_region)

        

**Before editing:**    def process(self, input_data) -> OutputDataclass:

1. Check `config.py` for existing parameters (confidence thresholds, model IDs, strategies)        """Process with structured output."""

2. Test locally with sample videos in `data/train/`        result = self._call_llm(input_data)

3. Run evaluation: `python3 src/evaluation_metrics.py` to validate changes        return self._parse_json_response(result)  # Fallback to rules if JSON fails

4. Verify JSON output matches specification in `references/````


**When adding CV features:**
- Update `cv_labeler.py` with new detection logic
- Add class names to `config.py` enum
- Set confidence thresholds per class
- Test with ground truth data

**When modifying LLM prompts:**
- Update prompts in `frame_refiner.py` or `hazard_assessor.py`
- Use Claude Sonnet 4 message format (system + user messages)
- Always include image data as base64 for vision tasks
- Test with diverse road scenarios
