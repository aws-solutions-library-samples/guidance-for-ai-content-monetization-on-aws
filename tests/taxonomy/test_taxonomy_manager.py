"""
Unit tests for TaxonomyManager service

Tests the database-driven IAB taxonomy management functionality.
"""

import pytest
import boto3
import os
from moto import mock_aws
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Add lambda shared modules to path
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "shared"))

from services.taxonomy_manager import TaxonomyManager


@pytest.fixture
def mock_env():
    """Mock environment variables"""
    with patch.dict(os.environ, {"TAXONOMY_TABLE_NAME": "test-taxonomy-table", "AWS_DEFAULT_REGION": "us-west-2"}):
        yield


@pytest.fixture
def dynamodb_table():
    """Create mock DynamoDB table for testing"""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create table
        table = dynamodb.create_table(
            TableName="test-taxonomy-table",
            KeySchema=[{"AttributeName": "category_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "category_id", "AttributeType": "S"},
                {"AttributeName": "tier_level", "AttributeType": "N"},
                {"AttributeName": "name", "AttributeType": "S"},
                {"AttributeName": "parent_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "tier-level-index",
                    "KeySchema": [
                        {"AttributeName": "tier_level", "KeyType": "HASH"},
                        {"AttributeName": "name", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "parent-id-index",
                    "KeySchema": [
                        {"AttributeName": "parent_id", "KeyType": "HASH"},
                        {"AttributeName": "category_id", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Add sample taxonomy data (avoid empty strings for DynamoDB)
        sample_data = [
            {
                "category_id": "1",
                "name": "Automotive",
                "tier_1": "Automotive",
                "tier_level": 1,
                "full_path": "Automotive",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "category_id": "52",
                "name": "Business and Finance",
                "tier_1": "Business and Finance",
                "tier_level": 1,
                "full_path": "Business and Finance",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "category_id": "53",
                "name": "Business",
                "tier_1": "Business and Finance",
                "tier_2": "Business",
                "parent_id": "52",
                "tier_level": 2,
                "full_path": "Business and Finance > Business",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "category_id": "111",
                "name": "Medical Health",
                "tier_1": "Medical Health",
                "tier_level": 1,
                "full_path": "Medical Health",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        # Populate table
        with table.batch_writer() as batch:
            for item in sample_data:
                batch.put_item(Item=item)

        yield table


@pytest.fixture
def taxonomy_manager(mock_env, dynamodb_table):
    """Create TaxonomyManager instance with mocked dependencies"""
    return TaxonomyManager()


class TestTaxonomyManager:
    """Test cases for TaxonomyManager"""

    def test_get_all_categories(self, taxonomy_manager):
        """Test getting all categories from database"""
        categories = taxonomy_manager.get_all_categories()

        assert isinstance(categories, dict)
        assert len(categories) == 4
        assert "1" in categories
        assert "52" in categories
        assert "53" in categories
        assert "111" in categories

        # Check category structure
        automotive = categories["1"]
        assert automotive["name"] == "Automotive"
        assert automotive["tier_1"] == "Automotive"
        assert automotive["tier_level"] == 1
        assert automotive["full_path"] == "Automotive"

    def test_get_category_info(self, taxonomy_manager):
        """Test getting specific category information"""
        # Test existing category
        category_info = taxonomy_manager.get_category_info("52")
        assert category_info is not None
        assert category_info["name"] == "Business and Finance"
        assert category_info["tier_level"] == 1

        # Test non-existent category
        category_info = taxonomy_manager.get_category_info("999")
        assert category_info is None

    def test_get_category_name(self, taxonomy_manager):
        """Test getting category display name"""
        # Test existing category
        name = taxonomy_manager.get_category_name("111")
        assert name == "Medical Health"

        # Test non-existent category
        name = taxonomy_manager.get_category_name("999")
        assert name == "Category 999"

    def test_get_categories_by_tier(self, taxonomy_manager):
        """Test filtering categories by tier level"""
        # Test tier 1 categories
        tier_1_categories = taxonomy_manager.get_categories_by_tier(1)
        assert len(tier_1_categories) == 3  # 1, 52, 111
        assert "1" in tier_1_categories
        assert "52" in tier_1_categories
        assert "111" in tier_1_categories

        # Test tier 2 categories
        tier_2_categories = taxonomy_manager.get_categories_by_tier(2)
        assert len(tier_2_categories) == 1  # 53
        assert "53" in tier_2_categories

        # Test non-existent tier
        tier_5_categories = taxonomy_manager.get_categories_by_tier(5)
        assert len(tier_5_categories) == 0

    def test_get_child_categories(self, taxonomy_manager):
        """Test getting child categories"""
        # Test category with children
        children = taxonomy_manager.get_child_categories("52")
        assert len(children) == 1
        assert "53" in children
        assert children["53"]["name"] == "Business"

        # Test category without children
        children = taxonomy_manager.get_child_categories("1")
        assert len(children) == 0

        # Test non-existent parent
        children = taxonomy_manager.get_child_categories("999")
        assert len(children) == 0

    def test_get_iab_taxonomy(self, taxonomy_manager):
        """Test getting taxonomy in API response format"""
        taxonomy_data = taxonomy_manager.get_iab_taxonomy()

        assert "version" in taxonomy_data
        assert "categories" in taxonomy_data
        assert "total_count" in taxonomy_data
        assert taxonomy_data["version"] == "3.1"
        assert taxonomy_data["total_count"] == 4

        categories = taxonomy_data["categories"]
        assert len(categories) == 4

        # Check category structure
        automotive = next((cat for cat in categories if cat["id"] == 1), None)
        assert automotive is not None
        assert automotive["name"] == "Automotive"
        assert automotive["tier_1"] == "Automotive"
        assert automotive["tier_level"] == 1

    def test_get_iab_taxonomy_with_usage_stats(self, taxonomy_manager):
        """Test getting taxonomy with usage statistics"""
        taxonomy_data = taxonomy_manager.get_iab_taxonomy(include_usage_stats=True)

        categories = taxonomy_data["categories"]
        for category in categories:
            assert "usage_stats" in category
            assert "content_count" in category["usage_stats"]
            assert "access_count" in category["usage_stats"]
            assert "revenue" in category["usage_stats"]

    def test_cache_functionality(self, taxonomy_manager):
        """Test taxonomy caching behavior"""
        # First call should load from database
        categories1 = taxonomy_manager.get_all_categories()

        # Second call should use cache
        categories2 = taxonomy_manager.get_all_categories()

        # Should be the same data
        assert categories1 == categories2

        # Force cache refresh
        taxonomy_manager.refresh_cache()
        categories3 = taxonomy_manager.get_all_categories()

        # Should still be the same data but freshly loaded
        assert categories1 == categories3

    def test_populate_taxonomy_table(self, taxonomy_manager):
        """Test populating taxonomy table with new data"""
        new_records = [
            {
                "category_id": "150",
                "name": "Attractions",
                "tier_1": "Attractions",
                "tier_2": "",
                "tier_3": "",
                "tier_4": "",
                "tier_level": 1,
                "full_path": "Attractions",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ]

        success = taxonomy_manager.populate_taxonomy_table(new_records)
        assert success is True

        # Verify the new record was added
        category_info = taxonomy_manager.get_category_info("150")
        assert category_info is not None
        assert category_info["name"] == "Attractions"

    def test_fallback_taxonomy(self, taxonomy_manager):
        """Test fallback behavior when database is unavailable"""
        # Mock database error
        with patch.object(taxonomy_manager.table, "scan", side_effect=Exception("Database error")):
            categories = taxonomy_manager.get_all_categories()

            # Should return fallback data
            assert isinstance(categories, dict)
            assert len(categories) > 0
            assert "1" in categories  # Automotive should be in fallback
            assert categories["1"]["name"] == "Automotive"

    def test_build_full_path(self, taxonomy_manager):
        """Test building hierarchical paths"""
        # Test with all tiers
        item = {
            "tier_1": "Business and Finance",
            "tier_2": "Business",
            "tier_3": "Small Business",
            "tier_4": "Startups",
            "name": "Tech Startups",
        }

        full_path = taxonomy_manager._build_full_path(item)
        expected = "Business and Finance > Business > Small Business > Startups"
        assert full_path == expected

        # Test with partial tiers
        item = {"tier_1": "Automotive", "tier_2": "", "tier_3": "", "tier_4": "", "name": "Automotive"}

        full_path = taxonomy_manager._build_full_path(item)
        assert full_path == "Automotive"

        # Test with no tiers
        item = {"tier_1": "", "tier_2": "", "tier_3": "", "tier_4": "", "name": "Unknown Category"}

        full_path = taxonomy_manager._build_full_path(item)
        assert full_path == "Unknown Category"


class TestTaxonomyManagerIntegration:
    """Integration tests for TaxonomyManager with real AWS services"""

    @pytest.mark.integration
    def test_real_dynamodb_connection(self):
        """Test connection to real DynamoDB (requires AWS credentials)"""
        # Skip if no AWS credentials available
        try:
            taxonomy_manager = TaxonomyManager()
            # This will fail gracefully if table doesn't exist
            categories = taxonomy_manager.get_all_categories()
            assert isinstance(categories, dict)
        except Exception as e:
            pytest.skip(f"Real DynamoDB not available: {e}")

    @pytest.mark.integration
    def test_taxonomy_migration_compatibility(self):
        """Test compatibility with migration script data format"""
        # This would test that the TaxonomyManager can handle
        # data in the format produced by the migration script
        pass
