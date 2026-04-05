# LightShip MVP – AWS Services & Responsibilities Architecture Note

> Purpose: A concise, implementation-ready architecture note for an AWS MVP that performs **dashcam video upload → frame extraction & selection → object detection (motorcycles, lanes, signs, construction) → LLM-powered video classification (4 types) → config JSON generation → annotated output storage**, deployed as an async batch-processing pipeline with a web UI.

---

## 1) MVP objectives and scope (what "done" looks like)

### Must-have capabilities
- **Web UI**: Upload videos (single or batch), upload single images, specify custom S3 output paths, view processing status, download/view results.
- **Frame selection pipeline**: Temporal sampling + motion-based filtering + event window expansion to reduce per-video frame count by ~93–97%.
- **Multi-model object detection**: Detect motorcycles, lane markings, road signs (50+ types), traffic signals, and construction objects (15+ labels) using pre-trained models (Rekognition primary, Detectron2/YOLOv12 XL benchmarked).
- **Cross-frame tracking**: Track objects across consecutive frames with trajectory analysis and flag hazards when objects enter the driver's lane.
- **LLM video classification**: Classify each video into one of 4 training types — reactivity_braking, qa_educational, hazard_detection, job_site_detection — using Amazon Bedrock.
- **Config JSON generation**: Auto-generate type-specific config JSONs matching the client's application schema, saved to S3.
- **Batch async processing**: SQS-based job queue with per-video isolation, DLQ for error handling, completion notifications.
- **Single image mode**: Upload a single frame for construction-site object detection.
- **Target metrics**:
  - ≥80% Motorcycle Detection Recall
  - ≥80% Lane Detection Accuracy (IoU)
  - ≥80% Road Sign Detection Precision
  - ≥80% Construction Object Precision
  - ≥80% Video Classification Accuracy (4 classes)
- **Weather-aware detection**: Maintain or improve detection performance under rainy/adverse conditions vs MVP baseline.
- **Road type classification**: Classify each video's road type as highway, city, town, or rural.
- **Infrastructure**: VPC with public/private subnets, encryption (KMS), secrets management, IAM least-privilege, CloudFormation IaC.

### Explicitly out of scope (MVP)
- Extension to non-driving environments (warehouses, job sites beyond dashcam footage).
- Agentic recommendation workflows for training type suggestions.
- Multi-account AWS organization with dedicated prod/staging.
- Custom fine-tuned models (MVP uses pre-trained only).
- Multi-region deployment.
- Real-time / synchronous video processing.
- Custom domain (ALB DNS name is sufficient for MVP).

---

## 2) High-level architecture (services)

### Compute & orchestration
- **AWS Step Functions**: Orchestrates the multi-step processing pipeline (frame extraction → detection → tracking → classification → output).
- **AWS Lambda**: Lightweight functions for job dispatch (SQS consumer), cross-frame tracking, output assembly, and notification.
- **Amazon ECS on Fargate (Web App)**: Hosts the frontend UI and backend API.
- **Amazon ECS on Fargate (Workers)**: Runs heavy video processing tasks — FFmpeg frame extraction, motion filtering, optional custom model inference (Detectron2).
- **Amazon ECR**: Container image registry for web app and worker images.

### AI services
- **Amazon Rekognition**: Managed object detection (motorcycles, vehicles, pedestrians, road signs). Primary detection service.
- **Amazon Bedrock**: LLM-based video classification (4 types), config JSON generation, Q&A generation for educational videos. Model: Claude on Bedrock.

### Messaging & events
- **Amazon SQS**: Job queue decoupling upload from processing. DLQ for failed messages after 3 attempts.
- **Amazon EventBridge**: S3 upload event trigger (optional alternative to API-initiated queue submission).
- **Amazon SNS**: Completion/failure notifications to users and ops.

### Storage & metadata
- **Amazon S3**: All inputs, intermediate artifacts (frames), and outputs (annotated frames, config JSONs).
- **Amazon DynamoDB**: Job status tracking (job_id, status, input/output paths, timestamps, error messages).

### Networking & routing
- **Amazon VPC**: Network isolation for all compute. Public subnets (ALB, NAT GW) + private subnets (ECS, Lambda).
- **Internet Gateway**: Inbound traffic to ALB.
- **NAT Gateway**: Outbound from private subnets (single NAT GW for MVP cost savings).
- **VPC Endpoints**: S3 gateway (free), ECR interface endpoints (required for Fargate pulls). Bedrock/Rekognition endpoints added as optimization.
- **Amazon Route 53**: DNS resolution (optional for MVP).
- **AWS WAF**: Protects ALB from common web attacks.
- **Application Load Balancer (ALB)**: Entry point, TLS termination, routes to ECS web app.

### Security, governance & observability
- **AWS IAM / IAM Identity Center**: Least-privilege roles per component, SSO for developer access.
- **AWS KMS**: Single CMK for encryption at rest across S3, SQS, DynamoDB, CloudWatch Logs.
- **AWS Secrets Manager**: Store sensitive config, model parameters, API keys.
- **AWS CloudTrail**: API audit logging.
- **Amazon CloudWatch**: Logs, custom metrics, alarms, dashboard.
- **AWS CloudFormation**: IaC — nested stacks for network, security, compute, processing, storage, monitoring.

---

## 3) S3 bucket layout (prefixes, ownership, lifecycle)

### Bucket (single bucket, prefix-separated)
`lightship-mvp-{account-id}`

### Prefix layout
| Prefix | Owner | Contents | Typical retention |
|---|---|---|---|
| `input/videos/{job_id}/` | Web App (ECS) | Uploaded dashcam videos (source of truth) | 60–90 days |
| `input/images/{job_id}/` | Web App (ECS) | Uploaded single images for construction detection | 60–90 days |
| `processing/frames/{job_id}/` | ECS Worker | All extracted raw frames (temp) | 14 days |
| `processing/selected_frames/{job_id}/` | ECS Worker | Temporally sampled + motion-filtered frames | 30 days |
| `processing/detection_results/{job_id}/` | Lambda / Rekognition | Per-frame detection JSON outputs | 30 days |
| `results/{custom_path OR default/{job_id}}/annotated_frames/` | Lambda (Output Assembler) | Frames with bounding boxes, lane overlays, labels | 90+ days |
| `results/{custom_path OR default/{job_id}}/config.json` | Lambda / Bedrock | Type-specific config JSON (client schema) | 90+ days |
| `results/{custom_path OR default/{job_id}}/detection_summary.json` | Lambda | Aggregated detection summary per video | 90+ days |

### Lifecycle rules (MVP defaults)
- `processing/frames/*` → expire after **14 days**
- `processing/selected_frames/*` and `processing/detection_results/*` → expire after **30 days**
- `input/*` → expire after **60 days** (or keep for MVP duration)
- `results/*` → expire after **180 days** (or keep)

### Security
- Block Public Access enabled
- SSE-KMS encryption enforced via bucket policy
- Versioning enabled

---

## 4) DynamoDB table (state model)

### Table: `lightship_jobs`
Partition key: `job_id`

Suggested attributes:
- `created_at`, `user_id`
- `input_s3_uri`, `input_type` (video / image)
- `output_s3_path` (custom or default)
- `status` (QUEUED, PROCESSING, COMPLETED, FAILED)
- `video_class` (reactivity_braking / qa_educational / hazard_detection / job_site_detection)
- `road_type` (highway / city / town / rural)
- `weather` (clear / rain / low_visibility)
- `step_functions_execution_arn`
- `completed_at`
- `error_message` (if failed)
- `detection_metrics` (summary counts: motorcycles, signs, lanes, construction objects)

### GSI
- `user_id-index`: Partition key = `user_id`, sort key = `created_at` — for listing a user's jobs.

### Capacity
- On-demand (effectively free tier at MVP volume of ~50–100 jobs/month).

---

## 5) Step Functions state machine (inputs/outputs per step)

### Input contract (StartExecution payload)
```json
{
  "job_id": "uuid",
  "bucket": "lightship-mvp-{account-id}",
  "input_key": "input/videos/{job_id}/{filename}.mp4",
  "input_type": "video",
  "output_path": "results/custom-path-or-default/{job_id}/",
  "user_id": "user@example.com"
}
```

### Workflow (recommended states)

1) **RegisterJob**
   - **Owner**: Lambda
   - **Input**: execution payload
   - **Output**: DynamoDB item created; status=PROCESSING

2) **ValidateAndRoute**
   - **Owner**: Lambda
   - **Input**: input S3 URI, input_type
   - **Output**: Branch decision: `VIDEO_PATH` or `IMAGE_PATH`
   - For images: skip to step 5 (detection only, no frame extraction or classification)

3) **VIDEO_PATH: ExtractAndSelectFrames**
   - **Owner**: ECS Fargate Worker (RunTask)
   - **Input**: video S3 URI
   - **Processing**:
     - FFmpeg extraction (30 FPS → 5 FPS = 83% reduction)
     - Temporal sampling (1 frame / 0.5s → ~600 frames for 5-min video)
     - Motion/scene-change filter (SSIM, drops 30–50% static frames → ~300–400 frames)
     - Generate frame manifest JSON (frame_number, timestamp_sec, selection_reason)
   - **Output**: selected frames uploaded to `processing/selected_frames/{job_id}/`, manifest JSON

4) **DetectObjects**
   - **Owner**: Lambda (orchestrates Rekognition API calls) + optional parallel ECS Fargate (Detectron2)
   - **Input**: selected frames in S3
   - **Processing**: Rekognition DetectLabels on each frame (batched). Optional: parallel Detectron2 inference for benchmarking.
   - **Output**: per-frame detection JSON to `processing/detection_results/{job_id}/`
     - Objects: class, bbox, confidence, estimated_distance
     - Lanes: lane_id, polygons, type (ego_lane / other_lane)
     - Signs: sign_label, bbox (speed/stop/yield/warning/construction/traffic_light)

5) **CrossFrameTracking**
   - **Owner**: Lambda
   - **Input**: per-frame detection results
   - **Processing**: IoU-based object matching across consecutive frames, trajectory analysis, hazard detection (object enters driver's lane)
   - **Output**: tracking results with hazard events (object_id, entry_frame, risk_level)

6) **EventWindowExpansion** (conditional)
   - **Owner**: ECS Fargate Worker (if events detected)
   - **When**: hazard events detected in step 5
   - **Processing**: extract additional frames ±2 seconds around each event at 5 FPS, re-run detection
   - **Output**: additional frames + detections merged into results

7) **ClassifyAndGenerateConfig**
   - **Owner**: Lambda + Bedrock
   - **Input**: aggregated detection metadata (object counts, types, hazard events, road conditions)
   - **Processing**:
     - Bedrock classifies video into 1 of 4 types
     - Bedrock generates type-specific config JSON matching client schema
     - For educational videos: generates ≥3 Q&A questions
     - Classifies road_type and weather
   - **Output**: video_class, config JSON, road_type, weather

8) **GenerateAnnotatedFrames**
   - **Owner**: Lambda (Pillow)
   - **Input**: selected frames + detection results
   - **Processing**: draw bounding boxes, lane overlays (colored), sign labels on frames
   - **Output**: annotated frames to `results/{output_path}/annotated_frames/`

9) **Finalize**
   - **Owner**: Lambda
   - **Output**:
     - Write config.json and detection_summary.json to `results/{output_path}/`
     - Update DynamoDB: status=COMPLETED, video_class, road_type, weather, completed_at

10) **Notify**
    - **Owner**: Lambda + SNS
    - **Output**: publish completion message (job_id, status, output_path)

11) **OnError**
    - **Owner**: Step Functions catch + Lambda
    - **Output**: status=FAILED; error_message set in DynamoDB; SNS failure notification

---

## 6) Web application responsibilities (upload + status + results)

### Upload responsibilities
- Provide video upload UX (single and batch mode).
- Provide single image upload for construction-site detection.
- Accept optional custom S3 output path from user (or use default).
- Upload video/image to S3 `input/` prefix via presigned URL.
- Submit processing job message to SQS with: job_id, input_s3_path, output_s3_path, input_type, user_id.
- Create initial DynamoDB job record (status=QUEUED).

### Status responsibilities
- Poll DynamoDB for job status updates.
- Display processing progress per video (QUEUED → PROCESSING → COMPLETED / FAILED).
- Show error messages for failed jobs.
- Completion notification in UI when all batch jobs finish.

### Results responsibilities
- List completed jobs with metadata (video_class, road_type, weather).
- Allow download of annotated frames, config JSONs, and detection summaries.
- Preview annotated frames in-browser.

### Batch handling
- Accept multiple video uploads in a single session.
- Each video gets its own job_id, SQS message, and S3 subfolder.
- One failed video does not block others.

---

## 7) Secrets Manager usage

### Recommended secret keys
- `lightship/mvp/config`
  - Bedrock model ID, detection confidence thresholds, frame sampling parameters, max concurrent workers
- `lightship/mvp/prompts/classification`
  - LLM prompt templates for video classification
- `lightship/mvp/prompts/config_generation/<video_type>`
  - LLM prompt templates for config JSON generation per type (reactivity, qa, hazard, job_site)
- `lightship/mvp/prompts/qa_generation`
  - LLM prompt template for educational Q&A generation
- `lightship/mvp/schema/config`
  - Client config JSON schema (or S3 pointer if large):
    `{"schema_s3_uri": "...", "schema_version": "...", "schema_hash": "..."}`

---

## 8) IAM permissions sketch (least privilege)

### 8.1 Roles (recommended)

#### Role: `lightship-web-app-task-role` (ECS task role)
**Purpose**: UI upload + job submission + status queries

Minimum permissions:
- S3: `s3:PutObject` to `input/*`, `s3:GetObject` from `results/*`, `s3:ListBucket` with prefix constraints
- SQS: `sqs:SendMessage` to the processing queue
- DynamoDB: `dynamodb:PutItem`, `dynamodb:GetItem`, `dynamodb:Query`, `dynamodb:UpdateItem` on `lightship_jobs`
- CloudWatch Logs: `logs:CreateLogStream`, `logs:PutLogEvents`

#### Role: `lightship-worker-task-role` (ECS Fargate Worker task role)
**Purpose**: Video frame extraction, motion filtering, optional custom model inference

Minimum permissions:
- S3: `s3:GetObject` from `input/*`, `s3:PutObject` to `processing/*`
- Rekognition: `rekognition:DetectLabels` (if running detection inside the worker)
- CloudWatch Logs: `logs:CreateLogStream`, `logs:PutLogEvents`
- Secrets Manager: `secretsmanager:GetSecretValue` scoped to `lightship/mvp/*`
- KMS: `kms:Decrypt`, `kms:GenerateDataKey`

#### Role: `lightship-dispatcher-lambda-role`
**Purpose**: Consume SQS messages and start Step Functions executions

Minimum permissions:
- SQS: `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`
- Step Functions: `states:StartExecution`
- DynamoDB: `dynamodb:UpdateItem` on `lightship_jobs`
- CloudWatch Logs: standard

#### Role: `lightship-pipeline-lambda-role`
**Purpose**: Common role for pipeline Lambda functions (tracking, output assembly, notification)

Minimum permissions:
- S3: `s3:GetObject` on `processing/*`, `input/*`; `s3:PutObject` on `processing/*`, `results/*`
- DynamoDB: `dynamodb:UpdateItem`, `dynamodb:GetItem` on `lightship_jobs`
- Bedrock: `bedrock:InvokeModel`
- Rekognition: `rekognition:DetectLabels`
- SNS: `sns:Publish` to the notification topic
- Secrets Manager: `secretsmanager:GetSecretValue` scoped to `lightship/mvp/*`
- KMS: `kms:Decrypt`, `kms:Encrypt`, `kms:GenerateDataKey`
- CloudWatch Logs: standard

#### Role: `lightship-sfn-execution-role`
**Purpose**: Step Functions invokes Lambda, ECS RunTask, and AWS AI services

Minimum permissions:
- Lambda: `lambda:InvokeFunction` on all pipeline Lambdas
- ECS: `ecs:RunTask`, `ecs:StopTask`, `ecs:DescribeTasks` (for worker tasks)
- IAM: `iam:PassRole` for ECS task roles
- Rekognition: `rekognition:DetectLabels`
- Bedrock: `bedrock:InvokeModel`
- S3: `s3:GetObject`, `s3:PutObject` (for native integrations)
- DynamoDB: `dynamodb:UpdateItem`
- SNS: `sns:Publish`
- CloudWatch Logs: standard

### 8.2 IAM guardrails
- Scope S3 access using prefix-level permissions.
- Enforce encryption at rest via S3 bucket policy requiring `s3:x-amz-server-side-encryption: aws:kms`.
- Use distinct roles for web app, workers, and pipeline Lambdas to reduce blast radius.
- No long-lived IAM credentials; use IAM Identity Center for human access with MFA enforced.

---

## 9) Network and VPC guidance

### VPC layout
```
VPC (10.0.0.0/16) — us-east-1

  Public Subnet AZ-a (10.0.1.0/24)
    - ALB
    - NAT Gateway
    - Internet Gateway (attached to VPC)

  Public Subnet AZ-b (10.0.2.0/24)
    - ALB (multi-AZ)

  Private Subnet AZ-a (10.0.10.0/24)
    - ECS Fargate (Web App)
    - ECS Fargate (Workers)

  Private Subnet AZ-b (10.0.11.0/24)
    - ECS Fargate (Web App)
    - ECS Fargate (Workers)
```

### VPC endpoints
- **Gateway endpoints (free)**:
  - S3
- **Interface endpoints (PrivateLink)** — prioritized for MVP:
  - ECR (ecr.api + ecr.dkr) — required for Fargate image pulls
  - SQS
  - Step Functions
  - Bedrock
  - Rekognition
  - Secrets Manager
  - CloudWatch Logs

> MVP recommendation: Deploy S3 gateway + ECR interface endpoints as minimum. Other services can route through NAT GW initially and be added as cost optimization.

### Security group patterns
- **ALB SG**: Inbound 443 from 0.0.0.0/0; outbound to ECS Web App SG on container port.
- **ECS Web App SG**: Inbound from ALB SG only; outbound to VPC endpoints and NAT GW.
- **ECS Worker SG**: No inbound (tasks are launched internally); outbound to VPC endpoints and NAT GW.
- **VPC Endpoint SG**: Inbound 443 from private subnet CIDRs.

### Additional network security
- NACLs as additional defense layer on subnet boundaries.
- VPC Flow Logs enabled for network auditing.

---

## 10) SQS configuration

### Main queue: `lightship-processing-queue`
- Visibility timeout: 900 seconds (matches Step Functions max expected duration)
- Message retention: 4 days
- Encryption: SSE-KMS
- Message format:
```json
{
  "job_id": "uuid",
  "input_s3_path": "input/videos/{job_id}/{filename}.mp4",
  "output_s3_path": "results/{custom_or_default}/{job_id}/",
  "input_type": "video",
  "user_id": "user@example.com"
}
```

### Dead Letter Queue: `lightship-processing-dlq`
- Receive count threshold: 3 (after 3 failed attempts, message moves to DLQ)
- Retention: 14 days
- CloudWatch alarm: DLQ message count > 0 triggers SNS notification

---

## 11) Observability (what to log/measure)

### CloudWatch logs (minimum)
- Lambda: per-step invocation, durations, errors, model call metadata (no sensitive content)
- Step Functions: execution history and state transitions
- ECS Web App: access logs, user actions (upload, status check, download)
- ECS Workers: frame extraction progress, frame counts, processing duration

### Custom metrics to emit
- `videos_processed` (count, per video_class)
- `frames_extracted` vs `frames_selected` (shows filter efficiency)
- `detection_api_calls` (Rekognition call count)
- `classification_accuracy` (on validation runs)
- `processing_duration_seconds` (end-to-end per video)
- `batch_completion_time` (total for a batch upload)
- `dlq_message_count` (error tracking)

### Alarms
- DLQ depth > 0
- ECS task failure count > 0
- Step Functions execution failure rate > 5%
- ALB 5XX error rate > 1%
- ECS CPU utilization > 80%

### Dashboard
- Single CloudWatch dashboard showing: pipeline throughput, error rates, processing times, detection call volumes, queue depth.

---

## 12) Frame selection pipeline detail

### The problem
A 5-minute dashcam video at 30 FPS = 9,000 frames. Running inference on every frame is wasteful, slow, and expensive.

### Pipeline stages (runs inside ECS Fargate Worker)

**Stage 1 — FFmpeg extraction**: Extract frames at reduced rate (5 FPS). 30 FPS → 5 FPS = 83% reduction. 5-min video: ~1,500 frames.

**Stage 2 — Temporal sampling**: Select 1 frame per 0.5 seconds (2 FPS effective). ~1,500 → ~600 candidate frames. Additional 60% reduction.

**Stage 3 — Motion/scene-change filter**: SSIM comparison between consecutive selected frames. Drop near-duplicate static frames. Typical reduction: 30–50% on highway footage. Result: ~300–400 frames for a 5-min video.

**Stage 4 — Event window expansion** (post-inference feedback loop): After initial detection pass, if an event is detected (object enters lane, new hazard), extract additional frames ±2 seconds around event at 5 FPS. Re-run detection on expanded window for temporal context.

### Cost impact
- Rekognition cost per video: ~$0.40 (400 frames × $0.001) instead of ~$9.00 (9,000 frames)
- At 50 videos/month: ~$20/month for Rekognition vs ~$450/month
- Processing time: ~3–5 minutes per video (frame extraction 30–60s + inference 2–3 min)

---

## 13) Model evaluation strategy

### Benchmarking requirement
At least 2 pre-trained models benchmarked per detection category on the client-provided golden dataset (≥20 videos, ≥5 per type).

### Candidates
| Category | Candidate 1 | Candidate 2 | Candidate 3 |
|---|---|---|---|
| Motorcycles | Amazon Rekognition | YOLOv12 XL | Detectron2 |
| Lane markings | Detectron2 (panoptic) | Custom lane model | — |
| Road signs | Amazon Rekognition | YOLOv12 XL | Detectron2 |
| Construction objects | Amazon Rekognition | YOLOv12 XL | Detectron2 |

### Licensing consideration
- **Rekognition**: Managed service, no licensing concern.
- **Detectron2**: Apache 2.0 license, permissive for internal and commercial use.
- **YOLOv12 (Ultralytics)**: AGPL license — may require code to be made public if used. Internal-only use may be permissible, but requires legal clarification. Recommendation: prefer Detectron2 over YOLO to avoid licensing risk.

### Custom models run environment
- Containerized Fargate tasks with model weights baked into Docker images.
- 4 vCPU, 16 GB RAM for CPU inference. GPU not required at MVP volume.

---

## 14) Deployment notes (CloudFormation)

### Nested stack structure
- `main.yaml` — parent stack orchestrating all nested stacks
- `network.yaml` — VPC, subnets, Internet GW, NAT GW, VPC endpoints, security groups
- `security.yaml` — KMS CMK, Secrets Manager secrets, IAM roles
- `compute.yaml` — ECS cluster, task definitions (web app + worker), ALB, target groups, ECR repos
- `processing.yaml` — SQS queues, Step Functions state machine, Lambda functions
- `storage.yaml` — S3 bucket (with lifecycle rules), DynamoDB table
- `monitoring.yaml` — CloudWatch log groups, dashboards, alarms, SNS topics

### Deployment
- Single `aws cloudformation deploy` command to provision the entire environment.
- Region: **us-east-1** (confirmed for cost efficiency).
- Deployable to both Commit AI-MVP AWS account and client's own AWS account via documented scripts.

### Suggested environments
- `dev` (looser controls, fast iteration on Commit account)
- `mvp` (locked down, deployed to client account for validation)

---

## 15) Cost estimates (MVP scale)

| Service | Estimate (monthly) | Notes |
|---|---|---|
| ECS Fargate (Web App) | ~$30 | 2 tasks, 0.5 vCPU / 1 GB each |
| ECS Fargate (Workers) | ~$20 | On-demand tasks, ~50 video/month × 5 min each |
| Rekognition | ~$20 | ~400 frames × 50 videos × $0.001/image |
| Bedrock (Claude) | ~$1–2 | ~50 classification + config calls, ~$0.02/call |
| S3 | ~$5 | Low storage at MVP scale |
| DynamoDB | ~$0 | Free tier at MVP volume |
| NAT Gateway | ~$32 | Fixed cost + data transfer |
| ALB | ~$22 | Fixed cost + LCU charges |
| SQS / SNS | <$1 | Low message volume |
| CloudWatch | ~$5 | Logs, metrics, dashboard |
| KMS | ~$1 | 1 CMK |
| VPC Endpoints (interface) | ~$7–50 | $7.20/month each, depends on how many deployed |
| **Total estimated** | **~$145–190/month** | |

---

## 16) References (AWS docs)

- Bedrock VPC interface endpoints: https://docs.aws.amazon.com/bedrock/latest/userguide/vpc-interface-endpoints.html
- Rekognition DetectLabels API: https://docs.aws.amazon.com/rekognition/latest/dg/labels-detect-labels-image.html
- Step Functions VPC endpoints: https://docs.aws.amazon.com/step-functions/latest/dg/vpc-endpoints.html
- ECS Fargate RunTask (Step Functions integration): https://docs.aws.amazon.com/step-functions/latest/dg/connect-ecs.html
- SQS dead-letter queues: https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
- S3 event notifications via EventBridge: https://docs.aws.amazon.com/AmazonS3/latest/userguide/EventBridge.html
- CloudFormation nested stacks: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-nested-stacks.html
- Secrets Manager overview: https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html
- CloudTrail: https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-user-guide.html
- PrivateLink supported services: https://docs.aws.amazon.com/vpc/latest/privatelink/aws-services-privatelink-support.html

---

## Appendix A – Example S3 bucket policy snippets

**Require SSE-KMS on uploads**
- Require `s3:x-amz-server-side-encryption` == `aws:kms`
- Require `s3:x-amz-server-side-encryption-aws-kms-key-id` == your CMK ARN

**Constrain Web App writes**
- Only allow PutObject to `input/*`
- Deny deletes for MVP stability

**Constrain Worker writes**
- Only allow PutObject to `processing/*`
- Only allow GetObject from `input/*`

**Constrain Pipeline Lambda writes**
- Allow PutObject to `processing/*` and `results/*`
- Allow GetObject from `processing/*` and `input/*`

---

## Appendix B – End-to-end data flow summary

```
User uploads video via Web UI
        ↓
Web App stores video to S3 (input/videos/{job_id}/)
        ↓
Web App sends job message to SQS
        ↓
Lambda Dispatcher consumes message, starts Step Functions
        ↓
Step Functions → ECS Worker: frame extraction + selection
        ↓
Selected frames written to S3 (processing/selected_frames/)
        ↓
Step Functions → Rekognition: object detection on selected frames
        ↓
Step Functions → Lambda: cross-frame tracking + hazard assessment
        ↓
Step Functions → Bedrock: video classification + config generation
        ↓
Step Functions → Lambda: generate annotated frames
        ↓
Results saved to S3 (results/{output_path}/)
        ↓
DynamoDB updated (status=COMPLETED)
        ↓
SNS notification sent
        ↓
User views/downloads results in UI
```

---

## Appendix C – Detection output schemas

### Per-frame detection output
```json
{
  "job_id": "uuid",
  "frame_number": 42,
  "timestamp_sec": 21.0,
  "objects": [
    {
      "class": "motorcycle",
      "confidence": 0.92,
      "bbox": [120, 200, 80, 60],
      "distance_m": 15.3
    }
  ],
  "lanes": [
    {
      "lane_id": "ego_lane",
      "type": "ego_lane",
      "polygon": [[100,400], [200,300], [300,300], [400,400]]
    }
  ],
  "road_signs": [
    {
      "label": "speed_limit",
      "bbox": [500, 50, 40, 50],
      "confidence": 0.88
    }
  ],
  "traffic_signals": [
    {
      "label": "green",
      "bbox": [450, 30, 30, 60],
      "confidence": 0.95
    }
  ]
}
```

### Video classification config output (example: hazard type)
```json
{
  "job_id": "uuid",
  "video_class": "hazard_detection",
  "road_type": "highway",
  "weather": "rain",
  "traffic": "moderate",
  "speed": "60mph",
  "hazards": [
    {
      "timestamp_sec": 45.5,
      "severity": "high",
      "description": "Motorcycle entering driver's lane from left",
      "object_id": "obj_003"
    }
  ],
  "config": {
    "training_type": "hazard_detection",
    "risk_levels": ["high"],
    "scenario_descriptions": ["Lane intrusion by motorcycle in rain"]
  }
}
```

---
