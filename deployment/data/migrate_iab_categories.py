#!/usr/bin/env python3
"""
Migration script to standardize IAB categories data structure.

This script converts existing IAB category data from various formats
(simple arrays, string arrays) to the standardized OpenAPI format
with full category objects.
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IABCategoryMigrator:
    """Handles migration of IAB category data to standardized format"""

    def __init__(self):
        """Initialize the migrator with AWS clients and IAB taxonomy data"""
        self.dynamodb = boto3.resource("dynamodb")
        self.s3 = boto3.client("s3")

        # Load IAB taxonomy mapping
        self.taxonomy_map = self._load_iab_taxonomy()

        # Get table names from environment or use defaults.
        # Default matches the deployed CDK name (cm-content, #24 iab-llm-* -> cm-* migration);
        # real runs still receive CONTENT_TABLE_NAME from the stack.
        self.content_table_name = os.environ.get("CONTENT_TABLE_NAME", "cm-content")
        self.content_bucket_name = os.environ.get("CONTENT_BUCKET_NAME", "iab-llm-ingest-content-bucket")

        try:
            self.content_table = self.dynamodb.Table(self.content_table_name)
        except Exception as e:
            logger.warning(f"Could not connect to DynamoDB table {self.content_table_name}: {e}")
            self.content_table = None

    def _load_iab_taxonomy(self) -> Dict[int, Dict[str, Any]]:
        """Load IAB taxonomy from assets file and create ID mapping"""
        taxonomy_map = {}

        # Get the project root directory
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(script_dir)
        taxonomy_file = os.path.join(project_root, "assets", "iab-content-taxonomy.tsv")

        logger.info(f"Loading IAB taxonomy from: {taxonomy_file}")

        if not os.path.exists(taxonomy_file):
            logger.error(f"IAB taxonomy file not found at {taxonomy_file}")
            return self._get_default_taxonomy_map()

        try:
            with open(taxonomy_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

                # Skip the first two header lines
                data_lines = lines[2:]

                for line in data_lines:
                    line = line.strip()
                    if not line:
                        continue

                    parts = line.split("\t")
                    if len(parts) < 7:
                        continue

                    # Extract data (columns: ID, Parent, Name, Tier1, Tier2, Tier3, Tier4)
                    try:
                        category_id = int(parts[0].strip())
                        parent_id = int(parts[1].strip()) if parts[1].strip() else None
                        name = parts[2].strip()
                        tier_1 = parts[3].strip()
                        tier_2 = parts[4].strip() if parts[4].strip() else None
                        tier_3 = parts[5].strip() if parts[5].strip() else None
                        tier_4 = parts[6].strip() if parts[6].strip() else None

                        taxonomy_map[category_id] = {
                            "id": category_id,
                            "name": name,
                            "tier_1": tier_1,
                            "tier_2": tier_2,
                            "tier_3": tier_3,
                            "tier_4": tier_4,
                            "parent_id": parent_id,
                        }

                    except (ValueError, IndexError) as e:
                        logger.warning(f"Skipping invalid line: {line} - {e}")
                        continue

            logger.info(f"Loaded {len(taxonomy_map)} IAB categories")
            return taxonomy_map

        except Exception as e:
            logger.error(f"Error loading IAB taxonomy: {e}")
            return self._get_default_taxonomy_map()

    def _get_default_taxonomy_map(self) -> Dict[int, Dict[str, Any]]:
        """Provide default taxonomy mapping for common categories"""
        return {
            111: {
                "id": 111,
                "name": "Medical Health",
                "tier_1": "Medical Health",
                "tier_2": None,
                "tier_3": None,
                "tier_4": None,
                "parent_id": None,
            },
            126: {
                "id": 126,
                "name": "Technology & Computing",
                "tier_1": "Technology & Computing",
                "tier_2": None,
                "tier_3": None,
                "tier_4": None,
                "parent_id": None,
            },
            52: {
                "id": 52,
                "name": "Business and Finance",
                "tier_1": "Business and Finance",
                "tier_2": None,
                "tier_3": None,
                "tier_4": None,
                "parent_id": None,
            },
            80: {
                "id": 80,
                "name": "Economy",
                "tier_1": "Economy",
                "tier_2": None,
                "tier_3": None,
                "tier_4": None,
                "parent_id": None,
            },
            107: {
                "id": 107,
                "name": "Finance",
                "tier_1": "Finance",
                "tier_2": None,
                "tier_3": None,
                "tier_4": None,
                "parent_id": None,
            },
        }

    def convert_to_standard_format(self, iab_categories: Any) -> List[Dict[str, Any]]:
        """
        Convert IAB categories from any format to standardized format

        Args:
            iab_categories: Categories in any format (list of IDs, strings, or objects)

        Returns:
            List of standardized category objects
        """
        if not iab_categories:
            return []

        standardized = []

        for category in iab_categories:
            if isinstance(category, dict) and "id" in category:
                # Already in standard format
                standardized.append(category)
            elif isinstance(category, int):
                # Simple ID format - convert to full object
                if category in self.taxonomy_map:
                    standardized.append(self.taxonomy_map[category])
                else:
                    logger.warning(f"Unknown IAB category ID: {category}")
                    # Create minimal object for unknown IDs
                    standardized.append(
                        {
                            "id": category,
                            "name": f"Unknown Category {category}",
                            "tier_1": f"Unknown Category {category}",
                            "tier_2": None,
                            "tier_3": None,
                            "tier_4": None,
                            "parent_id": None,
                        }
                    )
            elif isinstance(category, str):
                # String format - try to map to known categories
                mapped_category = self._map_string_to_category(category)
                if mapped_category:
                    standardized.append(mapped_category)
                else:
                    logger.warning(f"Could not map string category: {category}")

        return standardized

    def _map_string_to_category(self, category_string: str) -> Optional[Dict[str, Any]]:
        """Map a string category to a standardized category object"""
        # Try exact name match first
        for cat_id, cat_data in self.taxonomy_map.items():
            if cat_data["name"].lower() == category_string.lower():
                return cat_data

        # Try partial matches in tier names
        category_lower = category_string.lower()
        for cat_id, cat_data in self.taxonomy_map.items():
            if (
                (cat_data["tier_1"] and category_lower in cat_data["tier_1"].lower())
                or (cat_data["tier_2"] and category_lower in cat_data["tier_2"].lower())
                or (cat_data["tier_3"] and category_lower in cat_data["tier_3"].lower())
            ):
                return cat_data

        return None

    def migrate_dynamodb_content(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        Migrate IAB categories in DynamoDB content table

        Args:
            dry_run: If True, only analyze without making changes

        Returns:
            Migration results summary
        """
        if not self.content_table:
            return {"error": "DynamoDB table not available"}

        results = {"items_processed": 0, "items_updated": 0, "items_skipped": 0, "errors": []}

        try:
            # Scan the table to find items with IAB categories
            response = self.content_table.scan()
            items = response.get("Items", [])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self.content_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
                items.extend(response.get("Items", []))

            logger.info(f"Found {len(items)} items to process")

            for item in items:
                results["items_processed"] += 1

                if "iab_categories" not in item:
                    results["items_skipped"] += 1
                    continue

                original_categories = item["iab_categories"]
                standardized_categories = self.convert_to_standard_format(original_categories)

                # Check if conversion is needed
                if self._categories_need_update(original_categories, standardized_categories):
                    if not dry_run:
                        try:
                            # Update the item
                            self.content_table.update_item(
                                Key={"content_id": item["content_id"]},
                                UpdateExpression="SET iab_categories = :categories, updated_at = :updated_at",
                                ExpressionAttributeValues={
                                    ":categories": standardized_categories,
                                    ":updated_at": datetime.utcnow().isoformat(),
                                },
                            )
                            results["items_updated"] += 1
                            logger.info(f"Updated categories for content {item['content_id']}")
                        except Exception as e:
                            error_msg = f"Failed to update {item['content_id']}: {str(e)}"
                            results["errors"].append(error_msg)
                            logger.error(error_msg)
                    else:
                        results["items_updated"] += 1
                        logger.info(f"Would update categories for content {item['content_id']}")
                else:
                    results["items_skipped"] += 1

            return results

        except Exception as e:
            error_msg = f"Error during DynamoDB migration: {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
            return results

    def _categories_need_update(self, original: Any, standardized: List[Dict[str, Any]]) -> bool:
        """Check if categories need to be updated"""
        if not original:
            return False

        # If original is already a list of dicts with 'id' field, check if they match
        if isinstance(original, list) and original and isinstance(original[0], dict):
            if len(original) != len(standardized):
                return True
            for orig, std in zip(original, standardized):
                if orig.get("id") != std.get("id"):
                    return True
            return False

        # Otherwise, it needs updating
        return True

    def migrate_s3_embeddings(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        Migrate IAB categories in S3 embedding metadata

        Args:
            dry_run: If True, only analyze without making changes

        Returns:
            Migration results summary
        """
        results = {"files_processed": 0, "files_updated": 0, "files_skipped": 0, "errors": []}

        try:
            # List all embedding files
            response = self.s3.list_objects_v2(Bucket=self.content_bucket_name, Prefix="embeddings/")

            if "Contents" not in response:
                logger.info("No embedding files found in S3")
                return results

            for obj in response["Contents"]:
                if not obj["Key"].endswith(".json"):
                    continue

                results["files_processed"] += 1

                try:
                    # Get the embedding file
                    file_response = self.s3.get_object(Bucket=self.content_bucket_name, Key=obj["Key"])

                    embedding_data = json.loads(file_response["Body"].read())

                    # Check if metadata has IAB categories
                    metadata = embedding_data.get("metadata", {})
                    if "iab_categories" not in metadata:
                        results["files_skipped"] += 1
                        continue

                    original_categories = metadata["iab_categories"]
                    standardized_categories = self.convert_to_standard_format(original_categories)

                    if self._categories_need_update(original_categories, standardized_categories):
                        if not dry_run:
                            # Update the metadata
                            metadata["iab_categories"] = standardized_categories
                            embedding_data["metadata"] = metadata

                            # Write back to S3
                            self.s3.put_object(
                                Bucket=self.content_bucket_name,
                                Key=obj["Key"],
                                Body=json.dumps(embedding_data),
                                ContentType="application/json",
                            )
                            results["files_updated"] += 1
                            logger.info(f"Updated categories in {obj['Key']}")
                        else:
                            results["files_updated"] += 1
                            logger.info(f"Would update categories in {obj['Key']}")
                    else:
                        results["files_skipped"] += 1

                except Exception as e:
                    error_msg = f"Failed to process {obj['Key']}: {str(e)}"
                    results["errors"].append(error_msg)
                    logger.error(error_msg)

            return results

        except Exception as e:
            error_msg = f"Error during S3 migration: {str(e)}"
            results["errors"].append(error_msg)
            logger.error(error_msg)
            return results

    def run_migration(self, dry_run: bool = True) -> Dict[str, Any]:
        """
        Run complete migration of IAB categories

        Args:
            dry_run: If True, only analyze without making changes

        Returns:
            Complete migration results
        """
        logger.info(f"Starting IAB categories migration (dry_run={dry_run})")

        results = {
            "migration_started": datetime.utcnow().isoformat(),
            "dry_run": dry_run,
            "dynamodb_results": {},
            "s3_results": {},
            "summary": {},
        }

        # Migrate DynamoDB content
        logger.info("Migrating DynamoDB content...")
        results["dynamodb_results"] = self.migrate_dynamodb_content(dry_run)

        # Migrate S3 embeddings
        logger.info("Migrating S3 embeddings...")
        results["s3_results"] = self.migrate_s3_embeddings(dry_run)

        # Create summary
        total_processed = results["dynamodb_results"].get("items_processed", 0) + results["s3_results"].get(
            "files_processed", 0
        )
        total_updated = results["dynamodb_results"].get("items_updated", 0) + results["s3_results"].get(
            "files_updated", 0
        )
        total_errors = len(results["dynamodb_results"].get("errors", [])) + len(results["s3_results"].get("errors", []))

        results["summary"] = {
            "total_items_processed": total_processed,
            "total_items_updated": total_updated,
            "total_errors": total_errors,
            "migration_completed": datetime.utcnow().isoformat(),
        }

        logger.info(f"Migration completed: {total_updated}/{total_processed} items updated, {total_errors} errors")

        return results


def main():
    """Main function to run the migration"""
    import argparse

    parser = argparse.ArgumentParser(description="Migrate IAB categories to standardized format")
    parser.add_argument(
        "--dry-run", action="store_true", default=True, help="Run in dry-run mode (analyze only, no changes)"
    )
    parser.add_argument(
        "--execute", action="store_true", default=False, help="Execute the migration (make actual changes)"
    )
    parser.add_argument("--output", type=str, help="Output file for results (JSON format)")

    args = parser.parse_args()

    # Determine if this is a dry run
    dry_run = not args.execute

    if not dry_run:
        confirm = input("This will modify data in DynamoDB and S3. Are you sure? (yes/no): ")
        if confirm.lower() != "yes":
            print("Migration cancelled.")
            return

    # Run migration
    migrator = IABCategoryMigrator()
    results = migrator.run_migration(dry_run=dry_run)

    # Output results
    print(json.dumps(results, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
