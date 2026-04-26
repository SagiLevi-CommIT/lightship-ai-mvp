#!/usr/bin/env bash
set -euo pipefail

export AWS_PROFILE="${AWS_PROFILE:-lightship}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
REGION="us-east-1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "============================================"
echo " Lightship MVP — Infrastructure Deploy"
echo "============================================"
echo " Account: $(aws sts get-caller-identity --query Account --output text)"
echo " Region:  ${REGION}"
echo "============================================"

deploy_stack() {
  local stack_name="$1"
  local template="$2"
  shift 2
  local params=("$@")

  echo ""
  echo "--- Deploying: ${stack_name} ---"

  # Check if stack is in REVIEW_IN_PROGRESS (ghost changeset)
  local status
  status=$(aws cloudformation describe-stacks --stack-name "${stack_name}" \
    --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DOES_NOT_EXIST")

  if [ "${status}" = "REVIEW_IN_PROGRESS" ]; then
    echo "  Stack in REVIEW_IN_PROGRESS, deleting..."
    aws cloudformation delete-stack --stack-name "${stack_name}"
    aws cloudformation wait stack-delete-complete --stack-name "${stack_name}" || true
    sleep 5
  fi

  if [ "${status}" = "DOES_NOT_EXIST" ] || [ "${status}" = "REVIEW_IN_PROGRESS" ]; then
    echo "  Creating changeset for new stack..."
    local cs_name="deploy-$(date +%s)"
    aws cloudformation create-change-set \
      --stack-name "${stack_name}" \
      --change-set-name "${cs_name}" \
      --template-body "file://${template}" \
      --parameters "${params[@]}" \
      --capabilities CAPABILITY_NAMED_IAM \
      --change-set-type CREATE \
      --region "${REGION}"

    echo "  Waiting for changeset..."
    aws cloudformation wait change-set-create-complete \
      --stack-name "${stack_name}" \
      --change-set-name "${cs_name}" || true

    echo "  Executing changeset..."
    aws cloudformation execute-change-set \
      --stack-name "${stack_name}" \
      --change-set-name "${cs_name}"

    echo "  Waiting for stack creation..."
    aws cloudformation wait stack-create-complete --stack-name "${stack_name}"
  else
    echo "  Stack exists (${status}), running update..."
    aws cloudformation deploy \
      --template-file "${template}" \
      --stack-name "${stack_name}" \
      --parameter-overrides "${params[@]}" \
      --capabilities CAPABILITY_NAMED_IAM \
      --region "${REGION}" \
      --no-fail-on-empty-changeset || true
  fi

  local final_status
  final_status=$(aws cloudformation describe-stacks --stack-name "${stack_name}" \
    --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "FAILED")
  echo "  Status: ${final_status}"

  if [[ "${final_status}" == *"FAILED"* ]] || [[ "${final_status}" == *"ROLLBACK"* ]]; then
    echo "  ERROR: Stack failed! Checking events..."
    aws cloudformation describe-stack-events --stack-name "${stack_name}" \
      --query "StackEvents[?ResourceStatus=='CREATE_FAILED'].{R:LogicalResourceId,M:ResourceStatusReason}" \
      --output table 2>/dev/null || true
    return 1
  fi
}

# Use v2 suffix to avoid zombie stacks from previous failed deploys
VPC_STACK="lightship-mvp-vpc-v2"
APP_STACK="lightship-mvp-app-v2"
LAMBDA_STACK="lightship-mvp-backend-lambda-v2"

# Step 1: VPC
deploy_stack "${VPC_STACK}" \
  "infrastructure/vpc-stack.yaml" \
  "ParameterKey=ProjectName,ParameterValue=lightship" \
  "ParameterKey=Environment,ParameterValue=mvp"

# Step 2: App (ALB, ECR, S3, DynamoDB, SQS, SNS, ECS, IAM, KMS)
deploy_stack "${APP_STACK}" \
  "infrastructure/app-stack.yaml" \
  "ParameterKey=ProjectName,ParameterValue=lightship" \
  "ParameterKey=Environment,ParameterValue=mvp"

# Step 3: Backend Lambda
deploy_stack "${LAMBDA_STACK}" \
  "infrastructure/backend-lambda-stack.yaml" \
  "ParameterKey=ProjectName,ParameterValue=lightship" \
  "ParameterKey=Environment,ParameterValue=mvp"

echo ""
echo "============================================"
echo " Infrastructure deploy complete!"
echo " Now run: ./deploy.sh"
echo "============================================"
