#!/usr/bin/env python3

"""
Integration tests for processing status Lambda function.

Tests the processing status functionality with real DynamoDB integration
as part of task 2.2 - Replace mock processing status with real job data.
"""

import pytest
import json
import os
import boto3
from moto import mock_aws
from datetime import datetime, timedelta

# Import the module under test
import sys

sys.path.append("source/lambda")
from services.processing_jobs_service import (
    get_real_processing_status,
    calculate_performance_metrics,
)


class TestProcessingStatusIntegration:
    """Integration tests for processing status with real DynamoDB."""

    @mock_aws
    def test_processing_metrics_integration(self):
        """Test processing metrics calculation with real data."""
        # Create test data for the function
        completed_jobs = [
            {
                "created_at": datetime.utcnow().isoformat() + "Z",
                "completed_at": (datetime.utcnow() + timedelta(seconds=30)).isoformat() + "Z",
            }
        ]
        failed_jobs = [{"created_at": datetime.utcnow().isoformat() + "Z"}]

        metrics = calculate_performance_metrics(completed_jobs, failed_jobs)

        # Should have basic metrics structure
        assert "last_24_hours" in metrics
        assert "last_7_days" in metrics
        assert "successful_jobs" in metrics["last_24_hours"]
        assert "failed_jobs" in metrics["last_24_hours"]

    @mock_aws
    def test_real_processing_status_with_mock_table(self):
        """Test real processing status function with mocked DynamoDB."""
        # Set up environment
        os.environ["JOBS_TABLE_NAME"] = "test-jobs-table"

        # Create DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        table = dynamodb.create_table(
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
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add some test data
        current_time = datetime.utcnow()
        test_jobs = [
            {
                "job_id": "job_001",
                "content_id": "content_001",
                "status": "completed",
                "created_at": (current_time - timedelta(hours=2)).isoformat() + "Z",
                "updated_at": (current_time - timedelta(hours=1, minutes=50)).isoformat() + "Z",
                "content_type": "article",
                "partner_id": "partner_001",
            },
            {
                "job_id": "job_002",
                "content_id": "content_002",
                "status": "processing",
                "created_at": (current_time - timedelta(minutes=30)).isoformat() + "Z",
                "updated_at": (current_time - timedelta(minutes=25)).isoformat() + "Z",
                "content_type": "article",
                "partner_id": "partner_003",
            },
        ]

        for job in test_jobs:
            table.put_item(Item=job)

        # Patch the jobs table in the module
        import services.processing_jobs_service as processing_jobs_service

        processing_jobs_service.jobs_table = table

        # Test the function
        status = get_real_processing_status()

        # Verify status structure
        assert "active_jobs" in status
        assert "queued_jobs" in status
        assert "completed_today" in status
        assert "failed_today" in status
        assert "system_status" in status


if __name__ == "__main__":
    pytest.main([__file__])
