#!/usr/bin/env python3
"""
IAB Taxonomy Migration Script

Migrates IAB Content Taxonomy data from TSV file to DynamoDB table.
Replaces hardcoded taxonomy dictionaries with database-driven approach.
"""

import csv
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

# Add lambda source modules to path (services/ lives in source/lambda/)
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.taxonomy_manager import TaxonomyManager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class TaxonomyMigrator:
    """
    Migrates IAB taxonomy data from TSV file to DynamoDB.
    """

    def __init__(self):
        # Region and table name defaults (profile is caller's responsibility)
        os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
        os.environ.setdefault("TAXONOMY_TABLE_NAME", "cm-taxonomy")

        self.taxonomy_manager = TaxonomyManager()
        self.tsv_file_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "iab-content-taxonomy.tsv")

    def parse_tsv_file(self) -> List[Dict[str, Any]]:
        """
        Parse the IAB taxonomy TSV file and convert to DynamoDB records.

        Returns:
            List of taxonomy records ready for DynamoDB insertion
        """
        if not os.path.exists(self.tsv_file_path):
            raise FileNotFoundError(f"Taxonomy TSV file not found: {self.tsv_file_path}")

        taxonomy_records = []

        try:
            with open(self.tsv_file_path, "r", encoding="utf-8") as file:
                # Skip the header row
                next(file)

                reader = csv.reader(file, delimiter="\t")

                for row_num, row in enumerate(reader, start=2):  # Start at 2 because we skipped header
                    try:
                        if len(row) < 7:  # Ensure we have all required columns
                            logger.warning(f"Skipping row {row_num}: insufficient columns")
                            continue

                        unique_id = row[0].strip()
                        parent_id = row[1].strip()
                        name = row[2].strip()
                        tier_1 = row[3].strip()
                        tier_2 = row[4].strip()
                        tier_3 = row[5].strip()
                        tier_4 = row[6].strip()

                        # Skip empty rows
                        if not unique_id or not name:
                            continue

                        # Determine tier level based on which tier fields are populated
                        tier_level = 1
                        if tier_4:
                            tier_level = 4
                        elif tier_3:
                            tier_level = 3
                        elif tier_2:
                            tier_level = 2

                        # Build full hierarchical path
                        path_parts = [part for part in [tier_1, tier_2, tier_3, tier_4] if part]
                        full_path = " > ".join(path_parts) if path_parts else name

                        # Create DynamoDB record (avoid empty strings)
                        record = {
                            "category_id": unique_id,
                            "name": name,
                            "tier_level": tier_level,
                            "full_path": full_path,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }

                        # Only add non-empty tier fields
                        if tier_1:
                            record["tier_1"] = tier_1
                        if tier_2:
                            record["tier_2"] = tier_2
                        if tier_3:
                            record["tier_3"] = tier_3
                        if tier_4:
                            record["tier_4"] = tier_4
                        if parent_id and parent_id != unique_id:
                            record["parent_id"] = parent_id

                        taxonomy_records.append(record)

                    except Exception as e:
                        logger.error(f"Error processing row {row_num}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Error reading TSV file: {e}")
            raise

        logger.info(f"Parsed {len(taxonomy_records)} taxonomy records from TSV file")
        return taxonomy_records

    def validate_records(self, records: List[Dict[str, Any]]) -> bool:
        """
        Validate taxonomy records before migration.

        Args:
            records: List of taxonomy records to validate

        Returns:
            True if validation passes, False otherwise
        """
        if not records:
            logger.error("No records to validate")
            return False

        # Check for duplicate category IDs
        category_ids = [record["category_id"] for record in records]
        if len(category_ids) != len(set(category_ids)):
            logger.error("Duplicate category IDs found in records")
            return False

        # Validate required fields
        required_fields = ["category_id", "name", "tier_level"]
        for i, record in enumerate(records):
            for field in required_fields:
                if field not in record or not record[field]:
                    logger.error(f"Record {i}: Missing required field '{field}'")
                    return False

        logger.info(f"Validation passed for {len(records)} records")
        return True

    def migrate_to_dynamodb(self, records: List[Dict[str, Any]], batch_size: int = 25) -> bool:
        """
        Migrate taxonomy records to DynamoDB.

        Args:
            records: List of taxonomy records to migrate
            batch_size: Number of records to process in each batch

        Returns:
            True if migration successful, False otherwise
        """
        try:
            logger.info(f"Starting migration of {len(records)} records to DynamoDB")

            # Process records in batches
            for i in range(0, len(records), batch_size):
                batch = records[i : i + batch_size]

                success = self.taxonomy_manager.populate_taxonomy_table(batch)
                if not success:
                    logger.error(f"Failed to migrate batch starting at index {i}")
                    return False

                logger.info(f"Migrated batch {i // batch_size + 1}/{(len(records) + batch_size - 1) // batch_size}")

            logger.info("Migration completed successfully")
            return True

        except Exception as e:
            logger.error(f"Error during migration: {e}")
            return False

    def verify_migration(self, expected_count: int) -> bool:
        """
        Verify that the migration was successful.

        Args:
            expected_count: Expected number of records in the database

        Returns:
            True if verification passes, False otherwise
        """
        try:
            # Force refresh cache to get latest data
            self.taxonomy_manager.refresh_cache()

            # Get all categories from database
            categories = self.taxonomy_manager.get_all_categories()
            actual_count = len(categories)

            if actual_count != expected_count:
                logger.error(f"Verification failed: expected {expected_count} records, found {actual_count}")
                return False

            # Test a few specific lookups
            test_categories = ["1", "52", "111", "150"]  # Known category IDs
            for cat_id in test_categories:
                category_info = self.taxonomy_manager.get_category_info(cat_id)
                if not category_info:
                    logger.warning(f"Test category {cat_id} not found in database")
                else:
                    logger.info(f"Verified category {cat_id}: {category_info['name']}")

            logger.info(f"Migration verification passed: {actual_count} records in database")
            return True

        except Exception as e:
            logger.error(f"Error during verification: {e}")
            return False


def main():
    """Main migration function"""
    logger.info("Starting IAB Taxonomy migration to DynamoDB")

    try:
        # Initialize migrator
        migrator = TaxonomyMigrator()

        # Parse TSV file
        logger.info("Parsing taxonomy TSV file...")
        records = migrator.parse_tsv_file()

        # Validate records
        logger.info("Validating taxonomy records...")
        if not migrator.validate_records(records):
            logger.error("Record validation failed")
            return False

        # Migrate to DynamoDB
        logger.info("Migrating records to DynamoDB...")
        if not migrator.migrate_to_dynamodb(records):
            logger.error("Migration to DynamoDB failed")
            return False

        # Verify migration
        logger.info("Verifying migration...")
        if not migrator.verify_migration(len(records)):
            logger.error("Migration verification failed")
            return False

        logger.info("IAB Taxonomy migration completed successfully!")
        return True

    except Exception as e:
        logger.error(f"Migration failed with error: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
