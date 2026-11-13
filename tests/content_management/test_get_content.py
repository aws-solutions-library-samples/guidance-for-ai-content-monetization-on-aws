"""
Unit tests for content details functionality

Tests the handle_get_content function and related helper functions
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
    handle_get_content,
    _get_real_content_details,
    create_success_response,
    create_error_response,
)


class TestHandleGetContent:
    """Test cases for handle_get_content function (real-data-only contract)"""

    def setup_method(self):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        # Real-data-only: handle_get_content delegates to the module-level real
        # helper _get_real_content_details. Patch it to drive handler behavior.
        self.get_real_patcher = patch("services.content_service._get_real_content_details")
        self.mock_get_real = self.get_real_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method"""
        self.get_real_patcher.stop()

    def test_successful_content_retrieval(self):
        """Test successful content retrieval with real data"""
        content_id = "cnt_test_001"

        # Real source returns the content payload
        mock_content_data = {
            "content_id": content_id,
            "title": "Test Article",
            "content_type": "article",
            "markdown_schema_version": "1.0",
            "canonical_url": "https://news.example.com/article",
            "url_hash": "a" * 64,
            "edge_eligible": True,
            "md_s3_key": "content/news.example.com/article.md",
            "status": "completed",
            "content_text": "This is the full content of the test article.",
            "content_html": "<h1>Test Article</h1><p>This is the full content.</p>",
            "summary": "A test article for unit testing.",
            "author": "test_author",
            "publication_date": "2025-01-15T10:00:00Z",
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:05:00Z",
            "tags": ["test", "article"],
            "iab_categories": [111, 126],
            "custom_metadata": {"test": True},
            "processing_metrics": {"processing_time_ms": 2500, "tokens_processed": 1250, "images_processed": 0},
            "embedding_info": {"model": "titan-embed-text-v2", "dimensions": 1536},
            "images": [],
            "attachments": [],
        }

        self.mock_get_real.return_value = mock_content_data

        # Test the function
        result = handle_get_content(self.user_info, content_id)

        # Verify the result
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert response_body["content_id"] == content_id
        assert response_body["title"] == "Test Article"
        assert response_body["content_type"] == "article"
        assert response_body["status"] == "completed"
        assert response_body["content_text"] == "This is the full content of the test article."
        assert response_body["content_html"] == "<h1>Test Article</h1><p>This is the full content.</p>"
        assert response_body["summary"] == "A test article for unit testing."
        assert response_body["author"] == "test_author"
        assert response_body["tags"] == ["test", "article"]
        assert response_body["iab_categories"] == [111, 126]
        assert response_body["custom_metadata"] == {"test": True}
        assert "processing_metrics" in response_body
        assert "embedding_info" in response_body
        self.mock_get_real.assert_called_once_with(content_id)

    def test_content_not_found_404_response(self):
        """Test proper 404 response when content is not found"""
        content_id = "cnt_nonexistent"

        # Real source raises a 'not found' error -> 404
        self.mock_get_real.side_effect = Exception(f"Content {content_id} not found")

        # Test the function
        result = handle_get_content(self.user_info, content_id)

        # Verify 404 response
        assert result["statusCode"] == 404
        response_body = json.loads(result["body"])
        assert response_body["error"] == "not_found"
        assert f"Content {content_id} not found" in response_body["message"]

    def test_missing_content_id_400_response(self):
        """Test proper 400 response when content_id is missing"""
        # Test with None content_id
        result = handle_get_content(self.user_info, None)

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Content ID is required" in response_body["message"]

        # Test with empty string content_id
        result = handle_get_content(self.user_info, "")

        assert result["statusCode"] == 400
        response_body = json.loads(result["body"])
        assert response_body["error"] == "bad_request"
        assert "Content ID is required" in response_body["message"]

    def test_general_error_500_response(self):
        """Test proper 500 response for general (non-'not found') source errors"""
        content_id = "cnt_error_test"

        # Real source raises a generic error -> 500
        self.mock_get_real.side_effect = Exception("Database connection failed")

        # Test the function
        result = handle_get_content(self.user_info, content_id)

        # Verify 500 response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to get content details" in response_body["message"]

    def test_exception_handling(self):
        """Test exception handling in handle_get_content"""
        content_id = "cnt_exception_test"

        # Any unexpected error from the real source -> 500
        self.mock_get_real.side_effect = Exception("Unexpected error")

        # Test the function
        result = handle_get_content(self.user_info, content_id)

        # Verify 500 response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to get content details" in response_body["message"]


class TestGetRealContentDetails:
    """Test cases for _get_real_content_details function"""

    def setup_method(self):
        """Setup for each test method"""
        # Mock the content_table
        self.mock_content_table = MagicMock()
        self.content_table_patcher = patch("services.content_service.content_table", self.mock_content_table)
        self.content_table_patcher.start()

    def teardown_method(self):
        """Cleanup after each test method"""
        self.content_table_patcher.stop()

    def test_successful_content_retrieval_from_dynamodb(self):
        """Test successful content retrieval from DynamoDB"""
        content_id = "cnt_real_001"

        # Mock DynamoDB response with full content data
        mock_dynamodb_item = {
            "content_id": content_id,
            "title": "Real DynamoDB Article",
            "content_type": "article",
            "markdown_schema_version": "1.0",
            "canonical_url": "https://news.example.com/article",
            "url_hash": "a" * 64,
            "edge_eligible": True,
            "md_s3_key": "content/news.example.com/article.md",
            "status": "completed",
            "content_text": "This is real content from DynamoDB.",
            "content_html": "<h1>Real Article</h1><p>This is real content.</p>",
            "summary": "A real article from DynamoDB.",
            "author": "real_author",
            "publication_date": "2025-01-15T10:00:00Z",
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:05:00Z",
            "tags": ["real", "dynamodb"],
            "iab_categories": [111, 126],
            "custom_metadata": {"source": "DynamoDB"},
            "processing_metrics": {"processing_time_ms": 3000, "tokens_processed": 1500, "images_processed": 1},
            "embedding_info": {
                "model": "titan-embed-text-v2",
                "dimensions": 1536,
                "created_at": "2025-01-15T10:02:00Z",
            },
            "images": [{"url": "https://example.com/image.jpg"}],
            "attachments": [{"name": "document.pdf", "url": "https://example.com/doc.pdf"}],
            "ai_analysis": {
                "sentiment": {"overall_sentiment": "positive"},
                "entities": [{"text": "DynamoDB", "type": "TECHNOLOGY"}],
            },
        }

        self.mock_content_table.get_item.return_value = {"Item": mock_dynamodb_item}

        # The record names md_s3_key, so content_text is the stored Markdown object, which takes
        # precedence over the record's own content_text.
        stored_markdown = "# Real DynamoDB Article\n\nThis is the stored Markdown.\n"
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: stored_markdown.encode("utf-8"))}

        # Test the function
        with patch("services.content_service.s3", mock_s3):
            result = _get_real_content_details(content_id)
        mock_s3.get_object.assert_called_once_with(Bucket=mock.ANY, Key="content/news.example.com/article.md")

        # Verify DynamoDB was called correctly
        self.mock_content_table.get_item.assert_called_once_with(Key={"content_id": content_id})

        # Verify all fields are properly mapped
        assert result["content_id"] == content_id
        assert result["title"] == "Real DynamoDB Article"
        assert result["content_type"] == "article"
        assert result["markdown_schema_version"] == "1.0"
        assert result["canonical_url"] == "https://news.example.com/article"
        assert result["url_hash"] == "a" * 64
        assert result["edge_eligible"] is True
        assert result["md_s3_key"] == "content/news.example.com/article.md"
        assert result["status"] == "completed"
        assert result["content_text"] == stored_markdown
        assert result["content_html"] == "<h1>Real Article</h1><p>This is real content.</p>"
        assert result["summary"] == "A real article from DynamoDB."
        assert result["author"] == "real_author"
        assert result["publication_date"] == "2025-01-15T10:00:00Z"
        assert result["created_at"] == "2025-01-15T10:00:00Z"
        assert result["updated_at"] == "2025-01-15T10:05:00Z"
        assert result["tags"] == ["real", "dynamodb"]
        # iab_categories are wrapped through standardize_iab_categories -> list of category dicts
        assert isinstance(result["iab_categories"], list)
        for _cat in result["iab_categories"]:
            assert isinstance(_cat, dict)
            assert "id" in _cat and "name" in _cat and "tier_1" in _cat
        assert result["custom_metadata"] == {"source": "DynamoDB"}
        assert result["processing_metrics"]["processing_time_ms"] == 3000
        assert result["embedding_info"]["model"] == "titan-embed-text-v2"
        assert result["images"] == [{"url": "https://example.com/image.jpg"}]
        assert result["attachments"] == [{"name": "document.pdf", "url": "https://example.com/doc.pdf"}]
        assert result["ai_analysis"]["sentiment"]["overall_sentiment"] == "positive"

    def test_content_not_found_in_dynamodb(self):
        """Test handling when content is not found in DynamoDB"""
        content_id = "cnt_not_found"

        # Mock DynamoDB response with no item
        self.mock_content_table.get_item.return_value = {}

        # Test the function - should raise exception
        with pytest.raises(Exception, match=f"Content {content_id} not found"):
            _get_real_content_details(content_id)

        # Verify DynamoDB was called
        self.mock_content_table.get_item.assert_called_once_with(Key={"content_id": content_id})

    def test_minimal_content_data_with_defaults(self):
        """Test handling of content with minimal data (missing optional fields)"""
        content_id = "cnt_minimal"

        # Mock DynamoDB response with minimal data
        mock_dynamodb_item = {
            "content_id": content_id,
            "title": "Minimal Article",
            "content_type": "article",
            "status": "pending",
            # Missing many optional fields
        }

        self.mock_content_table.get_item.return_value = {"Item": mock_dynamodb_item}

        # Test the function
        result = _get_real_content_details(content_id)

        # Verify required fields are present
        assert result["content_id"] == content_id
        assert result["title"] == "Minimal Article"
        assert result["content_type"] == "article"
        assert result["status"] == "pending"
        assert result["markdown_schema_version"] == "legacy"
        assert result["edge_eligible"] is False
        for field in (
            "canonical_url",
            "url_hash",
            "edge_ineligible_reason",
            "md_s3_key",
        ):
            assert field not in result

        # Verify default values for missing fields
        assert result["content_text"] == ""
        assert result["content_html"] == ""
        assert result["summary"] == ""
        assert result["author"] == ""
        assert result["publication_date"] == ""
        assert result["created_at"] == ""
        assert result["updated_at"] == ""
        assert result["tags"] == []
        assert result["iab_categories"] == []
        assert result["custom_metadata"] == {}
        assert result["processing_metrics"] == {}
        assert result["embedding_info"] == {}
        assert result["images"] == []
        assert result["attachments"] == []
        assert "ai_analysis" not in result  # Should not be present if not in DynamoDB

    def test_dynamodb_exception_handling(self):
        """Test handling of DynamoDB exceptions"""
        content_id = "cnt_error"

        # Mock DynamoDB exception
        self.mock_content_table.get_item.side_effect = Exception("DynamoDB connection error")

        # Test the function - should raise exception
        with pytest.raises(Exception, match="DynamoDB connection error"):
            _get_real_content_details(content_id)

    def test_content_with_unknown_status(self):
        """Test handling of content with missing status field"""
        content_id = "cnt_no_status"

        # Mock DynamoDB response without status
        mock_dynamodb_item = {
            "content_id": content_id,
            "title": "No Status Article",
            "content_type": "article",
            # Missing status field
        }

        self.mock_content_table.get_item.return_value = {"Item": mock_dynamodb_item}

        # Test the function
        result = _get_real_content_details(content_id)

        # Verify default status is applied
        assert result["status"] == "unknown"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
