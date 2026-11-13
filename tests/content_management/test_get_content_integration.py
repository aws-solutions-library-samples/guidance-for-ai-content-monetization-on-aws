"""
Integration tests for content details functionality

Tests the complete integration of handle_get_content with feature flags,
mock data generation, and data source management. These tests validate the
end-to-end behavior including fallback mechanisms and data consistency.
"""

import pytest
import json
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from typing import Dict, Any

# Set up environment variables before importing modules
os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")
os.environ.setdefault("USE_REAL_CONTENT_DATA", "true")
os.environ.setdefault("ENABLE_GRACEFUL_FALLBACK", "true")

# Import the modules to test
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.content_service import handle_get_content, _get_real_content_details


class TestGetContentIntegration:
    """Integration tests for content details with real DynamoDB"""

    def setup_method(self, method):
        """Setup for each test method"""
        self.user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        # Mock the content_table for integration tests
        self.mock_content_table = MagicMock()
        self.content_table_patcher = patch("services.content_service.content_table", self.mock_content_table)
        self.content_table_patcher.start()

    def teardown_method(self, method):
        """Cleanup after each test method"""
        self.content_table_patcher.stop()

    def _create_mock_dynamodb_response(self, content_id: str, **kwargs) -> Dict[str, Any]:
        """Helper method to create mock DynamoDB response"""
        default_content = {
            "content_id": content_id,
            "title": f"Test Article {content_id}",
            "content_type": "article",
            "status": "completed",
            "content_text": f"This is the full content for {content_id}.",
            "content_html": f"<h1>Test Article {content_id}</h1><p>This is the full content.</p>",
            "summary": f"A test article for {content_id}.",
            "author": "integration_test_author",
            "publication_date": "2025-01-15T10:00:00Z",
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:05:00Z",
            "tags": ["integration", "test"],
            "iab_categories": [111, 126],
            "custom_metadata": {"test_type": "integration"},
            "processing_metrics": {"processing_time_ms": 2500, "tokens_processed": 1250, "images_processed": 0},
            "embedding_info": {
                "model": "titan-embed-text-v2",
                "dimensions": 1536,
                "created_at": "2025-01-15T10:02:00Z",
            },
            "images": [],
            "attachments": [],
        }

        # Override with any provided kwargs
        default_content.update(kwargs)

        return default_content

    def test_successful_real_data_retrieval(self):
        """Test successful content retrieval from real DynamoDB"""
        content_id = "cnt_integration_001"

        # Create mock DynamoDB response
        expected_content = self._create_mock_dynamodb_response(
            content_id,
            ai_analysis={
                "sentiment": {"overall_sentiment": "neutral", "confidence": 0.75},
                "entities": [{"text": "integration test", "type": "CONCEPT"}],
                "key_phrases": ["integration", "test", "DynamoDB"],
            },
        )

        # Mock DynamoDB get_item response
        self.mock_content_table.get_item.return_value = {"Item": expected_content}

        # Test the integration
        result = handle_get_content(self.user_info, content_id)

        # Verify successful response
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])

        # Verify all content fields are correctly retrieved
        assert response_body["content_id"] == content_id
        assert response_body["title"] == expected_content["title"]
        assert response_body["content_type"] == expected_content["content_type"]
        assert response_body["status"] == expected_content["status"]
        assert response_body["content_text"] == expected_content["content_text"]
        assert response_body["content_html"] == expected_content["content_html"]
        assert response_body["summary"] == expected_content["summary"]
        assert response_body["author"] == expected_content["author"]
        assert response_body["publication_date"] == expected_content["publication_date"]
        assert response_body["created_at"] == expected_content["created_at"]
        assert response_body["updated_at"] == expected_content["updated_at"]
        assert response_body["tags"] == expected_content["tags"]
        # iab_categories surface as standardized category dicts (list-of-dicts), not raw ints
        assert isinstance(response_body["iab_categories"], list)
        for _cat in response_body["iab_categories"]:
            assert isinstance(_cat, dict) and "id" in _cat and "name" in _cat and "tier_1" in _cat
        assert response_body["custom_metadata"] == expected_content["custom_metadata"]
        assert response_body["processing_metrics"] == expected_content["processing_metrics"]
        assert response_body["embedding_info"] == expected_content["embedding_info"]
        assert response_body["images"] == expected_content["images"]
        assert response_body["attachments"] == expected_content["attachments"]
        assert response_body["ai_analysis"] == expected_content["ai_analysis"]

    def test_content_not_found_with_fallback_disabled(self):
        """Test 404 response when content doesn't exist (real-data-only: no fallback)"""
        content_id = "cnt_nonexistent_integration"

        # Mock DynamoDB response with no item
        self.mock_content_table.get_item.return_value = {}

        # Test the integration
        result = handle_get_content(self.user_info, content_id)

        # Verify 404 response
        assert result["statusCode"] == 404
        response_body = json.loads(result["body"])
        assert response_body["error"] == "not_found"
        assert f"Content {content_id} not found" in response_body["message"]

    def test_minimal_content_data_integration(self):
        """Test retrieval of content with minimal data fields"""
        content_id = "cnt_minimal_integration"

        # Create content with only required fields
        minimal_content = {
            "content_id": content_id,
            "title": "Minimal Integration Test",
            "content_type": "article",
            "status": "draft",
        }

        # Mock DynamoDB response
        self.mock_content_table.get_item.return_value = {"Item": minimal_content}

        # Test the integration
        result = handle_get_content(self.user_info, content_id)

        # Verify successful response with defaults
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])

        # Verify required fields
        assert response_body["content_id"] == content_id
        assert response_body["title"] == "Minimal Integration Test"
        assert response_body["content_type"] == "article"
        assert response_body["status"] == "draft"

        # Verify default values for missing fields
        assert response_body["content_text"] == ""
        assert response_body["content_html"] == ""
        assert response_body["summary"] == ""
        assert response_body["author"] == ""
        assert response_body["tags"] == []
        assert response_body["iab_categories"] == []
        assert response_body["custom_metadata"] == {}
        assert response_body["processing_metrics"] == {}
        assert response_body["embedding_info"] == {}
        assert response_body["images"] == []
        assert response_body["attachments"] == []

    def test_source_failure_returns_500_integration(self):
        """Real-data-only: a DynamoDB failure surfaces as a 500 (no mock fallback)"""
        content_id = "cnt_failure_integration"

        # Mock DynamoDB failure
        self.mock_content_table.get_item.side_effect = Exception("DynamoDB unavailable")

        # Test the integration
        result = handle_get_content(self.user_info, content_id)

        # Source failure -> 500 error response
        assert result["statusCode"] == 500
        response_body = json.loads(result["body"])
        assert response_body["error"] == "internal_server_error"
        assert "Failed to get content details" in response_body["message"]

    def test_content_with_all_metadata_fields(self):
        """Test retrieval of content with all possible metadata fields"""
        content_id = "cnt_full_metadata_integration"

        # Create content with all possible fields
        full_content = self._create_mock_dynamodb_response(
            content_id,
            content_text="This is a comprehensive test article with all metadata fields populated.",
            content_html="<h1>Comprehensive Test</h1><p>This article has all fields.</p>",
            summary="A comprehensive test with all metadata fields.",
            author="comprehensive_test_author",
            tags=["comprehensive", "metadata", "test", "integration"],
            iab_categories=[111, 126, 52, 80],
            custom_metadata={"test_type": "comprehensive", "has_all_fields": True, "field_count": 15},
            processing_metrics={
                "processing_time_ms": 5000,
                "embedding_generation_time_ms": 1200,
                "categorization_time_ms": 500,
                "tokens_processed": 2500,
                "images_processed": 3,
            },
            embedding_info={
                "model": "titan-embed-text-v2",
                "dimensions": 1536,
                "created_at": "2025-01-15T10:02:00Z",
                "chunk_count": 5,
            },
            images=[
                {"url": "https://example.com/image1.jpg", "alt": "Test Image 1"},
                {"url": "https://example.com/image2.jpg", "alt": "Test Image 2"},
            ],
            attachments=[
                {"name": "document.pdf", "url": "https://example.com/doc.pdf", "size": 1024},
                {"name": "spreadsheet.xlsx", "url": "https://example.com/sheet.xlsx", "size": 2048},
            ],
            ai_analysis={
                "sentiment": {
                    "overall_sentiment": "positive",
                    "confidence": 0.85,
                    "positive_score": 0.6,
                    "negative_score": 0.1,
                    "neutral_score": 0.3,
                },
                "entities": [
                    {"text": "comprehensive test", "type": "CONCEPT", "confidence": 0.95},
                    {"text": "metadata", "type": "TECHNOLOGY", "confidence": 0.90},
                ],
                "key_phrases": ["comprehensive test", "metadata fields", "integration testing"],
                "language": "en",
                "readability_score": 8.5,
            },
        )

        # Mock DynamoDB response
        self.mock_content_table.get_item.return_value = {"Item": full_content}

        # Test the integration
        result = handle_get_content(self.user_info, content_id)

        # Verify successful response
        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])

        # Verify all metadata fields are present and correct
        assert response_body["content_id"] == content_id
        assert len(response_body["tags"]) == 4
        assert len(response_body["iab_categories"]) >= 1
        assert response_body["custom_metadata"]["has_all_fields"] is True
        assert response_body["processing_metrics"]["tokens_processed"] == 2500
        assert response_body["embedding_info"]["chunk_count"] == 5
        assert len(response_body["images"]) == 2
        assert len(response_body["attachments"]) == 2
        assert response_body["ai_analysis"]["sentiment"]["confidence"] == 0.85
        assert len(response_body["ai_analysis"]["entities"]) == 2
        assert len(response_body["ai_analysis"]["key_phrases"]) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
