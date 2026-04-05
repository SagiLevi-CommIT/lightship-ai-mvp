# Lightship AWS Deployment Guide

Complete deployment guide for the Lightship Financial Assistant on AWS infrastructure.

## Architecture Overview

The deployment architecture consists of:

```
Internet
   ↓
Application Load Balancer (Internet-Facing)
   ├─ / → ECS Fargate (Streamlit Frontend)
   └─ /chat, /health, /sessions/* → Lambda (Backend AI)
           ↓
   AWS Bedrock + S3 Data Sources
```

### Components

1. **VPC Infrastructure**
   - VPC with public and private subnets across 2 AZs
   - NAT Gateway for private subnet internet access
   - Security groups for ALB, ECS, and Lambda

2. **Application Load Balancer (ALB)**
   - Internet-facing load balancer
   - Routes traffic to frontend (ECS) and backend (Lambda)
   - HTTP/HTTPS support

3. **Frontend (ECS Fargate)**
   - Streamlit UI running in Docker containers
   - Deployed in private subnets
   - Auto-scaling capabilities

4. **Backend (Lambda)**
   - AI processing with AWS Bedrock integration
   - Container-based Lambda function
   - Integrated with ALB target group

5. **Storage (S3)**
   - cd ~B vector storage
   - Conversation results
   - Custom data sources

6. **Logging (CloudWatch)**
   - Frontend logs: `/ecs/lightship-mvp-frontend`
   - Backend logs: `/aws/lambda/lightship-mvp-backend`
   - 7-day retention policy

## Prerequisites

- AWS Account with appropriate permissions
- AWS CLI configured (`aws configure`)
- Docker installed and running
- Access to AWS Bedrock in us-east-1
- Git for repository management

## Deployment Steps

### 1. Deploy VPC Stack

```bash
# Set your AWS profile (if needed)
export AWS_PROFILE=lightship-dev

# Deploy VPC infrastructure
cd infrastructure
aws cloudformation create-stack \
  --stack-name lightship-mvp-vpc \
  --template-body file://vpc-stack.yaml \
  --parameters \
    ParameterKey=ProjectName,ParameterValue=lightship \
    ParameterKey=Environment,ParameterValue=mvp \
    ParameterKey=VpcCIDR,ParameterValue=10.145.16.0/20 \
  --region us-east-1

# Wait for stack creation
aws cloudformation wait stack-create-complete \
  --stack-name lightship-mvp-vpc \
  --region us-east-1
```

### 2. Deploy Application Stack

```bash
# Deploy app infrastructure (ALB, ECS, Lambda, ECR, S3, IAM)
aws cloudformation create-stack \
  --stack-name lightship-mvp-app \
  --template-body file://app-stack.yaml \
  --parameters \
    ParameterKey=ProjectName,ParameterValue=lightship \
    ParameterKey=Environment,ParameterValue=mvp \
    ParameterKey=VPCStackName,ParameterValue=lightship-mvp-vpc \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-east-1

# Wait for stack creation
aws cloudformation wait stack-create-complete \
  --stack-name lightship-mvp-app \
  --region us-east-1
```

### 3. Upload Data to S3

Upload your financial data to the custom datasources bucket:

```bash
# Get bucket name from CloudFormation outputs
BUCKET_NAME=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CustomDatasourcesBucketName`].OutputValue' \
  --output text)

echo "Data bucket: $BUCKET_NAME"

# Upload your data files
aws s3 sync ./data/ s3://${BUCKET_NAME}/data/ --region us-east-1
```

**Expected file structure:**
- `accounts.csv` - Chart of accounts
- `transactions.csv` - Transaction history
- `trial_balances.csv` - Trial balance data
- `account_subtypes.csv` - Account classifications

### 4. Deploy Frontend (Streamlit on ECS)

```bash
# Get ECR repository URI
FRONTEND_ECR=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`FrontendECRRepositoryUri`].OutputValue' \
  --output text)

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin ${FRONTEND_ECR}

# Build and push frontend image
cd s3-ui-fe
docker build -t ${FRONTEND_ECR}:latest .
docker push ${FRONTEND_ECR}:latest

# Create ECS task definition
aws ecs register-task-definition \
  --cli-input-json file://ecs-task-definition.json \
  --region us-east-1

# Create ECS service
ECS_CLUSTER=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ECSClusterName`].OutputValue' \
  --output text)

TARGET_GROUP=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`FrontendTargetGroupArn`].OutputValue' \
  --output text)

PRIVATE_SUBNETS=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-vpc \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`PrivateAppSubnets`].OutputValue' \
  --output text)

SECURITY_GROUP=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ECSSecurityGroupId`].OutputValue' \
  --output text)

aws ecs create-service \
  --cluster ${ECS_CLUSTER} \
  --service-name lightship-mvp-frontend-service \
  --task-definition lightship-mvp-frontend:1 \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[${PRIVATE_SUBNETS}],securityGroups=[${SECURITY_GROUP}],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=${TARGET_GROUP},containerName=lightship-frontend,containerPort=8501" \
  --health-check-grace-period-seconds 60 \
  --region us-east-1
```

### 5. Deploy Backend (Lambda)

```bash
# Get ECR repository URI for backend
BACKEND_ECR=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`BackendECRRepositoryUri`].OutputValue' \
  --output text)

# Build and push backend image
cd ../s3-lambda-be
export DOCKER_BUILDKIT=0
docker build -t ${BACKEND_ECR}:latest .
docker push ${BACKEND_ECR}:latest

# Get Lambda role and bucket names
LAMBDA_ROLE=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`LambdaExecutionRoleArn`].OutputValue' \
  --output text)

DATA_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CustomDatasourcesBucketName`].OutputValue' \
  --output text)

LANCEDB_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`LanceDBBucketName`].OutputValue' \
  --output text)

CONVERSATIONS_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ConversationResultsBucketName`].OutputValue' \
  --output text)

# Create Lambda function
aws lambda create-function \
  --function-name lightship-mvp-backend \
  --package-type Image \
  --code ImageUri=${BACKEND_ECR}:latest \
  --role ${LAMBDA_ROLE} \
  --timeout 300 \
  --memory-size 2048 \
  --environment "Variables={LOG_LEVEL=INFO,DATA_SOURCE_BUCKET=${DATA_BUCKET},CUSTOM_DATASOURCES_BUCKET=${DATA_BUCKET},LANCEDB_BUCKET=${LANCEDB_BUCKET},CONVERSATIONS_BUCKET=${CONVERSATIONS_BUCKET}}" \
  --region us-east-1

# Add permission for ALB to invoke Lambda
aws lambda add-permission \
  --function-name lightship-mvp-backend \
  --statement-id AllowELBInvoke \
  --action lambda:InvokeFunction \
  --principal elasticloadbalancing.amazonaws.com \
  --region us-east-1

# Register Lambda with ALB target group
BACKEND_TARGET_GROUP=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`BackendTargetGroupArn`].OutputValue' \
  --output text)

LAMBDA_ARN=$(aws lambda get-function \
  --function-name lightship-mvp-backend \
  --region us-east-1 \
  --query 'Configuration.FunctionArn' \
  --output text)

aws elbv2 register-targets \
  --target-group-arn ${BACKEND_TARGET_GROUP} \
  --targets Id=${LAMBDA_ARN} \
  --region us-east-1
```

### 6. Get Application URL

```bash
# Get ALB DNS name
ALB_URL=$(aws cloudformation describe-stacks \
  --stack-name lightship-mvp-app \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`LoadBalancerURL`].OutputValue' \
  --output text)

echo "Application URL: ${ALB_URL}"
echo "Frontend: ${ALB_URL}/"
echo "Backend API: ${ALB_URL}/chat"
```

## Testing the Deployment

### Test Backend API

```bash
# Test the chat endpoint
curl -X POST ${ALB_URL}/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What is the current cash balance?", "conversation_history": {}}'
```

### Access Frontend

Open your browser and navigate to the ALB URL to access the Streamlit interface.

## Monitoring and Logs

### View Lambda Logs

```bash
# Tail Lambda logs
aws logs tail /aws/lambda/lightship-mvp-backend \
  --region us-east-1 \
  --follow

# View recent logs
aws logs tail /aws/lambda/lightship-mvp-backend \
  --region us-east-1 \
  --since 1h
```

### View Frontend Logs

```bash
# Tail ECS logs
aws logs tail /ecs/lightship-mvp-frontend \
  --region us-east-1 \
  --follow

# View recent logs
aws logs tail /ecs/lightship-mvp-frontend \
  --region us-east-1 \
  --since 1h
```

### Monitor ECS Service

```bash
# Check service status
aws ecs describe-services \
  --cluster ${ECS_CLUSTER} \
  --services lightship-mvp-frontend-service \
  --region us-east-1
```

### Monitor Lambda Function

```bash
# Get Lambda function details
aws lambda get-function \
  --function-name lightship-mvp-backend \
  --region us-east-1
```

## Updating the Application

### Update Frontend

```bash
# Rebuild and push new image
cd s3-ui-fe
docker build -t ${FRONTEND_ECR}:latest .
docker push ${FRONTEND_ECR}:latest

# Force new deployment
aws ecs update-service \
  --cluster ${ECS_CLUSTER} \
  --service lightship-mvp-frontend-service \
  --force-new-deployment \
  --region us-east-1
```

### Update Backend

```bash
# Rebuild and push new image
cd s3-lambda-be
docker build -t ${BACKEND_ECR}:latest .
docker push ${BACKEND_ECR}:latest

# Update Lambda function code
aws lambda update-function-code \
  --function-name lightship-mvp-backend \
  --image-uri ${BACKEND_ECR}:latest \
  --region us-east-1
```

## Cleanup

To remove all resources:

```bash
# Delete ECS service
aws ecs delete-service \
  --cluster ${ECS_CLUSTER} \
  --service lightship-mvp-frontend-service \
  --force \
  --region us-east-1

# Delete Lambda function
aws lambda delete-function \
  --function-name lightship-mvp-backend \
  --region us-east-1

# Delete application stack
aws cloudformation delete-stack \
  --stack-name lightship-mvp-app \
  --region us-east-1

# Wait for deletion
aws cloudformation wait stack-delete-complete \
  --stack-name lightship-mvp-app \
  --region us-east-1

# Delete VPC stack
aws cloudformation delete-stack \
  --stack-name lightship-mvp-vpc \
  --region us-east-1

aws cloudformation wait stack-delete-complete \
  --stack-name lightship-mvp-vpc \
  --region us-east-1

# Empty and delete S3 buckets manually if needed
```

## Troubleshooting

### Lambda 502 Errors

If you encounter 502 errors from Lambda:
1. Check Lambda logs for errors
2. Verify Lambda has proper IAM permissions
3. Ensure Lambda environment variables are set correctly
4. Check Lambda timeout settings (should be 300 seconds)

### ECS Tasks Not Starting

If ECS tasks fail to start:
1. Check ECS service events for error messages
2. Verify ECR image was pushed successfully
3. Check CloudWatch logs for container errors
4. Ensure task definition has correct IAM roles

### No Data Returned

If queries return "No records found":
1. Verify data files are uploaded to S3
2. Check Lambda environment variable `DATA_SOURCE_BUCKET`
3. Verify S3 bucket permissions in IAM role
4. Check data file formats (CSV with correct headers)

## Cost Optimization

- **ECS**: Use Fargate Spot for non-production environments
- **Lambda**: Adjust memory size based on actual usage
- **S3**: Enable lifecycle policies for old data
- **CloudWatch**: Reduce log retention period if needed
- **NAT Gateway**: Consider NAT instances for dev/test environments

## Security Considerations

- ALB is internet-facing; consider adding WAF rules
- ECS tasks run in private subnets
- Lambda has minimal IAM permissions (principle of least privilege)
- S3 buckets have encryption enabled
- All communication uses VPC networking where possible

## Support

For issues or questions:
- Check CloudWatch logs first
- Review AWS CloudFormation events for deployment issues
- Verify all prerequisites are met
- Ensure AWS Bedrock access is enabled in us-east-1
