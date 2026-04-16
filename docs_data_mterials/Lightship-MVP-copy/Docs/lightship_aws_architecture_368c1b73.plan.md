---
name: LightShip AWS Architecture
overview: Comprehensive AWS architecture design for LightShip's AI-Powered Video Annotation and Classification MVP, synthesizing the kickoff deck, task management requirements, weekly meeting notes, and draft architecture into a single production-ready, Lucidchart-ready design.
todos:
  - id: review-feedback
    content: Review user feedback on the architecture plan and refine any sections
    status: pending
  - id: resolve-questions
    content: Resolve open questions with the user before finalizing
    status: pending
  - id: generate-mermaid
    content: Generate Mermaid diagram code for all 3 diagrams if user wants in-editor visualization
    status: pending
  - id: generate-cfn
    content: Optionally scaffold CloudFormation templates for the infrastructure
    status: pending
  - id: create-doc
    content: Write the final architecture document to a file in the LightShip directory
    status: pending
isProject: false
---

# LightShip MVP -- Complete AWS Architecture Design

---

## 1. Executive Summary

LightShip is a driver training and safety company that creates interactive training content from dashcam footage. The MVP builds an **AI-powered pipeline** that ingests dashcam videos (and single images), detects objects (motorcycles, lanes, road signs, construction objects), classifies each video into one of 4 training types (Reactivity, Educational/Q&A, Hazard Detection, Job Site), and auto-generates type-specific config JSONs -- all deployed on AWS with async batch processing, a web UI, and production-grade security.

**Scale context:** ~1,000--2,000 images/month, batch uploads of 10+ videos, 30-50 sample videos in the initial dataset. This is a low-to-moderate volume MVP with a $55K budget and 6-week timeline.

**Key architectural decision:** The system uses **Lambda for lightweight orchestration**, **ECS Fargate for heavy video/frame processing and custom model inference**, **Amazon Rekognition for managed object detection**, **Amazon Bedrock for LLM-based video classification and config generation**, and **AWS Step Functions to orchestrate the multi-stage pipeline**.

---

## 2. Requirements Extracted from Source Material

### 2.1 Business Requirements

- Automated dashcam video annotation replacing manual labeling
- Classify videos into 4 types: Reactivity/Braking, Educational/Q&A, Hazard Detection, Job Site
- Auto-generate type-specific config JSONs matching the client's application schema
- Improve detection accuracy over PoC baseline (>=80% across all categories)
- Support both video and single-image upload modes
- Batch async processing with per-video output isolation

### 2.2 Detection Requirements


| Category             | Target                                | Output                                                         |
| -------------------- | ------------------------------------- | -------------------------------------------------------------- |
| Motorcycles          | Recall >= 80%                         | class, bbox, distance_m                                        |
| Lane markings        | IoU >= 80%                            | lane_id, polygons, type, colored overlays                      |
| Road signs/signals   | Precision >= 80%                      | sign_label, bbox (speed/stop/yield/warning/info/traffic light) |
| Construction objects | Precision >= 80%                      | object_label, bbox, distance, description (15+ labels)         |
| Road type            | >= 80% accuracy                       | highway / city / town / rural                                  |
| Weather              | Correct field in output               | rain / low_visibility / clear                                  |
| Cross-frame tracking | Hazard when object enters driver lane | hazard with risk level                                         |


### 2.3 Classification Requirements

- LLM classifies video into: reactivity_braking, qa_educational, hazard_detection, job_site_detection
- > = 80% classification accuracy on 20 test videos (5 per class)
- Config JSON generated per video matches client schema and is saved to S3

### 2.4 UI Requirements

- Upload single video / batch of videos
- Upload single image for job-site detection
- Specify custom S3 output path (or use default)
- View processing status with progress indicator
- Download/view results (annotated frames + JSON)
- Completion notification in UI
- Error isolation: one failed video must not block others

### 2.5 Infrastructure Requirements

- Run on Commit AI-POC AWS account initially
- Deployable to client AWS account via scripts
- IaC via CloudFormation
- us-east-1 region
- VPC with public/private subnets
- Secrets management, encryption, IAM best practices

### 2.6 Model Evaluation Requirements

- At least 2 pre-trained models benchmarked per detection category
- Candidates: Detectron2, Amazon Rekognition, YOLOv12 XL
- YOLO AGPL licensing concern (internal use may be permissible, but needs clarification)
- Recommendation: Use Rekognition as primary managed service; benchmark custom models (Detectron2/YOLO) in containerized Fargate tasks

### 2.7 MVP vs Future Scope

**Required for MVP:**

- All detection categories with >= 80% metrics
- Video classification + config generation
- Batch async processing
- Single image mode
- Web UI
- CloudFormation deployment
- Documentation + architecture diagrams

**Future / Optional:**

- Extension to non-driving environments (warehouses, job sites beyond dashcam)
- Agentic workflows for recommendation system
- Multi-account AWS organization with dedicated prod/staging
- Custom fine-tuned models
- X-Ray tracing
- Multi-region deployment

---

## 3. Recommended AWS Architecture

### 3.1 Architecture Decision Summary


| Decision           | Choice                                                     | Rationale                                                                                                                                                                       |
| ------------------ | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Orchestration      | **Step Functions**                                         | Multi-step pipeline with branching (video vs image), error handling per step, visual debugging, native integration with Lambda/ECS/Rekognition                                  |
| Video processing   | **Lambda (orchestrator) + ECS Fargate (workers)**          | Lambda handles lightweight triggers; Fargate handles long-running FFmpeg frame extraction and custom model inference (15-min Lambda limit is insufficient for video processing) |
| Object detection   | **Rekognition (primary) + optional Fargate custom models** | Rekognition avoids YOLO licensing risk, is managed/scalable; custom models (Detectron2) run as Fargate tasks for benchmarking                                                   |
| LLM classification | **Amazon Bedrock**                                         | Managed, no infrastructure to maintain, supports Claude/Titan for video classification and config generation                                                                    |
| Queue              | **SQS with DLQ**                                           | Decouples upload from processing, handles batch, provides retry + error isolation                                                                                               |
| Storage            | **Single S3 bucket with prefix structure**                 | Simpler IAM, simpler lifecycle policies, prefixes provide logical separation                                                                                                    |
| Notifications      | **SNS -> UI polling / WebSocket**                          | SNS for backend completion events; UI polls or uses WebSocket for real-time status                                                                                              |
| IaC                | **CloudFormation**                                         | Explicitly mentioned in requirements; client familiarity with AWS-native tooling                                                                                                |
| Networking         | **Private subnets for compute, public only for ALB**       | Security best practice; NAT Gateway for outbound from private subnets                                                                                                           |


### 3.2 High-Level Architecture (Top-to-Bottom)

```
Users (Browser)
    |
Route 53 (DNS)
    |
AWS WAF
    |
Application Load Balancer (public subnet)
    |
ECS Fargate - Web App (private subnet)
    |                           |
    |-- Upload --> S3 (input/)  |-- Status API
    |                           |
S3 Event / API --> SQS (job queue)
    |
Lambda (Job Dispatcher)
    |
Step Functions (Pipeline Orchestrator)
    |
    +-- [Step 1] Lambda: Validate + classify input type (video vs image)
    |
    +-- [Step 2] ECS Fargate Worker: Frame extraction + selection (FFmpeg)
    |       |-- Writes selected frames to S3 (processing/selected_frames/)
    |
    +-- [Step 3a] Rekognition: Object detection on selected frames
    +-- [Step 3b] ECS Fargate: Custom model inference (Detectron2) -- parallel
    |       |-- Writes detection results to S3
    |
    +-- [Step 4] Lambda: Cross-frame tracking + hazard assessment
    |
    +-- [Step 5] Bedrock: Video classification + config JSON generation
    |       |-- Writes config JSON to S3 (results/)
    |
    +-- [Step 6] Lambda: Generate annotated frames + final output
    |       |-- Writes annotated frames to S3 (results/)
    |
    +-- [Step 7] SNS: Completion notification
    |
CloudWatch (logs, metrics, alarms)
```

### 3.3 Network Architecture

```
VPC (10.0.0.0/16) -- us-east-1
  |
  +-- Public Subnet AZ-a (10.0.1.0/24)
  |     +-- ALB
  |     +-- NAT Gateway
  |     +-- Internet Gateway (attached to VPC)
  |
  +-- Public Subnet AZ-b (10.0.2.0/24)
  |     +-- ALB (multi-AZ)
  |     +-- NAT Gateway (multi-AZ)
  |
  +-- Private Subnet AZ-a (10.0.10.0/24)
  |     +-- ECS Fargate (Web App)
  |     +-- ECS Fargate (Workers)
  |
  +-- Private Subnet AZ-b (10.0.11.0/24)
  |     +-- ECS Fargate (Web App)
  |     +-- ECS Fargate (Workers)
  |
  +-- VPC Endpoints:
        +-- S3 (Gateway endpoint - free)
        +-- SQS (Interface endpoint)
        +-- Bedrock (Interface endpoint)
        +-- Rekognition (Interface endpoint)
        +-- Secrets Manager (Interface endpoint)
        +-- CloudWatch Logs (Interface endpoint)
        +-- ECR (Interface endpoints: ecr.api + ecr.dkr + s3 gateway)
        +-- Step Functions (Interface endpoint)
```

---

## 4. Video / Frame Selection Design

### 4.1 The Problem

A dashcam video at 30 FPS for 5 minutes = 9,000 frames. Running inference on every frame is wasteful, slow, and expensive. Most consecutive frames are near-identical.

### 4.2 Frame Selection Pipeline (runs inside ECS Fargate Worker)

```
Input Video (S3)
    |
    v
[Stage 1] FFmpeg Frame Extraction
    Extract ALL frames at native resolution to a temp working directory
    (or extract at reduced rate e.g., 5 FPS to reduce I/O)
    |
    v
[Stage 2] Temporal Sampling
    Select 1 frame per 0.5 seconds (2 FPS effective)
    For a 30 FPS source: reduces frames by ~93%
    A 5-min video: 9,000 frames -> ~600 candidate frames
    |
    v
[Stage 3] Motion / Scene-Change Filter
    Lightweight pixel-level filtering on the temporally sampled frames:
    - Compute frame difference (absolute pixel diff between consecutive selected frames)
    - If diff < threshold: mark as "static" and skip
    - If diff > threshold: mark as "significant change" and keep
    Algorithm: structural similarity (SSIM) or histogram comparison
    Typical reduction: another 30-50% on highway footage (many static frames)
    Result: ~300-400 frames for a 5-min video
    |
    v
[Stage 4] Event Window Expansion (post-inference feedback loop)
    After initial inference on selected frames:
    - If an event is detected (object enters lane, new sign, hazard)
    - Expand the window: extract additional frames +/- 2 seconds around the event at higher density (e.g., 5 FPS)
    - Re-run inference on the expanded window
    This preserves temporal context for cross-frame tracking and hazard timing
    |
    v
[Output] Selected Frames
    Uploaded to S3: s3://bucket/processing/{job_id}/selected_frames/
    Metadata JSON: frame_number, timestamp_sec, selection_reason (temporal/motion/event_expansion)
```

### 4.3 Where Each Stage Runs


| Stage                  | Runs On                                    | Why                                                                                        |
| ---------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------ |
| FFmpeg extraction      | ECS Fargate Worker                         | CPU-intensive, needs filesystem, exceeds Lambda limits                                     |
| Temporal sampling      | ECS Fargate Worker (same task)             | Trivial logic, co-located with extraction                                                  |
| Motion filter          | ECS Fargate Worker (same task)             | Needs access to raw frames, lightweight OpenCV                                             |
| Event window expansion | ECS Fargate Worker (second pass) or Lambda | Triggered after initial inference results; may re-invoke Fargate for additional extraction |


### 4.4 Cost / Latency / Quality Impact

- **Cost:** Reduces Rekognition API calls from ~9,000 to ~300-400 per 5-min video. At $0.001/image, a 5-min video costs ~$0.40 instead of ~$9.00.
- **Latency:** Frame extraction + filtering takes ~30-60 seconds for a 5-min video on Fargate (2 vCPU). Inference on 400 frames through Rekognition takes ~2-3 minutes (batched). Total: ~3-5 min per video.
- **Quality:** Event window expansion ensures no critical events are missed. The 0.5s temporal sampling is dense enough to catch most road events. Motion filter removes only truly static frames.

### 4.5 Adaptive Sampling (Future Enhancement)

For production, implement adaptive sampling rates based on driving context:

- Highway (low change rate): 1 frame/second
- Urban intersection: 4 frames/second
- Construction zone: 3 frames/second
- Detected event: 10 frames/second for +/- 2 seconds

This can be driven by road_type classification output from an initial low-res pass.

---

## 5. Service-by-Service Rationale

### 5.1 VPC

- **Why:** Isolates all compute and data in a private network. Required for production security.
- **Responsibility:** Network boundary for all AWS resources.
- **Subnet design:** 2 public (ALB, NAT GW) + 2 private (ECS, Lambda VPC-attached if needed) across 2 AZs.
- **Security:** No compute in public subnets. All egress via NAT Gateway or VPC endpoints.

### 5.2 Internet Gateway

- **Why:** Required for ALB to receive inbound traffic from users.
- **Placement:** Attached to VPC, routes in public subnet route tables.

### 5.3 NAT Gateway

- **Why:** ECS Fargate tasks in private subnets need outbound internet access (ECR image pull, external APIs if needed).
- **Placement:** One per AZ in public subnets. For MVP cost savings, one NAT GW is acceptable (single AZ risk is tolerable for MVP).
- **Cost consideration:** NAT GW costs ~$32/month + data processing. For MVP with low traffic, single NAT GW is fine.

### 5.4 VPC Endpoints

- **Why:** Keeps traffic to AWS services (S3, SQS, Bedrock, Rekognition, etc.) on the AWS backbone network -- lower latency, no NAT GW data charges, better security.
- **S3 Gateway Endpoint:** Free, must-have.
- **Interface Endpoints:** SQS, Bedrock, Rekognition, Secrets Manager, CloudWatch Logs, ECR, Step Functions. Each costs ~$7.20/month. For MVP, prioritize S3 (gateway, free) + ECR (required for Fargate image pulls). Others can route through NAT GW initially and be added as cost optimization later.
- **MVP recommendation:** Deploy S3 gateway endpoint + ECR interface endpoints. Add Bedrock/Rekognition endpoints if data transfer costs through NAT GW become significant.

### 5.5 Route 53

- **Why:** DNS resolution for the web application domain.
- **Responsibility:** Maps custom domain (e.g., app.lightship.ai) to ALB.
- **Placement:** Global service (outside VPC).
- **MVP note:** Optional for MVP if using the ALB DNS name directly. Include if client wants a branded URL.

### 5.6 AWS WAF

- **Why:** Protects the web application from common attacks (SQL injection, XSS, bot traffic).
- **Responsibility:** Attached to ALB, filters inbound HTTP requests.
- **Placement:** Edge service (outside VPC, attached to ALB).
- **MVP config:** AWS Managed Rules (Core Rule Set + Known Bad Inputs). Rate limiting rule (e.g., 1000 requests per 5 min per IP).

### 5.7 Application Load Balancer (ALB)

- **Why:** Entry point for all user traffic. Distributes across ECS tasks. Terminates TLS.
- **Responsibility:** Routes /api/* to backend target group, /* to frontend target group (or single target group if monolithic).
- **Placement:** Public subnets (both AZs).
- **Security:** HTTPS listener (port 443) with ACM certificate. HTTP (80) redirects to HTTPS. Security group: inbound 443 from 0.0.0.0/0, outbound to ECS security group on container port.

### 5.8 ECS Fargate -- Web Application Service

- **Why:** Hosts the frontend UI and backend API. Fargate = serverless containers, no EC2 management.
- **Responsibility:** Serve web UI, handle uploads (presigned S3 URLs), submit jobs to SQS, query job status, serve results.
- **Placement:** Private subnets.
- **Scaling:** Desired count = 2 (one per AZ for HA). Auto-scale 2-4 based on CPU/request count.
- **Container spec:** 0.5 vCPU, 1 GB RAM (sufficient for web serving).
- **Security:** Security group allows inbound only from ALB security group. IAM task role with permissions for S3 (read/write), SQS (send), DynamoDB or Step Functions (status query).
- **Image:** Stored in ECR private repository.

### 5.9 Amazon S3

- **Why:** Central storage for all inputs, intermediate artifacts, and outputs.
- **Responsibility:** Store uploaded videos/images, extracted frames, selected frames, detection results, annotated frames, config JSONs.
- **Bucket structure (single bucket, prefix-separated):**

```
  s3://lightship-mvp-{account-id}/
    input/
      videos/{job_id}/{filename}
      images/{job_id}/{filename}
    processing/
      frames/{job_id}/
      selected_frames/{job_id}/
      detection_results/{job_id}/
    results/
      {custom_output_path OR default/{job_id}}/
        annotated_frames/
        config.json
        detection_summary.json
  

```

- **Security:** Bucket policy denies public access. Server-side encryption with KMS (SSE-KMS). Versioning enabled. Lifecycle policy: move processing/* to Glacier after 30 days, delete after 90.
- **Placement:** Regional service (us-east-1), accessed via VPC gateway endpoint.

### 5.10 Amazon SQS

- **Why:** Decouples upload API from processing pipeline. Enables batch processing, retry logic, and backpressure.
- **Responsibility:** Receives job messages from the web app API. Each message contains: job_id, input_s3_path, output_s3_path, input_type (video/image), user_id.
- **Configuration:**
  - Visibility timeout: 900 seconds (15 min, matches Step Functions execution time)
  - Message retention: 4 days
  - DLQ: After 3 failed receive attempts, message moves to DLQ
  - Encryption: SSE-SQS or SSE-KMS
- **Placement:** Regional service, accessed via VPC endpoint or NAT GW.

### 5.11 Dead Letter Queue (DLQ)

- **Why:** Captures failed processing jobs for investigation. Prevents poison messages from blocking the queue.
- **Responsibility:** Stores messages that failed 3 processing attempts.
- **Monitoring:** CloudWatch alarm on DLQ message count > 0 triggers SNS notification to ops team.

### 5.12 AWS Lambda -- Job Dispatcher

- **Why:** Lightweight trigger that consumes SQS messages and starts Step Functions executions.
- **Responsibility:** Receives SQS event, validates message, starts Step Functions state machine execution with job parameters.
- **Runtime:** Python 3.12, 256 MB RAM, 30-second timeout.
- **Placement:** Regional service (not VPC-attached unless needed, to avoid cold start overhead and ENI costs).
- **Concurrency:** Reserved concurrency = 10 (matches max parallel processing capacity for MVP).

### 5.13 AWS Step Functions

- **Why:** Orchestrates the multi-step processing pipeline with built-in error handling, retries, parallel execution, and visual monitoring. Superior to Lambda-chaining for this use case because the pipeline has 5+ steps with branching logic.
- **Responsibility:** Manages the entire processing lifecycle:
  1. Input validation + type routing (video vs image)
  2. Frame extraction + selection (Fargate task)
  3. Object detection (Rekognition API calls -- parallel with custom model if applicable)
  4. Cross-frame tracking + hazard assessment (Lambda)
  5. Video classification + config generation (Bedrock)
  6. Output assembly + notification (Lambda + SNS)
- **Placement:** Regional service.
- **Configuration:** Standard Workflows (not Express) -- executions can run up to 1 year, with per-state-transition pricing. MVP volume is low enough that Standard is fine.
- **Error handling:** Each step has Catch/Retry blocks. On unrecoverable failure, the state machine writes error status and sends failure notification.

### 5.14 ECS Fargate -- Video Processing Worker

- **Why:** Video frame extraction with FFmpeg, motion filtering with OpenCV, and optional custom model inference (Detectron2) all require significant CPU/memory and exceed Lambda's 15-min/10GB limits.
- **Responsibility:**
  - Download video from S3
  - Run FFmpeg frame extraction
  - Apply temporal sampling + motion filter
  - Upload selected frames to S3
  - (Optional) Run Detectron2 / YOLO inference on selected frames
- **Placement:** Private subnets.
- **Container spec:** 4 vCPU, 8 GB RAM (for FFmpeg + OpenCV). For custom model inference: 4 vCPU, 16 GB RAM (or GPU if needed -- but for MVP, CPU inference is acceptable given low volume).
- **Scaling:** Launched as individual tasks by Step Functions (RunTask API). No persistent service needed. Max concurrent tasks = 10 for MVP.
- **Security:** IAM task role with S3 read/write, no internet-facing ports.
- **Image:** Multi-stage Docker image with FFmpeg, OpenCV, Python, and model weights baked in.

### 5.15 Amazon Rekognition

- **Why:** Managed object detection service. No infrastructure to manage. Handles motorcycle, vehicle, person, road sign detection out of the box. Avoids YOLO AGPL licensing concern.
- **Responsibility:** DetectLabels / DetectCustomLabels API on selected frames. Returns: labels with confidence, bounding boxes.
- **Limitations:** Rekognition may not natively detect all 50+ road sign types or construction-specific objects with sufficient granularity. For specialized detection, custom models (Detectron2) may be needed as fallback/supplement.
- **Placement:** Regional managed service. Accessed from Lambda or Fargate via VPC endpoint or NAT GW.
- **Cost:** $0.001 per image (DetectLabels). ~400 frames/video * $0.001 = $0.40/video. At 50 videos/month: ~$20/month.
- **Rekognition Custom Labels:** Option to train domain-specific models (e.g., construction objects, specific road signs) using client's labeled data. Requires ~250+ labeled images per class. Inference runs on dedicated Rekognition endpoints ($4/hr while running).

### 5.16 Amazon Bedrock

- **Why:** Managed LLM service for video classification and config JSON generation. No model hosting infrastructure needed.
- **Responsibility:**
  1. **Video Classification:** Receives aggregated detection metadata from all frames of a video (object counts, types, scene descriptors, hazard events). Classifies into one of 4 video types.
  2. **Config Generation:** Given the video type and detection results, generates a type-specific config JSON matching the client's schema.
  3. **Q&A Generation:** For educational videos, generates >= 3 relevant questions per video.
- **Model:** Claude 3.5 Sonnet (via Bedrock) or Amazon Titan. Claude recommended for structured JSON output and reasoning quality.
- **Placement:** Regional managed service. Accessed from Lambda via Bedrock API.
- **Cost:** Claude 3.5 Sonnet: ~$3/million input tokens, ~$15/million output tokens. Each video classification call: ~2K input tokens + ~1K output tokens = ~$0.02/video. Negligible at MVP scale.
- **Security:** No customer data used for training. Bedrock does not store prompts/completions.

### 5.17 AWS Lambda -- Post-Processing Functions

Several lightweight Lambda functions handle steps between the heavy processing:

- **Cross-Frame Tracker Lambda:** Takes per-frame detection results, applies object tracking logic (IoU-based matching across consecutive frames), detects hazards (object entering driver's lane). Runtime: Python 3.12, 1 GB RAM, 5-min timeout.
- **Output Assembler Lambda:** Generates annotated frame overlays (bounding boxes, labels on frames), writes final output to S3. Runtime: Python 3.12 with Pillow, 2 GB RAM, 5-min timeout.
- **Notification Lambda:** Sends completion event to SNS topic and updates job status in DynamoDB.

### 5.18 Amazon DynamoDB (Recommended Addition)

- **Why:** Track job status for the UI. Step Functions execution ARN alone is not user-friendly for status queries.
- **Responsibility:** Store job records: job_id, user_id, status (QUEUED/PROCESSING/COMPLETED/FAILED), input_path, output_path, created_at, completed_at, error_message.
- **Placement:** Regional managed service.
- **Design:** Single table, partition key = job_id. GSI on user_id for listing user's jobs.
- **Cost:** On-demand capacity. At MVP scale, effectively free tier.

### 5.19 Amazon SNS

- **Why:** Sends completion/failure notifications.
- **Responsibility:** Publishes messages when Step Functions completes (success or failure). Subscribers: ops team email, potentially a Lambda that pushes WebSocket updates to the UI.
- **Placement:** Regional service.

### 5.20 Amazon CloudWatch

- **Why:** Centralized logging, metrics, and alerting.
- **Responsibility:**
  - **Logs:** All Lambda functions, ECS tasks, and Step Functions log to CloudWatch Logs.
  - **Metrics:** Custom metrics for: videos_processed, frames_selected, detection_calls, classification_accuracy, processing_duration_seconds.
  - **Alarms:** DLQ depth > 0, ECS task failures > 0, Step Functions execution failures > 0, 5XX error rate on ALB > 1%.
  - **Dashboard:** Single CloudWatch dashboard showing pipeline health, throughput, error rates.
- **Placement:** Regional service.

### 5.21 AWS Secrets Manager

- **Why:** Store sensitive configuration (API keys if any, database credentials if applicable, Bedrock model configuration).
- **Responsibility:** Secure storage and rotation of secrets.
- **Placement:** Regional service, accessed via VPC endpoint.
- **MVP use:** Store any third-party API keys, custom model configuration parameters. ECS tasks and Lambda functions read secrets at startup.

### 5.22 AWS KMS

- **Why:** Encryption key management for S3, SQS, Secrets Manager, DynamoDB.
- **Responsibility:** Single customer-managed KMS key (CMK) used across all services for encryption at rest.
- **Configuration:** Key policy grants access to IAM roles for Lambda, ECS, and Step Functions.

### 5.23 IAM / IAM Identity Center

- **Why:** Fine-grained access control.
- **Design:**
  - **ECS Web App Task Role:** S3 (read input/results), SQS (send messages), DynamoDB (read/write jobs).
  - **ECS Worker Task Role:** S3 (read input, write processing + results), Rekognition (DetectLabels), no SQS.
  - **Lambda Dispatcher Role:** SQS (receive/delete), Step Functions (start execution).
  - **Lambda Processing Roles:** S3 (read/write), Bedrock (InvokeModel), Rekognition (DetectLabels), DynamoDB (update), SNS (publish).
  - **Step Functions Role:** Lambda (invoke), ECS (RunTask), Rekognition, Bedrock, SNS, S3, DynamoDB.
  - **Developer access:** IAM Identity Center with SSO for the development team. PowerUserAccess for dev, ReadOnly for stakeholders.

### 5.24 AWS CloudFormation

- **Why:** IaC required by the project. AWS-native, integrates directly with all services.
- **Responsibility:** Define all infrastructure as nested stacks:
  - `network.yaml` -- VPC, subnets, NAT GW, endpoints
  - `security.yaml` -- KMS, Secrets Manager, IAM roles
  - `compute.yaml` -- ECS cluster, task definitions, ALB
  - `processing.yaml` -- SQS, Step Functions, Lambda functions
  - `storage.yaml` -- S3 bucket, DynamoDB table
  - `monitoring.yaml` -- CloudWatch dashboards, alarms, SNS topics
  - `main.yaml` -- Parent stack orchestrating all nested stacks
- **Deployment:** Single `aws cloudformation deploy` command to provision the entire environment.

### 5.25 Amazon ECR

- **Why:** Private Docker image registry for ECS Fargate containers.
- **Repositories:** `lightship-web-app`, `lightship-video-worker`, `lightship-model-inference` (if custom models are separate).

### 5.26 EventBridge (Recommended for MVP)

- **Why:** S3 event notifications for triggering processing on upload. More flexible than S3 direct-to-SQS.
- **Responsibility:** When a video/image is uploaded to `s3://bucket/input/`, EventBridge rule triggers, sends event to SQS queue.
- **Benefit:** Decouples the upload trigger from the API -- supports both API-initiated and direct S3 upload workflows.

---

## 6. Security and Production Best Practices

### 6.1 Network Security

- All compute in private subnets
- ALB in public subnets is the only internet-facing component
- Security groups follow least-privilege (ALB -> ECS on container port only)
- NACLs as additional defense layer on subnet boundaries
- VPC Flow Logs enabled for network auditing

### 6.2 Data Security

- All data encrypted at rest with KMS CMK (S3, SQS, DynamoDB, CloudWatch Logs)
- All data encrypted in transit (TLS 1.2+ everywhere)
- S3 bucket policy: Block Public Access enabled, enforce SSL
- No sensitive data in environment variables (use Secrets Manager)

### 6.3 Identity and Access

- IAM roles follow least-privilege per service
- No long-lived IAM credentials
- IAM Identity Center for human access
- MFA enforced for console access

### 6.4 Operational Security

- CloudTrail enabled for API audit logging
- GuardDuty recommended for threat detection
- Automated vulnerability scanning on ECR images

### 6.5 Resilience

- Multi-AZ for ALB and ECS service
- SQS message retry with DLQ for failed processing
- Step Functions retry policies on each state
- Individual video failure does not block batch

---

## 7. Lucidchart Diagram Instructions

### 7.1 General Layout

- **Orientation:** Top-to-bottom
- **Canvas size:** Large (at least 1400x1000 for the high-level diagram)
- **Outer boundary:** Draw a large rectangle labeled "AWS Cloud (us-east-1)"
- **Inside AWS Cloud:** Draw a rectangle labeled "VPC (10.0.0.0/16)"
- **Outside both:** Place "Users" icon at the very top, and global services (Route 53, WAF, CloudFront if applicable, IAM, CloudFormation) along the top edge outside the VPC but inside the AWS Cloud boundary

### 7.2 Diagram Layers (top to bottom inside the AWS Cloud box)

**Layer 1: Edge / Global Services** (outside VPC, inside AWS Cloud)

- Route 53
- AWS WAF
- IAM Identity Center
- CloudFormation
- CloudWatch (monitoring)

**Layer 2: Public Subnets** (inside VPC, top section)

- ALB
- NAT Gateway
- Internet Gateway (on VPC boundary)

**Layer 3: Private Subnets - Application** (inside VPC, middle section)

- ECS Fargate: Web App Service

**Layer 4: Private Subnets - Processing** (inside VPC, lower-middle section)

- ECS Fargate: Video Worker Tasks
- Lambda: Job Dispatcher
- Lambda: Post-Processing functions
- Step Functions

**Layer 5: AI Services** (outside VPC, right side)

- Amazon Rekognition
- Amazon Bedrock

**Layer 6: Storage & Data** (outside VPC, bottom)

- Amazon S3 (with prefix labels)
- Amazon DynamoDB
- Amazon SQS + DLQ

**Layer 7: Security & Secrets** (outside VPC, left side)

- AWS KMS
- AWS Secrets Manager
- VPC Endpoints (shown as dots on VPC boundary)

---

## 8. Diagram 1 Definition: High-Level AWS Solution Architecture

### Components to Place

**Top (outside AWS Cloud):**

- Users icon (laptop/person)

**Inside AWS Cloud, Outside VPC (top row):**

- Route 53
- AWS WAF
- CloudFormation
- IAM Identity Center

**Inside VPC - Public Subnets (row 2):**

- Internet Gateway (on VPC left edge)
- ALB (spanning both AZ boxes)
- NAT Gateway (in each AZ, or single)

**Inside VPC - Private Subnets (row 3-4):**

- ECS Fargate: "Web App" (2 tasks across AZs)
- ECS Fargate: "Video Workers" (task icon)
- Lambda: "Job Dispatcher"
- Lambda: "Post-Processing"
- Step Functions: "Pipeline Orchestrator"

**Inside AWS Cloud, Outside VPC (right side):**

- Amazon Rekognition
- Amazon Bedrock

**Inside AWS Cloud, Outside VPC (bottom):**

- Amazon S3 (show 3 prefix labels: input/, processing/, results/)
- Amazon SQS (show main queue + DLQ)
- Amazon DynamoDB (Jobs table)
- Amazon SNS
- Amazon EventBridge

**Inside AWS Cloud, Outside VPC (left side):**

- AWS KMS
- AWS Secrets Manager
- Amazon CloudWatch
- Amazon ECR

### Arrows to Draw (ordered by data flow)

1. **Users** --> **Route 53** (HTTPS)
2. **Route 53** --> **AWS WAF** (DNS resolution + filtering)
3. **AWS WAF** --> **ALB** (filtered traffic)
4. **ALB** --> **ECS Fargate Web App** (HTTP, private subnet)
5. **ECS Fargate Web App** --> **S3 /input** (upload video via presigned URL)
6. **ECS Fargate Web App** --> **SQS** (submit processing job)
7. **ECS Fargate Web App** --> **DynamoDB** (create/read job status)
8. **S3 /input** --> **EventBridge** (upload event, dashed line)
9. **EventBridge** --> **SQS** (optional alternative trigger)
10. **SQS** --> **Lambda Job Dispatcher** (trigger)
11. **Lambda Job Dispatcher** --> **Step Functions** (start execution)
12. **Step Functions** --> **ECS Fargate Video Worker** (RunTask: frame extraction)
13. **ECS Fargate Video Worker** --> **S3 /processing** (write selected frames)
14. **Step Functions** --> **Rekognition** (DetectLabels on frames)
15. **Rekognition** --> **S3 /processing** (read frames / write results)
16. **Step Functions** --> **Lambda Post-Processing** (cross-frame tracking)
17. **Step Functions** --> **Bedrock** (video classification)
18. **Bedrock** --> **S3 /results** (write config JSON, via Lambda)
19. **Step Functions** --> **Lambda** (output assembly)
20. **Lambda** --> **S3 /results** (write annotated frames + final JSON)
21. **Step Functions** --> **SNS** (completion notification)
22. **SNS** --> **Users** (email/notification, dashed line back to top)
23. **Step Functions** --> **DynamoDB** (update job status)
24. **All compute** --> **CloudWatch** (logs + metrics, thin gray lines)
25. **All encryption** --> **KMS** (thin gray lines from S3, SQS, DynamoDB)
26. **ECS tasks** --> **Secrets Manager** (read secrets at startup)
27. **CloudFormation** --> all services (dashed "manages" arrows, very light)

---

## 9. Diagram 2 Definition: Detailed Processing Pipeline

This diagram shows the end-to-end flow of a single video from upload to output.

### Layout: Left-to-right swimlane

**Swimlane 1: Ingestion**

- User uploads video
- Web App stores to S3 /input
- Job message sent to SQS
- Lambda dispatcher triggered

**Swimlane 2: Orchestration**

- Step Functions starts
- Input validation (video vs image branch)

**Swimlane 3: Frame Processing**

- ECS Worker: FFmpeg extraction
- Temporal sampling (1 frame / 0.5s)
- Motion detection filter
- Selected frames written to S3

**Swimlane 4: AI Inference**

- Rekognition: DetectLabels (parallel batch)
- [Optional] Custom model: Detectron2 (Fargate)
- Detection results merged
- Cross-frame tracker (Lambda)
- Hazard assessment

**Swimlane 5: Classification & Generation**

- Aggregate detection metadata
- Bedrock: Classify video type
- Bedrock: Generate config JSON
- Bedrock: Generate Q&A (if educational)

**Swimlane 6: Output**

- Annotated frame generation (Lambda)
- Write results to S3 /results/{output_path}
- Update DynamoDB job status
- SNS notification

### Components to Place

1. User icon
2. ECS Web App
3. S3 input bucket
4. SQS queue
5. Lambda Dispatcher
6. Step Functions (large box spanning swimlanes 2-6)
7. ECS Fargate Worker (frame processing)
8. S3 processing bucket
9. Rekognition
10. ECS Fargate Worker (custom model - dashed, optional)
11. Lambda: Cross-Frame Tracker
12. Bedrock
13. Lambda: Output Assembler
14. S3 results bucket
15. DynamoDB
16. SNS
17. DLQ (branching off SQS)

### Arrows (ordered flow)

1. User --> ECS Web App (upload)
2. ECS Web App --> S3 /input (store video)
3. ECS Web App --> SQS (job message)
4. SQS --> Lambda Dispatcher
5. SQS -x-> DLQ (on failure, dashed red)
6. Lambda Dispatcher --> Step Functions (start execution)
7. Step Functions --> ECS Worker (RunTask: extract frames)
8. ECS Worker <--> S3 /input (read video)
9. ECS Worker --> S3 /processing (write selected frames)
10. Step Functions --> Rekognition (DetectLabels per frame)
11. Rekognition <--> S3 /processing (read frames)
12. Step Functions --> Lambda Cross-Frame Tracker (detection results)
13. Lambda Cross-Frame Tracker --> S3 /processing (write tracking results)
14. Step Functions --> Bedrock (classification prompt)
15. Bedrock --> Step Functions (classification + config JSON)
16. Step Functions --> Lambda Output Assembler
17. Lambda Output Assembler --> S3 /results (annotated frames + config)
18. Step Functions --> DynamoDB (update status: COMPLETED)
19. Step Functions --> SNS (publish completion)

---

## 10. Diagram 3 Definition: Frame Selection Pipeline

This is a dedicated, detailed diagram of the frame extraction and selection logic.

### Layout: Top-to-bottom flowchart

### Components (boxes)

1. **Input Video** (S3 icon, labeled "s3://input/videos/{job_id}/video.mp4")
2. **ECS Fargate Worker** (large container box encompassing steps 3-7)
3. **FFmpeg Frame Extractor** (inside worker) -- "Extract at 5 FPS (6x reduction from 30 FPS)"
4. **Raw Frames Buffer** (temp disk inside worker) -- "~1,500 frames for 5-min video"
5. **Temporal Sampler** (inside worker) -- "Select 1 per 0.5s => ~600 frames"
6. **Motion/Scene-Change Filter** (inside worker) -- "SSIM comparison, drop static frames => ~300-400 frames"
7. **Frame Metadata Generator** (inside worker) -- "Generate manifest JSON with frame_number, timestamp, selection_reason"
8. **S3 Selected Frames** (S3 icon, labeled "s3://processing/selected_frames/{job_id}/")
9. **AI Inference** (Rekognition + Bedrock icons)
10. **Event Detector** (Lambda icon) -- "Detect hazards, object-enters-lane events"
11. **Decision Diamond:** "Events detected?"
12. **YES branch:** **Event Window Expander** (back to ECS Worker) -- "Extract +/- 2s around event at 5 FPS"
13. **Additional Frames** --> back to S3 Selected Frames (merge)
14. **NO branch / After expansion:** **Final Selected Frames** --> proceed to full inference pipeline

### Arrows

1. Input Video --> FFmpeg Frame Extractor ("download from S3")
2. FFmpeg Frame Extractor --> Raw Frames Buffer ("5 FPS extraction")
3. Raw Frames Buffer --> Temporal Sampler ("stride-based selection")
4. Temporal Sampler --> Motion/Scene-Change Filter ("drop near-duplicates")
5. Motion/Scene-Change Filter --> Frame Metadata Generator ("label each frame")
6. Frame Metadata Generator --> S3 Selected Frames ("upload batch")
7. S3 Selected Frames --> AI Inference ("initial inference pass")
8. AI Inference --> Event Detector ("detection results")
9. Event Detector --> Decision Diamond
10. Decision Diamond --YES--> Event Window Expander
11. Event Window Expander --> S3 Selected Frames ("additional frames")
12. Decision Diamond --NO--> Final Selected Frames ("proceed to full pipeline")
13. S3 Selected Frames (after merge) --> Final Selected Frames

### Annotations on Diagram

- At FFmpeg box: "30 FPS -> 5 FPS = 83% reduction"
- At Temporal Sampler: "5 FPS -> 2 FPS = 60% further reduction"
- At Motion Filter: "Drop ~30-50% static frames"
- At Final: "Total: ~93-97% frame reduction"
- Bottom note: "5-min video: 9,000 frames -> 300-400 selected frames"

---

## 11. Final Recommendations and Tradeoffs

### 11.1 Lambda-only vs Lambda + Fargate Workers

**Recommendation: Lambda + Fargate Workers**

- Lambda cannot run FFmpeg video processing (15-min limit, no persistent filesystem for large video files)
- Lambda is ideal for lightweight orchestration, post-processing, and Bedrock calls
- Fargate handles the heavy lifting: video extraction, OpenCV motion filtering, optional custom model inference
- Tradeoff: Fargate has ~30-60s cold start for new tasks. Acceptable for async batch processing.

### 11.2 Rekognition-only vs Rekognition + Custom Models

**Recommendation: Rekognition as primary, with custom model Fargate containers as benchmarking option**

- Rekognition avoids YOLO AGPL licensing issues entirely
- Rekognition is managed, scalable, and cost-effective at MVP volume
- Custom models (Detectron2) should be benchmarked on the golden dataset as required by the task management document
- If Rekognition falls short on specific categories (construction objects, specific road signs), deploy Detectron2 in Fargate containers
- Tradeoff: Rekognition may not achieve 80% on all categories without Custom Labels training

### 11.3 Synchronous vs Asynchronous Processing

**Recommendation: Fully asynchronous with SQS + Step Functions**

- Required by the batch processing requirement
- Ensures one failed video does not block others
- Enables UI to show real-time status via DynamoDB polling
- Tradeoff: Users wait for results (not real-time). Acceptable for this use case (training content creation is not time-critical).

### 11.4 Single Bucket vs Multiple Buckets

**Recommendation: Single bucket with prefix-based separation**

- Simpler IAM policies and lifecycle rules
- Prefixes (input/, processing/, results/) provide logical separation
- Cross-stage access within a single bucket is simpler
- Tradeoff: If different teams need different access patterns, multi-bucket may be needed later

### 11.5 Step Functions: Yes

**Recommendation: Include Step Functions**

- The pipeline has 5+ distinct stages with branching logic (video vs image)
- Built-in retry, error handling, parallel execution
- Visual execution history for debugging
- Native integration with Lambda, ECS RunTask, Bedrock, and Rekognition
- Tradeoff: Additional cost ($0.025 per 1,000 state transitions). At MVP scale (~50 videos/month * ~10 transitions each = 500 transitions/month), this is negligible.

### 11.6 NAT Gateway: Single for MVP

**Recommendation: Single NAT Gateway in one AZ for MVP**

- Saves ~$32/month vs dual NAT GW
- Acceptable single-AZ risk for an MVP
- Upgrade to dual NAT GW for production

---

## 12. Open Questions / Assumptions

### Open Questions (for client/team resolution)

1. **YOLO licensing decision:** Has the team resolved whether AGPL is acceptable for internal use? This affects whether YOLO is benchmarked or excluded entirely.
2. **Client AWS account structure:** Is the MVP deploying to the Commit POC account or the client's own account? The weekly notes mention both.
3. **Video file sizes and durations:** Average video duration and resolution affect Fargate task sizing and processing time estimates. Assumed 5 minutes at 1080p.
4. **Ground truth data readiness:** The 20+ GT videos with per-frame annotations are critical for benchmarking. Are they available?
5. **UI framework preference:** React? Next.js? The architecture is framework-agnostic but affects the ECS web app container.
6. **Custom domain required for MVP?** Affects whether Route 53 hosted zone is needed now.
7. **Rekognition Custom Labels:** Is the team willing to train custom Rekognition models for construction objects? Requires labeled data and dedicated inference endpoints ($4/hr).
8. **WebSocket vs polling for status:** Polling is simpler for MVP. WebSocket provides better UX. Recommend polling for MVP.
9. **Multi-account setup:** The weekly notes mention multi-account org for production. Is this MVP or post-MVP?

### Assumptions Made

- Region: us-east-1 (confirmed in weekly notes)
- Video duration: average 5 minutes, 1080p, 30 FPS
- Volume: 50-100 videos/month for MVP, 1,000-2,000 images/month
- Single AWS account for MVP (multi-account is post-MVP)
- Polling-based UI status updates (not WebSocket)
- CloudFormation for IaC (not Terraform)
- No custom domain required for MVP (ALB DNS name is sufficient)
- Detectron2 preferred over YOLO due to licensing clarity (Apache 2.0 license)
- Claude 3.5 Sonnet on Bedrock for LLM tasks
- DynamoDB added for job tracking (not in original draft but architecturally necessary)
- EventBridge added as upload trigger mechanism (cleaner than S3 -> SQS direct)

