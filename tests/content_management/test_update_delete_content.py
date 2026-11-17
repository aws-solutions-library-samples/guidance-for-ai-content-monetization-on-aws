"""
Unit tests for content update and delete functionality

Tests the handle_update_content and handle_delete_content functions
to ensure proper behavior with both real and mock data sources.
"""

import pytest
import json
import unittest.mock as mock
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from typing import Dict, Any
import uuid

# Set up environment variables before importing modules
import os

os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

# Import the modules to test
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from shared.content_delete import ContentDeleteEventError, ContentNotFound
from services.content_service import (
    handle_update_content,
    handle_delete_content,
    _update_real_content,
    _delete_real_content,
    standardize_iab_categories,
    create_success_response,
    create_error_response,
)


class TestHandleUpdateContent:
    """Test cases for handle_update_content function"""

    def setup_method(self):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        self.valid_update_data = {
            "title": "Updated Test Article",
            "summary": "Updated summary for the test article",
            "tags": ["updated", "test", "article"],
            "iab_categories": [111, 126],
            "custom_metadata": {"updated": True, "version": "2.0"},
        }

        # Real-data-only: handle_update_content delegates to the module-level
        # real helper _update_real_content. Patch it to drive handler behavior.
        self.update_real_patcher = patch("services.content_service._update_real_content")
        self.mock_update_real = self.update_real_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method"""
        self.update_real_patcher.stop()

    def test_successful_content_update_real_data(self):
        """Test successful content update with real DynamoDB"""
        content_id = "cnt_test_001"

        # Real source returns the updated content payload
        mock_updated_content = {
            "content_id": content_id,
            "title": "Updated Test Article",
            "summary": "Updated summary for the test article",
            "tags": ["updated", "test", "article"],
            "iab_categories": [
                {
                    "id": 111,
                    "name": "Medical Health",
                    "tier_1": "Medical Health",
                    "tier_2": None,
                    "tier_3": None,
                    "tier_4": None,
                    "parent_id": None,
                },
                {
                    "id": 126,
                    "name": "Technology & Computing",
                    "tier_1": "Technology & Computing",
                    "tier_2": None,
                    "tier_3": None,
                    "tier_4": None,
                    "parent_id": None,
                },
            ],
            "updated_at": "2025-01-15T10:05:00Z",
            "updated_by": self.user_info["user_id"],
        }

        self.mock_update_real.return_value = mock_updated_content

        # Test the function
        result = handle_update_content(self.user_info, content_id, self.valid_update_data)

        # Verify the result
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert response_body["content_id"] == content_id
        assert response_body["title"] == "Updated Test Article"
        assert response_body["summary"] == "Updated summary for the test article"
        assert response_body["tags"] == ["updated", "test", "article"]
        assert len(response_body["iab_categories"]) == 2
        assert response_body["updated_at"] == "2025-01-15T10:05:00Z"
        assert response_body["updated_by"] == self.user_info["user_id"]

    def test_content_update_not_found(self):
        """Test content update when content doesn't exist"""
        content_id = "cnt_nonexistent"

        # Real source raises a 'not found' error -> 404
        self.mock_update_real.side_effect = Exception(f"Content {content_id} not found")

        # Test the function
        result = handle_update_content(self.user_info, content_id, self.valid_update_data)

        # Verify 404 response
        assert result["statusCode"] == 404
        response_body = json.loads(result["body"])
        assert response_body["error"] == "not_found"
        assert f"Content {content_id} not found" in response_body["message"]

    def test_content_update_missing_content_id(self):
        """Test content update with missing content ID"""
        # Test with None content_id
        result = handle_update_content(self.user_info, None, self.valid_update_data)

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Content ID is required" in response_body["message"]

        # Test with empty string content_id
        result = handle_update_content(self.user_info, "", self.valid_update_data)

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Content ID is required" in response_body["message"]

    def test_content_update_with_reprocessing(self):
        """Test content update with reprocessing flag"""
        content_id = "cnt_reprocess_test"
        update_data_with_reprocess = self.valid_update_data.copy()
        update_data_with_reprocess["reprocess"] = True

        mock_updated_content = {
            "content_id": content_id,
            "title": "Updated Test Article",
            "updated_at": "2025-01-15T10:05:00Z",
            "updated_by": self.user_info["user_id"],
            "reprocessing_job_id": "job_abc123def456",
        }

        self.mock_update_real.return_value = mock_updated_content

        # Test the function
        result = handle_update_content(self.user_info, content_id, update_data_with_reprocess)

        # Verify the result includes reprocessing job ID
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert response_body["content_id"] == content_id
        assert response_body["reprocessing_job_id"] == "job_abc123def456"

    def test_content_update_general_error(self):
        """Test content update with general (non-'not found') error -> 500"""
        content_id = "cnt_error_test"

        # Real source raises a generic error -> 500
        self.mock_update_real.side_effect = Exception("Database connection failed")

        # Test the function
        result = handle_update_content(self.user_info, content_id, self.valid_update_data)

        # Verify 500 response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to update content" in response_body["message"]

    def test_content_update_exception_handling(self):
        """Test exception handling in handle_update_content"""
        content_id = "cnt_exception_test"

        # Any unexpected error from the real source -> 500
        self.mock_update_real.side_effect = Exception("Unexpected error")

        # Test the function
        result = handle_update_content(self.user_info, content_id, self.valid_update_data)

        # Verify 500 response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to update content" in response_body["message"]


class TestHandleDeleteContent:
    """Test cases for handle_delete_content function"""

    def setup_method(self):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        # Real-data-only: handle_delete_content delegates to the module-level
        # real helper _delete_real_content. Patch it to drive handler behavior.
        self.delete_real_patcher = patch("services.content_service._delete_real_content")
        self.mock_delete_real = self.delete_real_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method"""
        self.delete_real_patcher.stop()

    def test_successful_content_deletion_real_data(self):
        """An accepted delete answers 202 with the record's deleting status"""
        content_id = "cnt_test_001"

        self.mock_delete_real.return_value = {"content_id": content_id}

        result = handle_delete_content(self.user_info, content_id)

        # The deindexer completes the delete asynchronously, so the delete is only accepted here.
        assert result["statusCode"] == 202
        body = json.loads(result["body"])
        body.pop("_validation", None)  # added by the response validator outside production
        assert body == {"content_id": content_id, "status": "deleting"}
        self.mock_delete_real.assert_called_once_with(content_id, self.user_info)
        assert "Content-Type" in result["headers"]
        # TM-02 (security-hardening-phase2): the CORS origin is produced by the shared
        # resolver — a CONFIGURED origin, never a wildcard "*", and omitted (fail safe)
        # when no allow-list is resolvable (as in this unit env with no ALLOWED_ORIGINS/SSM).
        assert result["headers"].get("Access-Control-Allow-Origin") != "*"

    def test_content_deletion_not_found(self):
        """Test content deletion when content doesn't exist"""
        content_id = "cnt_nonexistent"

        # The soft delete's condition fails for an unknown record -> 404
        self.mock_delete_real.side_effect = ContentNotFound(content_id)

        # Test the function
        result = handle_delete_content(self.user_info, content_id)

        # Verify 404 response
        assert result["statusCode"] == 404
        response_body = json.loads(result["body"])
        assert response_body["error"] == "not_found"
        assert f"Content {content_id} not found" in response_body["message"]

    def test_content_deletion_missing_content_id(self):
        """Test content deletion with missing content ID"""
        # Test with None content_id
        result = handle_delete_content(self.user_info, None)

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Content ID is required" in response_body["message"]

        # Test with empty string content_id
        result = handle_delete_content(self.user_info, "")

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Content ID is required" in response_body["message"]

    def test_content_deletion_event_failure_is_500(self):
        """EventBridge refusing content.deleted is a 500; the record stays deleting for a retry"""
        content_id = "cnt_event_failure"

        self.mock_delete_real.side_effect = ContentDeleteEventError("content.deleted was not accepted")

        result = handle_delete_content(self.user_info, content_id)

        assert result["statusCode"] == 500
        assert "Failed to delete content" in json.loads(result["body"])["message"]

    def test_content_deletion_general_error(self):
        """Test content deletion with general (non-'not found') error -> 500"""
        content_id = "cnt_error_test"

        # Real source raises a generic error -> 500
        self.mock_delete_real.side_effect = Exception("Database connection failed")

        # Test the function
        result = handle_delete_content(self.user_info, content_id)

        # Verify 500 response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to delete content" in response_body["message"]

    def test_content_deletion_exception_handling(self):
        """Test exception handling in handle_delete_content"""
        content_id = "cnt_exception_test"

        # Any unexpected error from the real source -> 500
        self.mock_delete_real.side_effect = Exception("Unexpected error")

        # Test the function
        result = handle_delete_content(self.user_info, content_id)

        # Verify 500 response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to delete content" in response_body["message"]


class TestUpdateRealContent:
    """Test cases for _update_real_content function"""

    def setup_method(self):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "username": "test_user"}

        # Mock the content_table and jobs_table
        self.mock_content_table = MagicMock()
        self.mock_jobs_table = MagicMock()

        self.content_table_patcher = patch("services.content_service.content_table", self.mock_content_table)
        self.jobs_table_patcher = patch("services.content_service.jobs_table", self.mock_jobs_table)

        self.content_table_patcher.start()
        self.jobs_table_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method"""
        self.content_table_patcher.stop()
        self.jobs_table_patcher.stop()

    def test_successful_real_content_update(self):
        """Test successful content update in DynamoDB"""
        content_id = "cnt_real_001"
        update_data = {
            "title": "Updated Real Article",
            "summary": "Updated summary",
            "tags": ["updated", "real"],
            "iab_categories": [111, 126],
            "custom_metadata": {"updated": True},
        }

        # Mock DynamoDB responses
        self.mock_content_table.get_item.return_value = {"Item": {"content_id": content_id, "title": "Original Title"}}

        updated_item = {
            "content_id": content_id,
            "title": "Updated Real Article",
            "summary": "Updated summary",
            "tags": ["updated", "real"],
            "iab_categories": [111, 126],
            "custom_metadata": {"updated": True},
            "updated_at": "2025-01-15T10:05:00Z",
            "updated_by": self.user_info["user_id"],
        }

        self.mock_content_table.update_item.return_value = {"Attributes": updated_item}

        # Test the function
        result = _update_real_content(content_id, update_data, self.user_info)

        # Verify DynamoDB operations
        self.mock_content_table.get_item.assert_called_once_with(Key={"content_id": content_id})
        self.mock_content_table.update_item.assert_called_once()

        # Verify result
        assert result["content_id"] == content_id
        assert result["title"] == "Updated Real Article"
        assert result["summary"] == "Updated summary"
        assert result["tags"] == ["updated", "real"]
        assert result["updated_by"] == self.user_info["user_id"]

    def test_real_content_update_not_found(self):
        """Test updating content that doesn't exist in DynamoDB"""
        content_id = "cnt_not_found"
        update_data = {"title": "Updated Title"}

        # Mock DynamoDB response with no item
        self.mock_content_table.get_item.return_value = {}

        # Test the function - should raise exception
        with pytest.raises(Exception, match=f"Content {content_id} not found"):
            _update_real_content(content_id, update_data, self.user_info)

        # Verify DynamoDB was called
        self.mock_content_table.get_item.assert_called_once_with(Key={"content_id": content_id})
        self.mock_content_table.update_item.assert_not_called()

    def test_real_content_update_with_reprocessing_job(self):
        """Test content update with reprocessing job creation"""
        content_id = "cnt_reprocess"
        update_data = {"title": "Updated Title", "reprocess": True}

        # Mock DynamoDB responses
        self.mock_content_table.get_item.return_value = {"Item": {"content_id": content_id}}

        updated_item = {
            "content_id": content_id,
            "title": "Updated Title",
            "updated_at": "2025-01-15T10:05:00Z",
            "updated_by": self.user_info["user_id"],
        }

        self.mock_content_table.update_item.return_value = {"Attributes": updated_item}

        self.mock_jobs_table.put_item.return_value = {}

        # Test the function
        with patch("services.content_service.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "abcdef123456"

            result = _update_real_content(content_id, update_data, self.user_info)

        # Verify reprocessing job was created
        self.mock_jobs_table.put_item.assert_called_once()
        job_call_args = self.mock_jobs_table.put_item.call_args[1]["Item"]
        assert job_call_args["job_id"] == "job_abcdef123456"
        assert job_call_args["content_id"] == content_id
        assert job_call_args["job_type"] == "content_reprocessing"

        # Verify result includes reprocessing job ID
        assert "reprocessing_job_id" in result
        assert result["reprocessing_job_id"] == "job_abcdef123456"

    def test_real_content_update_iab_categories_standardization(self):
        """Test that IAB categories are properly standardized during update"""
        content_id = "cnt_iab_test"
        update_data = {"iab_categories": [111, {"id": 126, "name": "Technology & Computing"}]}

        # Mock DynamoDB responses
        self.mock_content_table.get_item.return_value = {"Item": {"content_id": content_id}}

        updated_item = {
            "content_id": content_id,
            "iab_categories": [111, 126],  # This will be standardized
            "updated_at": "2025-01-15T10:05:00Z",
            "updated_by": self.user_info["user_id"],
        }

        self.mock_content_table.update_item.return_value = {"Attributes": updated_item}

        # Test the function
        result = _update_real_content(content_id, update_data, self.user_info)

        # Verify IAB categories were standardized
        assert len(result["iab_categories"]) == 2
        for category in result["iab_categories"]:
            assert isinstance(category, dict)
            assert "id" in category
            assert "name" in category


class TestDeleteRealContent:
    """_delete_real_content is now a soft delete: mark the record deleting and emit the event.

    The search document, objects, jobs and the row itself are the deindexer's (see
    tests/services/test_content_deindexer.py), so none of them is touched here.
    """

    def setup_method(self):
        self.user_info = {"user_id": "test_user_001", "username": "test_user"}

        self.mock_content_table = MagicMock()
        self.mock_jobs_table = MagicMock()
        self.mock_s3 = MagicMock()
        self.mock_events = MagicMock()
        self.mock_events.put_events.return_value = {"FailedEntryCount": 0, "Entries": [{"EventId": "e1"}]}

        self.patchers = [
            patch("services.content_service.content_table", self.mock_content_table),
            patch("services.content_service.jobs_table", self.mock_jobs_table),
            patch("services.content_service.s3", self.mock_s3),
            patch("services.content_service.events", self.mock_events),
        ]
        for patcher in self.patchers:
            patcher.start()

    def teardown_method(self):
        for patcher in self.patchers:
            patcher.stop()

    def test_soft_delete_marks_deleting_and_emits_without_deleting_anything(self):
        content_id = "cnt_real_001"
        self.mock_content_table.update_item.return_value = {
            "Attributes": {
                "content_id": content_id,
                "status": "deleting",
                "md_s3_key": "content/example.com/a.md",
                "embedding_s3_key": "embeddings/cnt_real_001.json",
            }
        }

        detail = _delete_real_content(content_id, self.user_info)

        kwargs = self.mock_content_table.update_item.call_args.kwargs
        assert kwargs["Key"] == {"content_id": content_id}
        assert kwargs["ConditionExpression"] == "attribute_exists(content_id)"
        assert kwargs["ExpressionAttributeValues"][":deleting"] == "deleting"
        assert kwargs["ExpressionAttributeValues"][":deleted_by"] == "test_user_001"
        self.mock_events.put_events.assert_called_once()
        assert detail["content_id"] == content_id
        assert detail["md_s3_key"] == "content/example.com/a.md"
        assert detail["source_s3_key"] is None

        self.mock_content_table.delete_item.assert_not_called()
        self.mock_s3.delete_object.assert_not_called()
        self.mock_jobs_table.scan.assert_not_called()
        self.mock_jobs_table.delete_item.assert_not_called()

    def test_unknown_record_raises_content_not_found_and_emits_nothing(self):
        from botocore.exceptions import ClientError

        self.mock_content_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "failed"}}, "UpdateItem"
        )

        with pytest.raises(ContentNotFound, match="Content cnt_not_found not found"):
            _delete_real_content("cnt_not_found", self.user_info)

        self.mock_events.put_events.assert_not_called()

    def test_rejected_event_raises_so_the_handler_answers_500(self):
        self.mock_content_table.update_item.return_value = {"Attributes": {"content_id": "cnt_1"}}
        self.mock_events.put_events.return_value = {
            "FailedEntryCount": 1,
            "Entries": [{"ErrorCode": "InternalFailure", "ErrorMessage": "try again"}],
        }

        with pytest.raises(ContentDeleteEventError, match="InternalFailure"):
            _delete_real_content("cnt_1", self.user_info)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
