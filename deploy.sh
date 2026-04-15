#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# Lightship MVP — One-Command Deploy
#
# Usage:
#   ./deploy.sh              # Auto-detect: Docker if available, else CodeBuild
#   ./deploy.sh --codebuild  # Force CodeBuild builds
#   ./deploy.sh --docker     # Force local Docker builds
#
# Prerequisites:
#   - AWS CLI configured (credentials via env vars or profile)
#   - Stacks: lightship-mvp-vpc, lightship-mvp-app
#   - For --docker: Docker installed and running
#   - For --codebuild: CodeCommit repo + CodeBuild projects
###############################################################################

REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PROJECT="lightship"
ENV="mvp"
BUILD_MODE="${1:-auto}"

BACKEND_ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PROJECT}-backend"
FRONTEND_ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PROJECT}-frontend"
LAMBDA_NAME="${PROJECT}-${ENV}-backend"
ECS_CLUSTER="${PROJECT}-${ENV}-cluster"
ECS_SERVICE="${PROJECT}-${ENV}-frontend-service"

get_stack_output() {
  aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text --region "$REGION" 2>/dev/null || echo ""
}

ALB_DNS=$(get_stack_output "${PROJECT}-${ENV}-app" "LoadBalancerDNS")
PROCESSING_BUCKET=$(get_stack_output "${PROJECT}-${ENV}-app" "ProcessingBucketName")
DYNAMODB_TABLE=$(get_stack_output "${PROJECT}-${ENV}-app" "JobsTableName")
BACKEND_TG_ARN=$(get_stack_output "${PROJECT}-${ENV}-app" "BackendTargetGroupArn")
FRONTEND_TG_ARN=$(get_stack_output "${PROJECT}-${ENV}-app" "FrontendTargetGroupArn")
EXEC_ROLE_ARN=$(get_stack_output "${PROJECT}-${ENV}-app" "ECSTaskExecutionRoleArn")
TASK_ROLE_ARN=$(get_stack_output "${PROJECT}-${ENV}-app" "ECSTaskRoleArn")
ECS_SG=$(get_stack_output "${PROJECT}-${ENV}-app" "ECSSecurityGroupId")
PRIVATE_SUBNETS=$(get_stack_output "${PROJECT}-${ENV}-vpc" "PrivateAppSubnets")

if [ "$BUILD_MODE" = "auto" ]; then
  if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    BUILD_MODE="docker"
  else
    BUILD_MODE="codebuild"
  fi
fi

echo "============================================"
echo " Lightship MVP Deploy"
echo "============================================"
echo " Account:    ${ACCOUNT_ID}"
echo " Region:     ${REGION}"
echo " ALB DNS:    ${ALB_DNS}"
echo " Bucket:     ${PROCESSING_BUCKET}"
echo " Build mode: ${BUILD_MODE}"
echo "============================================"

# ─── CodeBuild functions ─────────────────────────────────────────────────────

run_codebuild() {
  local project_name="$1"
  echo "  Triggering CodeBuild: $project_name..."
  local build_id
  build_id=$(aws codebuild start-build --project-name "$project_name" \
    --source-version main --query 'build.id' --output text)
  echo "  Build ID: $build_id"
  for i in {1..80}; do
    local status
    status=$(aws codebuild batch-get-builds --ids "$build_id" \
      --query 'builds[0].buildStatus' --output text)
    local phase
    phase=$(aws codebuild batch-get-builds --ids "$build_id" \
      --query 'builds[0].currentPhase' --output text)
    printf "  [%2d] %-12s %s\n" "$i" "$status" "$phase"
    if [ "$status" = "SUCCEEDED" ] || [ "$status" = "FAILED" ] || [ "$status" = "STOPPED" ]; then
      if [ "$status" != "SUCCEEDED" ]; then
        echo "  ERROR: Build failed!"
        return 1
      fi
      return 0
    fi
    sleep 15
  done
  echo "  ERROR: Build timed out"
  return 1
}

push_to_codecommit() {
  echo "  Pushing code to CodeCommit..."
  git config --global credential.helper '!aws codecommit credential-helper $@' 2>/dev/null || true
  git config --global credential.UseHttpPath true 2>/dev/null || true
  local codecommit_url="https://git-codecommit.${REGION}.amazonaws.com/v1/repos/lightship-ai-mvp"
  git remote add codecommit "$codecommit_url" 2>/dev/null || true
  local current_branch
  current_branch=$(git rev-parse --abbrev-ref HEAD)
  git push codecommit "${current_branch}:main" --force 2>&1
}

# ─── Docker functions ────────────────────────────────────────────────────────

docker_build_push() {
  echo ">>> ECR login..."
  aws ecr get-login-password --region "$REGION" | \
    docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

  echo ">>> Building backend..."
  docker build --platform linux/amd64 -t "${PROJECT}-backend:latest" ./lambda-be/
  docker tag "${PROJECT}-backend:latest" "${BACKEND_ECR}:latest"
  docker push "${BACKEND_ECR}:latest"

  echo ">>> Building frontend..."
  docker build --platform linux/amd64 -t "${PROJECT}-frontend:latest" ./ui-fe/
  docker tag "${PROJECT}-frontend:latest" "${FRONTEND_ECR}:latest"
  docker push "${FRONTEND_ECR}:latest"
}

# ─── Build images ────────────────────────────────────────────────────────────

echo ""
echo ">>> Building and pushing images (${BUILD_MODE})..."
if [ "$BUILD_MODE" = "codebuild" ]; then
  push_to_codecommit
  echo ""
  echo ">>> Building backend via CodeBuild..."
  run_codebuild "${PROJECT}-${ENV}-backend"
  echo ""
  echo ">>> Building frontend via CodeBuild..."
  run_codebuild "${PROJECT}-${ENV}-frontend"
else
  docker_build_push
fi

# ─── Update Lambda ──────────────────────────────────────────────────────────

echo ""
echo ">>> Updating Lambda env vars..."
aws lambda update-function-configuration \
  --function-name "$LAMBDA_NAME" \
  --environment "{\"Variables\":{
    \"AWS_REGION_NAME\":\"${REGION}\",
    \"ENVIRONMENT\":\"${ENV}\",
    \"LOG_LEVEL\":\"INFO\",
    \"BEDROCK_MODEL_ID\":\"us.anthropic.claude-sonnet-4-20250514-v1:0\",
    \"TEMPERATURE\":\"0.1\",
    \"MAX_TOKENS\":\"15000\",
    \"TOP_P\":\"1.0\",
    \"TOP_K\":\"250\",
    \"REKOGNITION_MIN_CONFIDENCE\":\"60.0\",
    \"REKOGNITION_MAX_LABELS\":\"50\",
    \"DYNAMODB_TABLE\":\"${DYNAMODB_TABLE}\",
    \"PROCESSING_BUCKET\":\"${PROCESSING_BUCKET}\",
    \"RESULTS_BUCKET\":\"${PROCESSING_BUCKET}\",
    \"RESULTS_PREFIX\":\"results\"
  }}" \
  --region "$REGION" --query 'FunctionArn' --output text >/dev/null

aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
echo "  Lambda updated"

# ─── Register Lambda target ─────────────────────────────────────────────────

echo ""
echo ">>> Registering Lambda with ALB..."
LAMBDA_ARN=$(aws lambda get-function --function-name "$LAMBDA_NAME" \
  --query 'Configuration.FunctionArn' --output text --region "$REGION")
aws elbv2 register-targets --target-group-arn "$BACKEND_TG_ARN" \
  --targets "Id=${LAMBDA_ARN}" --region "$REGION" 2>/dev/null || true

# ─── Verify ─────────────────────────────────────────────────────────────────

echo ""
echo ">>> Verifying deployment..."
sleep 5
echo "  Backend:"
curl -s -m 15 "http://${ALB_DNS}/health" && echo ""
echo "  Frontend:"
curl -s -m 15 -o /dev/null -w "  HTTP %{http_code}\n" "http://${ALB_DNS}/"

echo ""
echo "============================================"
echo " DEPLOYMENT COMPLETE"
echo "============================================"
echo " Frontend: http://${ALB_DNS}/"
echo " Backend:  http://${ALB_DNS}/health"
echo " S3:       s3://${PROCESSING_BUCKET}/"
echo " DynamoDB: ${DYNAMODB_TABLE}"
echo "============================================"
