#!/usr/bin/env bash
# Build lambda-be Docker image, tag with git short SHA + latest, push to ECR,
# and point lightship-mvp-backend at the new digest.
#
# Prerequisites: Docker daemon, AWS CLI v2, aws sso login / profile.
# Usage (from repo root):
#   AWS_PROFILE=lightship ./scripts/publish_lambda_backend_image.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/lambda-be"

: "${AWS_PROFILE:=lightship}"
: "${AWS_REGION:=us-east-1}"
ACCOUNT="${ACCOUNT:-336090301206}"
REPO="${REPO:-lightship-backend}"
REGISTRY="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
TAG="$(git -C "$ROOT" rev-parse --short HEAD)"

echo "Building ${REPO}:${TAG} …"
# Lambda rejects Docker attestations / multi-arch manifest lists — single
# linux/amd64 image without provenance/SBOM (see docs/REKOGNITION_CUSTOM_LABELS_DRIFT.md).
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
  -t "${REPO}:${TAG}" . --load

echo "Logging in to ECR …"
aws ecr get-login-password --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

docker tag "${REPO}:${TAG}" "${REGISTRY}/${REPO}:${TAG}"
docker tag "${REPO}:${TAG}" "${REGISTRY}/${REPO}:latest"

echo "Pushing ${REGISTRY}/${REPO}:${TAG} and :latest …"
docker push "${REGISTRY}/${REPO}:${TAG}"
docker push "${REGISTRY}/${REPO}:latest"

IMAGE_URI="${REGISTRY}/${REPO}:${TAG}"
echo "Updating Lambda image to ${IMAGE_URI} …"
aws lambda update-function-code \
  --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --function-name lightship-mvp-backend \
  --image-uri "$IMAGE_URI"

echo "Done."
