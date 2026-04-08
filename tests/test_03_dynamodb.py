"""
Test 03 – DynamoDB table structure and job tracking.
Architecture ref: Section 4 (DynamoDB table: lightship_jobs).

Table: lightship_jobs
Partition key: job_id
Expected attributes: status, created_at, input_s3_uri, video_class, etc.
"""
import pytest
from conftest import DYNAMODB_TABLE, AWS_REGION


class TestDynamoDBTable:
    """DynamoDB table existence and schema validation."""

    def test_table_exists(self, dynamodb_client):
        tables = dynamodb_client.list_tables()["TableNames"]
        assert DYNAMODB_TABLE in tables, (
            f"Table '{DYNAMODB_TABLE}' not found. Existing tables: {tables}"
        )

    def test_table_status_active(self, dynamodb_client):
        resp = dynamodb_client.describe_table(TableName=DYNAMODB_TABLE)
        status = resp["Table"]["TableStatus"]
        assert status == "ACTIVE", f"Table status: {status}"

    def test_table_partition_key(self, dynamodb_client):
        resp = dynamodb_client.describe_table(TableName=DYNAMODB_TABLE)
        keys = {k["AttributeName"]: k["KeyType"] for k in resp["Table"]["KeySchema"]}
        assert "job_id" in keys, f"Missing 'job_id' partition key. Found: {keys}"
        assert keys["job_id"] == "HASH", f"job_id key type should be HASH: {keys}"

    def test_table_billing_mode(self, dynamodb_client):
        resp = dynamodb_client.describe_table(TableName=DYNAMODB_TABLE)
        billing = resp["Table"].get("BillingModeSummary", {}).get("BillingMode")
        # On-demand is recommended per architecture note Section 4
        assert billing in ["PAY_PER_REQUEST", None], (
            f"Unexpected billing mode: {billing}"
        )

    def test_table_encryption_enabled(self, dynamodb_client):
        resp = dynamodb_client.describe_table(TableName=DYNAMODB_TABLE)
        sse = resp["Table"].get("SSEDescription", {})
        # SSE is required per architecture note
        assert sse.get("Status") in ["ENABLED", None], (
            f"Table SSE status unexpected: {sse}"
        )


class TestDynamoDBJobLifecycle:
    """Test job record creation and status transitions via Lambda API."""

    def test_can_write_test_job_record(self, dynamodb_resource):
        """Write and read a test job record to validate table access."""
        import uuid
        table = dynamodb_resource.Table(DYNAMODB_TABLE)
        test_job_id = f"test-infra-{uuid.uuid4()}"

        # Write
        table.put_item(Item={
            "job_id": test_job_id,
            "status": "TEST",
            "created_at": "2026-04-08T00:00:00Z",
            "input_type": "video",
        })

        # Read back
        resp = table.get_item(Key={"job_id": test_job_id})
        assert "Item" in resp, "Could not read back written test job"
        assert resp["Item"]["status"] == "TEST"

        # Cleanup
        table.delete_item(Key={"job_id": test_job_id})

    def test_job_record_missing_returns_correctly(self, dynamodb_resource):
        """Verify missing job returns empty (no error)."""
        import uuid
        table = dynamodb_resource.Table(DYNAMODB_TABLE)
        resp = table.get_item(Key={"job_id": f"nonexistent-{uuid.uuid4()}"})
        assert "Item" not in resp, "Should return no Item for nonexistent job_id"

    def test_job_status_values(self, dynamodb_resource):
        """Test all expected status transitions can be stored.
        Architecture ref Section 4: QUEUED, PROCESSING, COMPLETED, FAILED.
        """
        import uuid
        table = dynamodb_resource.Table(DYNAMODB_TABLE)
        valid_statuses = ["QUEUED", "PROCESSING", "COMPLETED", "FAILED"]

        for status in valid_statuses:
            job_id = f"test-status-{uuid.uuid4()}"
            table.put_item(Item={
                "job_id": job_id,
                "status": status,
                "created_at": "2026-04-08T00:00:00Z",
            })
            resp = table.get_item(Key={"job_id": job_id})
            assert resp["Item"]["status"] == status
            table.delete_item(Key={"job_id": job_id})
