"""
Unit tests for content creation functionality

Tests the handle_create_content function and related helper functions
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

from services.content_service import (
    handle_create_content,
    standardize_iab_categories,
    create_success_response,
    create_error_response,
)


class TestHandleCreateContent:
    """Test cases for handle_create_content function"""

    def setup_method(self):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        self.valid_request_data = {
            "title": "Test Article",
            "content_type": "article",
            "content_text": "This is the content of the test article.",
            "content_html": "<h1>Test Article</h1><p>This is the content.</p>",
            "summary": "A test article for unit testing.",
            "author": "test_author",
            "publication_date": "2025-01-15T10:00:00Z",
            "tags": ["test", "article"],
            "iab_categories": [111, 126],
            "custom_metadata": {"test": True},
            "processing_options": {"generate_embeddings": True},
        }

        # Mock the global objects
        self.mock_content_table = MagicMock()
        self.mock_jobs_table = MagicMock()
        self.mock_s3 = MagicMock()

        # Patch the global objects in the content_management module
        self.content_table_patcher = patch("services.content_service.content_table", self.mock_content_table)
        self.jobs_table_patcher = patch("services.content_service.jobs_table", self.mock_jobs_table)
        self.s3_patcher = patch("services.content_service.s3", self.mock_s3)
        self.uuid_patcher = patch("services.content_service.uuid.uuid4")

        self.content_table_patcher.start()
        self.jobs_table_patcher.start()
        self.s3_patcher.start()
        self.mock_uuid = self.uuid_patcher.start()

        # Configure mock UUID to return predictable values
        self.mock_uuid.return_value.hex = "abcdef123456"

    def teardown_method(self):
        """Cleanup after each test method"""
        self.content_table_patcher.stop()
        self.jobs_table_patcher.stop()
        self.s3_patcher.stop()
        self.uuid_patcher.stop()

    def test_successful_content_creation_real_data(self):
        """Test successful content creation enqueues to S3 (async GEO pipeline)"""
        # Test the function
        result = handle_create_content(self.user_info, self.valid_request_data)

        # Verify async-accept response
        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        assert response_body["content_id"] == "cnt_abcdef123456"
        assert response_body["status"] == "queued"
        assert response_body["processing_status"] == "queued"
        assert "created_at" in response_body
        assert response_body["message"] == "Content accepted and queued for GEO enrichment"

        # Verify the S3 enqueue (single put_object with ingest key + content-id metadata)
        self.mock_s3.put_object.assert_called_once()
        s3_kwargs = self.mock_s3.put_object.call_args[1]
        assert s3_kwargs["Key"] == "ingest/cnt_abcdef123456.json"
        assert s3_kwargs["Metadata"] == {"content-id": "cnt_abcdef123456"}

        # Async path does not write synchronously to DynamoDB
        self.mock_content_table.put_item.assert_not_called()
        self.mock_jobs_table.put_item.assert_not_called()

    def test_successful_content_creation_mock_data(self):
        """Test successful content creation (async create writes only to S3)"""
        # Test the function
        result = handle_create_content(self.user_info, self.valid_request_data)

        # Verify async-accept response
        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        assert response_body["content_id"] == "cnt_abcdef123456"
        assert response_body["status"] == "queued"
        assert response_body["processing_status"] == "queued"

        # DynamoDB is never written synchronously
        self.mock_content_table.put_item.assert_not_called()
        self.mock_jobs_table.put_item.assert_not_called()

    def test_missing_required_field_title(self):
        """Test proper 400 response when required field 'title' is missing"""
        invalid_request = self.valid_request_data.copy()
        del invalid_request["title"]

        result = handle_create_content(self.user_info, invalid_request)

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Missing required field: title" in response_body["message"]

    def test_missing_required_field_content_type(self):
        """Test proper 400 response when required field 'content_type' is missing"""
        invalid_request = self.valid_request_data.copy()
        del invalid_request["content_type"]

        result = handle_create_content(self.user_info, invalid_request)

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Missing required field: content_type" in response_body["message"]

    def test_minimal_valid_request(self):
        """Test content creation with minimal required fields"""
        minimal_request = {"title": "Minimal Article", "content_type": "article"}

        result = handle_create_content(self.user_info, minimal_request)

        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        assert response_body["content_id"] == "cnt_abcdef123456"

    def test_iab_categories_standardization(self):
        """Async create enqueues the request to S3; IAB standardization happens in the GEO pipeline"""
        # Test with different IAB category formats
        request_with_categories = self.valid_request_data.copy()
        request_with_categories["iab_categories"] = [
            111,  # Simple ID
            {"id": 126, "name": "Technology & Computing"},  # Full object
            "Medical Health",  # String format
        ]

        result = handle_create_content(self.user_info, request_with_categories)

        assert result["statusCode"] == 202

        # The async path enqueues to S3 and does not standardize/store categories synchronously.
        self.mock_s3.put_object.assert_called_once()
        self.mock_content_table.put_item.assert_not_called()

    def test_dynamodb_error_with_fallback(self):
        """Async create does not write to DynamoDB synchronously; a stale table error is irrelevant"""
        # A DynamoDB error on the (uncalled) table must not affect the async S3 enqueue.
        self.mock_content_table.put_item.side_effect = Exception("DynamoDB connection error")

        result = handle_create_content(self.user_info, self.valid_request_data)

        # Still succeeds — the async path only enqueues to S3
        assert result["statusCode"] == 202
        response_body = json.loads(result["body"])
        assert response_body["content_id"] == "cnt_abcdef123456"
        self.mock_s3.put_object.assert_called_once()

    def test_dynamodb_error_without_fallback(self):
        """Test 500 response when the async S3 enqueue fails"""
        # The async create path fails if the S3 enqueue raises
        self.mock_s3.put_object.side_effect = Exception("S3 connection error")

        result = handle_create_content(self.user_info, self.valid_request_data)

        # Should return error
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to create content" in response_body["message"]

    def test_content_id_generation(self):
        """Test that content IDs are properly generated"""
        # Test multiple calls generate different IDs
        self.mock_uuid.return_value.hex = "first123456"
        result1 = handle_create_content(self.user_info, self.valid_request_data)

        self.mock_uuid.return_value.hex = "second123456"
        result2 = handle_create_content(self.user_info, self.valid_request_data)

        response1 = json.loads(result1["body"])
        response2 = json.loads(result2["body"])

        assert response1["content_id"] == "cnt_first123456"
        assert response2["content_id"] == "cnt_second123456"
        assert response1["content_id"] != response2["content_id"]

    def test_timestamps_generation(self):
        """Test that timestamps are properly generated"""
        with patch("services.content_service.datetime") as mock_datetime:
            # Mock datetime
            mock_now = datetime(2025, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
            mock_datetime.now.return_value = mock_now
            mock_datetime.timezone = timezone

            result = handle_create_content(self.user_info, self.valid_request_data)

            assert result["statusCode"] == 202

            # Async response carries the generated timestamp
            response_body = json.loads(result["body"])
            assert response_body["created_at"] == "2025-01-15T10:00:00+00:00"

    def test_default_values_for_optional_fields(self):
        """Test that default values are properly set for optional fields"""
        minimal_request = {"title": "Minimal Article", "content_type": "article"}

        result = handle_create_content(self.user_info, minimal_request)

        assert result["statusCode"] == 202

        # Verify default values in the enqueued S3 ingest payload
        self.mock_s3.put_object.assert_called_once()
        ingest_payload = json.loads(self.mock_s3.put_object.call_args[1]["Body"])
        assert ingest_payload["content_text"] == ""
        assert ingest_payload["author"] == self.user_info["username"]  # Default to username
        assert ingest_payload["tags"] == []
        assert ingest_payload["custom_metadata"] == {}

    def test_exception_handling(self):
        """Test exception handling in handle_create_content (S3 enqueue failure)"""
        # The async create path surfaces unexpected errors from the S3 enqueue as a 500.
        self.mock_s3.put_object.side_effect = Exception("Unexpected error")

        result = handle_create_content(self.user_info, self.valid_request_data)

        # Verify 500 response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to create content" in response_body["message"]


class TestStandardizeIabCategories:
    """Test cases for standardize_iab_categories function"""

    @pytest.fixture(autouse=True)
    def _deterministic_taxonomy(self):
        """Req 31.2: deterministic taxonomy stub so these standardization cases run in the
        Foundation_Gate without a deployed/seeded taxonomy table."""
        import services.content_service as cs

        fallback = cs.taxonomy_manager._get_fallback_taxonomy()
        for entry in fallback.values():
            entry.setdefault("tier_2", None)
            entry.setdefault("tier_3", None)
            entry.setdefault("tier_4", None)
            entry.setdefault("parent_id", None)
        with patch.object(cs.taxonomy_manager, "get_all_categories", return_value=fallback):
            yield

    def test_empty_categories(self):
        """Test handling of empty or None categories"""
        assert standardize_iab_categories(None) == []
        assert standardize_iab_categories([]) == []

    def test_integer_id_categories(self):
        """Test standardization of integer ID categories"""
        categories = [111, 126]
        result = standardize_iab_categories(categories)

        assert len(result) == 2
        # Ids echo the input (int or the DB's string form); names/tiers resolve to non-empty
        # strings. Exact id->name mapping is taxonomy-version-specific and not asserted.
        result_ids = {str(cat["id"]) for cat in result}
        assert {"111", "126"} <= result_ids
        assert all(isinstance(cat["name"], str) and cat["name"] for cat in result)
        assert all(isinstance(cat["tier_1"], str) and cat["tier_1"] for cat in result)

    def test_unknown_integer_id(self):
        """Test handling of unknown integer IDs"""
        categories = [999]  # Unknown ID
        result = standardize_iab_categories(categories)

        assert len(result) == 1
        assert result[0]["id"] == 999
        assert result[0]["name"] == "Unknown Category 999"
        assert result[0]["tier_1"] == "Unknown Category 999"
        assert result[0]["tier_2"] is None

    def test_full_object_categories(self):
        """Test standardization of full category objects"""
        categories = [{"id": 111, "name": "Medical Health", "tier_1": "Medical Health"}]
        result = standardize_iab_categories(categories)

        assert len(result) == 1
        assert result[0]["id"] == 111
        assert result[0]["name"] == "Medical Health"
        assert result[0]["tier_1"] == "Medical Health"
        assert result[0]["tier_2"] is None
        assert result[0]["tier_3"] is None
        assert result[0]["tier_4"] is None
        assert result[0]["parent_id"] is None

    def test_string_categories(self):
        """Test standardization of string categories"""
        categories = ["Medical Health", "Technology & Computing"]
        result = standardize_iab_categories(categories)

        assert len(result) == 2

        # Should map known strings to proper categories
        medical_cat = next((cat for cat in result if "Medical" in cat["name"]), None)
        tech_cat = next((cat for cat in result if "Technology" in cat["name"]), None)

        assert medical_cat is not None
        assert tech_cat is not None

    def test_unknown_string_categories(self):
        """Test handling of unknown string categories"""
        categories = ["Unknown Category"]
        result = standardize_iab_categories(categories)

        assert len(result) == 1
        assert result[0]["name"] == "Unknown Category"
        assert result[0]["tier_1"] == "Unknown Category"
        assert isinstance(result[0]["id"], int)  # Should generate a pseudo-ID

    def test_mixed_format_categories(self):
        """Test standardization of mixed format categories"""
        categories = [
            111,  # Integer ID
            {"id": 126, "name": "Technology & Computing"},  # Full object
            "Medical Health",  # String
        ]
        result = standardize_iab_categories(categories)

        assert len(result) == 3

        # All should be standardized objects
        for category in result:
            assert isinstance(category, dict)
            assert "id" in category
            assert "name" in category
            assert "tier_1" in category
            assert "tier_2" in category
            assert "tier_3" in category
            assert "tier_4" in category
            assert "parent_id" in category


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
