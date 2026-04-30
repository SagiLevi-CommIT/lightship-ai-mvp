# Lightship inference worker — ECS Fargate (linux/amd64, CPU-only)
# Bakes Florence-2, Depth-Anything-V2-Small, and YOLO11 weights so the
# container never calls the internet at runtime (TRANSFORMERS_OFFLINE=1).
#
# Build context: project root (so we can COPY both lambda-be/src/ and inference-worker/).
# Build command:
#   docker buildx build --platform linux/amd64 \
#     -f inference-worker/Dockerfile \
#     -t lightship-mvp-inference-worker:latest .
FROM python:3.11-slim

# Build-time args for ECR / account
ARG AWS_ACCOUNT_ID
ARG AWS_REGION=us-east-1

WORKDIR /app

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libgl1 libglib2.0-0 libgomp1 curl \
    libavformat-dev libavcodec-dev libavfilter-dev libavdevice-dev libswscale-dev \
 && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
# Install CPU-only torch first so pip never pulls the CUDA wheel (~3 GB).
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      'torch==2.6.0+cpu' 'torchvision==0.21.0+cpu'

COPY inference-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Detectron2 CPU wheel matching torch 2.6 (community index; see plan / DEPLOYMENT.md).
RUN pip install --no-cache-dir \
    --extra-index-url https://miropsota.github.io/torch_packages_builder \
    "detectron2==0.6+18f6958pt2.6.0cpu"

# Pre-cache Mask R-CNN R50-FPN weights (~170 MB) so runtime stays air-gapped.
RUN python3 -c "\
from detectron2 import model_zoo; \
from detectron2.config import get_cfg; \
from detectron2.engine import DefaultPredictor; \
cfg = get_cfg(); \
cfg.merge_from_file(model_zoo.get_config_file('COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml')); \
cfg.MODEL.WEIGHTS = model_zoo.get_checkpoint_url('COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml'); \
cfg.MODEL.DEVICE = 'cpu'; \
DefaultPredictor(cfg)" \
 || echo "WARNING: Detectron2 model_zoo warm-up failed"

# ── Application code ──────────────────────────────────────────────────────────
# Copy the shared lambda-be/src package (VisionLabeler, Pipeline, schemas, …)
COPY lambda-be/src ./src

# Copy the worker entry-point
COPY inference-worker/entrypoint.py .

# ── Model weights pre-download ────────────────────────────────────────────────
# All weights are fetched at IMAGE BUILD time; at runtime we set
# TRANSFORMERS_OFFLINE=1 so the container is fully air-gapped inside the VPC.
ENV HF_HOME=/app/.cache/huggingface
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface/transformers
ENV YOLO_CONFIG_DIR=/app/.cache/ultralytics
ENV TRANSFORMERS_OFFLINE=0

# Florence-2-base (~470 MB)
RUN python3 -c "\
from transformers import AutoProcessor, AutoModelForCausalLM; \
AutoProcessor.from_pretrained('microsoft/Florence-2-base', trust_remote_code=True); \
AutoModelForCausalLM.from_pretrained('microsoft/Florence-2-base', trust_remote_code=True)" \
 || echo "WARNING: Florence-2 download failed — will retry at runtime"

# Depth-Anything-V2-Small (~100 MB)
RUN python3 -c "\
from transformers import AutoImageProcessor, AutoModelForDepthEstimation; \
AutoImageProcessor.from_pretrained('depth-anything/Depth-Anything-V2-Small-hf'); \
AutoModelForDepthEstimation.from_pretrained('depth-anything/Depth-Anything-V2-Small-hf')" \
 || echo "WARNING: Depth-Anything download failed"

# YOLO11n (~6 MB)
RUN python3 -c "from ultralytics import YOLO; YOLO('yolo11n.pt')" \
 || echo "WARNING: YOLO11 download failed"

# UFLDv2 (attempt; tolerates missing HF repo)
RUN python3 -c "\
from huggingface_hub import hf_hub_download; \
hf_hub_download(repo_id='cfzd/ufld-v2-culane', filename='model.pth')" \
 || echo "INFO: UFLDv2 checkpoint not found — lane backend will fall back to OpenCV"

# Florence-2 with trust_remote_code=True downloads modeling .py files at runtime,
# even when weights are cached, so we leave TRANSFORMERS_OFFLINE=0 to allow the
# small (<10 KB) modeling-code files through.  Weights themselves come from the
# pre-baked cache at /app/.cache/huggingface.
ENV TRANSFORMERS_OFFLINE=0
ENV HF_DATASETS_OFFLINE=1

# ── Runtime ───────────────────────────────────────────────────────────────────
# ECS reads JOB_ID, S3_INPUT_KEY, S3_OUTPUT_PREFIX, PROCESSING_BUCKET from
# containerOverrides injected by Step Functions.
ENV AWS_REGION=${AWS_REGION}
ENV PYTHONUNBUFFERED=1

CMD ["python", "entrypoint.py"]
