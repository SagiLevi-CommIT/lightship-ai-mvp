"""
Test 04 – S3 bucket access and structure.
Architecture ref: Section 3 (S3 bucket layout).

Buckets:
  - lightship-mvp-processing-{account_id} → input/, processing/, results/ prefixes
  - s3-lightship-custom-datasources-us-east-1 → custom data source
  - lightship-mvp-lancedb-{account_id} → LanceDB storage
  - lightship-mvp-conversations-{account_id} → conversation results
"""
import pytest
import uuid
from conftest import (
    PROCESSING_BUCKET, LANCEDB_BUCKET, CUSTOM_DATASOURCES_BUCKET,
    AWS_ACCOUNT_ID, AWS_REGION,
)

CONVERSATIONS_BUCKET = f"lightship-mvp-conversations-{AWS_ACCOUNT_ID}"


class TestS3BucketExistence:
    """Verify all required S3 buckets exist and are accessible."""

    def test_processing_bucket_exists(self, s3_client):
        resp = s3_client.head_bucket(Bucket=PROCESSING_BUCKET)
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_lancedb_bucket_exists(self, s3_client):
        resp = s3_client.head_bucket(Bucket=LANCEDB_BUCKET)
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_conversations_bucket_exists(self, s3_client):
        resp = s3_client.head_bucket(Bucket=CONVERSATIONS_BUCKET)
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_custom_datasources_bucket_accessible(self, s3_client):
        """Custom datasources bucket should exist (created externally)."""
        try:
            resp = s3_client.head_bucket(Bucket=CUSTOM_DATASOURCES_BUCKET)
            assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
        except s3_client.exceptions.ClientError as e:
            error_code = e.response["Error"]["Code"]
            # 403 = exists but no access (acceptable), 404 = doesn't exist (fail)
            assert error_code == "403", (
                f"Custom datasources bucket not found: {CUSTOM_DATASOURCES_BUCKET}"
            )


class TestS3BucketSecurity:
    """Verify S3 bucket security settings per architecture note Section 3."""

    def test_processing_bucket_public_access_blocked(self, s3_client):
        resp = s3_client.get_public_access_block(Bucket=PROCESSING_BUCKET)
        config = resp["PublicAccessBlockConfiguration"]
        assert config["BlockPublicAcls"], "BlockPublicAcls must be True"
        assert config["BlockPublicPolicy"], "BlockPublicPolicy must be True"
        assert config["IgnorePublicAcls"], "IgnorePublicAcls must be True"
        assert config["RestrictPublicBuckets"], "RestrictPublicBuckets must be True"

    def test_processing_bucket_versioning(self, s3_client):
        resp = s3_client.get_bucket_versioning(Bucket=PROCESSING_BUCKET)
        status = resp.get("Status")
        assert status == "Enabled", (
            f"Processing bucket versioning should be Enabled, got: {status}"
        )


class TestS3PrefixStructure:
    """Test read/write access to expected S3 prefix paths.
    Architecture ref: Section 3 (Prefix layout).
    """

    def test_can_write_to_input_prefix(self, s3_client):
        """Lambda/ECS should be able to write to input/ prefix."""
        test_key = f"input/videos/test-job-{uuid.uuid4()}/test-file.txt"
        s3_client.put_object(
            Bucket=PROCESSING_BUCKET,
            Key=test_key,
            Body=b"test data for E2E validation",
        )
        # Verify it's readable
        resp = s3_client.get_object(Bucket=PROCESSING_BUCKET, Key=test_key)
        assert resp["Body"].read() == b"test data for E2E validation"
        # Cleanup
        s3_client.delete_object(Bucket=PROCESSING_BUCKET, Key=test_key)

    def test_can_write_to_processing_prefix(self, s3_client):
        """Workers write frames to processing/ prefix."""
        test_key = f"processing/selected_frames/test-job-{uuid.uuid4()}/frame_001.jpg"
        s3_client.put_object(
            Bucket=PROCESSING_BUCKET,
            Key=test_key,
            Body=b"fake frame data",
        )
        s3_client.delete_object(Bucket=PROCESSING_BUCKET, Key=test_key)

    def test_can_write_to_results_prefix(self, s3_client):
        """Lambda pipeline writes config.json and detection_summary.json to results/."""
        test_key = f"results/default/test-job-{uuid.uuid4()}/config.json"
        test_data = b'{"video_class": "hazard_detection", "test": true}'
        s3_client.put_object(
            Bucket=PROCESSING_BUCKET,
            Key=test_key,
            Body=test_data,
        )
        resp = s3_client.get_object(Bucket=PROCESSING_BUCKET, Key=test_key)
        assert resp["Body"].read() == test_data
        s3_client.delete_object(Bucket=PROCESSING_BUCKET, Key=test_key)
