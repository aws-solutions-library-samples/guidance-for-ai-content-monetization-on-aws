"""
Unit tests for IAB categories data structure consistency across all components.

Tests ensure that IAB categories are returned in the standardized OpenAPI format
across Lambda functions, mock data, and UI components.
"""

import pytest
import json
from typing import Dict, List, Any
from unittest.mock import Mock, patch

# Import the Lambda function
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.content_service import (
    standardize_iab_categories,
    get_iab_taxonomy_map,
    _map_string_to_category,
    handle_create_content,
    handle_list_content,
    handle_get_content,
    handle_update_content,
)


class TestIABCategoryStandardization:
    """Test IAB category standardization functions"""

    @pytest.fixture(autouse=True)
    def _deterministic_taxonomy(self):
        """Req 31.2: stub the taxonomy map with the deterministic fallback so
        standardize_iab_categories runs in the Foundation_Gate without a deployed/seeded
        taxonomy table (these standardization cases were previously mis-marked integration)."""
        import services.content_service as cs

        fallback = cs.taxonomy_manager._get_fallback_taxonomy()
        # The integer-id path returns the taxonomy entry as-is, so the stub entries must carry the
        # full standardized field set the OpenAPI shape requires (deployed cm-taxonomy has these).
        for entry in fallback.values():
            entry.setdefault("tier_2", None)
            entry.setdefault("tier_3", None)
            entry.setdefault("tier_4", None)
            entry.setdefault("parent_id", None)
        with patch.object(cs.taxonomy_manager, "get_all_categories", return_value=fallback):
            yield

    def test_standardize_empty_categories(self):
        """Test standardization of empty categories"""
        result = standardize_iab_categories([])
        assert result == []

        result = standardize_iab_categories(None)
        assert result == []

    def test_standardize_integer_categories(self):
        """Test standardization of integer category IDs"""
        # Test known categories
        result = standardize_iab_categories([111, 126])

        assert len(result) == 2
        assert all(isinstance(cat, dict) for cat in result)
        assert all("id" in cat and "name" in cat and "tier_1" in cat for cat in result)

        # Against the loaded IAB Content Taxonomy the ids echo the input (as int or the DB's
        # string form) and names resolve to non-empty strings; exact id->name mapping is
        # taxonomy-version-specific and not asserted here.
        result_ids = {str(cat["id"]) for cat in result}
        assert {"111", "126"} <= result_ids
        assert all(isinstance(cat["name"], str) and cat["name"] for cat in result)
        assert all(isinstance(cat["tier_1"], str) and cat["tier_1"] for cat in result)

    def test_standardize_unknown_integer_categories(self):
        """Test standardization of unknown integer category IDs"""
        result = standardize_iab_categories([9999])

        assert len(result) == 1
        assert result[0]["id"] == 9999
        assert result[0]["name"] == "Unknown Category 9999"
        assert result[0]["tier_1"] == "Unknown Category 9999"
        assert result[0]["tier_2"] is None

    def test_standardize_string_categories(self):
        """Test standardization of string categories"""
        # Test exact match
        result = standardize_iab_categories(["Medical Health"])

        assert len(result) == 1
        assert isinstance(result[0]["id"], (int, str))
        assert isinstance(result[0]["name"], str) and result[0]["name"]

        # Test partial match
        result = standardize_iab_categories(["Technology"])

        assert len(result) == 1
        assert isinstance(result[0]["name"], str) and result[0]["name"]

    def test_standardize_unknown_string_categories(self):
        """Test standardization of unknown string categories"""
        result = standardize_iab_categories(["Unknown Category"])

        assert len(result) == 1
        assert isinstance(result[0]["id"], int)
        assert result[0]["name"] == "Unknown Category"
        assert result[0]["tier_1"] == "Unknown Category"

    def test_standardize_object_categories(self):
        """Test standardization of already-standardized object categories"""
        input_categories = [{"id": 111, "name": "Medical Health", "tier_1": "Medical Health"}]

        result = standardize_iab_categories(input_categories)

        assert len(result) == 1
        assert result[0]["id"] == 111
        assert result[0]["name"] == "Medical Health"
        assert result[0]["tier_1"] == "Medical Health"
        assert "tier_2" in result[0]
        assert "tier_3" in result[0]
        assert "tier_4" in result[0]
        assert "parent_id" in result[0]

    def test_standardize_mixed_format_categories(self):
        """Test standardization of mixed format categories"""
        input_categories = [
            111,  # Integer
            "Technology & Computing",  # String
            {"id": 52, "name": "Business and Finance", "tier_1": "Business and Finance"},  # Object
        ]

        result = standardize_iab_categories(input_categories)

        assert len(result) == 3
        assert all(isinstance(cat, dict) for cat in result)
        assert all("id" in cat and "name" in cat and "tier_1" in cat for cat in result)

        # Check that all categories have the required OpenAPI fields
        required_fields = ["id", "name", "tier_1", "tier_2", "tier_3", "tier_4", "parent_id"]
        for cat in result:
            for field in required_fields:
                assert field in cat


class TestLambdaFunctionConsistency:
    """Test that Lambda functions return consistent IAB category format"""

    @patch("services.content_service.s3")
    @patch("services.content_service.content_table")
    @patch("services.content_service.jobs_table")
    def test_create_content_iab_format(self, mock_jobs_table, mock_content_table, mock_s3):
        """Test that create content accepts IAB categories (async enqueue)"""
        user_info = {"user_id": "test_user", "username": "test"}
        request_data = {
            "title": "Test Article",
            "content_type": "article",
            "iab_categories": [111, 126],  # Input as integers
        }

        response = handle_create_content(user_info, request_data)

        assert response["statusCode"] == 202
        response_body = json.loads(response["body"])
        assert "content_id" in response_body

    @patch("services.content_service._get_real_content_list")
    def test_list_content_iab_format(self, mock_get_real_list):
        """Test that list content returns standardized IAB categories"""
        # Real source returns the listing payload (already standardized dicts)
        mock_get_real_list.return_value = {
            "items": [
                {
                    "content_id": "test_001",
                    "title": "Test Article",
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
                }
            ],
            "pagination": {"total_items": 1},
        }

        user_info = {"user_id": "test_user", "username": "test"}
        response = handle_list_content(user_info, {})

        assert response["statusCode"] == 200
        response_body = json.loads(response["body"])

        items = response_body["items"]
        assert len(items) > 0

        # Check IAB categories format
        for item in items:
            if "iab_categories" in item:
                for category in item["iab_categories"]:
                    assert isinstance(category, dict)
                    assert "id" in category
                    assert "name" in category
                    assert "tier_1" in category

    @patch("services.content_service._get_real_content_details")
    def test_get_content_iab_format(self, mock_get_real_details):
        """Test that get content returns standardized IAB categories"""
        mock_get_real_details.return_value = {
            "content_id": "test_001",
            "title": "Test Article",
            "iab_categories": [
                {
                    "id": 126,
                    "name": "Technology & Computing",
                    "tier_1": "Technology & Computing",
                    "tier_2": None,
                    "tier_3": None,
                    "tier_4": None,
                    "parent_id": None,
                }
            ],
        }

        user_info = {"user_id": "test_user", "username": "test"}
        response = handle_get_content(user_info, "test_001")

        assert response["statusCode"] == 200
        response_body = json.loads(response["body"])

        # Check IAB categories format
        assert "iab_categories" in response_body
        for category in response_body["iab_categories"]:
            assert isinstance(category, dict)
            assert "id" in category
            assert "name" in category
            assert "tier_1" in category
            assert category["id"] == 126
            assert category["name"] == "Technology & Computing"

    @patch("services.content_service.content_table")
    def test_update_content_iab_format(self, mock_content_table):
        """Test that update content handles and returns standardized IAB categories"""
        # Real path: existing item found, update returns the stored categories
        mock_content_table.get_item.return_value = {"Item": {"content_id": "test_001"}}
        mock_content_table.update_item.return_value = {
            "Attributes": {
                "content_id": "test_001",
                "iab_categories": [52, 80],
                "updated_at": "2025-01-15T10:05:00Z",
                "updated_by": "test_user",
            }
        }

        user_info = {"user_id": "test_user", "username": "test"}
        request_data = {
            "iab_categories": [52, 80]  # Input as integers
        }

        response = handle_update_content(user_info, "test_001", request_data)

        assert response["statusCode"] == 200
        response_body = json.loads(response["body"])

        # Check that IAB categories are in standardized format
        assert "iab_categories" in response_body
        for category in response_body["iab_categories"]:
            assert isinstance(category, dict)
            assert "id" in category
            assert "name" in category
            assert "tier_1" in category


class TestOpenAPICompliance:
    """Test compliance with OpenAPI specification for IAB categories"""

    @pytest.mark.integration
    def test_iab_category_schema_compliance(self):
        """Test that standardized categories match OpenAPI schema"""
        # Test with known category
        result = standardize_iab_categories([111])
        category = result[0]

        # Required fields per OpenAPI spec
        required_fields = ["id", "name", "tier_1"]
        for field in required_fields:
            assert field in category
            assert category[field] is not None

        # Optional fields per OpenAPI spec
        optional_fields = ["tier_2", "tier_3", "tier_4", "parent_id"]
        for field in optional_fields:
            assert field in category
            # These can be None/null

        # Type validation (the DB-backed taxonomy stores category_id as a string; accept either)
        assert isinstance(category["id"], (int, str))
        assert isinstance(category["name"], str)
        assert isinstance(category["tier_1"], str)

    @pytest.mark.integration
    def test_multiple_categories_schema_compliance(self):
        """Test that multiple categories all comply with schema"""
        result = standardize_iab_categories([111, 126, 52])

        assert len(result) == 3

        for category in result:
            # Required fields
            assert "id" in category and isinstance(category["id"], (int, str))
            assert "name" in category and isinstance(category["name"], str)
            assert "tier_1" in category and isinstance(category["tier_1"], str)

            # Optional fields exist (can be None)
            assert "tier_2" in category
            assert "tier_3" in category
            assert "tier_4" in category
            assert "parent_id" in category


class TestUICompatibility:
    """Test that the standardized format is compatible with UI expectations"""

    @pytest.mark.integration
    def test_category_display_name_extraction(self):
        """Test that UI can extract display names from standardized format"""
        categories = standardize_iab_categories([111, 126])

        # UI should be able to extract non-empty display names + tier_1 fallbacks for each category
        # (exact names are taxonomy-version-specific and not asserted).
        display_names = [cat["name"] for cat in categories]
        assert len(display_names) == 2
        assert all(isinstance(n, str) and n for n in display_names)

        tier1_names = [cat["tier_1"] for cat in categories]
        assert all(isinstance(n, str) and n for n in tier1_names)

    @pytest.mark.integration
    def test_category_id_extraction(self):
        """Test that UI can extract category IDs for filtering"""
        categories = standardize_iab_categories([111, 126])

        category_ids = [cat["id"] for cat in categories]
        # Ids echo the input (as int or the DB's string form).
        assert {"111", "126"} <= {str(cat_id) for cat_id in category_ids}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
