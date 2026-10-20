"""
Unit tests for content listing functionality

Tests the handle_list_content function and related helper functions
to ensure proper behavior with both real and mock data sources.
"""

import pytest
import json
import unittest.mock as mock
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from typing import Dict, Any

# Set up environment variables before importing modules
import os

os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

# Import the modules to test
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.content_service import (
    handle_list_content,
    _get_real_content_list,
    create_success_response,
    create_error_response,
)


class TestHandleListContent:
    """Test cases for handle_list_content function (real-data-only contract)"""

    def setup_method(self):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        # Real-data-only: handle_list_content delegates to the module-level real
        # helper _get_real_content_list. Patch it to drive handler behavior.
        self.get_real_patcher = patch("services.content_service._get_real_content_list")
        self.mock_get_real = self.get_real_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method"""
        self.get_real_patcher.stop()

    def test_non_integer_limit_returns_400(self):
        """A non-integer 'limit' is a client error -> 400, not a 500 (wave0 Req 14.2/14.3)."""
        result = handle_list_content(self.user_info, {"limit": "not-an-int"})
        assert result["statusCode"] == 400
        # The real source must not be consulted for an invalid request.
        self.mock_get_real.assert_not_called()

    def test_successful_content_listing_no_filters(self):
        """Test successful content listing without filters"""
        # Real source returns the listing payload
        mock_content_data = {
            "items": [
                {"content_id": "cnt_001", "title": "Test Article", "content_type": "article", "status": "completed"}
            ],
            "pagination": {"items_per_page": 20, "has_next": False, "has_previous": False},
        }

        self.mock_get_real.return_value = mock_content_data

        # Test the function
        query_params = {"limit": "20"}
        result = handle_list_content(self.user_info, query_params)

        # Verify the result
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert "items" in response_body
        assert len(response_body["items"]) == 1
        assert response_body["items"][0]["content_id"] == "cnt_001"

    def test_content_listing_with_status_filter(self):
        """Test content listing with status filter"""
        mock_content_data = {
            "items": [{"content_id": "cnt_002", "title": "Completed Article", "status": "completed"}],
            "pagination": {"items_per_page": 20, "has_next": False, "has_previous": False},
        }

        self.mock_get_real.return_value = mock_content_data

        # Test with status filter
        query_params = {"status": "completed", "limit": "10"}
        result = handle_list_content(self.user_info, query_params)

        # Verify the real helper was called with correct parameters
        self.mock_get_real.assert_called_once()
        call_args = self.mock_get_real.call_args
        # signature: (status_filter, content_type_filter, limit, user_info, page_token)
        assert call_args[0][0] == "completed"
        assert call_args[0][2] == 10

        # Verify the result
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert len(response_body["items"]) == 1
        assert response_body["items"][0]["status"] == "completed"

    def test_content_listing_with_content_type_filter(self):
        """Test content listing with content_type filter"""
        mock_content_data = {
            "items": [{"content_id": "cnt_003", "title": "Video Content", "content_type": "video"}],
            "pagination": {"items_per_page": 20, "has_next": False, "has_previous": False},
        }

        self.mock_get_real.return_value = mock_content_data

        # Test with content_type filter
        query_params = {"content_type": "video"}
        result = handle_list_content(self.user_info, query_params)

        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert response_body["items"][0]["content_type"] == "video"
        # content_type filter passed as the second positional arg
        assert self.mock_get_real.call_args[0][1] == "video"

    def test_content_listing_with_pagination(self):
        """Test content listing with pagination token"""
        mock_content_data = {
            "items": [{"content_id": "cnt_004", "title": "Page 2 Content"}],
            "pagination": {
                "items_per_page": 5,
                "has_next": True,
                "has_previous": True,
                "next_page_token": "eyJjb250ZW50X2lkIjoiY250XzAwNCJ9",
            },
        }

        self.mock_get_real.return_value = mock_content_data

        # Test with pagination token
        query_params = {"limit": "5", "page_token": "eyJjb250ZW50X2lkIjoiY250XzAwMyJ9"}
        result = handle_list_content(self.user_info, query_params)

        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert response_body["pagination"]["has_next"] is True
        assert response_body["pagination"]["has_previous"] is True
        # page_token forwarded to the real helper
        assert self.mock_get_real.call_args[0][4] == "eyJjb250ZW50X2lkIjoiY250XzAwMyJ9"

    def test_content_listing_limit_validation(self):
        """Test that limit parameter is properly validated and capped"""
        mock_content_data = {
            "items": [],
            "pagination": {"items_per_page": 100, "has_next": False, "has_previous": False},
        }

        self.mock_get_real.return_value = mock_content_data

        # Test with limit exceeding maximum
        query_params = {"limit": "500"}  # Should be capped at 100
        result = handle_list_content(self.user_info, query_params)

        # Verify the limit was capped at 100 when forwarded to the real helper
        call_args = self.mock_get_real.call_args
        assert call_args[0][2] == 100
        assert result["statusCode"] == 200

    def test_data_source_failure(self):
        """Test handling of data source failure -> 500"""
        self.mock_get_real.side_effect = Exception("Database connection failed")

        query_params = {}
        result = handle_list_content(self.user_info, query_params)

        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert response_body["message"] == "Failed to list content"

    def test_exception_handling(self):
        """Test exception handling in handle_list_content"""
        # Any unexpected error from the real source -> 500
        self.mock_get_real.side_effect = Exception("Unexpected error")

        query_params = {}
        result = handle_list_content(self.user_info, query_params)

        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert response_body["message"] == "Failed to list content"


class TestGetRealContentList:
    """Test cases for _get_real_content_list function"""

    def setup_method(self):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "username": "test_user"}

        # Mock the content_table
        self.mock_content_table = MagicMock()
        self.content_table_patcher = patch("services.content_service.content_table", self.mock_content_table)
        self.content_table_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method"""
        self.content_table_patcher.stop()

    def test_scan_operation_no_filters(self):
        """Test scan operation when no filters are provided"""
        # Mock DynamoDB response
        mock_response = {
            "Items": [
                {
                    "content_id": "cnt_001",
                    "title": "Test Article",
                    "content_type": "article",
                    "markdown_schema_version": "1.0",
                    "status": "completed",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:00:00Z",
                    "iab_categories": [111, 126],
                    "tags": ["test", "article"],
                    "processing_metrics": {"processing_time_ms": 1500, "tokens_processed": 500, "images_processed": 0},
                }
            ]
        }

        self.mock_content_table.scan.return_value = mock_response

        result = _get_real_content_list(None, None, 20, self.user_info)

        # Verify scan was called with correct parameters
        self.mock_content_table.scan.assert_called_once()
        call_args = self.mock_content_table.scan.call_args[1]
        assert call_args["Limit"] == 20
        assert "FilterExpression" not in call_args

        # Verify result format
        assert "items" in result
        assert "pagination" in result
        assert len(result["items"]) == 1
        assert result["items"][0]["content_id"] == "cnt_001"
        assert result["items"][0]["markdown_schema_version"] == "1.0"
        assert result["items"][0]["edge_eligible"] is False
        for field in (
            "canonical_url",
            "url_hash",
            "edge_ineligible_reason",
            "md_s3_key",
        ):
            assert field not in result["items"][0]
        assert result["pagination"]["query_method"] == "scan"

    def test_scan_operation_with_status_filter(self):
        """Test scan operation with status filter (when GSI fails)"""
        mock_response = {
            "Items": [
                {
                    "content_id": "cnt_002",
                    "title": "Completed Article",
                    "status": "completed",
                    "content_type": "article",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:00:00Z",
                    "iab_categories": [],
                    "tags": [],
                }
            ]
        }

        # Mock GSI query to fail, forcing fallback to scan
        self.mock_content_table.query.side_effect = Exception("GSI not available")
        self.mock_content_table.scan.return_value = mock_response

        result = _get_real_content_list("completed", None, 10, self.user_info)

        # Verify both query and scan were called
        self.mock_content_table.query.assert_called_once()
        self.mock_content_table.scan.assert_called_once()

        # Verify scan was called with filter
        call_args = self.mock_content_table.scan.call_args[1]
        assert call_args["FilterExpression"] == "#status = :status"
        assert call_args["ExpressionAttributeNames"] == {"#status": "status"}
        assert call_args["ExpressionAttributeValues"] == {":status": "completed"}

        assert result["items"][0]["status"] == "completed"
        assert result["pagination"]["query_method"] == "scan"

    def test_scan_operation_with_content_type_filter(self):
        """Test scan operation with content_type filter (when GSI fails)"""
        mock_response = {
            "Items": [
                {
                    "content_id": "cnt_003",
                    "title": "Video Content",
                    "content_type": "video",
                    "status": "completed",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:00:00Z",
                    "iab_categories": [],
                    "tags": [],
                }
            ]
        }

        # Mock GSI query to fail, forcing fallback to scan
        self.mock_content_table.query.side_effect = Exception("GSI not available")
        self.mock_content_table.scan.return_value = mock_response

        result = _get_real_content_list(None, "video", 10, self.user_info)

        # Verify both query and scan were called
        self.mock_content_table.query.assert_called_once()
        self.mock_content_table.scan.assert_called_once()

        # Verify scan was called with filter
        call_args = self.mock_content_table.scan.call_args[1]
        assert call_args["FilterExpression"] == "content_type = :content_type"
        assert call_args["ExpressionAttributeValues"] == {":content_type": "video"}

        assert result["items"][0]["content_type"] == "video"
        assert result["pagination"]["query_method"] == "scan"

    def test_scan_operation_with_both_filters(self):
        """Test scan operation with both status and content_type filters"""
        mock_response = {"Items": []}
        self.mock_content_table.scan.return_value = mock_response

        result = _get_real_content_list("completed", "article", 10, self.user_info)

        # Verify scan was called with both filters
        call_args = self.mock_content_table.scan.call_args[1]
        assert "#status = :status AND content_type = :content_type" in call_args["FilterExpression"]
        assert call_args["ExpressionAttributeValues"] == {":status": "completed", ":content_type": "article"}

    def test_gsi_query_with_status_filter(self):
        """Test GSI query when only status filter is provided"""
        mock_response = {
            "Items": [
                {
                    "content_id": "cnt_004",
                    "title": "GSI Article",
                    "status": "processing",
                    "content_type": "article",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:00:00Z",
                    "iab_categories": [],
                    "tags": [],
                }
            ],
            "Count": 1,
        }

        self.mock_content_table.query.return_value = mock_response

        result = _get_real_content_list("processing", None, 10, self.user_info)

        # Verify query was called instead of scan
        self.mock_content_table.query.assert_called_once()
        call_args = self.mock_content_table.query.call_args[1]
        assert call_args["IndexName"] == "status-index"
        assert call_args["KeyConditionExpression"] == "#status = :status"

        assert result["pagination"]["query_method"] == "gsi_query"
        assert result["pagination"]["total_items"] == 1

    def test_gsi_fallback_to_scan(self):
        """Test fallback to scan when GSI query fails"""
        # Mock GSI query failure
        self.mock_content_table.query.side_effect = Exception("GSI not available")

        # Mock successful scan
        mock_scan_response = {
            "Items": [
                {
                    "content_id": "cnt_005",
                    "title": "Fallback Article",
                    "status": "completed",
                    "content_type": "article",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:00:00Z",
                    "iab_categories": [],
                    "tags": [],
                }
            ]
        }
        self.mock_content_table.scan.return_value = mock_scan_response

        result = _get_real_content_list("completed", None, 10, self.user_info)

        # Verify both query and scan were called
        self.mock_content_table.query.assert_called_once()
        self.mock_content_table.scan.assert_called_once()

        assert result["pagination"]["query_method"] == "scan"

    def test_pagination_with_last_evaluated_key(self):
        """Test pagination using LastEvaluatedKey"""
        mock_response = {
            "Items": [
                {
                    "content_id": "cnt_006",
                    "title": "Paginated Content",
                    "status": "completed",
                    "content_type": "article",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:00:00Z",
                    "iab_categories": [],
                    "tags": [],
                }
            ],
            "LastEvaluatedKey": {"content_id": "cnt_006"},
        }

        self.mock_content_table.scan.return_value = mock_response

        # Test with pagination token
        page_token = '{"content_id": "cnt_005"}'
        result = _get_real_content_list(None, None, 10, self.user_info, page_token)

        # Verify ExclusiveStartKey was set
        call_args = self.mock_content_table.scan.call_args[1]
        assert "ExclusiveStartKey" in call_args
        assert call_args["ExclusiveStartKey"] == {"content_id": "cnt_005"}

        # Verify pagination info
        assert result["pagination"]["has_next"] is True
        assert result["pagination"]["has_previous"] is True
        assert "next_page_token" in result["pagination"]

    def test_invalid_pagination_token(self):
        """Test handling of invalid pagination token"""
        mock_response = {"Items": []}
        self.mock_content_table.scan.return_value = mock_response

        # Test with invalid JSON token
        invalid_token = "invalid_json"
        result = _get_real_content_list(None, None, 10, self.user_info, invalid_token)

        # Should not crash and should not include ExclusiveStartKey
        call_args = self.mock_content_table.scan.call_args[1]
        assert "ExclusiveStartKey" not in call_args

    def test_item_formatting(self):
        """Test proper formatting of DynamoDB items to API format"""
        mock_response = {
            "Items": [
                {
                    "content_id": "cnt_007",
                    "title": "Full Featured Article",
                    "content_type": "article",
                    "status": "completed",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:00:00Z",
                    "iab_categories": [111, 126],
                    "tags": ["test", "article"],
                    "summary": "Test summary",
                    "file_size": 1024,
                    "access_count": 42,
                    "revenue_generated": 15.50,
                    "processing_metrics": {"processing_time_ms": 2000, "tokens_processed": 800, "images_processed": 2},
                }
            ]
        }

        self.mock_content_table.scan.return_value = mock_response

        result = _get_real_content_list(None, None, 10, self.user_info)

        item = result["items"][0]

        # Verify all expected fields are present and properly formatted
        assert item["content_id"] == "cnt_007"
        assert item["title"] == "Full Featured Article"
        assert item["content_type"] == "article"
        assert item["status"] == "completed"
        assert item["author"] == "test_author"
        assert item["summary"] == "Test summary"
        assert item["file_size"] == 1024
        assert item["access_count"] == 42
        assert item["revenue_generated"] == 15.50
        assert item["processing_metrics"]["processing_time_ms"] == 2000

    def test_missing_optional_fields(self):
        """Test handling of items with missing optional fields"""
        mock_response = {
            "Items": [
                {
                    "content_id": "cnt_008",
                    "title": "Minimal Article",
                    "content_type": "article",
                    "status": "pending",
                    # Missing many optional fields
                }
            ]
        }

        self.mock_content_table.scan.return_value = mock_response

        result = _get_real_content_list(None, None, 10, self.user_info)

        item = result["items"][0]

        # Verify default values are provided for missing fields
        assert item["author"] == ""
        assert item["markdown_schema_version"] == "legacy"
        assert item["summary"] == ""
        assert item["file_size"] == 0
        assert item["access_count"] == 0
        assert item["revenue_generated"] == 0.0
        assert item["iab_categories"] == []
        assert item["tags"] == []
        assert item["processing_metrics"]["processing_time_ms"] == 0

    def test_dynamodb_exception_handling(self):
        """Test handling of DynamoDB exceptions"""
        self.mock_content_table.scan.side_effect = Exception("DynamoDB error")

        with pytest.raises(Exception, match="DynamoDB error"):
            _get_real_content_list(None, None, 10, self.user_info)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
