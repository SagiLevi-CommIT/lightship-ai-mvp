---
name: aws-agentcore-api-reference
description: AWS Bedrock AgentCore API reference and boto3 usage patterns. Use when invoking AgentCore runtimes, handling responses, or debugging AgentCore API calls.
---

# AWS Bedrock AgentCore API Reference

Correct patterns for invoking AWS Bedrock AgentCore Runtime using boto3.

## Correct boto3 Usage

### Initialize Client
```python
import boto3

# CORRECT service name
agentcore_client = boto3.client('bedrock-agentcore', region_name='<REGION>')
```

### Invoke AgentCore Runtime
```python
import json

def invoke_agentcore(runtime_arn: str, query: str, session_id: str = None) -> dict:
    """Invoke AgentCore Runtime with a query."""
    
    # Prepare payload as bytes
    payload_dict = {"prompt": query}
    payload_bytes = json.dumps(payload_dict).encode('utf-8')
    
    # Generate session ID if not provided
    if not session_id:
        session_id = f"session-{hash(query) % 10000}"
    
    # Invoke AgentCore Runtime
    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        payload=payload_bytes,
        contentType='application/json',
        accept='application/json'
    )
    
    # Parse streaming response
    response_body = json.loads(response['response'].read())
    return response_body
```

## Common Mistakes

### Wrong Service Name
```python
# WRONG - This service doesn't exist
client = boto3.client('bedrock-agentcore-runtime')

# CORRECT
client = boto3.client('bedrock-agentcore')
```

### Wrong Method Name
```python
# WRONG - This method doesn't exist
response = client.invoke_runtime(...)

# CORRECT
response = client.invoke_agent_runtime(...)
```

### Wrong Parameters
```python
# WRONG - These parameter names are incorrect
response = client.invoke_agent_runtime(
    runtimeArn=arn,           # Wrong: should be agentRuntimeArn
    inputText=query,          # Wrong: should be payload (bytes)
    sessionId=session_id      # Wrong: not a direct parameter
)

# CORRECT
response = client.invoke_agent_runtime(
    agentRuntimeArn=arn,
    payload=json.dumps({"prompt": query}).encode('utf-8'),
    contentType='application/json',
    accept='application/json'
)
```

## Full Working Example

```python
import json
import boto3
import logging

logger = logging.getLogger(__name__)

# Configuration
AGENTCORE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:<REGION>:095128162384:runtime/<RuntimeName>"
AWS_REGION = "<REGION>"

# Initialize client
agentcore_client = boto3.client('bedrock-agentcore', region_name=AWS_REGION)


def invoke_agentcore(query: str) -> dict:
    """Invoke AgentCore Runtime with error handling."""
    try:
        logger.info(f"Invoking AgentCore: {query[:50]}...")
        
        payload = json.dumps({"prompt": query}).encode('utf-8')
        
        response = agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
            payload=payload,
            contentType='application/json',
            accept='application/json'
        )
        
        response_body = json.loads(response['response'].read())
        logger.info(f"Response received: {len(str(response_body))} chars")
        
        return response_body
        
    except agentcore_client.exceptions.ThrottlingException:
        logger.warning("AgentCore throttling - retry later")
        return {"error": "Throttling - please try again"}
        
    except agentcore_client.exceptions.ValidationException as e:
        logger.error(f"Validation error: {e}")
        return {"error": f"Invalid request: {str(e)}"}
        
    except agentcore_client.exceptions.ResourceNotFoundException:
        logger.error("AgentCore Runtime not found")
        return {"error": "Runtime not found - check ARN"}
        
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return {"error": str(e)}


# Usage
if __name__ == "__main__":
    result = invoke_agentcore("What is the status of my data?")
    print(json.dumps(result, indent=2))
```

## Available Client Methods

```python
# List available methods
import boto3
client = boto3.client('bedrock-agentcore', region_name='<REGION>')
methods = [m for m in dir(client) if not m.startswith('_')]
```

**Key methods:**
| Method | Purpose |
|--------|---------|
| `invoke_agent_runtime` | Invoke the AgentCore runtime |
| `invoke_code_interpreter` | Invoke code interpreter |
| `stop_runtime_session` | Stop a runtime session |

## Response Structure

### Successful Response
```json
{
  "result": "The response from Claude Sonnet 4 model",
  "metadata": {
    "agent_id": "...",
    "session_id": "...",
    "completion_reason": "..."
  }
}
```

### Error Response
```json
{
  "error": "Error message description"
}
```

## CLI Testing

```powershell
# Test AgentCore from CLI
aws bedrock-agentcore invoke-agent-runtime `
  --agent-runtime-arn "arn:aws:bedrock-agentcore:<REGION>:095128162384:runtime/<RuntimeName>" `
  --payload '{"prompt": "Test query"}' `
  --content-type 'application/json' `
  --accept 'application/json' `
  --region <REGION>
```

## IAM Permissions

### Required Policy for Invoking AgentCore
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime"
      ],
      "Resource": "arn:aws:bedrock-agentcore:<REGION>:095128162384:runtime/*"
    }
  ]
}
```

### AgentCore Runtime Trust Policy
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

## Multi-Agent Invocation Pattern

When orchestrating multiple agents:

```python
import os

# Sub-agent ARNs from environment
ANALYZER_ARN = os.getenv('ANALYZER_RUNTIME_ARN')
GROUNDER_ARN = os.getenv('GROUNDER_RUNTIME_ARN')

def invoke_analyzer(query: str) -> str:
    """Invoke analyzer sub-agent."""
    result = invoke_agentcore_with_arn(ANALYZER_ARN, query)
    return result.get("result", "Analysis unavailable")

def invoke_grounder(query: str) -> str:
    """Invoke grounder sub-agent."""
    result = invoke_agentcore_with_arn(GROUNDER_ARN, query)
    return result.get("result", "Grounding unavailable")

def invoke_agentcore_with_arn(arn: str, query: str) -> dict:
    """Generic AgentCore invocation."""
    payload = json.dumps({"prompt": query}).encode('utf-8')
    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        payload=payload,
        contentType='application/json',
        accept='application/json'
    )
    return json.loads(response['response'].read())
```

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| `UnknownServiceError` | Wrong service name | Use `bedrock-agentcore` |
| `ValidationException` | Invalid ARN or payload | Check ARN format and payload encoding |
| `ThrottlingException` | Rate limit exceeded | Implement exponential backoff |
| `ResourceNotFoundException` | Runtime doesn't exist | Verify runtime ARN in AWS Console |
| `AccessDeniedException` | Missing IAM permissions | Add `bedrock-agentcore:InvokeAgentRuntime` permission |

## Environment Setup

```powershell
# Ensure AWS credentials are configured
$env:AWS_PROFILE = "<your-profile>"
$env:AWS_DEFAULT_REGION = "<REGION>"

# Or use SSO
aws sso login --profile <sso-profile>
```
