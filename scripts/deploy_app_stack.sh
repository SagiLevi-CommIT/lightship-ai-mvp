#!/usr/bin/env bash
set -euo pipefail

APP_STACK_NAME="${APP_STACK_NAME:-lightship-mvp-app}"
APP_TEMPLATE_FILE="${APP_TEMPLATE_FILE:-infrastructure/app-stack.yaml}"
PROJECT_NAME="${PROJECT_NAME:-lightship}"
ENVIRONMENT="${ENVIRONMENT:-mvp}"
VPC_STACK_NAME="${VPC_STACK_NAME:-lightship-mvp-vpc}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ALLOWED_ALB_INGRESS_CIDR_DEFAULT="176.228.16.138/32"
LAMBDA_WARMUP_SCHEDULE_DEFAULT="rate(5 minutes)"
CLOUDFORMATION_TEMPLATE_BUCKET="${CLOUDFORMATION_TEMPLATE_BUCKET:-}"

describe_param() {
  local key="$1"
  aws cloudformation describe-stacks \
    --stack-name "$APP_STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Parameters[?ParameterKey=='${key}'].ParameterValue | [0]" \
    --output text
}

normalize_param() {
  local value="$1"
  if [[ "$value" == "None" ]]; then
    echo ""
  else
    echo "$value"
  fi
}

CURRENT_DOMAIN_NAME="$(normalize_param "$(describe_param DomainName)")"
CURRENT_CERTIFICATE_ARN="$(normalize_param "$(describe_param CertificateArn)")"
CURRENT_ALLOWED_CIDR="$(normalize_param "$(describe_param AllowedAlbIngressCidr)")"
CURRENT_FRONTEND_IMAGE_URI="$(normalize_param "$(describe_param FrontendImageUri)")"
CURRENT_BACKEND_IMAGE_URI="$(normalize_param "$(describe_param BackendImageUri)")"
CURRENT_WARMUP_SCHEDULE="$(normalize_param "$(describe_param LambdaWarmupScheduleExpression)")"

FRONTEND_IMAGE_URI="${FRONTEND_IMAGE_URI:-$CURRENT_FRONTEND_IMAGE_URI}"
BACKEND_IMAGE_URI="${BACKEND_IMAGE_URI:-$CURRENT_BACKEND_IMAGE_URI}"
ALLOWED_ALB_INGRESS_CIDR="${ALLOWED_ALB_INGRESS_CIDR:-${CURRENT_ALLOWED_CIDR:-$ALLOWED_ALB_INGRESS_CIDR_DEFAULT}}"
LAMBDA_WARMUP_SCHEDULE_EXPRESSION="${LAMBDA_WARMUP_SCHEDULE_EXPRESSION:-${CURRENT_WARMUP_SCHEDULE:-$LAMBDA_WARMUP_SCHEDULE_DEFAULT}}"

if [[ -z "$FRONTEND_IMAGE_URI" ]]; then
  echo "Missing FrontendImageUri. Set FRONTEND_IMAGE_URI or ensure the stack already has a value." >&2
  exit 1
fi

if [[ -z "$BACKEND_IMAGE_URI" ]]; then
  echo "Missing BackendImageUri. Set BACKEND_IMAGE_URI or ensure the stack already has a value." >&2
  exit 1
fi

if [[ ! -f "$APP_TEMPLATE_FILE" ]]; then
  echo "Template file not found: $APP_TEMPLATE_FILE" >&2
  exit 1
fi

template_size_bytes="$(wc -c < "$APP_TEMPLATE_FILE")"
if (( template_size_bytes > 51200 )) && [[ -z "$CLOUDFORMATION_TEMPLATE_BUCKET" ]]; then
  echo "Template $APP_TEMPLATE_FILE is ${template_size_bytes} bytes. Set CLOUDFORMATION_TEMPLATE_BUCKET for cloudformation deploy." >&2
  exit 1
fi

params=(
  "ProjectName=${PROJECT_NAME}"
  "Environment=${ENVIRONMENT}"
  "VPCStackName=${VPC_STACK_NAME}"
  "DomainName=${CURRENT_DOMAIN_NAME}"
  "CertificateArn=${CURRENT_CERTIFICATE_ARN}"
  "AllowedAlbIngressCidr=${ALLOWED_ALB_INGRESS_CIDR}"
  "FrontendImageUri=${FRONTEND_IMAGE_URI}"
  "BackendImageUri=${BACKEND_IMAGE_URI}"
  "LambdaWarmupScheduleExpression=${LAMBDA_WARMUP_SCHEDULE_EXPRESSION}"
)

deploy_args=(
  cloudformation deploy
  --stack-name "$APP_STACK_NAME"
  --template-file "$APP_TEMPLATE_FILE"
  --region "$AWS_REGION"
  --capabilities CAPABILITY_NAMED_IAM
  --no-fail-on-empty-changeset
  --parameter-overrides
)
deploy_args+=("${params[@]}")

if [[ -n "${CLOUDFORMATION_DEPLOY_ROLE_ARN:-}" ]]; then
  deploy_args+=(--role-arn "$CLOUDFORMATION_DEPLOY_ROLE_ARN")
fi

if [[ -n "$CLOUDFORMATION_TEMPLATE_BUCKET" ]]; then
  deploy_args+=(--s3-bucket "$CLOUDFORMATION_TEMPLATE_BUCKET" --s3-prefix "cloudformation/${APP_STACK_NAME}")
fi

aws "${deploy_args[@]}"
