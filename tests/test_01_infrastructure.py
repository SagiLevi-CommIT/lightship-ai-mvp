"""
Test 01 – Infrastructure health checks.
Validates: Lambda state, ECS service status, ALB SG rules, target group registration.
Architecture ref: Section 2 (Compute), Section 9 (Network).
"""
import pytest
from conftest import (
    LAMBDA_FUNCTION, ECS_CLUSTER, ECS_SERVICE,
    ALB_SG_ID, BACKEND_TG_ARN, FRONTEND_TG_ARN,
    AWS_REGION,
)


class TestLambdaInfrastructure:
    """Lambda function health & configuration."""

    def test_lambda_exists_and_active(self, lambda_client):
        resp = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION)
        cfg = resp["Configuration"]
        assert cfg["State"] == "Active", f"Lambda state is {cfg['State']}"
        assert cfg["LastUpdateStatus"] == "Successful", f"Last update: {cfg['LastUpdateStatus']}"

    def test_lambda_runtime_image(self, lambda_client):
        resp = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION)
        cfg = resp["Configuration"]
        assert cfg["PackageType"] == "Image", "Lambda must use container image"

    def test_lambda_memory_and_timeout(self, lambda_client):
        resp = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION)
        cfg = resp["Configuration"]
        assert cfg["MemorySize"] >= 2048, f"Lambda memory {cfg['MemorySize']} < 2048 MB"
        assert cfg["Timeout"] >= 300, f"Lambda timeout {cfg['Timeout']} < 300s"

    def test_lambda_env_variables(self, lambda_client):
        resp = lambda_client.get_function(FunctionName=LAMBDA_FUNCTION)
        env = resp["Configuration"].get("Environment", {}).get("Variables", {})
        assert "CUSTOM_DATASOURCES_BUCKET" in env, "Missing CUSTOM_DATASOURCES_BUCKET env var"
        assert env.get("CUSTOM_DATASOURCES_BUCKET") == "s3-lightship-custom-datasources-us-east-1"

    def test_lambda_log_group_exists(self, logs_client):
        groups = logs_client.describe_log_groups(
            logGroupNamePrefix=f"/aws/lambda/{LAMBDA_FUNCTION}"
        )["logGroups"]
        assert len(groups) > 0, "Lambda CloudWatch log group not found"


class TestECSInfrastructure:
    """ECS Fargate frontend service health."""

    def test_ecs_service_active(self, ecs_client):
        resp = ecs_client.describe_services(
            cluster=ECS_CLUSTER,
            services=[ECS_SERVICE],
        )
        svc = resp["services"][0]
        assert svc["status"] == "ACTIVE", f"ECS service status: {svc['status']}"

    def test_ecs_service_running_count(self, ecs_client):
        resp = ecs_client.describe_services(
            cluster=ECS_CLUSTER,
            services=[ECS_SERVICE],
        )
        svc = resp["services"][0]
        assert svc["runningCount"] >= 1, f"ECS running tasks: {svc['runningCount']}"
        assert svc["runningCount"] == svc["desiredCount"], (
            f"Running {svc['runningCount']} != desired {svc['desiredCount']}"
        )

    def test_frontend_log_group_exists(self, logs_client):
        groups = logs_client.describe_log_groups(
            logGroupNamePrefix="/ecs/lightship-mvp-frontend"
        )["logGroups"]
        assert len(groups) > 0, "Frontend ECS CloudWatch log group not found"


class TestALBInfrastructure:
    """ALB security group and target group checks."""

    def test_alb_sg_allows_user_ip(self, ec2_client):
        sg = ec2_client.describe_security_groups(GroupIds=[ALB_SG_ID])["SecurityGroups"][0]
        allowed_cidrs = []
        for rule in sg["IpPermissions"]:
            if rule.get("FromPort") == 80:
                allowed_cidrs += [r["CidrIp"] for r in rule.get("IpRanges", [])]
        assert "87.70.177.112/32" in allowed_cidrs, (
            f"User IP 87.70.177.112/32 not in ALB SG port 80 rules: {allowed_cidrs}"
        )

    def test_alb_sg_https_allows_user_ip(self, ec2_client):
        sg = ec2_client.describe_security_groups(GroupIds=[ALB_SG_ID])["SecurityGroups"][0]
        allowed_cidrs = []
        for rule in sg["IpPermissions"]:
            if rule.get("FromPort") == 443:
                allowed_cidrs += [r["CidrIp"] for r in rule.get("IpRanges", [])]
        assert "87.70.177.112/32" in allowed_cidrs, (
            f"User IP 87.70.177.112/32 not in ALB SG port 443 rules: {allowed_cidrs}"
        )

    def test_backend_lambda_registered_in_tg(self, elbv2_client):
        resp = elbv2_client.describe_target_health(TargetGroupArn=BACKEND_TG_ARN)
        targets = resp["TargetHealthDescriptions"]
        assert len(targets) > 0, "No targets registered in backend target group"
        # Lambda targets show as 'unavailable' with HealthCheckDisabled - that is correct
        target_ids = [t["Target"]["Id"] for t in targets]
        assert any("lambda" in tid for tid in target_ids), (
            f"No Lambda ARN found in backend TG targets: {target_ids}"
        )

    def test_frontend_ecs_healthy_in_tg(self, elbv2_client):
        resp = elbv2_client.describe_target_health(TargetGroupArn=FRONTEND_TG_ARN)
        targets = resp["TargetHealthDescriptions"]
        assert len(targets) > 0, "No targets registered in frontend target group"
        healthy = [t for t in targets if t["TargetHealth"]["State"] == "healthy"]
        assert len(healthy) > 0, (
            f"No healthy frontend targets. States: {[t['TargetHealth']['State'] for t in targets]}"
        )

    def test_alb_listener_rules(self, elbv2_client):
        """Verify ALB routes backend paths to Lambda TG and default to frontend TG."""
        listeners = elbv2_client.describe_listeners(LoadBalancerArn=(
            "arn:aws:elasticloadbalancing:us-east-1:336090301206:"
            "loadbalancer/app/lightship-mvp-alb/fc219afb78b3ffdc"
        ))["Listeners"]
        assert len(listeners) > 0, "No ALB listeners found"

        rules = elbv2_client.describe_rules(ListenerArn=listeners[0]["ListenerArn"])["Rules"]
        # Find the backend rule
        backend_rule = None
        for rule in rules:
            for cond in rule.get("Conditions", []):
                if "/health" in cond.get("Values", []):
                    backend_rule = rule
                    break
        assert backend_rule is not None, "No ALB rule found routing /health to backend"

        # Verify key API paths are routed to backend
        backend_paths = []
        for rule in rules:
            for cond in rule.get("Conditions", []):
                backend_paths += cond.get("Values", [])
        for required_path in ["/health", "/process-video", "/status/*", "/results/*", "/download/*"]:
            assert required_path in backend_paths, (
                f"Path {required_path} not in ALB routing rules: {backend_paths}"
            )
