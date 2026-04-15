#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# Lightship MVP — One-Command Deploy
#
# Usage:  ./deploy.sh
#
# Prerequisites:
#   - AWS CLI configured (credentials via env vars or profile)
#   - Docker installed and running
#   - Required stacks already created: lightship-mvp-vpc, lightship-mvp-app
###############################################################################

REGION="us-east-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
PROJECT="lightship"
ENV="mvp"

BACKEND_ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PROJECT}-backend"
FRONTEND_ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PROJECT}-frontend"
LAMBDA_NAME="${PROJECT}-${ENV}-backend"
ECS_CLUSTER="${PROJECT}-${ENV}-cluster"
ECS_SERVICE="${PROJECT}-${ENV}-frontend-service"

ALB_DNS=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='LoadBalancerDNS'].OutputValue" \
  --output text 2>/dev/null || echo "")

PROCESSING_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='ProcessingBucketName'].OutputValue" \
  --output text 2>/dev/null || echo "")

DYNAMODB_TABLE=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='JobsTableName'].OutputValue" \
  --output text 2>/dev/null || echo "lightship_jobs")

BACKEND_TG_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='BackendTargetGroupArn'].OutputValue" \
  --output text 2>/dev/null || echo "")

PRIVATE_SUBNETS=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-vpc" \
  --query "Stacks[0].Outputs[?OutputKey=='PrivateAppSubnets'].OutputValue" \
  --output text 2>/dev/null || echo "")

ECS_SG=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='ECSSecurityGroupId'].OutputValue" \
  --output text 2>/dev/null || echo "")

FRONTEND_TG_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='FrontendTargetGroupArn'].OutputValue" \
  --output text 2>/dev/null || echo "")

EXEC_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='ECSTaskExecutionRoleArn'].OutputValue" \
  --output text 2>/dev/null || echo "")

TASK_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-${ENV}-app" \
  --query "Stacks[0].Outputs[?OutputKey=='ECSTaskRoleArn'].OutputValue" \
  --output text 2>/dev/null || echo "")

echo "============================================"
echo " Lightship MVP Deploy"
echo "============================================"
echo " Account:  ${ACCOUNT_ID}"
echo " Region:   ${REGION}"
echo " ALB DNS:  ${ALB_DNS}"
echo " Bucket:   ${PROCESSING_BUCKET}"
echo "============================================"

# --- Step 1: Update app stack (ALB rules) ---
echo ""
echo ">>> Step 1: Updating app stack (ALB rules)..."
aws cloudformation update-stack \
  --stack-name "${PROJECT}-${ENV}-app" \
  --template-body file://infrastructure/app-stack.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "${REGION}" 2>/dev/null && \
  aws cloudformation wait stack-update-complete --stack-name "${PROJECT}-${ENV}-app" --region "${REGION}" 2>/dev/null || \
  echo "    (no changes or already up-to-date)"

# --- Step 2: ECR login ---
echo ""
echo ">>> Step 2: ECR login..."
aws ecr get-login-password --region "${REGION}" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# --- Step 3: Build & push backend image ---
echo ""
echo ">>> Step 3: Building backend Docker image..."
docker build --platform linux/amd64 -t "${PROJECT}-backend:latest" ./lambda-be/
docker tag "${PROJECT}-backend:latest" "${BACKEND_ECR}:latest"
docker tag "${PROJECT}-backend:latest" "${BACKEND_ECR}:$(date +%Y%m%d-%H%M%S)"
echo "    Pushing backend image..."
docker push "${BACKEND_ECR}:latest"

# --- Step 4: Deploy backend Lambda stack ---
echo ""
echo ">>> Step 4: Deploying backend Lambda stack..."
aws cloudformation deploy \
  --stack-name "${PROJECT}-${ENV}-backend-lambda" \
  --template-file infrastructure/backend-lambda-stack.yaml \
  --parameter-overrides \
    ProjectName="${PROJECT}" \
    Environment="${ENV}" \
    ImageUri="${BACKEND_ECR}:latest" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --region "${REGION}"

# --- Step 5: Update Lambda function code ---
echo ""
echo ">>> Step 5: Updating Lambda function code..."
aws lambda update-function-code \
  --function-name "${LAMBDA_NAME}" \
  --image-uri "${BACKEND_ECR}:latest" \
  --region "${REGION}" > /dev/null
echo "    Waiting for Lambda update..."
aws lambda wait function-updated --function-name "${LAMBDA_NAME}" --region "${REGION}"

# --- Step 6: Register Lambda target in ALB ---
echo ""
echo ">>> Step 6: Registering Lambda with ALB target group..."
LAMBDA_ARN=$(aws lambda get-function --function-name "${LAMBDA_NAME}" --query 'Configuration.FunctionArn' --output text --region "${REGION}")
aws elbv2 register-targets \
  --target-group-arn "${BACKEND_TG_ARN}" \
  --targets "Id=${LAMBDA_ARN}" \
  --region "${REGION}" 2>/dev/null || echo "    (already registered)"

# --- Step 7: Build & push frontend image ---
echo ""
echo ">>> Step 7: Building frontend Docker image..."
docker build --platform linux/amd64 -t "${PROJECT}-frontend:latest" ./ui-fe/
docker tag "${PROJECT}-frontend:latest" "${FRONTEND_ECR}:latest"
docker tag "${PROJECT}-frontend:latest" "${FRONTEND_ECR}:$(date +%Y%m%d-%H%M%S)"
echo "    Pushing frontend image..."
docker push "${FRONTEND_ECR}:latest"

# --- Step 8: Update ECS frontend task + service ---
echo ""
echo ">>> Step 8: Updating ECS frontend service..."
SUBNET_A=$(echo "${PRIVATE_SUBNETS}" | cut -d',' -f1)
SUBNET_B=$(echo "${PRIVATE_SUBNETS}" | cut -d',' -f2)

TASK_DEF_JSON=$(cat <<EOF
{
  "family": "${PROJECT}-${ENV}-frontend",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "${EXEC_ROLE_ARN}",
  "taskRoleArn": "${TASK_ROLE_ARN}",
  "containerDefinitions": [
    {
      "name": "${PROJECT}-frontend",
      "image": "${FRONTEND_ECR}:latest",
      "portMappings": [{"containerPort": 8501, "protocol": "tcp"}],
      "environment": [
        {"name": "BACKEND_API_URL", "value": "http://${ALB_DNS}"},
        {"name": "AWS_REGION", "value": "${REGION}"},
        {"name": "STREAMLIT_SERVER_ADDRESS", "value": "0.0.0.0"},
        {"name": "STREAMLIT_SERVER_PORT", "value": "8501"},
        {"name": "STREAMLIT_SERVER_HEADLESS", "value": "true"}
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/${PROJECT}-${ENV}-frontend",
          "awslogs-region": "${REGION}",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "essential": true
    }
  ]
}
EOF
)

TASK_DEF_ARN=$(echo "${TASK_DEF_JSON}" | aws ecs register-task-definition \
  --cli-input-json file:///dev/stdin \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text --region "${REGION}")

echo "    Registered task definition: ${TASK_DEF_ARN}"

# Check if service exists
SERVICE_EXISTS=$(aws ecs describe-services --cluster "${ECS_CLUSTER}" --services "${ECS_SERVICE}" \
  --query 'services[?status==`ACTIVE`].serviceName' --output text --region "${REGION}" 2>/dev/null || echo "")

if [ -n "${SERVICE_EXISTS}" ]; then
  echo "    Updating existing ECS service..."
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${ECS_SERVICE}" \
    --task-definition "${TASK_DEF_ARN}" \
    --force-new-deployment \
    --region "${REGION}" > /dev/null
else
  echo "    Creating ECS service..."
  aws ecs create-service \
    --cluster "${ECS_CLUSTER}" \
    --service-name "${ECS_SERVICE}" \
    --task-definition "${TASK_DEF_ARN}" \
    --desired-count 1 \
    --launch-type FARGATE \
    --network-configuration "awsvpcConfiguration={subnets=[${SUBNET_A},${SUBNET_B}],securityGroups=[${ECS_SG}],assignPublicIp=DISABLED}" \
    --load-balancers "targetGroupArn=${FRONTEND_TG_ARN},containerName=${PROJECT}-frontend,containerPort=8501" \
    --region "${REGION}" > /dev/null
fi

# --- Step 9: Verify health ---
echo ""
echo ">>> Step 9: Verifying deployment..."
sleep 5
echo "    Backend health:"
curl -s -m 10 "http://${ALB_DNS}/health" && echo ""
echo "    Frontend status:"
curl -s -m 10 -o /dev/null -w "    HTTP %{http_code}\n" "http://${ALB_DNS}/"

echo ""
echo "============================================"
echo " DEPLOYMENT COMPLETE"
echo "============================================"
echo " Frontend: http://${ALB_DNS}/"
echo " Backend:  http://${ALB_DNS}/health"
echo " S3:       s3://${PROCESSING_BUCKET}/"
echo " DynamoDB: ${DYNAMODB_TABLE}"
echo "============================================"
