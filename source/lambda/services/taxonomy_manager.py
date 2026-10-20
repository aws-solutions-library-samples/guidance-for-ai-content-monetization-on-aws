"""
IAB Taxonomy Manager Service

Provides centralized management of IAB Content Taxonomy data from DynamoDB.
Replaces hardcoded taxonomy dictionaries with database-driven approach.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class TaxonomyManager:
    """
    Manages IAB Content Taxonomy data from DynamoDB.

    Provides methods to:
    - Load taxonomy data from database
    - Get category information by ID
    - Get hierarchical category relationships
    - Support UI dropdown/form requirements
    """

    def __init__(self):
        self.dynamodb = boto3.resource("dynamodb")
        self.table_name = os.environ.get("TAXONOMY_TABLE_NAME", "cm-taxonomy")
        self.table = self.dynamodb.Table(self.table_name)
        self._cache = {}
        self._cache_timestamp = None
        self._cache_ttl = 3600  # Cache for 1 hour

    def _is_cache_valid(self) -> bool:
        """Check if the taxonomy cache is still valid"""
        if not self._cache_timestamp:
            return False

        cache_age = (datetime.now(timezone.utc) - self._cache_timestamp).total_seconds()
        return cache_age < self._cache_ttl

    def _load_taxonomy_from_db(self) -> Dict[str, Dict[str, Any]]:
        """Load all taxonomy data from DynamoDB"""
        try:
            response = self.table.scan()
            taxonomy_data = {}

            for item in response["Items"]:
                category_id = str(item["category_id"])
                taxonomy_data[category_id] = {
                    "id": category_id,
                    "name": item.get("name", ""),
                    "tier_1": item.get("tier_1", ""),
                    "tier_2": item.get("tier_2", ""),
                    "tier_3": item.get("tier_3", ""),
                    "tier_4": item.get("tier_4", ""),
                    "parent_id": item.get("parent_id", ""),
                    "tier_level": item.get("tier_level", 1),
                    "full_path": self._build_full_path(item),
                }

            # Handle pagination if needed
            while "LastEvaluatedKey" in response:
                response = self.table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
                for item in response["Items"]:
                    category_id = str(item["category_id"])
                    taxonomy_data[category_id] = {
                        "id": category_id,
                        "name": item.get("name", ""),
                        "tier_1": item.get("tier_1", ""),
                        "tier_2": item.get("tier_2", ""),
                        "tier_3": item.get("tier_3", ""),
                        "tier_4": item.get("tier_4", ""),
                        "parent_id": item.get("parent_id", ""),
                        "tier_level": item.get("tier_level", 1),
                        "full_path": self._build_full_path(item),
                    }

            return taxonomy_data

        except ClientError as e:
            logger.error(f"Error loading taxonomy from DynamoDB: {e}")
            return self._get_fallback_taxonomy()
        except Exception as e:
            logger.error(f"Unexpected error loading taxonomy: {e}")
            return self._get_fallback_taxonomy()

    def _build_full_path(self, item: Dict[str, Any]) -> str:
        """Build full hierarchical path for a category"""
        path_parts = []

        if item.get("tier_1"):
            path_parts.append(item["tier_1"])
        if item.get("tier_2"):
            path_parts.append(item["tier_2"])
        if item.get("tier_3"):
            path_parts.append(item["tier_3"])
        if item.get("tier_4"):
            path_parts.append(item["tier_4"])

        return " > ".join(path_parts) if path_parts else item.get("name", "")

    def _get_fallback_taxonomy(self) -> Dict[str, Dict[str, Any]]:
        """Provide fallback taxonomy data when database is unavailable"""
        logger.warning("Using fallback taxonomy data")
        return {
            "1": {"id": "1", "name": "Automotive", "tier_1": "Automotive", "tier_level": 1, "full_path": "Automotive"},
            "52": {
                "id": "52",
                "name": "Business and Finance",
                "tier_1": "Business and Finance",
                "tier_level": 1,
                "full_path": "Business and Finance",
            },
            "111": {
                "id": "111",
                "name": "Medical Health",
                "tier_1": "Medical Health",
                "tier_level": 1,
                "full_path": "Medical Health",
            },
            "126": {
                "id": "126",
                "name": "Technology & Computing",
                "tier_1": "Technology & Computing",
                "tier_level": 1,
                "full_path": "Technology & Computing",
            },
            "150": {
                "id": "150",
                "name": "Attractions",
                "tier_1": "Attractions",
                "tier_level": 1,
                "full_path": "Attractions",
            },
        }

    def get_all_categories(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all taxonomy categories with caching.

        Returns:
            Dict mapping category IDs to category information
        """
        if not self._is_cache_valid():
            logger.info("Loading taxonomy data from database")
            self._cache = self._load_taxonomy_from_db()
            self._cache_timestamp = datetime.now(timezone.utc)

        return self._cache

    def get_category_info(self, category_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information for a specific category ID.

        Args:
            category_id: The IAB category ID to look up

        Returns:
            Category information dict or None if not found
        """
        categories = self.get_all_categories()
        return categories.get(str(category_id))

    def get_category_name(self, category_id: str) -> str:
        """
        Get the display name for a category ID.

        Args:
            category_id: The IAB category ID to look up

        Returns:
            Category name or fallback string
        """
        category_info = self.get_category_info(category_id)
        if category_info:
            return category_info["name"]
        return f"Category {category_id}"

    def get_categories_by_tier(self, tier_level: int) -> Dict[str, Dict[str, Any]]:
        """
        Get categories filtered by tier level.

        Args:
            tier_level: The tier level to filter by (1-4)

        Returns:
            Dict of categories at the specified tier level
        """
        all_categories = self.get_all_categories()
        return {
            cat_id: cat_info for cat_id, cat_info in all_categories.items() if cat_info.get("tier_level") == tier_level
        }

    def get_child_categories(self, parent_id: str) -> Dict[str, Dict[str, Any]]:
        """
        Get child categories for a given parent category.

        Args:
            parent_id: The parent category ID

        Returns:
            Dict of child categories
        """
        all_categories = self.get_all_categories()
        return {
            cat_id: cat_info for cat_id, cat_info in all_categories.items() if cat_info.get("parent_id") == parent_id
        }

    def get_iab_taxonomy(self, include_usage_stats: bool = False, publisher_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get IAB taxonomy data in API response format.

        Args:
            include_usage_stats: Whether to include usage statistics
            publisher_id: Publisher ID for scoped statistics

        Returns:
            Formatted taxonomy response
        """
        categories = self.get_all_categories()

        # Convert to list format for API response
        category_list = []
        for cat_id, cat_info in categories.items():
            category_data = {
                "id": int(cat_id) if cat_id.isdigit() else cat_id,
                "name": cat_info["name"],
                "tier_1": cat_info.get("tier_1", ""),
                "tier_2": cat_info.get("tier_2", ""),
                "tier_3": cat_info.get("tier_3", ""),
                "tier_4": cat_info.get("tier_4", ""),
                "parent_id": cat_info.get("parent_id", ""),
                "tier_level": cat_info.get("tier_level", 1),
                "full_path": cat_info.get("full_path", cat_info["name"]),
            }

            # Add usage stats if requested (placeholder for future implementation)
            if include_usage_stats:
                category_data["usage_stats"] = {"content_count": 0, "access_count": 0, "revenue": 0.0}

            category_list.append(category_data)

        return {
            "version": "3.1",
            "categories": category_list,
            "total_count": len(categories),
            "last_updated": self._cache_timestamp.isoformat() if self._cache_timestamp else None,
        }

    def refresh_cache(self) -> None:
        """Force refresh of the taxonomy cache"""
        logger.info("Forcing taxonomy cache refresh")
        self._cache = self._load_taxonomy_from_db()
        self._cache_timestamp = datetime.now(timezone.utc)

    def populate_taxonomy_table(self, taxonomy_data: List[Dict[str, Any]]) -> bool:
        """
        Populate the taxonomy table with data (used by migration script).

        Args:
            taxonomy_data: List of taxonomy records to insert

        Returns:
            True if successful, False otherwise
        """
        try:
            # Use batch write for efficiency
            with self.table.batch_writer() as batch:
                for record in taxonomy_data:
                    batch.put_item(Item=record)

            logger.info(f"Successfully populated taxonomy table with {len(taxonomy_data)} records")
            self.refresh_cache()  # Refresh cache after population
            return True

        except ClientError as e:
            logger.error(f"Error populating taxonomy table: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error populating taxonomy table: {e}")
            return False
