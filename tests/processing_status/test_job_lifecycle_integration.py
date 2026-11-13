"""
Integration tests for job lifecycle functionality
Tests the complete flow from content creation to job completion
"""

import pytest
import json
import uuid
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock
from moto import mock_aws
import boto3

# Set environment variables before importing modules
os.environ["CONTENT_TABLE_NAME"] = "test-content-table"
os.environ["JOBS_TABLE_NAME"] = "test-jobs-table"
os.environ["CONTENT_BUCKET_NAME"] = "test-content-bucket"

# Add lambda directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), "../../source/lambda"))

# Import modules under test
from services.processing_jobs_service import (
    create_processing_job,
    update_job_status,
    update_job_progress,
    complete_job_operation,
    get_job_details,
    get_jobs_by_content_id,
    handle_processing_status,
)

from services.content_service import handle_create_content


@mock_aws
class TestJobLifecycleIntegration:
    """Integration tests for complete job lifecycle"""

    def setup_method(self, method):
        """Set up test environment with DynamoDB tables"""
        # Create mock DynamoDB resource
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create content table
        self.content_table = self.dynamodb.create_table(
            TableName="test-content-table",
            KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "content_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "content_type", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-index",
                    "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "content-type-index",
                    "KeySchema": [{"AttributeName": "content_type", "KeyType": "HASH"}],
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
                {"AttributeName": "content_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "content-id-index",
                    "KeySchema": [{"AttributeName": "content_id", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "status-index",
                    "KeySchema": [{"AttributeName": "status", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Set environment variables
        os.environ["CONTENT_TABLE_NAME"] = "test-content-table"
        os.environ["JOBS_TABLE_NAME"] = "test-jobs-table"
        os.environ["CONTENT_BUCKET_NAME"] = "test-content-bucket"
        os.environ["USE_REAL_CONTENT_DATA"] = "true"
        os.environ["USE_REAL_PROCESSING_DATA"] = "true"

        # Patch the get_jobs_table function to return our mock table
        self.jobs_table_patcher = patch(
            "services.processing_jobs_service.get_jobs_table", return_value=self.jobs_table
        )
        self.jobs_table_patcher.start()

        # Patch content management table access
        self.content_table_patcher = patch("services.content_service.content_table", self.content_table)
        self.content_table_patcher.start()

        self.content_jobs_table_patcher = patch("services.content_service.jobs_table", self.jobs_table)
        self.content_jobs_table_patcher.start()

        # Test data
        self.test_user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        self.test_content_data = {
            "title": "Test Article for Job Tracking",
            "content_type": "article",
            "content_text": "This is a test article for job tracking integration tests.",
            "summary": "Test article summary",
            "author": "Test Author",
            "tags": ["test", "integration", "job-tracking"],
            "iab_categories": [126, 111],  # Technology & Computing, Medical Health
        }
        self.test_job_options = {"generate_embeddings": True, "extract_entities": True, "categorize": True}

    def teardown_method(self, method):
        """Clean up after each test"""
        self.jobs_table_patcher.stop()
        self.content_table_patcher.stop()
        self.content_jobs_table_patcher.stop()

    @patch("services.content_service.s3")
    def test_complete_content_creation_to_job_completion_flow(self, mock_s3):
        """Test complete flow from content creation through job completion"""
        mock_s3.put_object.return_value = {}

        # Creation enqueues an ingest object. The test drives the generic job
        # lifecycle separately because GEO owns enrichment asynchronously.
        response = handle_create_content(self.test_user_info, self.test_content_data)

        # Verify content creation response
        assert response["statusCode"] == 202
        response_data = json.loads(response["body"])
        content_id = response_data["content_id"]
        assert response_data["status"] == "queued"
        # Content payload is enqueued to S3 ingest/ for the pipeline
        mock_s3.put_object.assert_called_once()

        # Now test the actual job lifecycle with real DynamoDB
        # Create a real job for lifecycle testing
        job_result = create_processing_job(
            content_id=content_id,
            user_id=self.test_user_info["user_id"],
            job_type="content_processing",
            processing_options=self.test_job_options,
        )

        assert job_result["success"] is True
        job_id = job_result["job_id"]

        # Simulate job processing lifecycle
        self._simulate_job_processing_lifecycle(job_id, content_id)

        # Verify final job state
        final_job_result = get_job_details(job_id)
        assert final_job_result["success"] is True

        final_job = final_job_result["job"]
        assert final_job["status"] == "completed"
        assert final_job["processing_metrics"]["progress_percentage"] == 100
        assert len(final_job["processing_metrics"]["operations_completed"]) == 3
        assert "completed_at" in final_job

    def _simulate_job_processing_lifecycle(self, job_id: str, content_id: str):
        """Simulate a realistic job processing lifecycle"""

        # Step 1: Start processing
        start_result = update_job_status(
            job_id=job_id, new_status="processing", operation_details={"started_by": "processing_engine"}
        )
        assert start_result["success"] is True

        # Step 2: Begin embedding generation
        progress_result = update_job_progress(
            job_id=job_id,
            progress_percentage=10,
            current_operation="embedding_generation",
            operation_metrics={"tokens_to_process": 1000},
        )
        assert progress_result["success"] is True

        # Step 3: Complete embedding generation
        embedding_result = complete_job_operation(
            job_id=job_id,
            operation_name="embedding_generation",
            operation_metrics={"tokens_processed": 1000, "embedding_dimensions": 1536, "processing_time_ms": 2500},
        )
        assert embedding_result["success"] is True

        # Step 4: Update progress for entity extraction
        progress_result = update_job_progress(
            job_id=job_id,
            progress_percentage=50,
            current_operation="entity_extraction",
            operation_metrics={"entities_found": 15},
        )
        assert progress_result["success"] is True

        # Step 5: Complete entity extraction
        from decimal import Decimal

        entity_result = complete_job_operation(
            job_id=job_id,
            operation_name="entity_extraction",
            operation_metrics={
                "entities_extracted": 15,
                "confidence_score": Decimal("0.92"),
                "processing_time_ms": 1200,
            },
        )
        assert entity_result["success"] is True

        # Step 6: Update progress for categorization
        progress_result = update_job_progress(
            job_id=job_id,
            progress_percentage=85,
            current_operation="categorization",
            operation_metrics={"categories_to_analyze": 5},
        )
        assert progress_result["success"] is True

        # Step 7: Complete categorization
        category_result = complete_job_operation(
            job_id=job_id,
            operation_name="categorization",
            operation_metrics={
                "categories_assigned": 2,
                "confidence_score": Decimal("0.88"),
                "processing_time_ms": 800,
            },
        )
        assert category_result["success"] is True

        # Step 8: Final progress update
        progress_result = update_job_progress(job_id=job_id, progress_percentage=100, current_operation="finalizing")
        assert progress_result["success"] is True

        # Step 9: Complete the job
        completion_result = update_job_status(
            job_id=job_id,
            new_status="completed",
            metrics={
                "total_tokens_processed": 1000,
                "total_entities_extracted": 15,
                "total_categories_assigned": 2,
                "final_confidence_score": Decimal("0.90"),
            },
            operation_details={"completed_by": "processing_engine"},
        )
        assert completion_result["success"] is True

    def test_multiple_jobs_for_same_content(self):
        """Test creating and tracking multiple jobs for the same content"""
        content_id = "cnt_multi_job_test"

        # Create initial processing job
        job1_result = create_processing_job(
            content_id=content_id, user_id=self.test_user_info["user_id"], job_type="content_processing"
        )
        assert job1_result["success"] is True
        job1_id = job1_result["job_id"]

        # Complete first job
        update_job_status(job1_id, "processing")
        update_job_status(job1_id, "completed", {"tokens_processed": 500})

        # Create reprocessing job
        job2_result = create_processing_job(
            content_id=content_id, user_id=self.test_user_info["user_id"], job_type="content_reprocessing"
        )
        assert job2_result["success"] is True
        job2_id = job2_result["job_id"]

        # Create bulk operation job
        job3_result = create_processing_job(
            content_id=content_id, user_id=self.test_user_info["user_id"], job_type="bulk_operation"
        )
        assert job3_result["success"] is True
        job3_id = job3_result["job_id"]

        # Get all jobs for content
        jobs_result = get_jobs_by_content_id(content_id)
        assert jobs_result["success"] is True

        jobs = jobs_result["jobs"]
        assert len(jobs) == 3

        # Verify job types
        job_types = [job["job_type"] for job in jobs]
        assert "content_processing" in job_types
        assert "content_reprocessing" in job_types
        assert "bulk_operation" in job_types

        # Verify first job is completed
        completed_jobs = [job for job in jobs if job["status"] == "completed"]
        assert len(completed_jobs) == 1
        assert completed_jobs[0]["job_id"] == job1_id

    def test_job_failure_handling(self):
        """Test job failure scenarios and error tracking"""
        content_id = "cnt_failure_test"

        # Create job
        job_result = create_processing_job(content_id=content_id, user_id=self.test_user_info["user_id"])
        job_id = job_result["job_id"]

        # Start processing
        update_job_status(job_id, "processing")

        # Simulate partial progress
        update_job_progress(job_id, 30, "embedding_generation")

        # Simulate failure
        failure_result = update_job_status(
            job_id=job_id,
            new_status="failed",
            metrics={"error_count": 1},
            operation_details={
                "error_type": "embedding_service_timeout",
                "error_message": "Embedding service timed out after 30 seconds",
                "failed_operation": "embedding_generation",
            },
        )
        assert failure_result["success"] is True

        # Verify failure state
        job_details = get_job_details(job_id)
        failed_job = job_details["job"]

        assert failed_job["status"] == "failed"
        assert "completed_at" in failed_job  # Failed jobs also get completion timestamp
        assert failed_job["processing_metrics"]["progress_percentage"] == 30

        # Check failure details in lifecycle events
        failure_events = [e for e in failed_job["lifecycle_events"] if e["event"] == "status_changed_to_failed"]
        assert len(failure_events) == 1
        assert failure_events[0]["details"]["error_type"] == "embedding_service_timeout"

    @patch("services.processing_jobs_service.get_user_from_token")
    def test_processing_status_endpoint_with_real_jobs(self, mock_auth):
        """Test processing status endpoint with real job data"""
        mock_auth.return_value = self.test_user_info

        # Create multiple jobs in different states
        jobs_data = [
            ("cnt_001", "queued", None),
            ("cnt_002", "processing", {"progress": 45}),
            ("cnt_003", "completed", {"tokens_processed": 1500}),
            ("cnt_004", "failed", {"error_count": 1}),
            ("cnt_005", "queued", None),
        ]

        created_jobs = []
        for content_id, status, metrics in jobs_data:
            job_result = create_processing_job(content_id=content_id, user_id=self.test_user_info["user_id"])
            job_id = job_result["job_id"]
            created_jobs.append(job_id)

            if status != "queued":
                if status == "processing":
                    update_job_status(job_id, "processing")
                    if metrics:
                        update_job_progress(job_id, metrics["progress"], "test_operation")
                elif status in ["completed", "failed"]:
                    update_job_status(job_id, "processing")
                    update_job_status(job_id, status, metrics or {})

        # Call processing status endpoint
        status_event = {
            "httpMethod": "GET",
            "path": "/processing/status",
            "headers": {"Authorization": "Bearer test_token"},
        }

        response = handle_processing_status(self.test_user_info)

        assert response["statusCode"] == 200
        status_data = json.loads(response["body"])

        # Verify counts match our test data
        assert status_data["queued_jobs"] == 2
        assert status_data["active_jobs"] == 1  # processing jobs
        assert status_data["completed_today"] >= 1  # at least our completed job
        assert status_data["failed_today"] >= 1  # at least our failed job

        # Verify recent jobs include our test jobs
        recent_jobs = status_data["recent_jobs"]
        assert len(recent_jobs) > 0

        # Verify job IDs from our created jobs appear in recent jobs
        recent_job_ids = [job["job_id"] for job in recent_jobs]
        assert any(job_id in recent_job_ids for job_id in created_jobs)

    def test_job_timing_accuracy(self):
        """Test accuracy of job timing calculations"""
        content_id = "cnt_timing_test"

        # Create job
        job_result = create_processing_job(content_id=content_id, user_id=self.test_user_info["user_id"])
        job_id = job_result["job_id"]

        # Record start time
        start_time = datetime.now(timezone.utc)

        # Start processing (simulate 1 second delay)
        import time

        time.sleep(0.1)  # Small delay for testing
        update_job_status(job_id, "processing")
        processing_start = datetime.now(timezone.utc)

        # Complete job (simulate another delay)
        time.sleep(0.1)
        update_job_status(job_id, "completed")
        completion_time = datetime.now(timezone.utc)

        # Get job details and verify timing
        job_details = get_job_details(job_id)
        job = job_details["job"]

        # Verify timing metrics exist and are reasonable
        metrics = job["processing_metrics"]
        assert "queue_time_ms" in metrics
        assert "processing_time_ms" in metrics
        assert "total_time_ms" in metrics

        # Queue time should be positive (job was queued before processing)
        assert metrics["queue_time_ms"] > 0

        # Processing time should be positive
        assert metrics["processing_time_ms"] > 0

        # Total time should be sum of queue + processing time
        expected_total = metrics["queue_time_ms"] + metrics["processing_time_ms"]
        assert abs(metrics["total_time_ms"] - expected_total) < 100  # Allow 100ms tolerance

    def test_concurrent_job_updates(self):
        """Test handling concurrent updates to the same job"""
        content_id = "cnt_concurrent_test"

        # Create job
        job_result = create_processing_job(content_id=content_id, user_id=self.test_user_info["user_id"])
        job_id = job_result["job_id"]

        # Start processing
        update_job_status(job_id, "processing")

        # Simulate concurrent progress updates
        update_job_progress(job_id, 25, "operation_1", {"metric_1": 100})
        update_job_progress(job_id, 50, "operation_2", {"metric_2": 200})
        complete_job_operation(job_id, "operation_1", {"result_1": "success"})
        update_job_progress(job_id, 75, "operation_3", {"metric_3": 300})
        complete_job_operation(job_id, "operation_2", {"result_2": "success"})

        # Get final state
        job_details = get_job_details(job_id)
        job = job_details["job"]

        # Verify all updates were applied
        assert job["processing_metrics"]["progress_percentage"] == 75
        assert job["processing_metrics"]["metric_1"] == 100
        assert job["processing_metrics"]["metric_2"] == 200
        assert job["processing_metrics"]["metric_3"] == 300
        assert job["processing_metrics"]["result_1"] == "success"
        assert job["processing_metrics"]["result_2"] == "success"

        # Verify operations were completed
        completed_ops = job["processing_metrics"]["operations_completed"]
        assert "operation_1" in completed_ops
        assert "operation_2" in completed_ops

        # Verify lifecycle events are in order
        events = job["lifecycle_events"]
        assert len(events) >= 7  # created + processing + 3 progress + 2 operations


if __name__ == "__main__":
    pytest.main([__file__])
