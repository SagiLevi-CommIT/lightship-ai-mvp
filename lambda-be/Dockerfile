# Backend Dockerfile - AWS Lambda Container
FROM public.ecr.aws/lambda/python:3.11

# Set environment variables for model caching in /tmp
ENV HF_HOME=/tmp/huggingface
ENV TRANSFORMERS_CACHE=/tmp/huggingface/transformers
ENV YOLO_CONFIG_DIR=/tmp/Ultralytics

# Install system dependencies needed for building Python packages and OpenCV
RUN yum install -y gcc gcc-c++ mesa-libGL && yum clean all

# Upgrade pip to latest version
RUN pip install --upgrade pip

# Copy requirements
COPY requirements.txt ${LAMBDA_TASK_ROOT}/

# Install dependencies (use binary wheels to avoid compilation)
RUN pip install --no-cache-dir --only-binary=:all: -r ${LAMBDA_TASK_ROOT}/requirements.txt || \
    pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Pre-download YOLO model to avoid runtime downloads
RUN python3 -c "from ultralytics import YOLO; YOLO('yolo11n.pt')" || echo "YOLO model download attempted"

# Pre-download Depth-Anything model to avoid runtime downloads
RUN python3 -c "from transformers import AutoImageProcessor, AutoModelForDepthEstimation; \
    model_name='depth-anything/Depth-Anything-V2-Small-hf'; \
    AutoImageProcessor.from_pretrained(model_name); \
    AutoModelForDepthEstimation.from_pretrained(model_name)" || echo "Depth model download attempted"

# Copy backend application code from src/ to task root
COPY src/ ${LAMBDA_TASK_ROOT}/src/

# Copy the dual-mode Lambda handler (API mode via Mangum + worker mode for async self-invocation)
COPY src/lambda_function.py ${LAMBDA_TASK_ROOT}/lambda_function.py

# Set the CMD to your handler
CMD [ "lambda_function.lambda_handler" ]
