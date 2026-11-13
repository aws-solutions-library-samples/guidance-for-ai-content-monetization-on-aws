"""
Unit tests for job tracking functionality in processing_status.py
"""

import pytest
import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock
from moto import mock_aws
import boto3

# Import the functions we're testing
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "../../source/lambda"))

from services.processing_jobs_service import (
    create_processing_job,
    update_job_status,
    calculate_job_timing_metrics,
    update_job_progress,
    complete_job_operation,
    get_job_details,
    get_jobs_by_content_id,
)


@mock_aws
class TestJobTracking:
    """Test job tracking functionality"""

    def setup_method(self, method):
        """Set up test environment"""
        # Create mock DynamoDB table
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

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

        # Set environment variable for the table name
        os.environ["JOBS_TABLE_NAME"] = "test-jobs-table"

        # Patch the get_jobs_table function to return our mock table
        self.jobs_table_patcher = patch(
            "services.processing_jobs_service.get_jobs_table", return_value=self.jobs_table
        )
        self.jobs_table_patcher.start()

        # Sample test data
        self.test_content_id = "cnt_test_001"
        self.test_user_id = "user_test_001"
        self.test_job_type = "content_processing"
        self.test_processing_options = {"generate_embeddings": True, "extract_entities": True, "categorize": True}

    def teardown_method(self, method):
        """Clean up after each test"""
        self.jobs_table_patcher.stop()

    def test_create_processing_job_success(self):
        """Test successful job creation"""
        result = create_processing_job(
            content_id=self.test_content_id,
            user_id=self.test_user_id,
            job_type=self.test_job_type,
            processing_options=self.test_processing_options,
        )

        # Verify result structure
        assert result["success"] is True
        assert "job_id" in result
        assert result["job_id"].startswith("job_")
        assert "job_item" in result

        # Verify job item structure
        job_item = result["job_item"]
        assert job_item["content_id"] == self.test_content_id
        assert job_item["created_by"] == self.test_user_id
        assert job_item["job_type"] == self.test_job_type
        assert job_item["status"] == "queued"
        assert job_item["processing_options"] == self.test_processing_options

        # Verify processing metrics structure
        assert "processing_metrics" in job_item
        metrics = job_item["processing_metrics"]
        assert metrics["queue_time_ms"] == 0
        assert metrics["processing_time_ms"] == 0
        assert metrics["total_time_ms"] == 0
        assert metrics["progress_percentage"] == 0
        assert isinstance(metrics["operations_completed"], list)

        # Verify lifecycle events
        assert "lifecycle_events" in job_item
        assert len(job_item["lifecycle_events"]) == 1
        assert job_item["lifecycle_events"][0]["event"] == "job_created"

    def test_create_processing_job_with_defaults(self):
        """Test job creation with default parameters"""
        result = create_processing_job(content_id=self.test_content_id, user_id=self.test_user_id)

        assert result["success"] is True
        job_item = result["job_item"]
        assert job_item["job_type"] == "content_processing"
        assert job_item["processing_options"] == {}

    @patch("services.processing_jobs_service.get_jobs_table")
    def test_create_processing_job_dynamodb_error(self, mock_get_table):
        """Test job creation with DynamoDB error"""
        # Mock DynamoDB error
        mock_table = Mock()
        mock_table.put_item.side_effect = Exception("DynamoDB error")
        mock_get_table.return_value = mock_table

        result = create_processing_job(content_id=self.test_content_id, user_id=self.test_user_id)

        assert result["success"] is False
        assert "error" in result
        assert "DynamoDB error" in result["error"]

    def test_update_job_status_to_processing(self):
        """Test updating job status to processing"""
        # First create a job
        create_result = create_processing_job(content_id=self.test_content_id, user_id=self.test_user_id)
        job_id = create_result["job_id"]

        # Update status to processing
        metrics = {"tokens_processed": 100}
        operation_details = {"operation": "embedding_generation"}

        result = update_job_status(
            job_id=job_id, new_status="processing", metrics=metrics, operation_details=operation_details
        )

        assert result["success"] is True
        updated_job = result["job"]
        assert updated_job["status"] == "processing"
        assert "started_at" in updated_job
        assert updated_job["processing_metrics"]["tokens_processed"] == 100

        # Check lifecycle events
        events = updated_job["lifecycle_events"]
        assert len(events) == 2  # job_created + status_changed_to_processing
        assert events[1]["event"] == "status_changed_to_processing"
        assert events[1]["details"] == operation_details

    def test_update_job_status_to_completed(self):
        """Test updating job status to completed with timing calculations"""
        # Create and start a job
        create_result = create_processing_job(content_id=self.test_content_id, user_id=self.test_user_id)
        job_id = create_result["job_id"]

        # Update to processing first
        update_job_status(job_id, "processing")

        # Update to completed
        completion_metrics = {
            "tokens_processed": 1500,
            "images_processed": 2,
            "operations_completed": ["embeddings", "categorization"],
        }

        result = update_job_status(job_id=job_id, new_status="completed", metrics=completion_metrics)

        assert result["success"] is True
        updated_job = result["job"]
        assert updated_job["status"] == "completed"
        assert "completed_at" in updated_job
        assert updated_job["processing_metrics"]["tokens_processed"] == 1500

        # Timing metrics should be calculated
        assert "queue_time_ms" in updated_job["processing_metrics"]
        assert "processing_time_ms" in updated_job["processing_metrics"]
        assert "total_time_ms" in updated_job["processing_metrics"]

    def test_calculate_job_timing_metrics(self):
        """Test timing metrics calculation"""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(minutes=5)
        started_at = now - timedelta(minutes=3)
        completed_at = now

        job = {
            "created_at": created_at.isoformat(),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
        }

        metrics = calculate_job_timing_metrics(job)

        assert metrics is not None
        assert metrics["queue_time_ms"] == 2 * 60 * 1000  # 2 minutes
        assert metrics["processing_time_ms"] == 3 * 60 * 1000  # 3 minutes
        assert metrics["total_time_ms"] == 5 * 60 * 1000  # 5 minutes

    def test_calculate_job_timing_metrics_no_started_at(self):
        """Test timing metrics calculation without started_at"""
        now = datetime.now(timezone.utc)
        created_at = now - timedelta(minutes=5)
        completed_at = now

        job = {"created_at": created_at.isoformat(), "completed_at": completed_at.isoformat()}

        metrics = calculate_job_timing_metrics(job)

        assert metrics is not None
        assert metrics["queue_time_ms"] == 0
        assert metrics["processing_time_ms"] == 5 * 60 * 1000  # Total time
        assert metrics["total_time_ms"] == 5 * 60 * 1000

    def test_calculate_job_timing_metrics_invalid_dates(self):
        """Test timing metrics calculation with invalid dates"""
        job = {"created_at": "invalid-date", "completed_at": "also-invalid"}

        metrics = calculate_job_timing_metrics(job)
        assert metrics is None

    def test_update_job_progress(self):
        """Test updating job progress"""
        # Create a job
        create_result = create_processing_job(content_id=self.test_content_id, user_id=self.test_user_id)
        job_id = create_result["job_id"]

        # Update progress
        operation_metrics = {"tokens_processed": 500}
        result = update_job_progress(
            job_id=job_id,
            progress_percentage=50,
            current_operation="embedding_generation",
            operation_metrics=operation_metrics,
        )

        assert result["success"] is True
        updated_job = result["job"]
        assert updated_job["processing_metrics"]["progress_percentage"] == 50
        assert updated_job["processing_metrics"]["current_operation"] == "embedding_generation"
        assert updated_job["processing_metrics"]["tokens_processed"] == 500

        # Check lifecycle event
        events = updated_job["lifecycle_events"]
        progress_event = next(e for e in events if e["event"] == "progress_update")
        assert progress_event["details"]["progress_percentage"] == 50

    def test_complete_job_operation(self):
        """Test completing a job operation"""
        # Create a job
        create_result = create_processing_job(content_id=self.test_content_id, user_id=self.test_user_id)
        job_id = create_result["job_id"]

        # Complete an operation
        operation_metrics = {"embedding_dimensions": 1536}
        result = complete_job_operation(
            job_id=job_id, operation_name="embedding_generation", operation_metrics=operation_metrics
        )

        assert result["success"] is True
        updated_job = result["job"]
        assert "embedding_generation" in updated_job["processing_metrics"]["operations_completed"]
        assert updated_job["processing_metrics"]["embedding_dimensions"] == 1536

        # Check lifecycle event
        events = updated_job["lifecycle_events"]
        operation_event = next(e for e in events if e["event"] == "operation_completed")
        assert operation_event["details"]["operation"] == "embedding_generation"

    def test_get_job_details_success(self):
        """Test getting job details"""
        # Create a job
        create_result = create_processing_job(content_id=self.test_content_id, user_id=self.test_user_id)
        job_id = create_result["job_id"]

        # Get job details
        result = get_job_details(job_id)

        assert result["success"] is True
        job = result["job"]
        assert job["job_id"] == job_id
        assert job["content_id"] == self.test_content_id

    def test_get_job_details_not_found(self):
        """Test getting details for non-existent job"""
        result = get_job_details("nonexistent_job_id")

        assert result["success"] is False
        assert result["error"] == "Job not found"

    def test_get_jobs_by_content_id(self):
        """Test getting all jobs for a content item"""
        # Create multiple jobs for the same content
        job1_result = create_processing_job(
            content_id=self.test_content_id, user_id=self.test_user_id, job_type="content_processing"
        )

        job2_result = create_processing_job(
            content_id=self.test_content_id, user_id=self.test_user_id, job_type="content_reprocessing"
        )

        # Get jobs by content ID
        result = get_jobs_by_content_id(self.test_content_id)

        assert result["success"] is True
        jobs = result["jobs"]
        assert len(jobs) == 2

        # Verify both jobs are returned
        job_ids = [job["job_id"] for job in jobs]
        assert job1_result["job_id"] in job_ids
        assert job2_result["job_id"] in job_ids

    @patch("services.processing_jobs_service.get_jobs_table")
    def test_update_job_status_dynamodb_error(self, mock_get_table):
        """Test job status update with DynamoDB error"""
        mock_table = Mock()
        mock_table.update_item.side_effect = Exception("DynamoDB update error")
        mock_get_table.return_value = mock_table

        result = update_job_status("test_job_id", "processing")

        assert result["success"] is False
        assert "DynamoDB update error" in result["error"]

    def test_job_lifecycle_complete_flow(self):
        """Test complete job lifecycle from creation to completion"""
        # Create job
        create_result = create_processing_job(
            content_id=self.test_content_id, user_id=self.test_user_id, processing_options=self.test_processing_options
        )
        job_id = create_result["job_id"]

        # Start processing
        update_job_status(job_id, "processing", {"current_operation": "starting"})

        # Update progress multiple times
        update_job_progress(job_id, 25, "embedding_generation", {"tokens_processed": 250})
        complete_job_operation(job_id, "embedding_generation", {"embedding_dimensions": 1536})

        update_job_progress(job_id, 75, "categorization", {"categories_identified": 3})
        from decimal import Decimal

        complete_job_operation(job_id, "categorization", {"confidence_score": Decimal("0.95")})

        # Complete job
        final_metrics = {
            "tokens_processed": 1000,
            "images_processed": 0,
            "categories_identified": 3,
            "embedding_dimensions": 1536,
        }
        update_job_status(job_id, "completed", final_metrics)

        # Verify final state
        result = get_job_details(job_id)
        job = result["job"]

        assert job["status"] == "completed"
        assert "completed_at" in job
        assert job["processing_metrics"]["tokens_processed"] == 1000
        assert "embedding_generation" in job["processing_metrics"]["operations_completed"]
        assert "categorization" in job["processing_metrics"]["operations_completed"]

        # Verify lifecycle events
        events = job["lifecycle_events"]
        event_types = [e["event"] for e in events]
        assert "job_created" in event_types
        assert "status_changed_to_processing" in event_types
        assert "progress_update" in event_types
        assert "operation_completed" in event_types
        assert "status_changed_to_completed" in event_types


if __name__ == "__main__":
    pytest.main([__file__])
