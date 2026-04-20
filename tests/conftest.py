"""
Shared fixtures and configuration for Lightship MVP E2E tests.
Architecture: ALB → ECS Fargate (Streamlit UI) / Lambda (FastAPI backend)
Data flow: Upload → S3 → Lambda pipeline → DynamoDB tracking → S3 results
"""
import boto3
from botocore.config import Config
import json
import os
import pytest
import time

# Lambda cold start with YOLO11 + Depth-Anything-V2 takes 25-90s
BOTO_LAMBDA_CONFIG = Config(read_timeout=330, connect_timeout=10, retries={"max_attempts": 0})

# ─── Environment / stack outputs ────────────────────────────────────────────
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "336090301206")
PROJECT_NAME = os.environ.get("PROJECT_NAME", "lightship")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "mvp")
APP_STACK = f"{PROJECT_NAME}-{ENVIRONMENT}-app"

ALB_DNS = os.environ.get(
    "ALB_DNS",
    "lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com",
)
ALB_URL = f"http://{ALB_DNS}"

LAMBDA_FUNCTION = f"{PROJECT_NAME}-{ENVIRONMENT}-backend"
ECS_CLUSTER = f"{PROJECT_NAME}-{ENVIRONMENT}-cluster"
ECS_SERVICE = os.environ.get("ECS_SERVICE", f"{PROJECT_NAME}-{ENVIRONMENT}-frontend-service")

DYNAMODB_TABLE = "lightship_jobs"
PROCESSING_BUCKET = f"{PROJECT_NAME}-{ENVIRONMENT}-processing-{AWS_ACCOUNT_ID}"
LANCEDB_BUCKET = f"{PROJECT_NAME}-{ENVIRONMENT}-lancedb-{AWS_ACCOUNT_ID}"
CUSTOM_DATASOURCES_BUCKET = "s3-lightship-custom-datasources-us-east-1"

BACKEND_LOG_GROUP = f"/aws/lambda/{LAMBDA_FUNCTION}"
FRONTEND_LOG_GROUP = f"/ecs/{PROJECT_NAME}-{ENVIRONMENT}-frontend"

BACKEND_TG_ARN = (
    f"arn:aws:elasticloadbalancing:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
    "targetgroup/lightship-mvp-backend-tg/92ec81e852ab285d"
)
FRONTEND_TG_ARN = (
    f"arn:aws:elasticloadbalancing:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
    "targetgroup/lightship-mvp-frontend-tg/3fae828b6a2e689e"
)
ALB_SG_ID = "sg-0fdd16d7add075a8a"
ALB_ARN = (
    f"arn:aws:elasticloadbalancing:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
    "loadbalancer/app/lightship-mvp-alb/fc219afb78b3ffdc"
)


# ─── Boto3 clients ────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def lambda_client():
    return boto3.client("lambda", region_name=AWS_REGION, config=BOTO_LAMBDA_CONFIG)


@pytest.fixture(scope="session")
def dynamodb_client():
    return boto3.client("dynamodb", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def dynamodb_resource():
    return boto3.resource("dynamodb", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def s3_client():
    return boto3.client("s3", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def ecs_client():
    return boto3.client("ecs", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def elbv2_client():
    return boto3.client("elbv2", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def logs_client():
    return boto3.client("logs", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def ec2_client():
    return boto3.client("ec2", region_name=AWS_REGION)


# ─── Lambda invocation helper ─────────────────────────────────────────────────
def invoke_lambda(lambda_client, method: str, path: str, body: dict = None, headers: dict = None):
    """Invoke Lambda with ALB-format event and return parsed response."""
    payload = {
        "httpMethod": method,
        "path": path,
        "headers": headers or {"Host": ALB_DNS, "Content-Type": "application/json"},
        "queryStringParameters": None,
        "body": json.dumps(body) if body else None,
        "isBase64Encoded": False,
        "requestContext": {
            "elb": {"targetGroupArn": BACKEND_TG_ARN}
        },
    }
    resp = lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )
    raw = resp["Payload"].read().decode("utf-8")
    result = json.loads(raw)
    return resp["StatusCode"], resp.get("FunctionError"), result


@pytest.fixture(scope="session")
def alb_invoke(lambda_client):
    """Return invoke helper pre-bound to session lambda_client."""
    def _invoke(method, path, body=None, headers=None):
        return invoke_lambda(lambda_client, method, path, body, headers)
    return _invoke
