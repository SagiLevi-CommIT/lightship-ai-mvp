---
name: aws-agentcore-project-implementation
description: Guide for implementing AWS Bedrock AgentCore projects with Strands Agents SDK. Use when creating new AgentCore agents, setting up multi-agent architectures, or deploying agent backends to AWS.
---

# AWS Bedrock AgentCore Project Implementation

Comprehensive guide for creating and deploying AWS Bedrock AgentCore projects with Strands Agents SDK.

## Architecture Overview

```
Internet
   ↓
Application Load Balancer (Internet-Facing)
   ├─ /              → ECS Fargate (Frontend - Streamlit)
   ├─ /chat          → AgentCore Runtime (Main/Orchestrator Agent)
   ├─ /agent-a/*     → AgentCore Runtime (Sub-Agent A)
   └─ /agent-b/*     → AgentCore Runtime (Sub-Agent B)
           ↓
   Backend Containers (ARM64 - Strands Agents SDK)
           ↓
   AWS Bedrock Claude Sonnet 4
```

## Project Structure

### Single Agent Project
```
project-root/
├── infrastructure/              # CloudFormation templates
│   ├── vpc-stack.yaml          # VPC, subnets, NAT, routing
│   ├── app-stack.yaml          # ALB, ECS, AgentCore, ECR, IAM, S3
│   └── deploy.sh               # Deployment automation
│
├── agentcore-be/               # Backend AgentCore module
│   ├── src/                    # Python application code
│   │   ├── agentcore_agent.py  # Main entry point
│   │   ├── tools/              # Agent tools
│   │   ├── prompt/             # System prompts
│   │   └── utils/              # Utilities
│   ├── Dockerfile              # ARM64 container configuration
│   ├── buildspec.yml           # CodeBuild specification
│   ├── requirements.txt        # Python dependencies
│   └── ecs-task-definition.json
│
├── ui-frontend/                # Frontend Streamlit Application
│   ├── src/
│   │   └── app.py              # Streamlit application
│   ├── Dockerfile
│   ├── buildspec.yml
│   ├── requirements.txt
│   └── ecs-task-definition.json
│
├── cicd/                       # CI/CD CloudFormation
│   └── cicd-stack.yaml
│
└── README.md
```

### Multi-Agent Project Structure
```
project-root/
├── infrastructure/
│   ├── vpc-stack.yaml
│   ├── app-stack.yaml          # Must include resources for ALL agents
│   └── deploy.sh
│
├── agentcore-be-orchestrator/  # Orchestrator Agent (invokes sub-agents)
│   ├── src/
│   │   ├── agentcore_agent.py
│   │   ├── tools/
│   │   │   └── invoke_sub_agents.py  # Tool to call other agents
│   │   └── prompt/
│   ├── Dockerfile
│   ├── buildspec.yml
│   └── requirements.txt
│
├── agentcore-be-analyzer/      # Analyzer Sub-Agent
│   ├── src/
│   │   ├── agentcore_agent.py
│   │   ├── tools/
│   │   └── prompt/
│   ├── Dockerfile
│   ├── buildspec.yml
│   └── requirements.txt
│
├── agentcore-be-grounder/      # Grounder Sub-Agent
│   ├── src/
│   │   ├── agentcore_agent.py
│   │   ├── tools/
│   │   └── prompt/
│   ├── Dockerfile
│   ├── buildspec.yml
│   └── requirements.txt
│
├── ui-frontend/
└── cicd/
```

## Critical Requirements

### ARM64 Architecture Requirement
⚠️ **Amazon Bedrock AgentCore requires ARM64 architecture for ALL deployed agents.**

```powershell
# ALWAYS build with ARM64 platform
docker build --platform linux/arm64 -t <image-name>:arm64 .
```

### Bedrock Model Configuration
```python
# ALWAYS use Claude Sonnet 4
BEDROCK_MODEL_ID = "anthropic.claude-sonnet-4-20250514-v1:0"
```

### Credentials
- **NEVER** use AWS access keys in code
- Use IAM roles with account-connected credentials
- Trust policy for AgentCore: `bedrock-agentcore.amazonaws.com`

## Infrastructure Requirements per Agent

### For Each AgentCore Module, You Need:

| Resource | Naming Pattern | Purpose |
|----------|----------------|---------|
| ECR Repository | `<project>-<agent-name>` | Container image storage |
| AgentCore Runtime | `<Project><Env><AgentName>Runtime-*` | Serverless agent execution |
| CloudWatch Log Group | `/aws/agentcore/<project>-<agent>` | Agent logging |
| IAM Role | `<project>-<env>-<agent>-role` | Agent permissions |
| ALB Target Group | `<project>-<env>-<agent>-tg` | Load balancer routing |
| ALB Listener Rule | Path: `/<agent-path>/*` | Route traffic to agent |
| CodeBuild Project | `cb-<project>-<agent>` | CI/CD build |

### app-stack.yaml Additions for Multi-Agent

```yaml
# For each new agent, add:

# 1. ECR Repository
AnalyzerECRRepository:
  Type: AWS::ECR::Repository
  Properties:
    RepositoryName: !Sub "${ProjectName}-analyzer"

# 2. Target Group
AnalyzerTargetGroup:
  Type: AWS::ElasticLoadBalancingV2::TargetGroup
  Properties:
    Name: !Sub "${ProjectName}-${Environment}-analyzer-tg"
    TargetType: ip
    Protocol: HTTP
    Port: 8080
    VpcId: !Ref VPC

# 3. Listener Rule
AnalyzerListenerRule:
  Type: AWS::ElasticLoadBalancingV2::ListenerRule
  Properties:
    ListenerArn: !Ref ALBListener
    Priority: 20  # Unique priority per rule
    Conditions:
      - Field: path-pattern
        Values:
          - "/analyzer/*"
    Actions:
      - Type: forward
        TargetGroupArn: !Ref AnalyzerTargetGroup

# 4. IAM Role (if different permissions needed)
AnalyzerAgentCoreRole:
  Type: AWS::IAM::Role
  Properties:
    RoleName: !Sub "${ProjectName}-${Environment}-analyzer-role"
    AssumeRolePolicyDocument:
      Statement:
        - Effect: Allow
          Principal:
            Service: bedrock-agentcore.amazonaws.com
          Action: sts:AssumeRole
```

## Backend Agent Template

### agentcore_agent.py (Entry Point)
```python
import time
from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from tools.my_tool import my_tool_function
from prompt.system_prompt import SYSTEM_PROMPT
import logging

logger = logging.getLogger(__name__)

# Initialize agent with tools and system prompt
agent = Agent(tools=[my_tool_function], system_prompt=SYSTEM_PROMPT)

# Initialize AgentCore app
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    start_time = time.time()
    query = payload.get("prompt", payload.get("query", ""))
    logger.info(f"=== REQUEST START === Query: {query[:100]}...")
    
    response = agent(query)
    
    runtime_ms = round((time.time() - start_time) * 1000, 2)
    logger.info(f"=== REQUEST END === Runtime: {runtime_ms}ms")
    return {"result": str(response)}

if __name__ == "__main__":
    logger.info("Starting AgentCore Backend...")
    app.run()
```

### Dockerfile (ARM64)
```dockerfile
FROM --platform=linux/arm64 python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./

# Expose AgentCore ports
EXPOSE 8080 8000

# Run the agent
CMD ["python", "agentcore_agent.py"]
```

### requirements.txt
```
bedrock-agentcore>=0.1.0
strands-agents>=0.1.0
boto3>=1.34.0
```

### buildspec.yml
```yaml
version: 0.2

env:
  variables:
    AWS_DEFAULT_REGION: "<REGION>"
    ECR_REPO_NAME: "<project>-<agent-name>"
    IMAGE_TAG: "latest"

phases:
  pre_build:
    commands:
      - echo Logging in to Amazon ECR...
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
      - REPOSITORY_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/$ECR_REPO_NAME
  build:
    commands:
      - echo Building ARM64 Docker image...
      - docker build --platform linux/arm64 -t $REPOSITORY_URI:$IMAGE_TAG .
      - docker tag $REPOSITORY_URI:$IMAGE_TAG $REPOSITORY_URI:$CODEBUILD_RESOLVED_SOURCE_VERSION
  post_build:
    commands:
      - echo Pushing Docker image to ECR...
      - docker push $REPOSITORY_URI:$IMAGE_TAG
      - docker push $REPOSITORY_URI:$CODEBUILD_RESOLVED_SOURCE_VERSION
      - echo Build completed on `date`
```

## Multi-Agent Orchestration

### Orchestrator Tool to Invoke Sub-Agents
```python
import boto3
import json
import os

agentcore_client = boto3.client('bedrock-agentcore', region_name=os.getenv('AWS_REGION'))

# Sub-agent ARNs (from environment or config)
ANALYZER_RUNTIME_ARN = os.getenv('ANALYZER_RUNTIME_ARN')
GROUNDER_RUNTIME_ARN = os.getenv('GROUNDER_RUNTIME_ARN')

def invoke_sub_agent(runtime_arn: str, query: str, session_id: str) -> dict:
    """Invoke a sub-agent and return its response."""
    payload = json.dumps({"prompt": query}).encode('utf-8')
    
    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=payload,
        contentType='application/json',
        accept='application/json'
    )
    
    return json.loads(response['response'].read())

def analyze_data(query: str) -> str:
    """Tool: Invoke the analyzer agent for data analysis."""
    result = invoke_sub_agent(ANALYZER_RUNTIME_ARN, query, "analyzer-session")
    return result.get("result", "Analysis failed")

def ground_response(query: str) -> str:
    """Tool: Invoke the grounder agent for fact verification."""
    result = invoke_sub_agent(GROUNDER_RUNTIME_ARN, query, "grounder-session")
    return result.get("result", "Grounding failed")
```

## Deployment Phases

### Phase 1: Infrastructure (VPC)
```powershell
aws cloudformation create-stack `
  --stack-name <project>-<env>-vpc `
  --template-body file://infrastructure/vpc-stack.yaml `
  --parameters ParameterKey=ProjectName,ParameterValue=<project> `
               ParameterKey=Environment,ParameterValue=<env> `
  --region <REGION>
```

### Phase 2: Application Infrastructure
```powershell
aws cloudformation create-stack `
  --stack-name <project>-<env>-app `
  --template-body file://infrastructure/app-stack.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --region <REGION>
```

### Phase 3: Build and Deploy Each Agent
```powershell
# For each agent module:
cd agentcore-be-<agent-name>

# Build ARM64 image
docker build --platform linux/arm64 -t <ecr-uri>:latest .
docker push <ecr-uri>:latest

# Create/Update AgentCore Runtime in AWS Console:
# Bedrock > AgentCore > Runtimes > Create/Update
```

### Phase 4: Deploy Frontend
```powershell
cd ui-frontend
docker build -t <frontend-ecr-uri>:latest .
docker push <frontend-ecr-uri>:latest

aws ecs update-service `
  --cluster <cluster-name> `
  --service <frontend-service> `
  --force-new-deployment
```

## Environment Variables

### Backend (AgentCore Runtime)
| Variable | Description |
|----------|-------------|
| `AWS_REGION` | AWS region |
| `MODEL_ID` | Bedrock model ID |
| `LOG_LEVEL` | Logging level |
| `*_RUNTIME_ARN` | Sub-agent ARNs (for orchestrator) |

### Frontend (ECS)
| Variable | Description |
|----------|-------------|
| `AGENTCORE_RUNTIME_ARN` | Main agent runtime ARN |
| `AWS_DEFAULT_REGION` | AWS region |
| `STREAMLIT_SERVER_PORT` | Usually 8501 |

## Testing & Verification

### Test AgentCore via CLI
```powershell
aws bedrock-agentcore invoke-agent-runtime `
  --agent-runtime-arn <runtime-arn> `
  --payload '{"prompt": "Test query"}' `
  --content-type 'application/json' `
  --region <REGION>
```

### Monitor Logs
```powershell
# AgentCore logs
aws logs tail /aws/agentcore/<project>-<agent> --region <REGION> --follow

# Frontend logs
aws logs tail /ecs/<project>-frontend --region <REGION> --follow
```

## Important Notes

1. **VPC Connectivity**: If agents need to access RDS or other VPC resources, ensure VPC peering and security group rules are configured
2. **NAT Gateway**: Only 1 NAT gateway needed; use shared route table for all private subnets
3. **ALB Security**: ALB should be internet-facing but restricted to specific IPs; internal agent-to-agent communication uses private IPs
4. **Session Management**: Use unique session IDs for conversation continuity
5. **Cold Start**: AgentCore cold start is 10-30 seconds; subsequent calls are faster

## References

- [AWS Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/)
- [Strands Agents SDK](https://github.com/strands-ai/strands)
- AWS Account: `095128162384` (reference implementation)
