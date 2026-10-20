"""
Integration tests for content creation functionality

Tests the complete content creation flow including DynamoDB integration,
S3 operations, and embedding generation in a realistic environment.
"""

import pytest
import json
import boto3
import os
from moto import mock_aws
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from typing import Dict, Any

# Set up environment variables before importing modules
os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")
os.environ.setdefault("USE_REAL_CONTENT_DATA", "true")
os.environ.setdefault("USE_REAL_PROCESSING_DATA", "true")

# Import the modules to test
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.content_service import ContentService, handle_create_content, standardize_iab_categories


@mock_aws
class TestContentCreationIntegration:
    """Integration test cases for content creation with real AWS services"""

    def setup_method(self, method):
        """Setup for each test method with mock AWS services"""
        # Create mock DynamoDB tables
        self.dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create content table
        self.content_table = self.dynamodb.create_table(
            TableName="test-content-table",
            KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "content_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "content_type", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "content-type-index",
                    "KeySchema": [
                        {"AttributeName": "content_type", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Create jobs table
        self.jobs_table = self.dynamodb.create_table(
            TableName="test-jobs-table",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "job_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Create S3 bucket
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket="test-content-bucket")

        # Async create writes only to S3 via the module-global client; patch it with a
        # MagicMock for a deterministic 202 (the moto class decorator does not reliably
        # cover the import-time client used inside handle_create_content).
        self._s3_patch = patch("services.content_service.s3", MagicMock())
        self.mock_s3 = self._s3_patch.start()

        # User info for testing
        self.user_info = {
            "user_id": "integration_test_user",
            "email": "integration@example.com",
            "username": "integration_user",
        }

        # Valid request data
        self.valid_request_data = {
            "title": "Integration Test Article",
            "content_type": "article",
            "content_text": "This is a comprehensive integration test article with substantial content for testing purposes.",
            "content_html": "<h1>Integration Test Article</h1><p>This is a comprehensive integration test article.</p>",
            "summary": "An integration test article for testing the complete content creation flow.",
            "author": "integration_author",
            "publication_date": "2025-01-15T10:00:00Z",
            "tags": ["integration", "test", "article"],
            "iab_categories": [111, 126],
            "custom_metadata": {"test_type": "integration", "environment": "test"},
            "processing_options": {
                "generate_embeddings": False,  # Disable embeddings for integration tests
            },
        }

    def teardown_method(self, method):
        """Stop the S3 patch started in setup_method."""
        self._s3_patch.stop()

    def test_complete_content_creation_flow(self):
        """Test the complete async content creation flow (S3 enqueue, 202 accepted)"""
        # Create content
        result = handle_create_content(self.user_info, self.valid_request_data)

        # Verify async-accept response
        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        content_id = response_body["content_id"]

        assert content_id.startswith("cnt_")
        assert response_body["status"] == "queued"
        assert response_body["processing_status"] == "queued"
        assert response_body["message"] == "Content accepted and queued for GEO enrichment"

        # Async create enqueues to S3 (no synchronous DynamoDB write)
        self.mock_s3.put_object.assert_called_once()
        s3_kwargs = self.mock_s3.put_object.call_args[1]
        assert s3_kwargs["Key"] == f"ingest/{content_id}.json"
        assert s3_kwargs["Metadata"] == {"content-id": content_id}

        # The enqueued payload carries the submitted fields for the GEO pipeline
        ingest_payload = json.loads(s3_kwargs["Body"])
        assert ingest_payload["title"] == "Integration Test Article"
        assert ingest_payload["author"] == "integration_author"
        assert ingest_payload["tags"] == ["integration", "test", "article"]

    def test_lambda_handler_integration(self):
        """Test the complete Lambda handler integration"""
        # Create Lambda event
        event = {
            "httpMethod": "POST",
            "path": "/content",
            "headers": {"Authorization": "Bearer test_token", "Content-Type": "application/json"},
            "body": json.dumps(self.valid_request_data),
        }

        # Mock context
        context = MagicMock()

        # Mock token validation
        with patch("services.content_service.get_user_from_token") as mock_get_user:
            mock_get_user.return_value = self.user_info

            # Call content service request handler
            result = ContentService().handle_request(event, context)

            # Verify async-accept response
            assert result["statusCode"] == 202
            response_body = json.loads(result["body"])
            assert response_body["content_id"].startswith("cnt_")
            assert response_body["status"] == "queued"

    def test_multiple_content_creation(self):
        """Test creating multiple content items"""
        content_ids = []

        for i in range(3):
            request_data = self.valid_request_data.copy()
            request_data["title"] = f"Integration Test Article {i + 1}"
            request_data["tags"] = ["integration", "test", f"article_{i + 1}"]

            result = handle_create_content(self.user_info, request_data)

            assert result["statusCode"] == 202
            response_body = json.loads(result["body"])
            content_ids.append(response_body["content_id"])

        # Verify content IDs are unique
        assert len(set(content_ids)) == 3

    def test_content_creation_with_minimal_data(self):
        """Test content creation with minimal required data"""
        minimal_request = {"title": "Minimal Integration Test", "content_type": "article"}

        result = handle_create_content(self.user_info, minimal_request)

        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        assert response_body["content_id"].startswith("cnt_")

        # Verify default values in the enqueued S3 ingest payload
        ingest_payload = json.loads(self.mock_s3.put_object.call_args[1]["Body"])
        assert ingest_payload["title"] == "Minimal Integration Test"
        assert ingest_payload["content_type"] == "article"
        assert ingest_payload["content_text"] == ""
        assert ingest_payload["author"] == self.user_info["username"]  # Default to username
        assert ingest_payload["tags"] == []
        assert ingest_payload["custom_metadata"] == {}

    def test_iab_categories_standardization_integration(self):
        """Test IAB categories standardization in integration environment"""
        request_data = self.valid_request_data.copy()
        request_data["iab_categories"] = [
            111,  # Simple ID
            {"id": 126, "name": "Technology & Computing", "tier_1": "Technology & Computing"},  # Full object
            "Medical Health",  # String format
            999,  # Unknown ID
        ]

        result = handle_create_content(self.user_info, request_data)

        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        assert response_body["content_id"].startswith("cnt_")

        # Async create enqueues to S3; IAB standardization now happens in the GEO pipeline,
        # not synchronously at create time.
        self.mock_s3.put_object.assert_called_once()

    def test_content_creation_with_custom_metadata(self):
        """Test content creation with complex custom metadata"""
        request_data = self.valid_request_data.copy()
        request_data["custom_metadata"] = {
            "source": "integration_test",
            "priority": "high",
            "workflow": {"stage": "initial", "approver": "test_approver"},
            "metrics": {"expected_views": 1000, "target_engagement": 0.75},
        }

        result = handle_create_content(self.user_info, request_data)

        assert result["statusCode"] == 202

        # Verify custom metadata is carried in the enqueued S3 payload
        ingest_payload = json.loads(self.mock_s3.put_object.call_args[1]["Body"])
        custom_metadata = ingest_payload["custom_metadata"]
        assert custom_metadata["source"] == "integration_test"
        assert custom_metadata["priority"] == "high"
        assert custom_metadata["workflow"]["stage"] == "initial"
        assert custom_metadata["workflow"]["approver"] == "test_approver"
        assert custom_metadata["metrics"]["expected_views"] == 1000
        assert custom_metadata["metrics"]["target_engagement"] == 0.75

    def test_content_creation_queues_without_legacy_processing_options(self):
        """The GEO pipeline owns processing; create only persists the ingest payload."""
        request_data = self.valid_request_data.copy()
        request_data.pop("processing_options")

        result = handle_create_content(self.user_info, request_data)

        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        assert response_body["content_id"].startswith("cnt_")
        ingest_payload = json.loads(self.mock_s3.put_object.call_args[1]["Body"])
        assert "processing_options" not in ingest_payload

    def test_error_handling_invalid_request(self):
        """Test error handling with invalid request data"""
        invalid_request = {
            "content_type": "article"
            # Missing required 'title' field
        }

        result = handle_create_content(self.user_info, invalid_request)

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Missing required field: title" in response_body["message"]

        # Verify no content was created in DynamoDB
        # Since we don't have the content_id, we can scan the table to verify it's empty
        scan_response = self.content_table.scan()
        assert scan_response["Count"] == 0

    def test_concurrent_content_creation(self):
        """Test concurrent content creation to verify no race conditions"""
        import threading
        import time

        results = []
        errors = []

        def create_content(index):
            try:
                request_data = self.valid_request_data.copy()
                request_data["title"] = f"Concurrent Test Article {index}"
                request_data["tags"] = ["concurrent", "test", f"thread_{index}"]

                result = handle_create_content(self.user_info, request_data)
                results.append(result)
            except Exception as e:
                errors.append(str(e))

        # Create multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=create_content, args=(i,))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 5

        # Verify all creations were successful
        content_ids = []
        for result in results:
            assert result["statusCode"] == 202
            response_body = json.loads(result["body"])
            content_ids.append(response_body["content_id"])

        # Verify all content IDs are unique
        assert len(set(content_ids)) == 5


class TestContentCreationWithFeatureFlags:
    """Test content creation with different feature flag configurations"""

    def setup_method(self, method):
        """Setup for feature flag tests"""
        self.user_info = {"user_id": "feature_test_user", "username": "feature_user"}

        self.valid_request_data = {
            "title": "Feature Flag Test Article",
            "content_type": "article",
            "content_text": "Testing feature flags for content creation.",
        }

    def test_content_creation_with_real_data_disabled(self):
        """Async create ignores the data-source flag; still enqueues to S3 (202)"""
        with patch.dict(os.environ, {"USE_REAL_CONTENT_DATA": "false", "USE_REAL_PROCESSING_DATA": "false"}), patch(
            "services.content_service.s3", MagicMock()
        ):
            result = handle_create_content(self.user_info, self.valid_request_data)

            # Should still succeed — the async path only enqueues to S3
            assert result["statusCode"] == 202
            response_body = json.loads(result["body"])
            assert "content_id" in response_body
            assert response_body["status"] == "queued"

    def test_content_creation_with_mixed_feature_flags(self):
        """Test content creation with mixed feature flag settings"""
        with patch.dict(os.environ, {"USE_REAL_CONTENT_DATA": "true", "USE_REAL_PROCESSING_DATA": "false"}), patch(
            "services.content_service.s3", MagicMock()
        ):
            result = handle_create_content(self.user_info, self.valid_request_data)

            # Async accept
            assert result["statusCode"] == 202


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
