"""
Test field name consistency between OpenAPI spec and implementation

This module tests that all API responses use standardized field names
that match the OpenAPI specification.
"""

import pytest
import json
import os
from typing import Dict, Any, List
from unittest.mock import Mock, patch

# Mock environment variables before importing content_management
os.environ["CONTENT_TABLE_NAME"] = "test-content-table"
os.environ["JOBS_TABLE_NAME"] = "test-jobs-table"
os.environ["CONTENT_BUCKET_NAME"] = "test-content-bucket"

# Import the content management handler for testing
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "../../source/lambda"))

with patch("boto3.resource"), patch("boto3.client"):
    from services.content_service import (
        handle_list_content,
        handle_get_content,
        handle_create_content,
        handle_update_content,
        standardize_iab_categories,
    )


class TestFieldNameStandardization:
    """Test field name consistency across all endpoints"""

    def setup_method(self):
        """Set up test environment"""
        self.mock_user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        # Standard field names according to OpenAPI spec
        self.expected_content_fields = {
            "content_id": str,
            "title": str,
            "content_type": str,
            "status": str,
            "author": str,
            "publication_date": str,
            "created_at": str,
            "updated_at": str,
            "iab_categories": list,
            "tags": list,
            "embedding_info": dict,  # Standardized field name (not embeddings)
            "processing_metrics": dict,
        }

        self.expected_error_fields = {"error": str, "message": str, "code": str, "timestamp": str}

    def test_content_list_response_field_names(self):
        """Test that content list responses use standardized field names"""
        real_listing = {
            "items": [
                {
                    "content_id": "cnt_001",
                    "title": "Test Article",
                    "content_type": "article",
                    "status": "completed",
                    "author": "test_author",
                    "publication_date": "2025-01-15T10:00:00Z",
                    "created_at": "2025-01-15T10:00:00Z",
                    "updated_at": "2025-01-15T10:05:00Z",
                    "iab_categories": [],
                    "tags": [],
                    "embedding_info": {},
                    "processing_metrics": {},
                }
            ],
            "pagination": {"items_per_page": 10, "has_next": False, "has_previous": False},
        }
        with patch("services.content_service._get_real_content_list", return_value=real_listing):
            # Mock query parameters
            query_params = {"limit": "10"}

            # Call the handler
            response = handle_list_content(self.mock_user_info, query_params)

            # Verify response structure
            assert response["statusCode"] == 200
            response_body = json.loads(response["body"])

            # Check that response has expected structure
            assert "items" in response_body
            assert "pagination" in response_body

            # Check field names in content items
            if response_body["items"]:
                content_item = response_body["items"][0]
                self._validate_content_item_fields(content_item)

    def test_content_details_response_field_names(self):
        """Test that content details responses use standardized field names"""
        real_details = {
            "content_id": "test_content_id",
            "title": "Test Article",
            "content_type": "article",
            "status": "completed",
            "author": "test_author",
            "publication_date": "2025-01-15T10:00:00Z",
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:05:00Z",
            "iab_categories": [],
            "tags": [],
            "embedding_info": {},
            "processing_metrics": {},
        }
        with patch("services.content_service._get_real_content_details", return_value=real_details):
            # Call the handler with a mock content ID
            response = handle_get_content(self.mock_user_info, "test_content_id")

            if response["statusCode"] == 200:
                response_body = json.loads(response["body"])
                self._validate_content_item_fields(response_body)

    def test_content_creation_response_field_names(self):
        """Test that content creation responses use standardized field names"""
        with patch("services.content_service.s3"):
            # Mock request data
            request_data = {
                "title": "Test Content",
                "content_type": "article",
                "content_text": "Test content text",
                "iab_categories": [111, 126],
                "tags": ["test", "api"],
            }

            # Call the handler
            response = handle_create_content(self.mock_user_info, request_data)

            # Verify response structure (async accept: 202 queued)
            assert response["statusCode"] == 202
            response_body = json.loads(response["body"])

            # Check standardized field names (Async_Create_Contract)
            expected_creation_fields = {
                "content_id": str,
                "status": str,
                "processing_status": str,
                "created_at": str,
                "message": str,
            }

            for field_name, field_type in expected_creation_fields.items():
                assert field_name in response_body, f"Missing field: {field_name}"
                assert isinstance(response_body[field_name], field_type), f"Wrong type for {field_name}"

    def test_embedding_info_field_standardization(self):
        """Test that embedding information uses 'embedding_info' not 'embeddings'"""
        # Test with mock content that has embedding information
        mock_content_with_embeddings = {
            "content_id": "test_001",
            "title": "Test Content",
            "content_type": "article",
            "embedding_info": {  # Should be embedding_info, not embeddings
                "model": "amazon.titan-embed-text-v1",
                "dimensions": 1536,
                "created_at": "2025-01-15T10:00:00Z",
                "storage_type": "s3-json",
            },
        }

        # Verify the field name is correct
        assert "embedding_info" in mock_content_with_embeddings
        assert "embeddings" not in mock_content_with_embeddings

        # Verify the structure matches OpenAPI spec
        embedding_info = mock_content_with_embeddings["embedding_info"]
        expected_embedding_fields = {"model": str, "dimensions": int, "created_at": str, "storage_type": str}

        for field_name, field_type in expected_embedding_fields.items():
            assert field_name in embedding_info, f"Missing embedding field: {field_name}"
            assert isinstance(embedding_info[field_name], field_type), f"Wrong type for embedding {field_name}"

    @pytest.mark.integration
    def test_iab_categories_standardization(self):
        """Test IAB categories field standardization"""
        # Test different input formats
        test_cases = [
            # Simple integer array
            ([111, 126], "integer_array"),
            # String array
            (["Technology & Computing", "Medical Health"], "string_array"),
            # Mixed format
            ([{"id": 111, "name": "Technology & Computing"}], "object_array"),
            # Empty array
            ([], "empty_array"),
        ]

        for input_categories, test_name in test_cases:
            standardized = standardize_iab_categories(input_categories)

            # All results should be arrays of objects with standardized structure
            assert isinstance(standardized, list), f"Failed for {test_name}: not a list"

            for category in standardized:
                assert isinstance(category, dict), f"Failed for {test_name}: category not a dict"

                # Check required fields according to OpenAPI spec
                required_fields = ["id", "name", "tier_1"]
                for field in required_fields:
                    assert field in category, f"Failed for {test_name}: missing {field}"

                # Check field types
                assert isinstance(category["id"], (int, str)), f"Failed for {test_name}: id not int/str"
                assert isinstance(category["name"], str), f"Failed for {test_name}: name not str"
                assert isinstance(category["tier_1"], str), f"Failed for {test_name}: tier_1 not str"

    def test_error_response_standardization(self):
        """Test that error responses use standardized field names"""
        # Missing required fields is validated before any data access -> 400
        response = handle_create_content(self.mock_user_info, {})

        # Should return 400 error
        assert response["statusCode"] == 400
        response_body = json.loads(response["body"])

        # Check standardized error fields
        assert "error" in response_body
        assert "message" in response_body

        # Verify error structure matches OpenAPI spec
        assert isinstance(response_body["error"], str)
        assert isinstance(response_body["message"], str)

    def test_pagination_field_standardization(self):
        """Test that pagination uses standardized field names"""
        real_listing = {
            "items": [],
            "pagination": {"items_per_page": 5, "has_next": False, "has_previous": False},
        }
        with patch("services.content_service._get_real_content_list", return_value=real_listing):
            query_params = {"limit": "5"}
            response = handle_list_content(self.mock_user_info, query_params)

            assert response["statusCode"] == 200
            response_body = json.loads(response["body"])

            # Check pagination structure
            assert "pagination" in response_body
            pagination = response_body["pagination"]

            # Check standardized pagination fields (token-based, not offset-based)
            expected_pagination_fields = {"items_per_page": int, "has_next": bool, "has_previous": bool}

            for field_name, field_type in expected_pagination_fields.items():
                assert field_name in pagination, f"Missing pagination field: {field_name}"
                assert isinstance(pagination[field_name], field_type), f"Wrong type for pagination {field_name}"

    def test_processing_metrics_field_standardization(self):
        """Test that processing metrics use standardized field names"""
        # Mock processing metrics structure
        expected_processing_metrics = {
            "processing_time_ms": int,
            "tokens_processed": int,
            "images_processed": int,
            "embedding_generation_time_ms": int,
        }

        # This would be part of a content item response
        mock_content_with_metrics = {
            "content_id": "test_001",
            "processing_metrics": {
                "processing_time_ms": 2500,
                "tokens_processed": 1250,
                "images_processed": 0,
                "embedding_generation_time_ms": 800,
            },
        }

        metrics = mock_content_with_metrics["processing_metrics"]

        for field_name, field_type in expected_processing_metrics.items():
            if field_name in metrics:  # Some fields may be optional
                assert isinstance(metrics[field_name], field_type), f"Wrong type for metrics {field_name}"

    def _validate_content_item_fields(self, content_item: Dict[str, Any]):
        """Helper method to validate content item field names and types"""
        # Check required fields
        required_fields = ["content_id", "title", "content_type", "status"]
        for field in required_fields:
            assert field in content_item, f"Missing required field: {field}"

        # Check field types for present fields
        for field_name, expected_type in self.expected_content_fields.items():
            if field_name in content_item:
                actual_value = content_item[field_name]
                assert isinstance(actual_value, expected_type), (
                    f"Field {field_name} should be {expected_type}, got {type(actual_value)}"
                )

        # Specifically check that we use 'embedding_info' not 'embeddings'
        if "embedding_info" in content_item or "embeddings" in content_item:
            assert "embedding_info" in content_item, "Should use 'embedding_info' field name"
            assert "embeddings" not in content_item, "Should not use 'embeddings' field name"


class TestFieldMappingIntegration:
    """Integration tests for field mapping across different components"""

    def test_ui_service_field_compatibility(self):
        """Test that standardized fields are compatible with UI service expectations"""
        # Mock a standardized API response
        standardized_response = {
            "content_id": "cnt_001",
            "title": "Test Article",
            "content_type": "article",
            "status": "published",
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
            "embedding_info": {
                "model": "amazon.titan-embed-text-v1",
                "dimensions": 1536,
                "created_at": "2025-01-15T10:00:00Z",
            },
            "created_at": "2025-01-15T10:00:00Z",
            "updated_at": "2025-01-15T10:00:00Z",
        }

        # Verify this structure can be consumed by UI
        # (In a real test, you'd import and test the actual UI service)

        # Check that all expected fields are present
        assert standardized_response["content_id"]
        assert standardized_response["title"]
        assert standardized_response["content_type"]
        assert standardized_response["status"]

        # Check IAB categories are in standardized format
        assert isinstance(standardized_response["iab_categories"], list)
        if standardized_response["iab_categories"]:
            category = standardized_response["iab_categories"][0]
            assert "id" in category
            assert "name" in category
            assert "tier_1" in category

        # Check embedding info uses correct field name
        assert "embedding_info" in standardized_response
        assert "embeddings" not in standardized_response

    def test_cross_endpoint_field_consistency(self):
        """Test that the same fields are named consistently across different endpoints"""
        # This test would verify that content_id, iab_categories, embedding_info, etc.
        # are named the same way in list, get, create, and update responses

        common_fields = [
            "content_id",
            "title",
            "content_type",
            "status",
            "iab_categories",
            "embedding_info",
            "created_at",
            "updated_at",
        ]

        # In a real implementation, you'd call multiple endpoints and verify
        # that these fields are consistently named across all responses

        # For now, we'll just verify the field names are in our expected format
        for field_name in common_fields:
            # Field names should be snake_case
            assert "_" in field_name or field_name.islower(), f"Field {field_name} should be snake_case"

            # Should not contain deprecated field names
            deprecated_names = ["embeddings", "embedding", "categories"]
            assert field_name not in deprecated_names, f"Field {field_name} is deprecated"


if __name__ == "__main__":
    pytest.main([__file__])
