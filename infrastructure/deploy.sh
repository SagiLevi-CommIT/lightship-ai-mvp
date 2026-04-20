#!/bin/bash

 # Lightship AI Project - Complete Infrastructure Deployment
 # This script deploys the entire infrastructure from scratch

set -e

# Configuration
PROJECT_NAME="lightship"
REGION="us-east-1"
VPC_CIDR="10.145.16.0/20"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    # Check if AWS CLI is installed
    if ! command -v aws &> /dev/null; then
        print_error "AWS CLI is not installed. Please install it first."
        exit 1
    fi
    
    # Check if Docker is installed
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed. Please install it first."
        exit 1
    fi
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        print_error "AWS credentials not configured. Please run 'aws configure' first."
        exit 1
    fi
    
    # Get AWS Account ID
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    print_success "AWS Account ID: $AWS_ACCOUNT_ID"
    
    # Get available AZs
    AZ1=$(aws ec2 describe-availability-zones --region $REGION --query 'AvailabilityZones[0].ZoneName' --output text)
    AZ2=$(aws ec2 describe-availability-zones --region $REGION --query 'AvailabilityZones[1].ZoneName' --output text)
    print_success "Using AZs: $AZ1, $AZ2"
}

# Function to get user input
get_user_input() {
    read -p "Environment (dev/staging/prod) [dev]: " ENVIRONMENT
    ENVIRONMENT=${ENVIRONMENT:-dev}
    
    read -p "VPC CIDR [$VPC_CIDR]: " USER_VPC_CIDR
    VPC_CIDR=${USER_VPC_CIDR:-$VPC_CIDR}
    
    read -p "Domain name (optional): " DOMAIN_NAME
    read -p "ACM Certificate ARN (optional): " CERT_ARN
    
    print_status "Configuration:"
    echo "  Project: $PROJECT_NAME"
    echo "  Environment: $ENVIRONMENT"
    echo "  Region: $REGION"
    echo "  VPC CIDR: $VPC_CIDR"
    echo "  Domain: ${DOMAIN_NAME:-None}"
    echo "  Certificate: ${CERT_ARN:-None}"
    echo ""
    
    read -p "Continue with this configuration? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_warning "Deployment cancelled."
        exit 0
    fi
}

# Function to deploy VPC stack
deploy_vpc_stack() {
    print_status "Deploying VPC infrastructure..."
    
    VPC_STACK_NAME="${PROJECT_NAME}-${ENVIRONMENT}-vpc"
    
    aws cloudformation deploy \
        --template-file infrastructure/vpc-stack.yaml \
        --stack-name $VPC_STACK_NAME \
        --parameter-overrides \
            ProjectName=$PROJECT_NAME \
            Environment=$ENVIRONMENT \
            VpcCidr=$VPC_CIDR \
            AvailabilityZone1=$AZ1 \
            AvailabilityZone2=$AZ2 \
        --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
        --region $REGION \
        --tags \
            Project=$PROJECT_NAME \
            Environment=$ENVIRONMENT
    
    if [ $? -eq 0 ]; then
        print_success "VPC stack deployed successfully: $VPC_STACK_NAME"
    else
        print_error "Failed to deploy VPC stack"
        exit 1
    fi
}

# Function to deploy application stack
deploy_app_stack() {
    print_status "Deploying application infrastructure..."
    
    APP_STACK_NAME="${PROJECT_NAME}-${ENVIRONMENT}-app"
    VPC_STACK_NAME="${PROJECT_NAME}-${ENVIRONMENT}-vpc"
    
    PARAMS="ProjectName=$PROJECT_NAME Environment=$ENVIRONMENT VPCStackName=$VPC_STACK_NAME"
    
    if [ -n "$DOMAIN_NAME" ]; then
        PARAMS="$PARAMS DomainName=$DOMAIN_NAME"
    fi
    
    if [ -n "$CERT_ARN" ]; then
        PARAMS="$PARAMS CertificateArn=$CERT_ARN"
    fi
    
    aws cloudformation deploy \
        --template-file infrastructure/app-stack.yaml \
        --stack-name $APP_STACK_NAME \
        --parameter-overrides $PARAMS \
        --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
        --region $REGION \
        --tags \
            Project=$PROJECT_NAME \
            Environment=$ENVIRONMENT
    
    if [ $? -eq 0 ]; then
        print_success "Application stack deployed successfully: $APP_STACK_NAME"
    else
        print_error "Failed to deploy application stack"
        exit 1
    fi
}

# Function to build and push Docker images
build_and_push_images() {
    print_status "Building and pushing Docker images..."
    
    # Get ECR repository URIs from CloudFormation outputs.
    # The exported output keys are FrontendECRRepositoryUri / BackendECRRepositoryUri
    # (see infrastructure/app-stack.yaml:Outputs).
    FRONTEND_ECR=$(aws cloudformation describe-stacks \
        --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-app" \
        --query 'Stacks[0].Outputs[?OutputKey==`FrontendECRRepositoryUri`].OutputValue' \
        --output text \
        --region $REGION)

    BACKEND_ECR=$(aws cloudformation describe-stacks \
        --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-app" \
        --query 'Stacks[0].Outputs[?OutputKey==`BackendECRRepositoryUri`].OutputValue' \
        --output text \
        --region $REGION)
    
    # Login to ECR
    print_status "Logging into ECR..."
    aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
    
    # Build and push frontend image
    print_status "Building frontend image..."
    cd ui-fe
    docker build --build-arg NEXT_PUBLIC_API_BASE="" -t $FRONTEND_ECR:latest .
    docker push $FRONTEND_ECR:latest
    cd ..
    
    print_success "Frontend image pushed: $FRONTEND_ECR:latest"
    
    # Build and push backend image
    print_status "Building backend image..."
    cd lambda-be
    docker build -t $BACKEND_ECR:latest .
    docker push $BACKEND_ECR:latest
    cd ..
    
    print_success "Backend image pushed: $BACKEND_ECR:latest"
}

# Function to update Lambda function with new image
update_lambda_function() {
    print_status "Updating Lambda function with new image..."
    
    LAMBDA_FUNCTION_NAME="${PROJECT_NAME}-${ENVIRONMENT}-backend"
    BACKEND_ECR=$(aws cloudformation describe-stacks \
        --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-app" \
        --query 'Stacks[0].Outputs[?OutputKey==`BackendECRRepositoryUri`].OutputValue' \
        --output text \
        --region $REGION)
    
    aws lambda update-function-code \
        --function-name $LAMBDA_FUNCTION_NAME \
        --image-uri $BACKEND_ECR:latest \
        --region $REGION
    
    print_success "Lambda function updated with new image"
}

# Function to update ECS service
update_ecs_service() {
    print_status "Updating ECS service..."
    
    ECS_CLUSTER="${PROJECT_NAME}-${ENVIRONMENT}-cluster"
    ECS_SERVICE="${PROJECT_NAME}-${ENVIRONMENT}-frontend-service"
    
    aws ecs update-service \
        --cluster $ECS_CLUSTER \
        --service $ECS_SERVICE \
        --force-new-deployment \
        --region $REGION
    
    print_success "ECS service deployment initiated"
}

# Function to display deployment results
show_deployment_results() {
    print_success "Deployment completed successfully!"
    echo ""
    print_status "📋 Deployment Summary:"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Get output values
    ALB_URL=$(aws cloudformation describe-stacks \
        --stack-name "${PROJECT_NAME}-${ENVIRONMENT}-app" \
        --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerURL`].OutputValue' \
        --output text \
        --region $REGION 2>/dev/null || echo "Not available")
    
    echo "🌐 Frontend URL:       $ALB_URL"
    echo "🔗 Backend health:     ${ALB_URL}/health"
    echo "🔗 Jobs API:           ${ALB_URL}/jobs"
    echo "🔗 Batch API:          ${ALB_URL}/batch/process (POST)"
    echo "📊 Environment:        $ENVIRONMENT"
    echo "🏗️ Region:            $REGION"
    echo ""
    print_status "🔍 CloudFormation Stacks:"
    echo "  • VPC:            ${PROJECT_NAME}-${ENVIRONMENT}-vpc"
    echo "  • App:            ${PROJECT_NAME}-${ENVIRONMENT}-app"
    echo "  • Frontend ECS:   ${PROJECT_NAME}-${ENVIRONMENT}-frontend (frontend-service-stack.yaml)"
    echo "  • Backend Lambda: ${PROJECT_NAME}-${ENVIRONMENT}-backend (backend-lambda-stack.yaml)"
    echo "  • Step Functions: ${PROJECT_NAME}-${ENVIRONMENT}-pipeline (provisioned by backend stack)"
    echo ""
    print_status "📱 Next Steps:"
    echo "1. Wait for ECS service to stabilize (~5-10 minutes)"
    echo "2. Access frontend at: $ALB_URL"
    echo "3. Test backend health: ${ALB_URL}/health"
    echo "4. Submit a test job: curl -X POST ${ALB_URL}/process-s3-video -H 'Content-Type: application/json' -d '{\"s3_uri\":\"s3://.../clip.mp4\"}'"
    echo "5. Watch pipeline executions: aws stepfunctions list-executions --state-machine-arn \$(aws cloudformation describe-stacks --stack-name ${PROJECT_NAME}-${ENVIRONMENT}-backend --query 'Stacks[0].Outputs[?OutputKey==\`PipelineStateMachineArn\`].OutputValue' --output text)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# Main deployment function
main() {
    echo "🚀 Lightship AI Project - Complete Infrastructure Deployment"
    echo "=========================================================="
    
    check_prerequisites
    get_user_input
    
    print_status "Starting deployment process..."
    
    # Deploy infrastructure
    deploy_vpc_stack
    deploy_app_stack
    
    # Build and deploy applications
    build_and_push_images
    update_lambda_function
    update_ecs_service
    
    # Show results
    show_deployment_results
}

# Execute main function
main "$@"