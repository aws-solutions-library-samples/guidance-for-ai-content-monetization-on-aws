"""
Integration tests for content update and delete functionality

Tests the complete update and delete flow including DynamoDB integration,
S3 operations, and job management in a realistic environment.
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

from content_deindexer.handler import deindex
from services.content_service import (
    ContentService,
    handle_update_content,
    handle_delete_content,
    handle_create_content,
    handle_reprocess_content,
    standardize_iab_categories,
)


@mock_aws
class TestUpdateDeleteIntegration:
    """Integration test cases for content update and delete with real AWS services"""

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

        # Create jobs table
        self.jobs_table = self.dynamodb.create_table(
            TableName="test-jobs-table",
            KeySchema=[{"AttributeName": "job_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "job_id", "AttributeType": "S"}],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Create S3 bucket
        self.s3 = boto3.client("s3", region_name="us-east-1")
        self.s3.create_bucket(Bucket="test-content-bucket")

        # User info for testing
        self.user_info = {
            "user_id": "integration_test_user",
            "email": "integration@example.com",
            "username": "integration_user",
        }

        # Point the content_service module-global clients at these moto resources so the handler
        # operates on the same tables/bucket the test reads (the import-time globals otherwise
        # bind to a different region/client than the moto tables created here).
        self.content_table_patcher = patch("services.content_service.content_table", self.content_table)
        self.jobs_table_patcher = patch("services.content_service.jobs_table", self.jobs_table)
        self.s3_patcher = patch("services.content_service.s3", self.s3)
        # EventBridge is a stub: the test reads back the content.deleted detail it was given.
        self.events = MagicMock()
        self.events.put_events.return_value = {"FailedEntryCount": 0, "Entries": [{"EventId": "evt-1"}]}
        self.events_patcher = patch("services.content_service.events", self.events)
        self.content_table_patcher.start()
        self.jobs_table_patcher.start()
        self.s3_patcher.start()
        self.events_patcher.start()

        # Create test content for update/delete operations
        self.test_content_id = "cnt_integration_test"
        self._create_test_content()

    def teardown_method(self, method):
        """Cleanup after each test method"""
        self.content_table_patcher.stop()
        self.jobs_table_patcher.stop()
        self.s3_patcher.stop()
        self.events_patcher.stop()

    def _emitted_detail(self):
        self.events.put_events.assert_called_once()
        (entry,) = self.events.put_events.call_args.kwargs["Entries"]
        assert entry["Source"] == "cm.content"
        assert entry["DetailType"] == "content.deleted"
        return json.loads(entry["Detail"])

    def _create_test_content(self):
        """Create test content in DynamoDB for update/delete operations"""
        test_content = {
            "content_id": self.test_content_id,
            "title": "Original Integration Test Article",
            "content_type": "article",
            "content_text": "Original content for integration testing.",
            "content_html": "<h1>Original Article</h1><p>Original content for integration testing.</p>",
            "summary": "Original summary for integration testing.",
            "author": "Original Author",
            "publication_date": "2025-01-15T10:00:00Z",
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:00:00Z",
            "created_by": self.user_info["user_id"],
            "tags": ["original", "test", "integration"],
            "iab_categories": [
                {
                    "id": 111,
                    "name": "Medical Health",
                    "tier_1": "Medical Health",
                    "tier_2": None,
                    "tier_3": None,
                    "tier_4": None,
                    "parent_id": None,
                }
            ],
            "custom_metadata": {"test_type": "integration", "version": "1.0"},
            "status": "completed",
            "processing_status": "completed",
        }

        # Store in DynamoDB
        self.content_table.put_item(Item=test_content)

        # Create associated S3 files
        self.s3.put_object(
            Bucket="test-content-bucket",
            Key=f"embeddings/{self.test_content_id}.json",
            Body=json.dumps({"content_id": self.test_content_id, "embedding": [0.1, 0.2, 0.3], "model": "test-model"}),
            ContentType="application/json",
        )

        self.s3.put_object(
            Bucket="test-content-bucket",
            Key=f"content/{self.test_content_id}.md",
            Body="# Original content file",
            ContentType="text/markdown",
        )

        # Update content item with S3 keys
        self.content_table.update_item(
            Key={"content_id": self.test_content_id},
            UpdateExpression="SET embedding_s3_key = :embedding_key, md_s3_key = :md_key",
            ExpressionAttributeValues={
                ":embedding_key": f"embeddings/{self.test_content_id}.json",
                ":md_key": f"content/{self.test_content_id}.md",
            },
        )

        # Create associated jobs
        test_jobs = [
            {
                "job_id": f"job_001_{self.test_content_id}",
                "content_id": self.test_content_id,
                "job_type": "content_processing",
                "status": "completed",
                "created_at": "2025-01-15T10:01:00Z",
                "created_by": self.user_info["user_id"],
            },
            {
                "job_id": f"job_002_{self.test_content_id}",
                "content_id": self.test_content_id,
                "job_type": "embedding_generation",
                "status": "completed",
                "created_at": "2025-01-15T10:02:00Z",
                "created_by": self.user_info["user_id"],
            },
        ]

        for job in test_jobs:
            self.jobs_table.put_item(Item=job)

    def test_complete_content_update_flow(self):
        """Test the complete content update flow with real DynamoDB storage"""
        update_data = {
            "title": "Updated Integration Test Article",
            "summary": "Updated summary for integration testing with real DynamoDB.",
            "tags": ["updated", "integration", "test", "dynamodb"],
            "iab_categories": [111, 126],  # Medical Health, Technology & Computing
            "custom_metadata": {"test_type": "integration", "version": "2.0", "updated": True},
        }

        # Update content
        result = handle_update_content(self.user_info, self.test_content_id, update_data)

        # Verify successful update
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])

        assert response_body["content_id"] == self.test_content_id
        assert response_body["title"] == "Updated Integration Test Article"
        assert response_body["summary"] == "Updated summary for integration testing with real DynamoDB."
        assert response_body["tags"] == ["updated", "integration", "test", "dynamodb"]
        assert response_body["updated_by"] == self.user_info["user_id"]
        assert "updated_at" in response_body

        # Verify IAB categories were standardized
        assert len(response_body["iab_categories"]) == 2
        for category in response_body["iab_categories"]:
            assert isinstance(category, dict)
            assert "id" in category
            assert "name" in category
            assert "tier_1" in category

        # Verify content was actually updated in DynamoDB
        content_response = self.content_table.get_item(Key={"content_id": self.test_content_id})
        assert "Item" in content_response

        stored_content = content_response["Item"]
        assert stored_content["title"] == "Updated Integration Test Article"
        assert stored_content["summary"] == "Updated summary for integration testing with real DynamoDB."
        assert stored_content["tags"] == ["updated", "integration", "test", "dynamodb"]
        assert stored_content["updated_by"] == self.user_info["user_id"]
        assert stored_content["custom_metadata"]["version"] == "2.0"
        assert stored_content["custom_metadata"]["updated"] is True

        # Verify IAB categories were properly stored
        assert len(stored_content["iab_categories"]) == 2

    def test_content_update_with_reprocessing(self):
        """Test content update with reprocessing job creation"""
        update_data = {"title": "Updated Article with Reprocessing", "reprocess": True}

        # Update content with reprocessing
        result = handle_update_content(self.user_info, self.test_content_id, update_data)

        # Verify successful update
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])

        assert response_body["content_id"] == self.test_content_id
        assert response_body["title"] == "Updated Article with Reprocessing"
        assert "reprocessing_job_id" in response_body

        reprocessing_job_id = response_body["reprocessing_job_id"]
        assert reprocessing_job_id.startswith("job_")

        # Verify reprocessing job was created in DynamoDB
        job_response = self.jobs_table.get_item(Key={"job_id": reprocessing_job_id})
        assert "Item" in job_response

        stored_job = job_response["Item"]
        assert stored_job["content_id"] == self.test_content_id
        assert stored_job["job_type"] == "content_reprocessing"
        assert stored_job["status"] == "queued"
        assert stored_job["created_by"] == self.user_info["user_id"]

    def test_content_update_not_found(self):
        """Test updating content that doesn't exist (real-data-only: 404, no mock fallback)"""
        nonexistent_id = "cnt_does_not_exist"
        update_data = {"title": "Updated Title"}

        result = handle_update_content(self.user_info, nonexistent_id, update_data)

        # Real-data-only: missing content -> 404
        assert result["statusCode"] == 404
        response_body = json.loads(result["body"])
        assert response_body["error"] == "not_found"
        assert f"Content {nonexistent_id} not found" in response_body["message"]

    def test_content_update_partial_fields(self):
        """Test updating only specific fields"""
        update_data = {
            "title": "Partially Updated Title"
            # Only updating title, other fields should remain unchanged
        }

        # Get original content for comparison
        original_response = self.content_table.get_item(Key={"content_id": self.test_content_id})
        original_content = original_response["Item"]

        # Update content
        result = handle_update_content(self.user_info, self.test_content_id, update_data)

        # Verify successful update
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])

        assert response_body["title"] == "Partially Updated Title"

        # Verify only title was updated in DynamoDB
        updated_response = self.content_table.get_item(Key={"content_id": self.test_content_id})
        updated_content = updated_response["Item"]

        assert updated_content["title"] == "Partially Updated Title"
        # Other fields should remain unchanged
        assert updated_content["summary"] == original_content["summary"]
        assert updated_content["tags"] == original_content["tags"]
        assert updated_content["content_text"] == original_content["content_text"]

    def test_complete_content_deletion_flow(self):
        """DELETE soft-deletes and emits content.deleted; the deindexer then removes everything"""
        result = handle_delete_content(self.user_info, self.test_content_id)

        assert result["statusCode"] == 202
        body = json.loads(result["body"])
        body.pop("_validation", None)  # added by the response validator outside production
        assert body == {"content_id": self.test_content_id, "status": "deleting"}

        # Accepted, not done: the record is "deleting" and nothing else is touched yet.
        record = self.content_table.get_item(Key={"content_id": self.test_content_id})["Item"]
        assert record["status"] == "deleting"
        assert record["deleted_by"] == self.user_info["user_id"]
        assert record["deleting_at"]
        self.s3.head_object(Bucket="test-content-bucket", Key=f"content/{self.test_content_id}.md")

        detail = self._emitted_detail()
        assert detail == {
            "content_id": self.test_content_id,
            "md_s3_key": f"content/{self.test_content_id}.md",
            "source_s3_key": None,
            "embedding_s3_key": f"embeddings/{self.test_content_id}.json",
            "deleted_by": self.user_info["user_id"],
            "deleting_at": record["deleting_at"],
        }

        # The deindexer, given that event, completes the delete.
        search_deletes = []
        summary = deindex(
            detail,
            content_table=self.content_table,
            jobs_table=self.jobs_table,
            s3_client=self.s3,
            bucket="test-content-bucket",
            delete_search_document=lambda content_id: search_deletes.append(content_id) or True,
        )

        assert summary["result"] == "deleted"
        assert summary["jobs"] == 2
        assert search_deletes == [self.test_content_id]
        assert "Item" not in self.content_table.get_item(Key={"content_id": self.test_content_id})
        for key in (f"embeddings/{self.test_content_id}.json", f"content/{self.test_content_id}.md"):
            with pytest.raises(Exception):  # NoSuchKey / 404
                self.s3.head_object(Bucket="test-content-bucket", Key=key)
        jobs_response = self.jobs_table.scan(
            FilterExpression="content_id = :content_id", ExpressionAttributeValues={":content_id": self.test_content_id}
        )
        assert len(jobs_response["Items"]) == 0

    def test_repeat_delete_is_accepted_and_re_emits(self):
        """A delete of a record already deleting retries: 202 and a second event"""
        assert handle_delete_content(self.user_info, self.test_content_id)["statusCode"] == 202
        assert handle_delete_content(self.user_info, self.test_content_id)["statusCode"] == 202

        assert self.events.put_events.call_count == 2

    def test_update_of_a_deleting_record_is_409(self):
        """An update must not write to a record the deindexer is removing"""
        handle_delete_content(self.user_info, self.test_content_id)

        result = handle_update_content(self.user_info, self.test_content_id, {"title": "Too late", "reprocess": True})

        assert result["statusCode"] == 409
        record = self.content_table.get_item(Key={"content_id": self.test_content_id})["Item"]
        assert record["title"] == "Original Integration Test Article"
        assert record["status"] == "deleting"
        jobs = self.jobs_table.scan()["Items"]
        assert all(job["job_type"] != "content_reprocessing" for job in jobs)

    def test_update_condition_refuses_a_delete_accepted_after_the_read(self):
        """The update's condition closes the race with a delete accepted between read and write"""
        original_get_item = self.content_table.get_item

        def read_then_delete(**kwargs):
            item = original_get_item(**kwargs)
            handle_delete_content(self.user_info, self.test_content_id)
            return item

        with patch.object(self.content_table, "get_item", side_effect=read_then_delete):
            result = handle_update_content(self.user_info, self.test_content_id, {"title": "Raced"})

        assert result["statusCode"] == 409
        record = self.content_table.get_item(Key={"content_id": self.test_content_id})["Item"]
        assert record["title"] == "Original Integration Test Article"

    def test_reprocess_withdraws_its_job_if_a_delete_lands_after_the_check(self):
        """The re-read after the job write closes the race with a delete accepted meanwhile"""
        original_put = self.jobs_table.put_item

        def put_then_delete(**kwargs):
            result = original_put(**kwargs)
            handle_delete_content(self.user_info, self.test_content_id)
            return result

        with patch.object(self.jobs_table, "put_item", side_effect=put_then_delete):
            result = handle_reprocess_content(self.user_info, self.test_content_id, {})

        assert result["statusCode"] == 409
        jobs = self.jobs_table.scan()["Items"]
        assert all(job["job_type"] != "content_reprocessing" for job in jobs)

    def test_update_of_a_record_removed_after_the_read_is_404(self):
        """A record the deindexer removed between read and write is not found, not 'being deleted'"""
        original_get_item = self.content_table.get_item
        calls = {"n": 0}

        def read_then_remove(**kwargs):
            calls["n"] += 1
            item = original_get_item(**kwargs)
            if calls["n"] == 1:
                self.content_table.delete_item(Key={"content_id": self.test_content_id})
            return item

        with patch.object(self.content_table, "get_item", side_effect=read_then_remove):
            result = handle_update_content(self.user_info, self.test_content_id, {"title": "Gone"})

        assert result["statusCode"] == 404
        assert "Item" not in self.content_table.get_item(Key={"content_id": self.test_content_id})

    def test_reprocess_of_a_deleting_record_is_409(self):
        """Reprocess must not queue a job the deindexer has already cleared"""
        handle_delete_content(self.user_info, self.test_content_id)

        result = handle_reprocess_content(self.user_info, self.test_content_id, {})

        assert result["statusCode"] == 409
        jobs = self.jobs_table.scan()["Items"]
        assert all(job["job_type"] != "content_reprocessing" for job in jobs)

    def test_reprocess_of_an_unknown_record_is_404(self):
        """Reprocess reads the record first, so an unknown id queues nothing"""
        result = handle_reprocess_content(self.user_info, "cnt_does_not_exist", {})

        assert result["statusCode"] == 404
        assert not [job for job in self.jobs_table.scan()["Items"] if job.get("content_id") == "cnt_does_not_exist"]

    def test_reprocess_of_a_live_record_is_queued(self):
        result = handle_reprocess_content(self.user_info, self.test_content_id, {})

        assert result["statusCode"] == 202
        assert json.loads(result["body"])["status"] == "queued"

    def test_details_read_shows_a_deleting_record(self):
        """The dashboard keeps showing the record, as deleting, until the deindexer removes it"""
        handle_delete_content(self.user_info, self.test_content_id)

        from services.content_service import handle_get_content

        with patch("services.content_service._details_markdown", return_value=""):
            result = handle_get_content(self.user_info, self.test_content_id)

        assert result["statusCode"] == 200
        assert json.loads(result["body"])["status"] == "deleting"

    def test_content_deletion_not_found(self):
        """Test deleting content that doesn't exist (real-data-only: 404, no mock fallback)"""
        nonexistent_id = "cnt_does_not_exist"

        result = handle_delete_content(self.user_info, nonexistent_id)

        # Real-data-only: missing content -> 404
        assert result["statusCode"] == 404
        response_body = json.loads(result["body"])
        assert response_body["error"] == "not_found"
        assert f"Content {nonexistent_id} not found" in response_body["message"]

    def test_content_deletion_minimal_content(self):
        """Test deleting content with minimal data (no S3 files)"""
        # Create minimal content without S3 files
        minimal_content_id = "cnt_minimal_test"
        minimal_content = {
            "content_id": minimal_content_id,
            "title": "Minimal Test Article",
            "content_type": "article",
            "created_at": "2025-01-15T10:00:00Z",
            "created_by": self.user_info["user_id"],
            "status": "pending",
        }

        self.content_table.put_item(Item=minimal_content)

        # Delete minimal content
        result = handle_delete_content(self.user_info, minimal_content_id)

        # Accepted; the record has no objects, so every key in the event is null
        assert result["statusCode"] == 202
        content_response = self.content_table.get_item(Key={"content_id": minimal_content_id})
        assert content_response["Item"]["status"] == "deleting"
        detail = self._emitted_detail()
        assert detail["md_s3_key"] is None
        assert detail["source_s3_key"] is None
        assert detail["embedding_s3_key"] is None

    def test_lambda_handler_update_integration(self):
        """Test the complete Lambda handler integration for content update"""
        # Create Lambda event for content update
        update_data = {
            "title": "Lambda Handler Updated Article",
            "summary": "Updated via Lambda handler integration test",
        }

        event = {
            "httpMethod": "PUT",
            "path": f"/content/{self.test_content_id}",
            "pathParameters": {"content_id": self.test_content_id},
            "headers": {"Authorization": "Bearer demo_token", "Content-Type": "application/json"},
            "body": json.dumps(update_data),
        }

        # Mock context
        context = MagicMock()

        # Mock token validation
        with patch("services.content_service.get_user_from_token") as mock_get_user:
            mock_get_user.return_value = self.user_info

            # Call content service request handler
            result = ContentService().handle_request(event, context)

            # Verify successful response
            assert result["statusCode"] == 200
            response_body = json.loads(result["body"])
            assert response_body["content_id"] == self.test_content_id
            assert response_body["title"] == "Lambda Handler Updated Article"

            # Verify content was updated in DynamoDB
            content_response = self.content_table.get_item(Key={"content_id": self.test_content_id})
            stored_content = content_response["Item"]
            assert stored_content["title"] == "Lambda Handler Updated Article"

    def test_lambda_handler_delete_integration(self):
        """Test the complete Lambda handler integration for content deletion"""
        # Create Lambda event for content deletion
        event = {
            "httpMethod": "DELETE",
            "path": f"/content/{self.test_content_id}",
            "pathParameters": {"content_id": self.test_content_id},
            "headers": {"Authorization": "Bearer demo_token", "Content-Type": "application/json"},
        }

        # Mock context
        context = MagicMock()

        # Mock token validation
        with patch("services.content_service.get_user_from_token") as mock_get_user:
            mock_get_user.return_value = self.user_info

            # Call content service request handler
            result = ContentService().handle_request(event, context)

            # Accepted: the record is deleting until the deindexer removes it
            assert result["statusCode"] == 202
            assert json.loads(result["body"])["status"] == "deleting"
            content_response = self.content_table.get_item(Key={"content_id": self.test_content_id})
            assert content_response["Item"]["status"] == "deleting"

    def test_concurrent_update_operations(self):
        """Test concurrent update operations to verify no race conditions"""
        import threading
        import time

        results = []
        errors = []

        def update_content(index):
            try:
                update_data = {"title": f"Concurrent Update {index}", "tags": ["concurrent", "test", f"thread_{index}"]}

                result = handle_update_content(self.user_info, self.test_content_id, update_data)
                results.append(result)
            except Exception as e:
                errors.append(str(e))

        # Create multiple threads
        threads = []
        for i in range(3):
            thread = threading.Thread(target=update_content, args=(i,))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 3

        # Verify all updates were successful
        for result in results:
            assert result["statusCode"] == 200

        # Verify final state in DynamoDB
        content_response = self.content_table.get_item(Key={"content_id": self.test_content_id})
        assert "Item" in content_response

        # One of the concurrent updates should have succeeded
        stored_content = content_response["Item"]
        assert stored_content["title"].startswith("Concurrent Update")

    def test_update_delete_error_handling(self):
        """Test error handling in update and delete operations"""
        # Test update with invalid content ID format
        result = handle_update_content(self.user_info, "", {"title": "Test"})
        assert result["statusCode"] == 400

        # Test delete with invalid content ID format
        result = handle_delete_content(self.user_info, None)
        assert result["statusCode"] == 400

        # Test update with malformed data (should handle gracefully)
        result = handle_update_content(self.user_info, self.test_content_id, {"iab_categories": "invalid"})
        # Should either succeed with error handling or return appropriate error
        assert result["statusCode"] in [200, 400, 500]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
