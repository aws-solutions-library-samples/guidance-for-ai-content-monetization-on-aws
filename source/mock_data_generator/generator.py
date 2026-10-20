"""
Main mock data generator class with DynamoDB client initialization.
"""

import csv
import hashlib
import logging
import math
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
from faker import Faker

from .config import GeneratorConfig
from .partner_operations import KEY_GATED_OPERATION_IDS
from .validation import ReferentialIntegrityValidator, SchemaValidator, ValidationReporter, ValidationResult

logger = logging.getLogger(__name__)


class MockDataGenerator:
    """
    Main orchestrator for mock data generation with DynamoDB integration.

    This class manages the overall data generation process, coordinates between
    different data generators, and handles DynamoDB client initialization.
    """

    # DynamoDB table names from CDK stack. The partner table has a CloudFormation-generated name
    # (it is replaced, not renamed, when its key schema changes), so it has no literal here: each
    # instance resolves it from config/``PARTNER_TABLE_NAME`` (see ``resolve_partner_table_name``).
    TABLE_NAMES = {
        "content": "cm-content",
        "partners": "",
        "jobs": "cm-jobs",
        "access_logs": "iab-access-logs",
        "analytics": "cm-analytics",
        "bot_rules": "cm-bot-rules",
        "pricing_rules": "cm-pricing-rules",
        "pricing_transactions": "cm-pricing-transactions",
    }

    # Batch write limits for DynamoDB
    BATCH_WRITE_LIMIT = 25  # DynamoDB batch_write_item limit

    # Shared by synthetic access logs and aggregate metrics; discovery combines
    # the former search (40%) and query (15%) traffic.
    API_ENDPOINT_WEIGHTS = {
        "/api/v1/query": 55,
        "/api/v1/content": 30,
        "/api/v1/analytics": 10,
        "/api/v1/bid": 5,
    }

    # Tables whose items must never be bulk-cleared. Partner ``KEY#`` items track live API Gateway
    # keys; deleting them orphans the keys, and the next seed would issue new ones. Partner records
    # and keys are managed by the lifecycle service and ``deployment/data/add-sample-partners.py``.
    NEVER_CLEARED = frozenset({"partners"})

    def __init__(self, config: GeneratorConfig):
        """
        Initialize the mock data generator with configuration and AWS clients.

        Args:
            config: Generator configuration with AWS settings and data counts
        """
        self.config = config
        self.TABLE_NAMES = {
            **type(self).TABLE_NAMES,
            "partners": config.partner_table_name or os.environ.get("PARTNER_TABLE_NAME", ""),
        }
        self._dynamodb_client: Optional[boto3.client] = None
        self._dynamodb_resource: Optional[boto3.resource] = None

        # Initialize AWS session. Prefer the configured named profile, but fall back to
        # ambient/default credentials when that profile is not present — e.g. in CI, which
        # runs with ambient (OIDC/role) credentials and has no 'genai' profile (wave0 CI fix).
        profile = config.aws_profile or None
        try:
            if profile:
                try:
                    self._session = boto3.Session(profile_name=profile, region_name=config.aws_region)
                    logger.info(
                        f"Initialized AWS session with profile '{profile}' in region '{config.aws_region}'"
                    )
                except ProfileNotFound:
                    logger.warning(
                        f"AWS profile '{profile}' not found; falling back to ambient credentials "
                        f"in region '{config.aws_region}'"
                    )
                    self._session = boto3.Session(region_name=config.aws_region)
            else:
                self._session = boto3.Session(region_name=config.aws_region)
                logger.info(f"Initialized AWS session with ambient credentials in region '{config.aws_region}'")
        except Exception as e:
            logger.error(f"Failed to initialize AWS session: {e}")
            raise

    @property
    def dynamodb_client(self) -> boto3.client:
        """
        Lazy initialization of DynamoDB client.

        Returns:
            Configured DynamoDB client

        Raises:
            NoCredentialsError: If AWS credentials are not configured
            ClientError: If DynamoDB client initialization fails
        """
        if self._dynamodb_client is None:
            try:
                self._dynamodb_client = self._session.client("dynamodb")
                logger.info("DynamoDB client initialized successfully")
            except NoCredentialsError:
                logger.error("AWS credentials not found. Please configure AWS credentials.")
                raise
            except ClientError as e:
                logger.error(f"Failed to initialize DynamoDB client: {e}")
                raise

        return self._dynamodb_client

    @property
    def dynamodb_resource(self) -> boto3.resource:
        """
        Lazy initialization of DynamoDB resource.

        Returns:
            Configured DynamoDB resource

        Raises:
            NoCredentialsError: If AWS credentials are not configured
            ClientError: If DynamoDB resource initialization fails
        """
        if self._dynamodb_resource is None:
            try:
                self._dynamodb_resource = self._session.resource("dynamodb")
                logger.info("DynamoDB resource initialized successfully")
            except NoCredentialsError:
                logger.error("AWS credentials not found. Please configure AWS credentials.")
                raise
            except ClientError as e:
                logger.error(f"Failed to initialize DynamoDB resource: {e}")
                raise

        return self._dynamodb_resource

    def validate_aws_connectivity(self) -> bool:
        """
        Validate AWS connectivity and DynamoDB access.

        Returns:
            True if connectivity is successful, False otherwise
        """
        try:
            # Test DynamoDB connectivity by listing tables
            response = self.dynamodb_client.list_tables(Limit=1)
            logger.info("AWS connectivity validated successfully")
            return True
        except NoCredentialsError:
            logger.error("AWS credentials not configured")
            return False
        except ClientError as e:
            logger.error(f"AWS connectivity validation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during connectivity validation: {e}")
            return False

    def get_table_info(self, table_name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a DynamoDB table.

        Args:
            table_name: Name of the DynamoDB table

        Returns:
            Table information dictionary or None if table doesn't exist
        """
        try:
            response = self.dynamodb_client.describe_table(TableName=table_name)
            return response["Table"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.warning(f"Table '{table_name}' not found")
                return None
            else:
                logger.error(f"Error describing table '{table_name}': {e}")
                raise

    def validate_table_access(self, table_name: str) -> bool:
        """
        Validate access to a specific DynamoDB table.

        Args:
            table_name: Name of the DynamoDB table to validate

        Returns:
            True if table exists and is accessible, False otherwise
        """
        table_info = self.get_table_info(table_name)
        if table_info is None:
            return False

        table_status = table_info.get("TableStatus")
        if table_status != "ACTIVE":
            logger.warning(f"Table '{table_name}' is not active (status: {table_status})")
            return False

        logger.info(f"Table '{table_name}' is accessible and active")
        return True

    def batch_write_items(self, table_name: str, items: List[Dict[str, Any]]) -> bool:
        """
        Write items to DynamoDB table using batch operations.

        Args:
            table_name: Name of the DynamoDB table
            items: List of items to write (max 25 per batch)

        Returns:
            True if all items were written successfully, False otherwise

        Raises:
            ClientError: If DynamoDB operation fails
        """
        if not items:
            logger.info(f"No items to write to table '{table_name}'")
            return True

        if len(items) > self.BATCH_WRITE_LIMIT:
            logger.error(f"Batch size {len(items)} exceeds DynamoDB limit of {self.BATCH_WRITE_LIMIT}")
            return False

        try:
            # Prepare batch write request
            request_items = {table_name: [{"PutRequest": {"Item": item}} for item in items]}

            # Execute batch write
            response = self.dynamodb_client.batch_write_item(RequestItems=request_items)

            # Check for unprocessed items
            unprocessed_items = response.get("UnprocessedItems", {})
            if unprocessed_items:
                logger.warning(
                    f"Some items were not processed for table '{table_name}': {len(unprocessed_items.get(table_name, []))}"
                )
                # TODO: Implement retry logic for unprocessed items
                return False

            logger.info(f"Successfully wrote {len(items)} items to table '{table_name}'")
            return True

        except ClientError as e:
            logger.error(f"Failed to batch write items to table '{table_name}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during batch write to table '{table_name}': {e}")
            raise

    def batch_write_items_chunked(self, table_name: str, items: List[Dict[str, Any]]) -> bool:
        """
        Write large number of items to DynamoDB table using chunked batch operations.

        Args:
            table_name: Name of the DynamoDB table
            items: List of items to write (will be chunked automatically)

        Returns:
            True if all items were written successfully, False otherwise
        """
        if not items:
            logger.info(f"No items to write to table '{table_name}'")
            return True

        total_items = len(items)
        chunks = math.ceil(total_items / self.BATCH_WRITE_LIMIT)
        successful_writes = 0

        logger.info(f"Writing {total_items} items to table '{table_name}' in {chunks} batches")

        for i in range(0, total_items, self.BATCH_WRITE_LIMIT):
            chunk = items[i : i + self.BATCH_WRITE_LIMIT]
            chunk_num = (i // self.BATCH_WRITE_LIMIT) + 1

            logger.debug(f"Writing batch {chunk_num}/{chunks} ({len(chunk)} items)")

            if self.batch_write_items(table_name, chunk):
                successful_writes += len(chunk)
            else:
                logger.error(f"Failed to write batch {chunk_num}/{chunks}")
                return False

            # Add small delay between batches to avoid throttling
            if chunk_num < chunks:
                time.sleep(0.1)  # nosemgrep: arbitrary-sleep - rate limiting for DynamoDB writes

        logger.info(f"Successfully wrote all {successful_writes} items to table '{table_name}'")
        return True

    def scan_table_items(self, table_name: str) -> List[Dict[str, Any]]:
        """
        Scan all items from a DynamoDB table.

        Args:
            table_name: Name of the DynamoDB table to scan

        Returns:
            List of all items in the table

        Raises:
            ClientError: If DynamoDB operation fails
        """
        try:
            items = []
            scan_kwargs = {"TableName": table_name}

            while True:
                response = self.dynamodb_client.scan(**scan_kwargs)
                items.extend(response.get("Items", []))

                # Check if there are more items to scan
                last_evaluated_key = response.get("LastEvaluatedKey")
                if not last_evaluated_key:
                    break

                scan_kwargs["ExclusiveStartKey"] = last_evaluated_key

            logger.info(f"Scanned {len(items)} items from table '{table_name}'")
            return items

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                logger.warning(f"Table '{table_name}' not found during scan")
                return []
            else:
                logger.error(f"Failed to scan table '{table_name}': {e}")
                raise
        except Exception as e:
            logger.error(f"Unexpected error during table scan '{table_name}': {e}")
            raise

    def batch_delete_items(self, table_name: str, items: List[Dict[str, Any]], key_attrs: List[str]) -> bool:
        """
        Delete items from DynamoDB table using batch operations.

        Args:
            table_name: Name of the DynamoDB table
            items: List of items to delete
            key_attrs: List of key attribute names (partition key and sort key if applicable)

        Returns:
            True if all items were deleted successfully, False otherwise

        Raises:
            ClientError: If DynamoDB operation fails
        """
        if not items:
            logger.info(f"No items to delete from table '{table_name}'")
            return True

        if len(items) > self.BATCH_WRITE_LIMIT:
            logger.error(f"Batch size {len(items)} exceeds DynamoDB limit of {self.BATCH_WRITE_LIMIT}")
            return False

        try:
            # Prepare batch delete request
            delete_requests = []
            for item in items:
                # Extract key attributes for delete operation
                key = {attr: item[attr] for attr in key_attrs if attr in item}
                if len(key) == len(key_attrs):
                    delete_requests.append({"DeleteRequest": {"Key": key}})
                else:
                    logger.warning(f"Skipping item with missing key attributes: {key}")

            if not delete_requests:
                logger.warning(f"No valid items to delete from table '{table_name}'")
                return True

            request_items = {table_name: delete_requests}

            # Execute batch delete
            response = self.dynamodb_client.batch_write_item(RequestItems=request_items)

            # Check for unprocessed items
            unprocessed_items = response.get("UnprocessedItems", {})
            if unprocessed_items:
                logger.warning(
                    f"Some items were not deleted from table '{table_name}': {len(unprocessed_items.get(table_name, []))}"
                )
                return False

            logger.info(f"Successfully deleted {len(delete_requests)} items from table '{table_name}'")
            return True

        except ClientError as e:
            logger.error(f"Failed to batch delete items from table '{table_name}': {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during batch delete from table '{table_name}': {e}")
            raise

    def clear_table(self, table_name: str) -> bool:
        """
        Clear all items from a DynamoDB table using scan and batch delete operations.

        Args:
            table_name: Name of the DynamoDB table to clear

        Returns:
            True if table was cleared successfully, False otherwise
        """
        try:
            # Get table information to determine key schema
            table_info = self.get_table_info(table_name)
            if not table_info:
                logger.error(f"Cannot clear table '{table_name}' - table not found")
                return False

            # Extract key attributes from table schema
            key_schema = table_info.get("KeySchema", [])
            key_attrs = [key["AttributeName"] for key in key_schema]

            if not key_attrs:
                logger.error(f"Cannot determine key schema for table '{table_name}'")
                return False

            logger.info(f"Clearing table '{table_name}' using key attributes: {key_attrs}")

            # Scan all items from the table
            items = self.scan_table_items(table_name)

            if not items:
                logger.info(f"Table '{table_name}' is already empty")
                return True

            # Delete items in batches
            total_items = len(items)
            chunks = math.ceil(total_items / self.BATCH_WRITE_LIMIT)
            deleted_items = 0

            logger.info(f"Deleting {total_items} items from table '{table_name}' in {chunks} batches")

            for i in range(0, total_items, self.BATCH_WRITE_LIMIT):
                chunk = items[i : i + self.BATCH_WRITE_LIMIT]
                chunk_num = (i // self.BATCH_WRITE_LIMIT) + 1

                logger.debug(f"Deleting batch {chunk_num}/{chunks} ({len(chunk)} items)")

                if self.batch_delete_items(table_name, chunk, key_attrs):
                    deleted_items += len(chunk)
                else:
                    logger.error(f"Failed to delete batch {chunk_num}/{chunks}")
                    return False

                # Add small delay between batches to avoid throttling
                if chunk_num < chunks:
                    time.sleep(0.1)  # nosemgrep: arbitrary-sleep - rate limiting for DynamoDB writes

            logger.info(f"Successfully cleared table '{table_name}' - deleted {deleted_items} items")
            return True

        except Exception as e:
            logger.error(f"Failed to clear table '{table_name}': {e}")
            return False

    def clear_all_tables(self) -> bool:
        """
        Clear all mock data from all DynamoDB tables.

        Returns:
            True if all tables were cleared successfully, False otherwise
        """
        logger.info("Clearing all DynamoDB tables")

        success = True
        for table_type, table_name in self.TABLE_NAMES.items():
            if table_type in self.NEVER_CLEARED:
                logger.info(f"Not clearing {table_type} table: {table_name} (records are upserted)")
                continue
            logger.info(f"Clearing {table_type} table: {table_name}")

            if not self.clear_table(table_name):
                logger.error(f"Failed to clear {table_type} table: {table_name}")
                success = False
            else:
                logger.info(f"Successfully cleared {table_type} table: {table_name}")

        if success:
            logger.info("Successfully cleared all tables")
        else:
            logger.error("Some tables failed to clear")

        return success

    def validate_all_tables(self) -> bool:
        """
        Validate access to all required DynamoDB tables.

        Returns:
            True if all tables are accessible, False otherwise
        """
        logger.info("Validating access to all DynamoDB tables")

        success = True
        for table_type, table_name in self.TABLE_NAMES.items():
            if not table_name:
                logger.error(
                    f"No {table_type} table name: set PARTNER_TABLE_NAME or pass --stack-name "
                    "(the PartnerTableName stack output)"
                )
                success = False
                continue
            if not self.validate_table_access(table_name):
                logger.error(f"Cannot access {table_type} table: {table_name}")
                success = False
            else:
                logger.info(f"Successfully validated {table_type} table: {table_name}")

        return success

    def load_iab_taxonomy(self) -> Dict[str, List[str]]:
        """
        Load IAB taxonomy categories from the project assets.

        Returns:
            Dictionary mapping content types to appropriate IAB categories
        """
        # Get the project root directory (parent of mock_data_generator)
        project_root = os.path.dirname(os.path.dirname(__file__))
        taxonomy_file = os.path.join(project_root, "assets", "iab-content-taxonomy.tsv")

        logger.info(f"Loading IAB taxonomy from: {taxonomy_file}")

        if not os.path.exists(taxonomy_file):
            logger.warning(f"IAB taxonomy file not found at {taxonomy_file}")
            return self._get_default_taxonomy()

        try:
            categories_by_type = {"article": [], "report": [], "video": [], "image": [], "document": []}

            with open(taxonomy_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

                # Skip the first two header lines
                data_lines = lines[2:]

                for line in data_lines:
                    line = line.strip()
                    if not line:
                        continue

                    parts = line.split("\t")
                    if len(parts) < 7:  # Need at least 7 columns based on the header
                        continue

                    # Extract tier information (columns 3, 4, 5, 6)
                    tier1 = parts[3].strip() if len(parts) > 3 else ""
                    tier2 = parts[4].strip() if len(parts) > 4 else ""
                    tier3 = parts[5].strip() if len(parts) > 5 else ""
                    tier4 = parts[6].strip() if len(parts) > 6 else ""

                    if not tier1:
                        continue

                    # Create category path
                    category_parts = [tier1]
                    if tier2:
                        category_parts.append(tier2)
                    if tier3:
                        category_parts.append(tier3)
                    if tier4:
                        category_parts.append(tier4)

                    category = " > ".join(category_parts)

                    # Assign categories to content types based on relevance
                    tier1_lower = tier1.lower()

                    # Articles and reports - news, business, politics, science
                    if any(
                        keyword in tier1_lower
                        for keyword in ["business", "politics", "science", "news", "careers", "education"]
                    ):
                        if category not in categories_by_type["article"]:
                            categories_by_type["article"].append(category)
                        if category not in categories_by_type["report"]:
                            categories_by_type["report"].append(category)

                    # Videos and images - entertainment, sports, arts
                    if any(
                        keyword in tier1_lower
                        for keyword in ["sports", "entertainment", "fine art", "pop culture", "video gaming", "style"]
                    ):
                        if category not in categories_by_type["video"]:
                            categories_by_type["video"].append(category)
                        if category not in categories_by_type["image"]:
                            categories_by_type["image"].append(category)

                    # Documents - technical, legal, medical, educational
                    if any(
                        keyword in tier1_lower for keyword in ["technology", "medical", "law", "education", "science"]
                    ):
                        if category not in categories_by_type["document"]:
                            categories_by_type["document"].append(category)
                        if category not in categories_by_type["report"]:
                            categories_by_type["report"].append(category)

                    # Food, travel, lifestyle content for images and articles
                    if any(
                        keyword in tier1_lower for keyword in ["food", "travel", "home", "hobbies", "healthy living"]
                    ):
                        if category not in categories_by_type["image"]:
                            categories_by_type["image"].append(category)
                        if category not in categories_by_type["article"]:
                            categories_by_type["article"].append(category)

            # Ensure each content type has at least some categories
            for content_type in categories_by_type:
                if not categories_by_type[content_type]:
                    categories_by_type[content_type] = self._get_default_categories_for_type(content_type)

            logger.info(
                f"Loaded IAB taxonomy with {sum(len(cats) for cats in categories_by_type.values())} total category mappings"
            )
            return categories_by_type

        except Exception as e:
            logger.error(f"Failed to load IAB taxonomy: {e}")
            return self._get_default_taxonomy()

    def _convert_to_standard_iab_format(self, category_names: List[str]) -> List[Dict[str, Any]]:
        """
        Convert category names to standardized IAB format with full objects

        Args:
            category_names: List of category name strings

        Returns:
            List of standardized category objects
        """
        # Simple mapping for common categories - in production this would load from taxonomy file
        category_mapping = {
            "Business > Business Banking & Finance": {
                "S": "52",
                "name": {"S": "Business and Finance"},
                "tier_1": {"S": "Business and Finance"},
                "tier_2": {"NULL": True},
                "tier_3": {"NULL": True},
                "tier_4": {"NULL": True},
                "parent_id": {"NULL": True},
            },
            "Medical Health": {
                "S": "111",
                "name": {"S": "Medical Health"},
                "tier_1": {"S": "Medical Health"},
                "tier_2": {"NULL": True},
                "tier_3": {"NULL": True},
                "tier_4": {"NULL": True},
                "parent_id": {"NULL": True},
            },
            "Technology & Computing": {
                "S": "126",
                "name": {"S": "Technology & Computing"},
                "tier_1": {"S": "Technology & Computing"},
                "tier_2": {"NULL": True},
                "tier_3": {"NULL": True},
                "tier_4": {"NULL": True},
                "parent_id": {"NULL": True},
            },
            "Economy": {
                "S": "80",
                "name": {"S": "Economy"},
                "tier_1": {"S": "Economy"},
                "tier_2": {"NULL": True},
                "tier_3": {"NULL": True},
                "tier_4": {"NULL": True},
                "parent_id": {"NULL": True},
            },
            "Finance": {
                "S": "107",
                "name": {"S": "Finance"},
                "tier_1": {"S": "Finance"},
                "tier_2": {"NULL": True},
                "tier_3": {"NULL": True},
                "tier_4": {"NULL": True},
                "parent_id": {"NULL": True},
            },
        }

        standardized = []
        for category_name in category_names:
            if category_name in category_mapping:
                standardized.append(category_mapping[category_name])
            else:
                # Create a generic category object for unknown categories
                category_id = str(hash(category_name) % 10000)
                standardized.append(
                    {
                        "id": {"S": category_id},
                        "name": {"S": category_name},
                        "tier_1": {"S": category_name},
                        "tier_2": {"NULL": True},
                        "tier_3": {"NULL": True},
                        "tier_4": {"NULL": True},
                        "parent_id": {"NULL": True},
                    }
                )

        return standardized

    def _get_default_taxonomy(self) -> Dict[str, List[str]]:
        """
        Get default taxonomy categories if the file cannot be loaded.

        Returns:
            Default category mappings for each content type
        """
        return {
            "article": [
                "Business and Finance > Business",
                "Politics > Political Issues & Policy",
                "Science > Biological Sciences",
                "Technology & Computing > Artificial Intelligence",
                "Healthy Living > Fitness and Exercise",
            ],
            "report": [
                "Business and Finance > Business > Business Accounting & Finance",
                "Education > College Education",
                "Medical Health > Diseases and Conditions",
                "Science > Chemistry",
                "Technology & Computing > Robotics",
            ],
            "video": [
                "Pop Culture > Celebrity Families",
                "Sports > American Football",
                "Video Gaming > Console Games",
                "Fine Art > Dance",
                "Entertainment > Movies",
            ],
            "image": [
                "Fine Art > Costume",
                "Style & Fashion > Beauty",
                "Travel > Travel Locations",
                "Food & Drink > Barbecues and Grilling",
                "Hobbies & Interests > Arts and Crafts",
            ],
            "document": [
                "Education > Adult Education",
                "Law > Legal Issues",
                "Medical Health > Cosmetic Medical Services",
                "Technology & Computing > Augmented Reality",
                "Business and Finance > Personal Finance",
            ],
        }

    def _get_default_categories_for_type(self, content_type: str) -> List[str]:
        """
        Get default categories for a specific content type.

        Args:
            content_type: The content type to get categories for

        Returns:
            List of default categories for the content type
        """
        defaults = self._get_default_taxonomy()
        return defaults.get(content_type, ["General > Miscellaneous"])

    def generate_content_data(self) -> List[Dict[str, Any]]:
        """
        Generate realistic content items with IAB taxonomy categories.

        Returns:
            List of content items matching DynamoDB table structure
        """
        logger.info(f"Generating {self.config.content_count} content items")

        fake = Faker()
        taxonomy = self.load_iab_taxonomy()
        content_items = []

        # Calculate date range for content creation
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.config.date_range_months * 30)

        for i in range(self.config.content_count):
            content_type = random.choice(self.config.content_types)

            # Generate content ID with type prefix
            type_prefixes = {"article": "art", "report": "rpt", "video": "vid", "image": "img", "document": "doc"}
            content_id = f"{type_prefixes.get(content_type, 'cnt')}_{i + 1:03d}"

            # Generate realistic timestamps
            created_at = fake.date_time_between(start_date=start_date, end_date=end_date)
            updated_at = fake.date_time_between(start_date=created_at, end_date=end_date)

            # Select appropriate IAB categories for this content type
            available_categories = taxonomy.get(content_type, taxonomy["article"])
            num_categories = random.randint(1, min(3, len(available_categories)))
            selected_category_names = random.sample(available_categories, num_categories)

            # Convert to standardized format with full objects
            iab_categories = self._convert_to_standard_iab_format(selected_category_names)

            # Generate content based on type (pass original category names for topic extraction)
            content_data = self._generate_content_by_type(fake, content_type, selected_category_names)

            # Create content item matching DynamoDB schema
            content_item = {
                "content_id": {"S": content_id},
                "title": {"S": content_data["title"]},
                "content_type": {"S": content_type},
                "description": {"S": content_data["description"]},
                "content": {"S": content_data["content"]},
                "author": {"S": fake.name()},
                "publisher": {"S": fake.company()},
                "created_at": {"S": created_at.isoformat()},
                "updated_at": {"S": updated_at.isoformat()},
                "iab_categories": {"L": [{"M": cat} for cat in iab_categories]},
                "tags": {"SS": content_data["tags"]},
                "url": {"S": fake.url()},
                "access_level": {"S": random.choice(["public", "premium", "restricted"])},
                "pricing_tier": {"S": random.choice(["free", "basic", "premium"])},
                "language": {"S": "en"},
                "word_count": {"N": str(content_data["word_count"])},
                "metadata": {
                    "M": {
                        "source": {"S": fake.domain_name()},
                        "format": {"S": content_data["format"]},
                        "quality_score": {"N": str(round(random.uniform(0.7, 1.0), 2))},
                        "engagement_score": {"N": str(round(random.uniform(0.1, 1.0), 2))},
                    }
                },
            }

            content_items.append(content_item)

            if (i + 1) % 10 == 0:
                logger.debug(f"Generated {i + 1}/{self.config.content_count} content items")

        logger.info(f"Successfully generated {len(content_items)} content items")
        return content_items

    def generate_partner_data(self) -> List[Dict[str, Any]]:
        """
        Generate realistic partner ``META`` records.

        Partners are keyed on their identity (``PARTNER#<id>`` / ``META``). Credentials are API
        Gateway keys provisioned by ``PartnersService.provision_key`` and recorded as ``KEY#``
        items; the generator never fabricates them, so generated partners have no keys until one
        is issued through the lifecycle API or the sample-partner seed.

        Returns:
            List of partner META records matching the DynamoDB table structure
        """
        logger.info(f"Generating {self.config.partner_count} partner records")

        fake = Faker()
        partner_records = []

        # Calculate date range for partner creation
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.config.date_range_months * 30)

        for i in range(self.config.partner_count):
            partner_id = f"partner_{i + 1:03d}"

            created_at = fake.date_time_between(start_date=start_date, end_date=end_date)
            updated_at = fake.date_time_between(start_date=created_at, end_date=end_date)

            partner_type = random.choice(self.config.partner_types)
            partner_attributes = self._generate_partner_attributes(fake, partner_type)

            partner_record = {
                "PK": {"S": f"PARTNER#{partner_id}"},
                "SK": {"S": "META"},
                "partner_id": {"S": partner_id},
                "partner_name": {"S": partner_attributes["company_name"]},
                "partner_type": {"S": partner_type},
                "contact_email": {"S": partner_attributes["contact_email"]},
                "contact_name": {"S": partner_attributes["contact_name"]},
                "pricing_tier": {"S": partner_attributes["pricing_tier"]},
                "status": {"S": partner_attributes["status"]},
                "identity_source": {"S": "direct"},
                "allowed_endpoints": {"L": [{"S": op} for op in partner_attributes["allowed_endpoints"]]},
                "created_at": {"S": created_at.isoformat()},
                "updated_at": {"S": updated_at.isoformat()},
            }

            partner_records.append(partner_record)

            if (i + 1) % 5 == 0:
                logger.debug(f"Generated {i + 1}/{self.config.partner_count} partner records")

        logger.info(f"Successfully generated {len(partner_records)} partner records")
        return partner_records

    def _generate_partner_attributes(self, fake: Faker, partner_type: str) -> Dict[str, Any]:
        """
        Generate partner attributes based on partner type.

        Args:
            fake: Faker instance for generating data
            partner_type: Type of partner (publisher, ai_operator, analytics_provider)

        Returns:
            Dictionary with partner-specific attributes
        """
        # Base attributes for all partners
        contact_name = fake.name()

        if partner_type == "publisher":
            company_name = self._generate_publisher_name(fake)
            pricing_tier = random.choice(["premium", "enterprise"])
            allowed_endpoints = ["queryContent", "submitLog", "validateLicense"]

        elif partner_type == "ai_operator":
            company_name = self._generate_ai_company_name(fake)
            pricing_tier = random.choice(["standard", "premium", "enterprise"])
            allowed_endpoints = ["queryContent", "submitLog"]

        else:  # analytics_provider
            company_name = self._generate_analytics_company_name(fake)
            pricing_tier = random.choice(["basic", "standard", "premium"])
            allowed_endpoints = ["queryContent"]

        status = random.choice(["active"] * 9 + ["suspended"])  # 90% active; "deleted" is never seeded
        unknown = set(allowed_endpoints) - KEY_GATED_OPERATION_IDS
        if unknown:  # a partner must never be seeded with an allow-list that locks it out (D3)
            raise ValueError(f"generator allowed_endpoints not key-gated operation ids: {sorted(unknown)}")

        return {
            "company_name": company_name,
            "contact_email": f"api@{fake.domain_name()}",
            "contact_name": contact_name,
            "pricing_tier": pricing_tier,
            "allowed_endpoints": allowed_endpoints,
            "status": status,
        }

    def _generate_publisher_name(self, fake: Faker) -> str:
        """Generate realistic publisher company names."""
        prefixes = ["Global", "Digital", "Modern", "Independent", "Premier", "Elite", "Metro"]
        suffixes = ["News Network", "Media Group", "Publishing", "Press", "Tribune", "Herald", "Times"]
        return f"{random.choice(prefixes)} {random.choice(suffixes)}"

    def _generate_ai_company_name(self, fake: Faker) -> str:
        """Generate realistic AI company names."""
        prefixes = ["Neural", "Quantum", "Cognitive", "Intelligent", "Smart", "Advanced", "Next-Gen"]
        suffixes = ["AI Systems", "Intelligence", "Labs", "Technologies", "Solutions", "Dynamics", "Innovations"]
        return f"{random.choice(prefixes)} {random.choice(suffixes)}"

    def _generate_analytics_company_name(self, fake: Faker) -> str:
        """Generate realistic analytics company names."""
        prefixes = ["Data", "Insight", "Analytics", "Business", "Market", "Strategic", "Predictive"]
        suffixes = ["Analytics", "Insights", "Intelligence", "Research", "Solutions", "Metrics", "Dynamics"]
        return f"{random.choice(prefixes)} {random.choice(suffixes)}"

    def _generate_ip_whitelist(self, fake: Faker) -> List[str]:
        """Generate realistic IP whitelist for partner."""
        num_ips = random.randint(1, 5)
        ips = []
        for _ in range(num_ips):
            # Generate realistic IP addresses (avoid private ranges for external partners)
            ip = fake.ipv4_public()
            ips.append(ip)
        return ips

    def _generate_content_by_type(self, fake: Faker, content_type: str, categories: List[str]) -> Dict[str, Any]:
        """
        Generate content data specific to the content type.

        Args:
            fake: Faker instance for generating data
            content_type: Type of content to generate
            categories: IAB categories assigned to this content

        Returns:
            Dictionary with content-specific data
        """
        # Extract topic from categories for coherent content generation
        primary_category = categories[0] if categories else "General"

        # Handle both string and dict category formats
        if isinstance(primary_category, dict):
            # If it's a dict, extract the name or tier_1 field
            category_name = primary_category.get("name", primary_category.get("tier_1", "General"))
        else:
            category_name = str(primary_category)

        topic_keywords = category_name.lower().split(" > ")

        if content_type == "article":
            return self._generate_article_content(fake, topic_keywords)
        elif content_type == "report":
            return self._generate_report_content(fake, topic_keywords)
        elif content_type == "video":
            return self._generate_video_content(fake, topic_keywords)
        elif content_type == "image":
            return self._generate_image_content(fake, topic_keywords)
        elif content_type == "document":
            return self._generate_document_content(fake, topic_keywords)
        else:
            return self._generate_generic_content(fake, topic_keywords)

    def _generate_article_content(self, fake: Faker, topic_keywords: List[str]) -> Dict[str, Any]:
        """Generate article-specific content."""
        title = f"{fake.catch_phrase()} in {topic_keywords[0].title() if topic_keywords else 'Industry'}"
        description = fake.text(max_nb_chars=200)
        content = fake.text(max_nb_chars=2000)
        word_count = len(content.split())

        tags = [
            fake.word().lower(),
            topic_keywords[0] if topic_keywords else "general",
            fake.word().lower(),
            "news",
            "analysis",
        ]

        return {
            "title": title,
            "description": description,
            "content": content,
            "word_count": word_count,
            "tags": tags[:5],  # Limit to 5 tags
            "format": "text/html",
        }

    def _generate_report_content(self, fake: Faker, topic_keywords: List[str]) -> Dict[str, Any]:
        """Generate report-specific content."""
        title = f"Annual {topic_keywords[0].title() if topic_keywords else 'Industry'} Report {fake.year()}"
        description = (
            f"Comprehensive analysis of {topic_keywords[0] if topic_keywords else 'market'} trends and insights."
        )
        content = fake.text(max_nb_chars=5000)
        word_count = len(content.split())

        tags = ["report", "analysis", topic_keywords[0] if topic_keywords else "research", "data", "insights"]

        return {
            "title": title,
            "description": description,
            "content": content,
            "word_count": word_count,
            "tags": tags,
            "format": "application/pdf",
        }

    def _generate_video_content(self, fake: Faker, topic_keywords: List[str]) -> Dict[str, Any]:
        """Generate video-specific content."""
        title = f"{fake.catch_phrase()}: {topic_keywords[0].title() if topic_keywords else 'Entertainment'} Special"
        description = fake.text(max_nb_chars=300)
        content = f"Video content about {topic_keywords[0] if topic_keywords else 'entertainment'}. {fake.text(max_nb_chars=500)}"

        # Videos have duration instead of word count
        duration_minutes = random.randint(2, 120)
        word_count = duration_minutes * 150  # Approximate words per minute for transcription

        tags = [
            "video",
            topic_keywords[0] if topic_keywords else "entertainment",
            fake.word().lower(),
            "multimedia",
            "streaming",
        ]

        return {
            "title": title,
            "description": description,
            "content": content,
            "word_count": word_count,
            "tags": tags,
            "format": "video/mp4",
        }

    def _generate_image_content(self, fake: Faker, topic_keywords: List[str]) -> Dict[str, Any]:
        """Generate image-specific content."""
        title = f"{fake.word().title()} {topic_keywords[0].title() if topic_keywords else 'Gallery'} Collection"
        description = fake.text(max_nb_chars=150)
        content = f"Image collection featuring {topic_keywords[0] if topic_keywords else 'visual content'}. {fake.text(max_nb_chars=300)}"

        # Images have minimal word count (alt text, captions)
        word_count = random.randint(10, 100)

        tags = [
            "image",
            "visual",
            topic_keywords[0] if topic_keywords else "photography",
            fake.word().lower(),
            "gallery",
        ]

        return {
            "title": title,
            "description": description,
            "content": content,
            "word_count": word_count,
            "tags": tags,
            "format": "image/jpeg",
        }

    def _generate_document_content(self, fake: Faker, topic_keywords: List[str]) -> Dict[str, Any]:
        """Generate document-specific content."""
        title = f"{topic_keywords[0].title() if topic_keywords else 'Technical'} Documentation Guide"
        description = f"Comprehensive guide covering {topic_keywords[0] if topic_keywords else 'technical topics'}."
        content = fake.text(max_nb_chars=3000)
        word_count = len(content.split())

        tags = ["document", "guide", topic_keywords[0] if topic_keywords else "reference", "documentation", "manual"]

        return {
            "title": title,
            "description": description,
            "content": content,
            "word_count": word_count,
            "tags": tags,
            "format": "application/pdf",
        }

    def _generate_generic_content(self, fake: Faker, topic_keywords: List[str]) -> Dict[str, Any]:
        """Generate generic content for unknown types."""
        title = fake.sentence(nb_words=6)
        description = fake.text(max_nb_chars=200)
        content = fake.text(max_nb_chars=1000)
        word_count = len(content.split())

        tags = [
            fake.word().lower(),
            topic_keywords[0] if topic_keywords else "general",
            fake.word().lower(),
            "content",
            "misc",
        ]

        return {
            "title": title,
            "description": description,
            "content": content,
            "word_count": word_count,
            "tags": tags,
            "format": "text/plain",
        }

    def generate_job_data(self, content_ids: List[str], partner_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Generate processing job data linked to content and partners.

        Args:
            content_ids: List of content IDs to link jobs to
            partner_ids: List of partner IDs to link jobs to

        Returns:
            List of job records matching DynamoDB table structure
        """
        logger.info(f"Generating {self.config.job_count} job records")

        if not content_ids:
            logger.warning("No content IDs provided for job generation")
            return []

        if not partner_ids:
            logger.warning("No partner IDs provided for job generation")
            return []

        fake = Faker()
        job_records = []

        # Calculate date range for job creation
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.config.date_range_months * 30)

        # Job types and their characteristics
        job_types = {
            "embedding": {
                "description": "Generate vector embeddings for content",
                "avg_processing_time_ms": 5000,
                "failure_rate": 0.05,
                "priority_range": (3, 7),
            },
            "processing": {
                "description": "Process and analyze content",
                "avg_processing_time_ms": 15000,
                "failure_rate": 0.08,
                "priority_range": (2, 8),
            },
            "analysis": {
                "description": "Perform content analysis and categorization",
                "avg_processing_time_ms": 8000,
                "failure_rate": 0.03,
                "priority_range": (4, 9),
            },
        }

        # Job status distribution (realistic distribution)
        status_weights = {
            "completed": 0.70,  # 70% completed
            "failed": 0.10,  # 10% failed
            "processing": 0.05,  # 5% currently processing
            "pending": 0.15,  # 15% pending (matches validation schema)
        }

        for i in range(self.config.job_count):
            # Generate unique job ID
            job_id = f"job_{i + 1:03d}"

            # Select random content and partner (ensuring referential integrity)
            content_id = random.choice(content_ids)
            partner_id = random.choice(partner_ids)

            # Select job type and get its characteristics
            job_type = random.choice(list(job_types.keys()))
            job_config = job_types[job_type]

            # Select job status based on realistic distribution
            status = random.choices(list(status_weights.keys()), weights=list(status_weights.values()))[0]

            # Generate realistic timestamps based on status
            created_at = fake.date_time_between(start_date=start_date, end_date=end_date)
            timestamps = self._generate_job_timestamps(created_at, status, job_config["avg_processing_time_ms"])

            # Generate job priority
            priority = random.randint(*job_config["priority_range"])

            # Generate retry count (higher for failed jobs)
            retry_count = 0
            if status == "failed":
                retry_count = random.randint(1, 3)
            elif status == "completed" and random.random() < 0.2:  # 20% of completed jobs had retries
                retry_count = random.randint(1, 2)

            # Generate input parameters based on job type
            input_params = self._generate_job_input_params(job_type, content_id)

            # Generate output data and error messages based on status
            output_data, error_message = self._generate_job_results(status, job_type, job_config)

            # Create job record matching DynamoDB schema
            job_record = {
                "job_id": {"S": job_id},
                "content_id": {"S": content_id},
                "partner_id": {"S": partner_id},
                "job_type": {"S": job_type},
                "status": {"S": status},
                "created_at": {"S": created_at.isoformat()},
                "priority": {"N": str(priority)},
                "retry_count": {"N": str(retry_count)},
                "input_params": {"M": self._convert_dict_to_dynamodb_format(input_params)},
                "metadata": {
                    "M": {
                        "description": {"S": job_config["description"]},
                        "estimated_duration_ms": {"N": str(job_config["avg_processing_time_ms"])},
                        "queue_name": {"S": f"{job_type}_queue"},
                        "worker_type": {"S": f"{job_type}_worker"},
                        "resource_requirements": {
                            "M": {
                                "cpu_cores": {"N": str(random.randint(1, 4))},
                                "memory_mb": {"N": str(random.randint(512, 2048))},
                                "gpu_required": {"BOOL": job_type == "embedding"},
                            }
                        },
                    }
                },
            }

            # Add optional timestamp fields based on status
            if timestamps["started_at"]:
                job_record["started_at"] = {"S": timestamps["started_at"].isoformat()}

            if timestamps["completed_at"]:
                job_record["completed_at"] = {"S": timestamps["completed_at"].isoformat()}
                job_record["processing_time_ms"] = {"N": str(timestamps["processing_time_ms"])}

            # Add output data if job has results
            if output_data:
                job_record["output_data"] = {"M": self._convert_dict_to_dynamodb_format(output_data)}

            # Add error message if job failed
            if error_message:
                job_record["error_message"] = {"S": error_message}

            job_records.append(job_record)

            if (i + 1) % 20 == 0:
                logger.debug(f"Generated {i + 1}/{self.config.job_count} job records")

        logger.info(f"Successfully generated {len(job_records)} job records")
        return job_records

    def _generate_job_timestamps(
        self, created_at: datetime, status: str, avg_processing_time_ms: int
    ) -> Dict[str, Any]:
        """
        Generate realistic timestamps for a job based on its status.

        Args:
            created_at: When the job was created
            status: Current job status
            avg_processing_time_ms: Average processing time for this job type

        Returns:
            Dictionary with timestamp information
        """
        timestamps = {"started_at": None, "completed_at": None, "processing_time_ms": 0}

        if status == "pending":
            # Pending jobs haven't started yet
            return timestamps

        # Job has started - generate start time
        max_queue_wait = timedelta(hours=24)  # Max 24 hours in queue
        queue_wait = timedelta(seconds=random.randint(0, int(max_queue_wait.total_seconds())))
        timestamps["started_at"] = created_at + queue_wait

        if status == "processing":
            # Currently processing - no completion time
            return timestamps

        # Job has completed (successfully or failed)
        # Generate realistic processing time with variation
        base_time_ms = avg_processing_time_ms
        variation = random.uniform(0.5, 2.0)  # 50% to 200% of base time
        actual_processing_time_ms = int(base_time_ms * variation)

        # Failed jobs might take longer due to retries and timeouts
        if status == "failed":
            timeout_factor = random.uniform(1.5, 3.0)
            actual_processing_time_ms = int(actual_processing_time_ms * timeout_factor)

        processing_duration = timedelta(milliseconds=actual_processing_time_ms)
        timestamps["completed_at"] = timestamps["started_at"] + processing_duration
        timestamps["processing_time_ms"] = actual_processing_time_ms

        return timestamps

    def _generate_job_input_params(self, job_type: str, content_id: str) -> Dict[str, Any]:
        """
        Generate input parameters for a job based on its type.

        Args:
            job_type: Type of job (embedding, processing, analysis)
            content_id: ID of the content being processed

        Returns:
            Dictionary with job-specific input parameters
        """
        base_params = {"content_id": content_id, "timestamp": datetime.now(timezone.utc).isoformat(), "version": "1.0"}

        if job_type == "embedding":
            return {
                **base_params,
                "model_name": random.choice(["text-embedding-ada-002", "sentence-transformers", "bert-base"]),
                "chunk_size": random.choice([512, 1024, 2048]),
                "overlap": random.choice([50, 100, 200]),
                "normalize": True,
                "batch_size": random.choice([16, 32, 64]),
            }

        elif job_type == "processing":
            return {
                **base_params,
                "extract_metadata": True,
                "generate_summary": True,
                "detect_language": True,
                "quality_check": True,
                "output_format": random.choice(["json", "xml", "csv"]),
                "compression": random.choice(["gzip", "none"]),
            }

        elif job_type == "analysis":
            return {
                **base_params,
                "analysis_types": random.sample(
                    ["sentiment", "topics", "entities", "keywords"], k=random.randint(2, 4)
                ),
                "confidence_threshold": round(random.uniform(0.7, 0.95), 2),
                "max_results": random.choice([10, 25, 50, 100]),
                "include_scores": True,
            }

        return base_params

    def _generate_job_results(
        self, status: str, job_type: str, job_config: Dict[str, Any]
    ) -> tuple[Dict[str, Any], str]:
        """
        Generate output data and error messages based on job status and type.

        Args:
            status: Job status (completed, failed, processing, pending)
            job_type: Type of job
            job_config: Job configuration with failure rates

        Returns:
            Tuple of (output_data, error_message)
        """
        output_data = {}
        error_message = ""

        if status == "completed":
            # Generate successful output based on job type
            if job_type == "embedding":
                output_data = {
                    "embedding_count": random.randint(10, 500),
                    "vector_dimension": random.choice([512, 768, 1024, 1536]),
                    "total_tokens": random.randint(1000, 50000),
                    "chunks_processed": random.randint(5, 100),
                    "success_rate": round(random.uniform(0.95, 1.0), 3),
                }

            elif job_type == "processing":
                output_data = {
                    "processed_size_bytes": random.randint(1024, 1048576),
                    "metadata_extracted": random.randint(10, 50),
                    "summary_length": random.randint(100, 500),
                    "language_detected": random.choice(["en", "es", "fr", "de", "it"]),
                    "quality_score": round(random.uniform(0.7, 1.0), 2),
                }

            elif job_type == "analysis":
                output_data = {
                    "sentiment_score": round(random.uniform(-1.0, 1.0), 3),
                    "topics_found": random.randint(3, 15),
                    "entities_extracted": random.randint(5, 30),
                    "keywords_count": random.randint(10, 50),
                    "confidence_avg": round(random.uniform(0.8, 0.95), 3),
                }

        elif status == "failed":
            # Generate realistic error messages
            error_types = {
                "embedding": [
                    "Model loading failed: CUDA out of memory",
                    "Content parsing error: Invalid UTF-8 encoding",
                    "API rate limit exceeded for embedding service",
                    "Timeout: Content too large for processing",
                    "Model inference failed: Input sequence too long",
                ],
                "processing": [
                    "File format not supported: Corrupted content",
                    "Memory allocation failed: Content size exceeds limits",
                    "Network timeout: Unable to fetch external resources",
                    "Validation error: Required metadata fields missing",
                    "Processing pipeline failed: Dependency service unavailable",
                ],
                "analysis": [
                    "Analysis model unavailable: Service temporarily down",
                    "Content language not supported by analysis engine",
                    "Insufficient content length for reliable analysis",
                    "Configuration error: Invalid analysis parameters",
                    "Resource exhaustion: Too many concurrent analysis jobs",
                ],
            }

            error_message = random.choice(error_types.get(job_type, ["Unknown error occurred"]))

        return output_data, error_message

    def _convert_dict_to_dynamodb_format(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a regular dictionary to DynamoDB attribute format.

        Args:
            data: Dictionary to convert

        Returns:
            Dictionary in DynamoDB attribute format
        """
        result = {}

        for key, value in data.items():
            # Check for boolean first (before string check) since bool is a subclass of int in Python
            if isinstance(value, bool):
                result[key] = {"BOOL": value}
            elif isinstance(value, str):
                # Handle string representations of booleans
                if value.lower() in ("true", "false"):
                    result[key] = {"BOOL": value.lower() == "true"}
                else:
                    result[key] = {"S": value}
            elif isinstance(value, int):
                result[key] = {"N": str(value)}
            elif isinstance(value, float):
                result[key] = {"N": str(value)}
            elif isinstance(value, list):
                if all(isinstance(item, str) for item in value):
                    result[key] = {"SS": value}
                elif all(isinstance(item, (int, float)) for item in value):
                    result[key] = {"NS": [str(item) for item in value]}
                else:
                    # Mixed list - convert to string representation
                    result[key] = {"S": str(value)}
            elif isinstance(value, dict):
                result[key] = {"M": self._convert_dict_to_dynamodb_format(value)}
            else:
                # Convert unknown types to string
                result[key] = {"S": str(value)}

        return result

    def generate_access_logs(self, partner_ids: List[str], content_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Generate realistic access log data with usage patterns.

        Args:
            partner_ids: List of partner IDs to distribute logs across
            content_ids: List of content IDs to reference in logs

        Returns:
            List of access log records matching DynamoDB table structure
        """
        logger.info(f"Generating {self.config.access_log_count} access log entries")

        if not partner_ids:
            logger.warning("No partner IDs provided for access log generation")
            return []

        fake = Faker()
        access_logs = []

        # Calculate date range for access logs (ensure end_date is in the past)
        end_date = datetime.now(timezone.utc) - timedelta(hours=1)  # At least 1 hour ago
        start_date = end_date - timedelta(days=self.config.date_range_months * 30)

        # API endpoints and their characteristics
        endpoints = {
            "/api/v1/content": {
                "method": "GET",
                "success_rate": 0.98,
                "avg_response_time_ms": 80,
                "requires_content": True,
                "billing_units": 2,
            },
            "/api/v1/query": {
                "method": "POST",
                "success_rate": 0.92,
                "avg_response_time_ms": 300,
                "requires_content": False,
                "billing_units": 3,
            },
            "/api/v1/analytics": {
                "method": "GET",
                "success_rate": 0.97,
                "avg_response_time_ms": 200,
                "requires_content": False,
                "billing_units": 1,
            },
            "/api/v1/bid": {
                "method": "POST",
                "success_rate": 0.89,
                "avg_response_time_ms": 120,
                "requires_content": True,
                "billing_units": 5,
            },
        }

        # Generate usage patterns with realistic distributions
        usage_patterns = self._generate_usage_patterns(start_date, end_date, self.config.access_log_count)

        for i, timestamp in enumerate(usage_patterns):
            # Generate unique log ID
            log_id = str(uuid.uuid4())

            # Select partner with weighted distribution (some partners more active)
            partner_id = self._select_weighted_partner(partner_ids, i)

            # Select endpoint based on realistic usage patterns
            endpoint = self._select_endpoint_by_usage_pattern(endpoints)
            endpoint_config = endpoints[endpoint]

            # Select content ID if endpoint requires it
            content_id = None
            if endpoint_config["requires_content"] and content_ids:
                content_id = random.choice(content_ids)

            # Generate response code based on endpoint success rate
            response_code = self._generate_response_code(endpoint_config["success_rate"])
            if endpoint == "/api/v1/query" and response_code < 400:
                response_code = 200

            # Generate response time with realistic variation
            response_time_ms = self._generate_response_time(endpoint_config["avg_response_time_ms"], response_code)

            # Generate request and response sizes
            request_size, response_size = self._generate_request_response_sizes(endpoint, response_code)

            # Generate IP address and user agent
            ip_address = fake.ipv4_public()
            user_agent = self._generate_realistic_user_agent()

            # Discovery parameters are a JSON body, not URL query parameters.
            query_params = self._generate_query_params(endpoint, content_id)
            request_body = None
            if endpoint == "/api/v1/query":
                request_body = query_params
                query_params = {}

            # Generate error details for failed requests
            error_details = ""
            if response_code >= 400:
                error_details = self._generate_error_details(response_code, endpoint)

            # Calculate billing units (may be 0 for failed requests)
            billing_units = endpoint_config["billing_units"] if response_code < 400 else 0

            # Create access log record matching DynamoDB schema
            access_log = {
                "log_id": {"S": log_id},
                "partner_id": {"S": partner_id},
                "timestamp": {"S": timestamp.isoformat()},
                "endpoint": {"S": endpoint},
                "method": {"S": endpoint_config["method"]},
                "response_code": {"N": str(response_code)},
                "response_time_ms": {"N": str(response_time_ms)},
                "request_size": {"N": str(request_size)},
                "response_size": {"N": str(response_size)},
                "ip_address": {"S": ip_address},
                "user_agent": {"S": user_agent},
                "billing_units": {"N": str(billing_units)},
                "query_params": {"M": self._convert_dict_to_dynamodb_format(query_params)},
                "metadata": {
                    "M": {
                        "request_id": {"S": f"req_{uuid.uuid4().hex[:16]}"},
                        "session_id": {"S": f"sess_{uuid.uuid4().hex[:12]}"},
                        "api_version": {"S": "v1"},
                        "client_type": {"S": self._determine_client_type(user_agent)},
                        "geographic_region": {"S": fake.country_code()},
                        "cache_hit": {"BOOL": endpoint != "/api/v1/query" and random.random() < 0.3},
                    }
                },
            }

            if request_body is not None:
                access_log["metadata"]["M"]["request_body"] = TypeSerializer().serialize(request_body)

            # Add content_id if present
            if content_id:
                access_log["content_id"] = {"S": content_id}

            # Add error_details if present
            if error_details:
                access_log["error_details"] = {"S": error_details}

            access_logs.append(access_log)

            if (i + 1) % 100 == 0:
                logger.debug(f"Generated {i + 1}/{self.config.access_log_count} access log entries")

        logger.info(f"Successfully generated {len(access_logs)} access log entries")
        return access_logs

    def _generate_usage_patterns(self, start_date: datetime, end_date: datetime, total_logs: int) -> List[datetime]:
        """
        Generate realistic usage patterns with peak hours and seasonal trends.

        Args:
            start_date: Start of the date range
            end_date: End of the date range
            total_logs: Total number of log entries to generate

        Returns:
            List of timestamps with realistic usage patterns
        """
        timestamps = []
        total_days = max(1, (end_date - start_date).days)

        # If we have very few days, just distribute evenly
        if total_days <= 1:
            # Generate all timestamps for a single day or short period
            for _ in range(total_logs):
                # Random time within the date range
                time_delta = (end_date - start_date).total_seconds()
                random_seconds = random.uniform(0, time_delta)
                timestamp = start_date + timedelta(seconds=random_seconds)
                timestamps.append(timestamp)
        else:
            # Generate base distribution across days
            logs_per_day = total_logs // total_days
            remaining_logs = total_logs % total_days

            current_date = start_date

            while current_date < end_date and len(timestamps) < total_logs:
                # Determine logs for this day (with some variation)
                base_logs = logs_per_day
                if remaining_logs > 0:
                    base_logs += 1
                    remaining_logs -= 1

                # Apply day-of-week multiplier (weekdays busier than weekends)
                day_multiplier = self._get_day_of_week_multiplier(current_date.weekday())
                daily_logs = max(1, int(base_logs * day_multiplier))

                # Generate timestamps for this day with peak hour patterns
                day_timestamps = self._generate_daily_timestamps(current_date, daily_logs)
                timestamps.extend(day_timestamps)

                current_date += timedelta(days=1)

        # Sort timestamps and ensure we have exactly the requested count
        timestamps.sort()

        # If we have too few, generate additional random timestamps
        while len(timestamps) < total_logs:
            time_delta = (end_date - start_date).total_seconds()
            random_seconds = random.uniform(0, time_delta)
            timestamp = start_date + timedelta(seconds=random_seconds)
            timestamps.append(timestamp)

        # If we have too many, trim to exact count
        timestamps.sort()
        return timestamps[:total_logs]

    def _get_day_of_week_multiplier(self, weekday: int) -> float:
        """
        Get usage multiplier based on day of week.

        Args:
            weekday: Day of week (0=Monday, 6=Sunday)

        Returns:
            Multiplier for usage on this day
        """
        # Monday=0, Sunday=6
        multipliers = {
            0: 1.2,  # Monday - high
            1: 1.3,  # Tuesday - highest
            2: 1.3,  # Wednesday - highest
            3: 1.2,  # Thursday - high
            4: 1.0,  # Friday - normal
            5: 0.7,  # Saturday - low
            6: 0.6,  # Sunday - lowest
        }
        return multipliers.get(weekday, 1.0)

    def _generate_daily_timestamps(self, date: datetime, count: int) -> List[datetime]:
        """
        Generate timestamps for a single day with peak hour patterns.

        Args:
            date: The date to generate timestamps for
            count: Number of timestamps to generate

        Returns:
            List of timestamps distributed across the day
        """
        timestamps = []

        # Define peak hours (business hours in different time zones)
        peak_hours = [
            (8, 12),  # Morning peak (8 AM - 12 PM)
            (13, 17),  # Afternoon peak (1 PM - 5 PM)
            (19, 22),  # Evening peak (7 PM - 10 PM)
        ]

        # Distribute logs across hours with peak patterns
        for _ in range(count):
            # 70% chance of peak hour, 30% chance of off-peak
            if random.random() < 0.7:
                # Select random peak period
                start_hour, end_hour = random.choice(peak_hours)
                hour = random.randint(start_hour, end_hour - 1)
            else:
                # Off-peak hours
                off_peak_hours = list(range(0, 24))
                for start, end in peak_hours:
                    for h in range(start, end):
                        if h in off_peak_hours:
                            off_peak_hours.remove(h)
                hour = random.choice(off_peak_hours) if off_peak_hours else random.randint(0, 23)

            # Generate random minute and second
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            microsecond = random.randint(0, 999999)

            timestamp = date.replace(hour=hour, minute=minute, second=second, microsecond=microsecond)
            timestamps.append(timestamp)

        return timestamps

    def _select_weighted_partner(self, partner_ids: List[str], index: int) -> str:
        """
        Select partner with weighted distribution (some partners more active).

        Args:
            partner_ids: List of available partner IDs
            index: Current log index for deterministic variation

        Returns:
            Selected partner ID
        """
        # Create weighted distribution - first few partners are more active
        weights = []
        for i, _ in enumerate(partner_ids):
            if i < len(partner_ids) // 3:
                weights.append(3)  # Top third - high activity
            elif i < 2 * len(partner_ids) // 3:
                weights.append(2)  # Middle third - medium activity
            else:
                weights.append(1)  # Bottom third - low activity

        return random.choices(partner_ids, weights=weights)[0]

    def _select_endpoint_by_usage_pattern(self, endpoints: Dict[str, Dict]) -> str:
        """
        Select endpoint based on realistic usage patterns.

        Args:
            endpoints: Dictionary of available endpoints

        Returns:
            Selected endpoint path
        """
        endpoint_list = list(endpoints.keys())
        weights = [self.API_ENDPOINT_WEIGHTS.get(ep, 1) for ep in endpoint_list]

        return random.choices(endpoint_list, weights=weights)[0]

    def _generate_response_code(self, success_rate: float) -> int:
        """
        Generate HTTP response code based on endpoint success rate.

        Args:
            success_rate: Success rate for the endpoint (0.0 to 1.0)

        Returns:
            HTTP response code
        """
        if random.random() < success_rate:
            # Success responses
            return random.choices([200, 201, 202, 204], weights=[85, 5, 5, 5])[0]
        else:
            # Error responses with realistic distribution
            return random.choices(
                [400, 401, 403, 404, 429, 500, 502, 503, 504], weights=[25, 20, 15, 15, 10, 5, 3, 4, 3]
            )[0]

    def _generate_response_time(self, avg_time_ms: int, response_code: int) -> int:
        """
        Generate response time with realistic variation.

        Args:
            avg_time_ms: Average response time for the endpoint
            response_code: HTTP response code

        Returns:
            Response time in milliseconds
        """
        # Error responses may take longer or shorter depending on error type
        if response_code >= 500:
            # Server errors - often timeout related, so longer
            multiplier = random.uniform(2.0, 5.0)
        elif response_code == 429:
            # Rate limiting - very fast response
            multiplier = random.uniform(0.1, 0.3)
        elif response_code in [401, 403]:
            # Auth errors - fast response
            multiplier = random.uniform(0.2, 0.5)
        elif response_code >= 400:
            # Client errors - normal to fast response
            multiplier = random.uniform(0.5, 1.2)
        else:
            # Success responses - normal variation
            multiplier = random.uniform(0.5, 2.0)

        # Add some random variation
        base_time = int(avg_time_ms * multiplier)
        variation = random.randint(-base_time // 4, base_time // 4)

        return max(1, base_time + variation)  # Minimum 1ms

    def _generate_request_response_sizes(self, endpoint: str, response_code: int) -> tuple[int, int]:
        """
        Generate realistic request and response sizes.

        Args:
            endpoint: API endpoint
            response_code: HTTP response code

        Returns:
            Tuple of (request_size, response_size) in bytes
        """
        # Base sizes by endpoint type
        base_sizes = {
            "/api/v1/content": {"request": 150, "response": 5000},
            "/api/v1/query": {"request": 800, "response": 2000},
            "/api/v1/analytics": {"request": 100, "response": 800},
            "/api/v1/bid": {"request": 300, "response": 400},
        }

        sizes = base_sizes.get(endpoint, {"request": 200, "response": 1000})

        # Generate request size with variation
        request_size = int(sizes["request"] * random.uniform(0.5, 2.0))

        # Response size depends on success/failure
        if response_code < 400:
            # Success - full response
            response_size = int(sizes["response"] * random.uniform(0.3, 3.0))
        else:
            # Error - smaller response (just error message)
            response_size = random.randint(50, 500)

        return request_size, response_size

    def _generate_realistic_user_agent(self) -> str:
        """
        Generate realistic user agent strings for API clients.

        Returns:
            User agent string
        """
        user_agents = [
            "Mozilla/5.0 (compatible; IAB-Client/1.0; +http://example.com/bot)",
            "Python/3.9 requests/2.28.1",
            "curl/7.68.0",
            "PostmanRuntime/7.29.2",
            "Apache-HttpClient/4.5.13 (Java/11.0.16)",
            "Go-http-client/1.1",
            "okhttp/4.9.3",
            "axios/0.27.2",
            "node-fetch/3.2.10",
            "RestSharp/108.0.1.0",
            "HTTPie/3.2.1",
            "Insomnia/2022.5.1",
        ]

        return random.choice(user_agents)

    def _generate_query_params(self, endpoint: str, content_id: str = None) -> Dict[str, Any]:
        """
        Generate example parameters (a JSON body for POST /query).

        Args:
            endpoint: API endpoint
            content_id: Content ID if applicable

        Returns:
            Dictionary of request parameters
        """
        params = {}

        if endpoint == "/api/v1/query":
            params = {
                "query": random.choice(
                    [
                        "artificial intelligence",
                        "machine learning",
                        "data science",
                        "business analytics",
                        "market trends",
                        "technology news",
                    ]
                ),
                "content_type": [random.choice(self.config.content_types)],
                "max_results": random.randint(1, 50),
            }

        elif endpoint == "/api/v1/content":
            params = {
                "format": random.choice(["json", "xml", "html"]),
                "include_metadata": random.choice([True, False]),
            }
            if content_id:
                params["id"] = content_id

        elif endpoint == "/api/v1/analytics":
            params = {
                "metric": random.choice(["views", "engagement", "revenue"]),
                "period": random.choice(["day", "week", "month"]),
                "aggregate": random.choice([True, False]),
            }

        elif endpoint == "/api/v1/bid":
            params = {
                "content_type": random.choice(["article", "video", "image"]),
                "max_bid": round(random.uniform(0.01, 1.00), 2),
                "currency": "USD",
            }

        return params

    def _generate_error_details(self, response_code: int, endpoint: str) -> str:
        """
        Generate realistic error details based on response code.

        Args:
            response_code: HTTP response code
            endpoint: API endpoint that failed

        Returns:
            Error details string
        """
        error_messages = {
            400: [
                "Invalid query parameter format",
                "Missing required field in request body",
                "Request payload exceeds maximum size limit",
            ],
            401: ["Invalid API key provided", "API key has expired", "Authentication header missing"],
            403: [
                "Insufficient permissions for this endpoint",
                "API key does not have access to requested resource",
                "Rate limit exceeded for current subscription tier",
            ],
            404: [
                "Requested content not found",
                "Endpoint not available",
                "Resource has been removed or is no longer accessible",
            ],
            429: [
                "Rate limit exceeded - too many requests",
                "Quota exhausted for current billing period",
                "Concurrent request limit reached",
            ],
            500: [
                "Internal server error occurred",
                "Database connection failed",
                "Unexpected error during request processing",
            ],
            502: [
                "Bad gateway - upstream service unavailable",
                "Load balancer configuration error",
                "Backend service returned invalid response",
            ],
            503: ["Service temporarily unavailable", "System maintenance in progress", "All worker processes are busy"],
            504: [
                "Gateway timeout - request took too long",
                "Upstream service did not respond in time",
                "Processing timeout exceeded",
            ],
        }

        messages = error_messages.get(response_code, ["Unknown error occurred"])
        return random.choice(messages)

    def _determine_client_type(self, user_agent: str) -> str:
        """
        Determine client type based on user agent string.

        Args:
            user_agent: User agent string

        Returns:
            Client type classification
        """
        user_agent_lower = user_agent.lower()

        if "python" in user_agent_lower or "requests" in user_agent_lower:
            return "python_client"
        elif "curl" in user_agent_lower:
            return "curl"
        elif "postman" in user_agent_lower:
            return "postman"
        elif "java" in user_agent_lower or "apache-httpclient" in user_agent_lower:
            return "java_client"
        elif "go-http-client" in user_agent_lower:
            return "go_client"
        elif "node" in user_agent_lower or "axios" in user_agent_lower:
            return "nodejs_client"
        elif "okhttp" in user_agent_lower:
            return "android_client"
        elif "mozilla" in user_agent_lower:
            return "browser_client"
        else:
            return "unknown_client"

    def validate_generated_data(
        self,
        content_records: List[Dict[str, Any]],
        partner_records: List[Dict[str, Any]],
        job_records: List[Dict[str, Any]],
        access_log_records: List[Dict[str, Any]],
        analytics_records: List[Dict[str, Any]] = None,
        bot_rules_records: List[Dict[str, Any]] = None,
        pricing_rules_records: List[Dict[str, Any]] = None,
        pricing_transactions_records: List[Dict[str, Any]] = None,
    ) -> Dict[str, ValidationResult]:
        """
        Validate all generated data for schema compliance and referential integrity.

        Args:
            content_records: Generated content records
            partner_records: Generated partner records
            job_records: Generated job records
            access_log_records: Generated access log records
            analytics_records: Generated analytics records (optional)
            bot_rules_records: Generated bot rules records (optional)
            pricing_rules_records: Generated pricing rules records (optional)
            pricing_transactions_records: Generated pricing transactions records (optional)

        Returns:
            Dictionary mapping table names to validation results
        """
        logger.info("Starting comprehensive data validation")

        schema_validator = SchemaValidator()
        integrity_validator = ReferentialIntegrityValidator()

        validation_results = {}

        # Validate schema compliance for each table
        logger.info("Validating schema compliance...")
        validation_results["content"] = schema_validator.validate_records(content_records, "content")
        validation_results["partners"] = schema_validator.validate_records(partner_records, "partners")
        validation_results["jobs"] = schema_validator.validate_records(job_records, "jobs")
        validation_results["access_logs"] = schema_validator.validate_records(access_log_records, "access_logs")

        # Validate analytics records if provided
        if analytics_records:
            validation_results["analytics"] = schema_validator.validate_records(analytics_records, "analytics")

        # Validate bot rules records if provided
        if bot_rules_records:
            validation_results["bot_rules"] = schema_validator.validate_records(bot_rules_records, "bot_rules")

        # Validate pricing rules records if provided
        if pricing_rules_records:
            validation_results["pricing_rules"] = schema_validator.validate_records(
                pricing_rules_records, "pricing_rules"
            )

        # Validate pricing transactions records if provided
        if pricing_transactions_records:
            validation_results["pricing_transactions"] = schema_validator.validate_records(
                pricing_transactions_records, "pricing_transactions"
            )

        # Validate referential integrity
        logger.info("Validating referential integrity...")
        validation_results["referential_integrity"] = integrity_validator.validate_referential_integrity(
            content_records,
            partner_records,
            job_records,
            access_log_records,
            analytics_records,
            bot_rules_records,
            pricing_rules_records,
            pricing_transactions_records,
        )

        # Generate and log validation report
        reporter = ValidationReporter()
        reporter.log_validation_summary(validation_results)

        # Generate detailed report if there are errors
        total_errors = sum(len(result.errors) for result in validation_results.values())
        if total_errors > 0:
            detailed_report = reporter.generate_report(validation_results)
            logger.error("Validation failed. Detailed report:")
            for line in detailed_report.split("\n"):
                logger.error(line)

        logger.info("Data validation complete")
        return validation_results

    def validate_single_record(self, record: Dict[str, Any], table_type: str) -> ValidationResult:
        """
        Validate a single record against its table schema.

        Args:
            record: The record to validate
            table_type: Type of table ('content', 'partners', 'jobs', 'access_logs')

        Returns:
            ValidationResult for the record
        """
        schema_validator = SchemaValidator()
        return schema_validator.validate_record(record, table_type)

    def generate_analytics_data(
        self, content_ids: List[str], partner_ids: List[str], bot_rules_records: List[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Generate realistic analytics metrics and bot access tracking data.

        Args:
            content_ids: List of content IDs for referential integrity
            partner_ids: List of partner IDs for referential integrity
            bot_rules_records: List of bot rules to generate analytics that conform to these rules

        Returns:
            List of analytics records matching DynamoDB table structure
        """
        logger.info(f"Generating analytics data for {self.config.date_range_months} months")

        fake = Faker()
        analytics_records = []

        # Calculate date range for analytics data
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.config.date_range_months * 30)

        # Generate daily analytics data
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")

            # Generate various metric types for each day
            daily_records = []

            # API request metrics
            daily_records.extend(self._generate_api_request_metrics(date_str, fake))

            # Content access metrics
            daily_records.extend(self._generate_content_access_metrics(date_str, content_ids, fake))

            # Bot access metrics
            daily_records.extend(
                self._generate_bot_access_metrics(date_str, content_ids, partner_ids, fake, bot_rules_records)
            )

            # MCP server request metrics
            daily_records.extend(self._generate_mcp_request_metrics(date_str, content_ids, fake))

            # Revenue metrics
            daily_records.extend(self._generate_revenue_metrics(date_str, fake))

            # Search query metrics
            daily_records.extend(self._generate_search_query_metrics(date_str, content_ids, fake))

            analytics_records.extend(daily_records)
            current_date += timedelta(days=1)

        logger.info(f"Generated {len(analytics_records)} analytics records")
        return analytics_records

    def get_existing_bot_rules(self) -> List[Dict[str, Any]]:
        """
        Retrieve existing bot rules from the deployed DynamoDB table.

        Returns:
            List of existing bot rules records, empty list if none found or table doesn't exist
        """
        try:
            table_name = self.TABLE_NAMES["bot_rules"]

            # Check if table exists and is accessible
            if not self.validate_table_access(table_name):
                logger.info(f"Bot rules table {table_name} not accessible, will generate mock data")
                return []

            # Scan the table to get existing rules
            response = self.dynamodb_client.scan(TableName=table_name, Select="ALL_ATTRIBUTES")

            existing_rules = response.get("Items", [])

            # Handle pagination if needed
            while "LastEvaluatedKey" in response:
                response = self.dynamodb_client.scan(
                    TableName=table_name, Select="ALL_ATTRIBUTES", ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                existing_rules.extend(response.get("Items", []))

            logger.info(f"Retrieved {len(existing_rules)} existing bot rules from {table_name}")
            return existing_rules

        except Exception as e:
            logger.warning(f"Failed to retrieve existing bot rules: {e}")
            return []

    def generate_bot_rules_data(self) -> List[Dict[str, Any]]:
        """
        Generate realistic bot control rules for WAF synchronization.

        Returns:
            List of bot rules records matching DynamoDB table structure
        """
        logger.info("Generating bot rules data")

        fake = Faker()
        bot_rules_records = []

        # Generate a reasonable number of bot rules (10-20)
        num_rules = random.randint(10, 20)

        # Common bot patterns and rule types
        bot_patterns = [
            {"name": "Block Malicious Crawlers", "pattern": "user-agent", "value": "BadBot|MaliciousCrawler|SpamBot"},
            {"name": "Rate Limit Search Bots", "pattern": "user-agent", "value": "Googlebot|Bingbot|YandexBot"},
            {"name": "Block Scraping Tools", "pattern": "user-agent", "value": "wget|curl|scrapy|python-requests"},
            {"name": "Geographic Restriction", "pattern": "geo", "value": "CN|RU|KP"},
            {"name": "IP Range Block", "pattern": "ip", "value": "192.168.1.0/24"},
            {"name": "Suspicious User Agents", "pattern": "user-agent", "value": "Mozilla/4.0|Mozilla/3.0"},
            {"name": "API Abuse Prevention", "pattern": "rate-limit", "value": "100/hour"},
            {"name": "Content Scraping Block", "pattern": "path", "value": "/api/v1/content/bulk"},
            {"name": "Bot Detection Bypass", "pattern": "header", "value": "X-Forwarded-For"},
            {"name": "Automated Tool Block", "pattern": "user-agent", "value": "HeadlessChrome|PhantomJS"},
        ]

        actions = ["block", "allow", "count"]
        statuses = ["active", "inactive"]
        sync_statuses = ["synced", "pending", "error"]

        for i in range(num_rules):
            rule_id = f"rule_{i + 1:03d}"

            # Select a bot pattern
            pattern = random.choice(bot_patterns)

            # Generate timestamps
            created_at = fake.date_time_between(start_date="-6M", end_date="now")
            updated_at = fake.date_time_between(start_date=created_at, end_date="now")

            # Determine sync status and related fields
            sync_status = random.choice(sync_statuses)
            waf_rule_id = None
            last_synced = None

            if sync_status == "synced":
                waf_rule_id = str(uuid.uuid4())
                last_synced = fake.date_time_between(start_date=updated_at, end_date="now")
            elif sync_status == "pending" and random.random() > 0.5:
                # Some pending rules might have WAF IDs from previous sync attempts
                waf_rule_id = str(uuid.uuid4())

            # Generate WAF rule conditions based on pattern type
            conditions = self._generate_waf_conditions(pattern, fake)

            # Generate rule priority (1-1000, with some clustering around common values)
            if random.random() < 0.3:  # 30% high priority
                priority = random.randint(1, 100)
            elif random.random() < 0.6:  # 60% medium priority
                priority = random.randint(101, 500)
            else:  # 10% low priority
                priority = random.randint(501, 1000)

            # Ensure unique priorities for active rules
            while any(
                r.get("priority", {}).get("N") == str(priority) and r.get("status", {}).get("S") == "active"
                for r in bot_rules_records
            ):
                priority = random.randint(1, 1000)

            record = {
                "rule_id": {"S": rule_id},
                "rule_name": {"S": pattern["name"]},
                "description": {
                    "S": f"Automatically generated rule to {pattern['name'].lower()} based on {pattern['pattern']} matching"
                },
                "action": {"S": random.choice(actions)},
                "priority": {"N": str(priority)},
                "conditions": {"M": conditions},
                "status": {"S": random.choice(statuses)},
                "created_at": {"S": created_at.isoformat() + "Z"},
                "updated_at": {"S": updated_at.isoformat() + "Z"},
                "sync_status": {"S": sync_status},
                "metadata": {
                    "M": {
                        "rule_type": {"S": pattern["pattern"]},
                        "auto_generated": {"BOOL": True},
                        "last_triggered": {
                            "S": fake.date_time_between(start_date="-1M", end_date="now").isoformat() + "Z"
                        },
                        "trigger_count": {"N": str(random.randint(0, 1000))},
                        "effectiveness_score": {"N": str(round(random.uniform(0.1, 1.0), 2))},
                    }
                },
            }

            # Add optional fields
            if waf_rule_id:
                record["waf_rule_id"] = {"S": waf_rule_id}

            if last_synced:
                record["last_synced"] = {"S": last_synced.isoformat() + "Z"}

            bot_rules_records.append(record)

        logger.info(f"Generated {len(bot_rules_records)} bot rules records")
        return bot_rules_records

    def _generate_waf_conditions(self, pattern: Dict[str, str], fake: Faker) -> Dict[str, Any]:
        """
        Generate WAF rule conditions based on pattern type.

        Args:
            pattern: Dictionary with pattern type and configuration
            fake: Faker instance for generating realistic data

        Returns:
            DynamoDB-formatted conditions map
        """
        conditions = {}

        if pattern["pattern"] == "user-agent":
            conditions = {
                "field_to_match": {"M": {"type": {"S": "SINGLE_HEADER"}, "data": {"S": "user-agent"}}},
                "text_transformation": {"S": "LOWERCASE"},
                "comparison_operator": {"S": "CONTAINS"},
                "target_string": {"S": pattern["value"]},
                "negated": {"BOOL": False},
            }
        elif pattern["pattern"] == "geo":
            conditions = {
                "field_to_match": {"M": {"type": {"S": "GEO_MATCH"}, "data": {"S": "country_code"}}},
                "text_transformation": {"S": "NONE"},
                "comparison_operator": {"S": "EQ"},
                "target_string": {"S": pattern["value"]},
                "negated": {"BOOL": False},
            }
        elif pattern["pattern"] == "ip":
            conditions = {
                "field_to_match": {"M": {"type": {"S": "IP"}, "data": {"S": "source_ip"}}},
                "text_transformation": {"S": "NONE"},
                "comparison_operator": {"S": "IPV4_MATCH"},
                "target_string": {"S": pattern["value"]},
                "negated": {"BOOL": False},
            }
        elif pattern["pattern"] == "path":
            conditions = {
                "field_to_match": {"M": {"type": {"S": "URI"}, "data": {"S": "path"}}},
                "text_transformation": {"S": "LOWERCASE"},
                "comparison_operator": {"S": "STARTS_WITH"},
                "target_string": {"S": pattern["value"]},
                "negated": {"BOOL": False},
            }
        elif pattern["pattern"] == "rate-limit":
            # Rate limiting conditions
            rate_parts = pattern["value"].split("/")
            limit = rate_parts[0] if len(rate_parts) > 0 else "100"
            period = rate_parts[1] if len(rate_parts) > 1 else "hour"

            conditions = {
                "rate_limit": {"M": {"limit": {"N": limit}, "period": {"S": period}, "aggregation_key": {"S": "IP"}}},
                "action_on_limit": {"S": "BLOCK"},
                "scope": {"S": "CLOUDFRONT"},
            }
        elif pattern["pattern"] == "header":
            conditions = {
                "field_to_match": {"M": {"type": {"S": "SINGLE_HEADER"}, "data": {"S": pattern["value"].lower()}}},
                "text_transformation": {"S": "NONE"},
                "comparison_operator": {"S": "EXISTS"},
                "negated": {"BOOL": True},  # Block if header doesn't exist
            }
        else:
            # Default condition for unknown patterns
            conditions = {
                "field_to_match": {"M": {"type": {"S": "SINGLE_HEADER"}, "data": {"S": "user-agent"}}},
                "text_transformation": {"S": "LOWERCASE"},
                "comparison_operator": {"S": "CONTAINS"},
                "target_string": {"S": "bot"},
                "negated": {"BOOL": False},
            }

        return conditions

    def generate_pricing_rules_data(self) -> List[Dict[str, Any]]:
        """
        Generate realistic pricing rules with versioning and effective dates.
        Includes IAB category multipliers based on actual taxonomy data.

        Returns:
            List of pricing rules records matching DynamoDB table structure
        """
        logger.info("Generating pricing rules data with IAB taxonomy integration")

        fake = Faker()
        pricing_rules_records = []

        # Get sample category IDs from taxonomy for multipliers
        sample_category_multipliers = self._get_sample_category_multipliers()

        # Generate pricing rule versions for different time periods
        rule_sets = [
            {
                "rule_set_id": "default-pricing",
                "versions": [
                    {
                        "effective_date": "2024-01-01",
                        "status": "inactive",
                        "pricing_rules": {
                            "per_access": {
                                "article": "0.03",
                                "report": "0.15",
                                "video": "0.08",
                                "image": "0.01",
                                "document": "0.05",
                            },
                            "multipliers": {
                                "iab_category": sample_category_multipliers["2024-01-01"],
                                "recency": {"last_24h": "1.5", "last_week": "1.2", "last_month": "1.0", "older": "0.8"},
                                "demand": {"high": "1.3", "medium": "1.0", "low": "0.8"},
                            },
                            "mcp_tools": {"search_content": "0.01", "get_content": "0.03", "list_categories": "0.005"},
                        },
                        "change_reason": "Initial pricing structure with IAB category multipliers",
                    },
                    {
                        "effective_date": "2024-06-01",
                        "status": "inactive",
                        "pricing_rules": {
                            "per_access": {
                                "article": "0.04",
                                "report": "0.20",
                                "video": "0.10",
                                "image": "0.015",
                                "document": "0.06",
                            },
                            "multipliers": {
                                "iab_category": sample_category_multipliers["2024-06-01"],
                                "recency": {"last_24h": "1.6", "last_week": "1.3", "last_month": "1.0", "older": "0.7"},
                                "demand": {"high": "1.4", "medium": "1.0", "low": "0.7"},
                            },
                            "mcp_tools": {"search_content": "0.015", "get_content": "0.04", "list_categories": "0.008"},
                        },
                        "change_reason": "Mid-year pricing adjustment with updated category multipliers",
                    },
                    {
                        "effective_date": "2025-01-01",
                        "status": "active",
                        "pricing_rules": {
                            "per_access": {
                                "article": "0.05",
                                "report": "0.25",
                                "video": "0.12",
                                "image": "0.02",
                                "document": "0.08",
                            },
                            "multipliers": {
                                "iab_category": sample_category_multipliers["2025-01-01"],
                                "recency": {"last_24h": "1.5", "last_week": "1.2", "last_month": "1.0", "older": "0.8"},
                                "demand": {"high": "1.3", "medium": "1.0", "low": "0.8"},
                            },
                            "mcp_tools": {"search_content": "0.02", "get_content": "0.05", "list_categories": "0.01"},
                        },
                        "change_reason": "Annual pricing update with enhanced category-based pricing",
                    },
                ],
            },
            {
                "rule_set_id": "premium-pricing",
                "versions": [
                    {
                        "effective_date": "2024-03-01",
                        "status": "inactive",
                        "pricing_rules": {
                            "per_access": {
                                "article": "0.08",
                                "report": "0.40",
                                "video": "0.20",
                                "image": "0.03",
                                "document": "0.12",
                            },
                            "multipliers": {
                                "iab_category": sample_category_multipliers["premium"],
                                "recency": {"last_24h": "2.0", "last_week": "1.5", "last_month": "1.2", "older": "1.0"},
                                "demand": {"high": "1.8", "medium": "1.3", "low": "1.0"},
                            },
                            "mcp_tools": {"search_content": "0.03", "get_content": "0.08", "list_categories": "0.015"},
                        },
                        "change_reason": "Premium tier introduction with enhanced category pricing",
                    },
                    {
                        "effective_date": "2025-01-01",
                        "status": "active",
                        "pricing_rules": {
                            "per_access": {
                                "article": "0.10",
                                "report": "0.50",
                                "video": "0.25",
                                "image": "0.04",
                                "document": "0.15",
                            },
                            "multipliers": {
                                "iab_category": sample_category_multipliers["premium-2025"],
                                "recency": {"last_24h": "2.2", "last_week": "1.6", "last_month": "1.3", "older": "1.0"},
                                "demand": {"high": "2.0", "medium": "1.4", "low": "1.1"},
                            },
                            "mcp_tools": {"search_content": "0.04", "get_content": "0.10", "list_categories": "0.02"},
                        },
                        "change_reason": "Premium tier pricing update with refined category multipliers",
                    },
                ],
            },
        ]

        version_counter = 1

        for rule_set in rule_sets:
            for version_data in rule_set["versions"]:
                # Generate version timestamp
                effective_date = datetime.strptime(version_data["effective_date"], "%Y-%m-%d")
                version_timestamp = effective_date.replace(hour=0, minute=0, second=0, microsecond=0)

                # Generate created timestamp (slightly before effective date)
                created_at = fake.date_time_between(
                    start_date=effective_date - timedelta(days=30), end_date=effective_date - timedelta(days=1)
                )

                record = {
                    "rule_set_id": {"S": rule_set["rule_set_id"]},
                    "version_timestamp": {"S": version_timestamp.isoformat() + "Z"},
                    "effective_date": {"S": version_data["effective_date"]},
                    "status": {"S": version_data["status"]},
                    "created_by": {"S": fake.email()},
                    "created_at": {"S": created_at.isoformat() + "Z"},
                    "pricing_rules": {"M": self._convert_pricing_rules_to_dynamodb(version_data["pricing_rules"])},
                    "audit_trail": {
                        "M": {
                            "version": {"N": str(version_counter)},
                            "change_reason": {"S": version_data["change_reason"]},
                            "previous_version": {"S": "" if version_counter == 1 else f"v{version_counter - 1}"},
                        }
                    },
                }

                # Add activation timestamp for active rules
                if version_data["status"] == "active":
                    activated_at = fake.date_time_between(
                        start_date=effective_date, end_date=effective_date + timedelta(days=1)
                    )
                    record["activated_at"] = {"S": activated_at.isoformat() + "Z"}

                pricing_rules_records.append(record)
                version_counter += 1

        logger.info(f"Generated {len(pricing_rules_records)} pricing rules records")
        return pricing_rules_records

    def generate_pricing_transactions_data(
        self, content_ids: List[str], partner_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Generate realistic pricing transaction records for historical cost tracking.

        Args:
            content_ids: List of content IDs for referential integrity
            partner_ids: List of partner IDs for referential integrity

        Returns:
            List of pricing transaction records matching DynamoDB table structure
        """
        logger.info("Generating pricing transaction data")

        fake = Faker()
        pricing_transactions_records = []

        # Generate transactions over the date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=self.config.date_range_months * 30)

        # Generate a reasonable number of transactions (based on access logs)
        num_transactions = min(self.config.access_log_count, 2000)

        content_types = ["article", "report", "video", "image", "document"]
        access_types = ["preview", "full", "download"]

        for i in range(num_transactions):
            # Generate transaction timestamp (timezone-naive)
            transaction_timestamp_naive = fake.date_time_between(start_date=start_date, end_date=end_date)
            # Convert to timezone-aware
            transaction_timestamp = transaction_timestamp_naive.replace(tzinfo=timezone.utc)
            transaction_date = transaction_timestamp.strftime("%Y-%m-%d")

            # Select random content and partner
            content_id = random.choice(content_ids)
            partner_id = random.choice(partner_ids)
            content_type = random.choice(content_types)
            access_type = random.choice(access_types)

            # Generate transaction ID
            transaction_id = f"txn_{transaction_timestamp.strftime('%Y%m%d')}_{i + 1:06d}"

            # Determine pricing version based on transaction date
            if transaction_timestamp < datetime(2024, 6, 1, tzinfo=timezone.utc):
                pricing_version = "default-pricing#2024-01-01T00:00:00Z"
                base_cost = self._get_historical_cost(content_type, access_type, "2024-01-01")
            elif transaction_timestamp < datetime(2025, 1, 1, tzinfo=timezone.utc):
                pricing_version = "default-pricing#2024-06-01T00:00:00Z"
                base_cost = self._get_historical_cost(content_type, access_type, "2024-06-01")
            else:
                pricing_version = "default-pricing#2025-01-01T00:00:00Z"
                base_cost = self._get_historical_cost(content_type, access_type, "2025-01-01")

            # Apply multiplier for access type
            multiplier = 1.0
            if access_type == "preview":
                multiplier = 0.1
            elif access_type == "download":
                multiplier = 1.5

            final_cost = base_cost * multiplier

            # Generate cost calculation details
            cost_calculation = {
                "base_cost": {"N": str(base_cost)},
                "multiplier": {"N": str(multiplier)},
                "final_cost": {"N": str(final_cost)},
                "currency": {"S": "USD"},
                "pricing_version": {"S": pricing_version},
                "calculation_timestamp": {"S": transaction_timestamp.isoformat() + "Z"},
            }

            # Generate metadata
            metadata = {
                "user_agent": {"S": fake.user_agent()},
                "ip_address": {"S": fake.ipv4()},
                "request_id": {"S": str(uuid.uuid4())},
                "api_version": {"S": random.choice(["v1", "v2"])},
                "response_time_ms": {"N": str(random.randint(50, 500))},
            }

            record = {
                "transaction_date": {"S": transaction_date},
                "transaction_id": {"S": transaction_id},
                "transaction_timestamp": {"S": transaction_timestamp.isoformat().replace("+00:00", "Z")},
                "content_id": {"S": content_id},
                "partner_id": {"S": partner_id},
                "access_type": {"S": access_type},
                "content_type": {"S": content_type},
                "pricing_rule_version": {"S": pricing_version},
                "cost_calculation": {"M": cost_calculation},
                "metadata": {"M": metadata},
            }

            pricing_transactions_records.append(record)

        logger.info(f"Generated {len(pricing_transactions_records)} pricing transaction records")
        return pricing_transactions_records

    def _get_sample_category_multipliers(self) -> Dict[str, Dict[str, str]]:
        """
        Generate sample IAB category multipliers using actual taxonomy data.

        Returns:
            Dictionary mapping time periods to category multipliers
        """
        logger.info("Generating IAB category multipliers from taxonomy data")

        # Try to load actual taxonomy data
        try:
            # Get the project root directory
            project_root = os.path.dirname(os.path.dirname(__file__))
            taxonomy_file = os.path.join(project_root, "assets", "iab-content-taxonomy.tsv")

            category_multipliers = {}

            if os.path.exists(taxonomy_file):
                # Parse TSV file to get category IDs
                with open(taxonomy_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                    # Skip header lines
                    data_lines = lines[2:] if len(lines) > 2 else lines[1:]

                    # Collect category IDs from high-value categories
                    high_value_categories = []
                    medium_value_categories = []
                    standard_categories = []

                    for line in data_lines:
                        parts = line.strip().split("\t")
                        if len(parts) < 4:
                            continue

                        category_id = parts[0].strip()
                        tier1 = parts[3].strip().lower() if len(parts) > 3 else ""

                        if not category_id or not tier1:
                            continue

                        # Categorize based on tier1 content for pricing
                        if any(
                            keyword in tier1
                            for keyword in ["medical", "health", "finance", "business", "law", "government"]
                        ):
                            high_value_categories.append(category_id)
                        elif any(keyword in tier1 for keyword in ["technology", "science", "education", "careers"]):
                            medium_value_categories.append(category_id)
                        else:
                            standard_categories.append(category_id)

                    # Limit to reasonable number of categories for pricing rules
                    high_value_sample = high_value_categories[:8]
                    medium_value_sample = medium_value_categories[:10]
                    standard_sample = standard_categories[:12]

                    logger.info(
                        f"Using {len(high_value_sample)} high-value, {len(medium_value_sample)} medium-value, and {len(standard_sample)} standard categories"
                    )

            else:
                logger.warning("Taxonomy file not found, using fallback category IDs")
                # Fallback to known category IDs
                high_value_sample = ["111", "52", "158", "159"]  # Medical, Business, Law, Government
                medium_value_sample = ["126", "127", "128", "129", "130"]  # Technology categories
                standard_sample = ["1", "150", "151", "152", "153", "154"]  # Automotive, Travel, etc.

            # Generate multipliers for different time periods
            category_multipliers = {
                "2024-01-01": {},
                "2024-06-01": {},
                "2025-01-01": {},
                "premium": {},
                "premium-2025": {},
            }

            # 2024-01-01 multipliers (conservative)
            for cat_id in high_value_sample:
                category_multipliers["2024-01-01"][cat_id] = "1.2"
            for cat_id in medium_value_sample:
                category_multipliers["2024-01-01"][cat_id] = "1.1"
            for cat_id in standard_sample:
                category_multipliers["2024-01-01"][cat_id] = "1.0"

            # 2024-06-01 multipliers (slight increase)
            for cat_id in high_value_sample:
                category_multipliers["2024-06-01"][cat_id] = "1.3"
            for cat_id in medium_value_sample:
                category_multipliers["2024-06-01"][cat_id] = "1.15"
            for cat_id in standard_sample:
                category_multipliers["2024-06-01"][cat_id] = "1.0"

            # 2025-01-01 multipliers (current)
            for cat_id in high_value_sample:
                category_multipliers["2025-01-01"][cat_id] = "1.25"
            for cat_id in medium_value_sample:
                category_multipliers["2025-01-01"][cat_id] = "1.1"
            for cat_id in standard_sample:
                category_multipliers["2025-01-01"][cat_id] = "1.0"

            # Premium tier multipliers
            for cat_id in high_value_sample:
                category_multipliers["premium"][cat_id] = "1.5"
            for cat_id in medium_value_sample:
                category_multipliers["premium"][cat_id] = "1.3"
            for cat_id in standard_sample:
                category_multipliers["premium"][cat_id] = "1.2"

            # Premium 2025 multipliers
            for cat_id in high_value_sample:
                category_multipliers["premium-2025"][cat_id] = "1.6"
            for cat_id in medium_value_sample:
                category_multipliers["premium-2025"][cat_id] = "1.35"
            for cat_id in standard_sample:
                category_multipliers["premium-2025"][cat_id] = "1.25"

            logger.info(f"Generated category multipliers for {len(category_multipliers)} time periods")
            return category_multipliers

        except Exception as e:
            logger.error(f"Failed to generate category multipliers from taxonomy: {e}")
            # Fallback to hardcoded multipliers
            return {
                "2024-01-01": {"111": "1.2", "126": "1.1", "52": "1.3", "1": "1.0", "150": "1.0"},
                "2024-06-01": {"111": "1.3", "126": "1.15", "52": "1.35", "1": "1.0", "150": "1.0"},
                "2025-01-01": {"111": "1.25", "126": "1.1", "52": "1.3", "1": "1.0", "150": "1.0"},
                "premium": {"111": "1.5", "126": "1.3", "52": "1.6", "1": "1.2", "150": "1.2"},
                "premium-2025": {"111": "1.6", "126": "1.35", "52": "1.7", "1": "1.25", "150": "1.25"},
            }

    def _convert_pricing_rules_to_dynamodb(self, pricing_rules: Dict[str, Any]) -> Dict[str, Any]:
        """Convert pricing rules to DynamoDB format with support for nested multipliers."""
        dynamodb_rules = {}

        for category, rules in pricing_rules.items():
            if isinstance(rules, dict):
                dynamodb_rules[category] = {"M": {}}
                for key, value in rules.items():
                    if isinstance(value, dict):
                        # Handle nested dictionaries (like multipliers)
                        dynamodb_rules[category]["M"][key] = {"M": {}}
                        for nested_key, nested_value in value.items():
                            if isinstance(nested_value, dict):
                                # Handle double-nested dictionaries (like iab_category within multipliers)
                                dynamodb_rules[category]["M"][key]["M"][nested_key] = {"M": {}}
                                for deep_key, deep_value in nested_value.items():
                                    dynamodb_rules[category]["M"][key]["M"][nested_key]["M"][deep_key] = {
                                        "S": str(deep_value)
                                    }
                            else:
                                dynamodb_rules[category]["M"][key]["M"][nested_key] = {"S": str(nested_value)}
                    else:
                        dynamodb_rules[category]["M"][key] = {"S": str(value)}
            else:
                dynamodb_rules[category] = {"S": str(rules)}

        return dynamodb_rules

    def _get_historical_cost(self, content_type: str, access_type: str, effective_date: str) -> float:
        """Get historical cost based on content type, access type, and effective date."""
        # Historical pricing data
        pricing_history = {
            "2024-01-01": {"article": 0.03, "report": 0.15, "video": 0.08, "image": 0.01, "document": 0.05},
            "2024-06-01": {"article": 0.04, "report": 0.20, "video": 0.10, "image": 0.015, "document": 0.06},
            "2025-01-01": {"article": 0.05, "report": 0.25, "video": 0.12, "image": 0.02, "document": 0.08},
        }

        return pricing_history.get(effective_date, pricing_history["2025-01-01"]).get(content_type, 0.05)

    def _generate_api_request_metrics(self, date_str: str, fake: Faker) -> List[Dict[str, Any]]:
        """Generate API request metrics for a specific date."""
        records = []

        # Generate only a few API request counts per day (not hourly)
        for hour in range(0, 24, 8):  # Only 3 records per day instead of 24
            timestamp = f"{date_str}T{hour:02d}:00:00Z"

            # Simulate realistic API traffic patterns (higher during business hours)
            base_requests = 50
            if 8 <= hour <= 18:  # Business hours
                base_requests = random.randint(100, 300)
            elif 19 <= hour <= 23:  # Evening
                base_requests = random.randint(50, 150)
            else:  # Night
                base_requests = random.randint(10, 50)

            endpoint_counts = {
                endpoint: int(base_requests * weight / 100)
                for endpoint, weight in self.API_ENDPOINT_WEIGHTS.items()
            }
            # Keep the aggregate total exact after integer rounding.
            endpoint_counts["/api/v1/bid"] += base_requests - sum(endpoint_counts.values())

            record = {
                "metric_date": {"S": date_str},
                "metric_type_timestamp": {"S": f"api_requests_{timestamp}"},
                "metric_type": {"S": "api_requests"},
                "timestamp": {"S": timestamp},
                "value": {"N": str(base_requests)},
                "metadata": {
                    "M": {
                        "endpoint_breakdown": {
                            "M": {endpoint: {"N": str(count)} for endpoint, count in endpoint_counts.items()}
                        },
                        "response_time_avg_ms": {"N": str(random.randint(50, 200))},
                        "error_rate": {"N": str(round(random.uniform(0.01, 0.05), 3))},
                    }
                },
            }
            records.append(record)

        return records

    def _generate_content_access_metrics(
        self, date_str: str, content_ids: List[str], fake: Faker
    ) -> List[Dict[str, Any]]:
        """Generate content access metrics for a specific date."""
        records = []

        # Generate fewer content access events per day
        num_access_events = random.randint(2, 5)  # Much fewer events per day

        for _ in range(num_access_events):
            hour = random.randint(0, 23)
            minute = random.randint(0, 59)
            timestamp = f"{date_str}T{hour:02d}:{minute:02d}:00Z"

            content_id = random.choice(content_ids)
            access_count = random.randint(1, 10)

            record = {
                "metric_date": {"S": date_str},
                "metric_type_timestamp": {"S": f"content_access_{timestamp}_{content_id}_{_}"},
                "metric_type": {"S": "content_access"},
                "timestamp": {"S": timestamp},
                "value": {"N": str(access_count)},
                "metadata": {
                    "M": {
                        "content_id": {"S": content_id},
                        "access_method": {"S": random.choice(["direct_api", "mcp_server", "ui_browse"])},
                        "user_type": {"S": random.choice(["bot", "human", "mcp_agent"])},
                        "response_size_bytes": {"N": str(random.randint(1024, 50000))},
                        "cache_hit": {"BOOL": random.choice([True, False])},
                    }
                },
            }
            records.append(record)

        return records

    def _generate_bot_access_metrics(
        self,
        date_str: str,
        content_ids: List[str],
        partner_ids: List[str],
        fake: Faker,
        bot_rules_records: List[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate bot access tracking metrics for a specific date that conform to existing bot rules."""
        records = []

        # Analyze bot rules to understand what should be blocked/allowed
        blocked_patterns = []
        allowed_patterns = []
        rate_limits = {}

        if bot_rules_records:
            for rule in bot_rules_records:
                if rule.get("status", {}).get("S") == "active":
                    action = rule.get("action", {}).get("S", "allow")
                    rule_name = rule.get("rule_name", {}).get("S", "Unknown Rule")

                    if action == "block":
                        blocked_patterns.append(rule_name)
                    elif action == "allow":
                        allowed_patterns.append(rule_name)
                    elif action == "count":
                        # Count rules typically indicate rate limiting
                        priority = int(rule.get("priority", {}).get("N", "500"))
                        rate_limits[rule_name] = priority

        # Generate fewer bot access events per day
        base_bot_events = random.randint(2, 8)  # Much fewer events per day

        bot_types = [
            "openai-gpt",
            "claude-3-sonnet",
            "bedrock-agent",
            "custom-bot",
            "research-assistant",
            "content-analyzer",
            "data-scraper",
            "googlebot",
            "bingbot",
        ]

        successful_events = 0
        blocked_events = 0

        for i in range(base_bot_events):
            hour = random.randint(0, 23)
            minute = random.randint(0, 59)
            timestamp = f"{date_str}T{hour:02d}:{minute:02d}:00Z"

            bot_id = random.choice(bot_types)
            partner_id = random.choice(partner_ids)

            # Determine if this request should be blocked based on rules
            is_blocked = False
            blocking_rule = None

            if bot_rules_records:
                # Simple pattern matching based on bot type and rule names
                for pattern in blocked_patterns:
                    if (
                        ("crawler" in pattern.lower() and "bot" in bot_id.lower())
                        or ("scraping" in pattern.lower() and "scraper" in bot_id.lower())
                        or ("malicious" in pattern.lower() and random.random() < 0.1)
                    ):  # 10% chance of malicious detection
                        is_blocked = True
                        blocking_rule = pattern
                        break

            if is_blocked:
                blocked_events += 1
                # Generate blocked access metric
                record = {
                    "metric_date": {"S": date_str},
                    "metric_type_timestamp": {"S": f"bot_access_blocked_{timestamp}_{bot_id}_{i}"},
                    "metric_type": {"S": "bot_access"},
                    "timestamp": {"S": timestamp},
                    "value": {"N": "0"},  # 0 content accessed due to block
                    "metadata": {
                        "M": {
                            "bot_id": {"S": bot_id},
                            "partner_id": {"S": partner_id},
                            "access_method": {"S": random.choice(["direct_api", "mcp_server"])},
                            "status": {"S": "blocked"},
                            "blocking_rule": {"S": blocking_rule or "Unknown"},
                            "user_agent": {"S": f"{bot_id.title()}/1.0"},
                            "ip_address": {"S": fake.ipv4()},
                            "block_reason": {"S": "WAF rule enforcement"},
                            "revenue_impact": {"N": "0.0"},
                        }
                    },
                }
            else:
                successful_events += 1
                accessed_content = random.sample(content_ids, min(random.randint(1, 5), len(content_ids)))

                # Generate successful access metric
                record = {
                    "metric_date": {"S": date_str},
                    "metric_type_timestamp": {"S": f"bot_access_{timestamp}_{bot_id}_{i}"},
                    "metric_type": {"S": "bot_access"},
                    "timestamp": {"S": timestamp},
                    "value": {"N": str(len(accessed_content))},
                    "metadata": {
                        "M": {
                            "bot_id": {"S": bot_id},
                            "partner_id": {"S": partner_id},
                            "access_method": {"S": random.choice(["direct_api", "mcp_server"])},
                            "content_accessed": {"SS": accessed_content},
                            "query_type": {"S": random.choice(["search", "direct_query", "bulk_access"])},
                            "status": {"S": "allowed"},
                            "user_agent": {"S": f"{bot_id.title()}/1.0"},
                            "ip_address": {"S": fake.ipv4()},
                            "session_duration_ms": {"N": str(random.randint(1000, 30000))},
                            "revenue_impact": {"N": str(round(random.uniform(0.5, 10.0), 2))},
                        }
                    },
                }

            records.append(record)

        # Add a summary metric for the day
        if bot_rules_records:
            summary_record = {
                "metric_date": {"S": date_str},
                "metric_type_timestamp": {"S": f"bot_access_summary_{date_str}"},
                "metric_type": {"S": "bot_access"},
                "timestamp": {"S": f"{date_str}T23:59:59Z"},
                "value": {"N": str(successful_events)},
                "metadata": {
                    "M": {
                        "summary_type": {"S": "daily_bot_activity"},
                        "total_attempts": {"N": str(base_bot_events)},
                        "successful_accesses": {"N": str(successful_events)},
                        "blocked_attempts": {"N": str(blocked_events)},
                        "block_rate": {
                            "N": str(round(blocked_events / base_bot_events if base_bot_events > 0 else 0, 3))
                        },
                        "active_rules_count": {
                            "N": str(len([r for r in bot_rules_records if r.get("status", {}).get("S") == "active"]))
                        },
                        "rules_effectiveness": {"N": str(round(random.uniform(0.7, 0.95), 2))},
                    }
                },
            }
            records.append(summary_record)

        return records

    def _generate_mcp_request_metrics(self, date_str: str, content_ids: List[str], fake: Faker) -> List[Dict[str, Any]]:
        """Generate MCP server request metrics for a specific date."""
        records = []

        # Generate fewer MCP tool usage events per day
        num_mcp_events = random.randint(1, 3)

        mcp_tools = [
            "search_content",
            "get_content_details",
            "list_categories",
            "analyze_content_performance",
            "get_usage_analytics",
        ]

        agent_types = [
            "claude-3-sonnet",
            "claude-3-haiku",
            "bedrock-agent",
            "custom-research-agent",
            "content-analysis-agent",
        ]

        for _ in range(num_mcp_events):
            hour = random.randint(0, 23)
            minute = random.randint(0, 59)
            timestamp = f"{date_str}T{hour:02d}:{minute:02d}:00Z"

            tool_name = random.choice(mcp_tools)
            agent_type = random.choice(agent_types)
            agent_id = f"agent_{fake.uuid4()[:8]}"

            record = {
                "metric_date": {"S": date_str},
                "metric_type_timestamp": {"S": f"mcp_tool_usage_{timestamp}_{tool_name}_{_}"},
                "metric_type": {"S": "mcp_tool_usage"},
                "timestamp": {"S": timestamp},
                "value": {"N": "1"},
                "metadata": {
                    "M": {
                        "tool_name": {"S": tool_name},
                        "agent_id": {"S": agent_id},
                        "agent_type": {"S": agent_type},
                        "server_id": {"S": "iab-content-mcp-server"},
                        "session_id": {"S": f"session_{fake.uuid4()[:12]}"},
                        "response_time_ms": {"N": str(random.randint(100, 2000))},
                        "content_accessed_count": {"N": str(random.randint(1, 25))},
                        "query_complexity_score": {"N": str(round(random.uniform(1.0, 10.0), 1))},
                        "business_context": {
                            "M": {
                                "query_intent": {
                                    "S": random.choice(["research", "analysis", "discovery", "validation"])
                                },
                                "content_categories": {
                                    "SS": random.sample(
                                        ["technology", "business", "science", "health"], random.randint(1, 4)
                                    )
                                },
                                "usage_pattern": {
                                    "S": random.choice(["exploratory_search", "targeted_query", "bulk_analysis"])
                                },
                            }
                        },
                    }
                },
            }
            records.append(record)

        return records

    def _generate_revenue_metrics(self, date_str: str, fake: Faker) -> List[Dict[str, Any]]:
        """Generate revenue metrics for a specific date."""
        records = []

        # Generate daily revenue summary
        timestamp = f"{date_str}T23:59:59Z"

        # Calculate realistic daily revenue
        base_revenue = random.uniform(100.0, 1000.0)

        record = {
            "metric_date": {"S": date_str},
            "metric_type_timestamp": {"S": f"revenue_daily_{timestamp}"},
            "metric_type": {"S": "revenue_daily"},
            "timestamp": {"S": timestamp},
            "value": {"N": str(round(base_revenue, 2))},
            "metadata": {
                "M": {
                    "revenue_breakdown": {
                        "M": {
                            "content_access": {"N": str(round(base_revenue * 0.6, 2))},
                            "api_usage": {"N": str(round(base_revenue * 0.3, 2))},
                            "premium_features": {"N": str(round(base_revenue * 0.1, 2))},
                        }
                    },
                    "transaction_count": {"N": str(random.randint(50, 500))},
                    "average_transaction_value": {"N": str(round(base_revenue / random.randint(50, 500), 2))},
                    "top_revenue_content_types": {"SS": ["article", "report", "document"]},
                    "currency": {"S": "USD"},
                }
            },
        }
        records.append(record)

        return records

    def _generate_search_query_metrics(
        self, date_str: str, content_ids: List[str], fake: Faker
    ) -> List[Dict[str, Any]]:
        """Generate search query metrics for a specific date."""
        records = []

        # Generate fewer search query events per day
        num_search_events = random.randint(1, 3)

        search_terms = [
            "artificial intelligence",
            "machine learning",
            "data science",
            "business analytics",
            "market research",
            "technology trends",
            "financial analysis",
            "healthcare innovation",
            "climate change",
            "digital transformation",
            "cybersecurity",
            "blockchain",
        ]

        for _ in range(num_search_events):
            hour = random.randint(0, 23)
            minute = random.randint(0, 59)
            timestamp = f"{date_str}T{hour:02d}:{minute:02d}:00Z"

            query = random.choice(search_terms)
            results_count = random.randint(1, 50)

            record = {
                "metric_date": {"S": date_str},
                "metric_type_timestamp": {"S": f"search_query_{timestamp}_{hash(query + str(_)) % 100000}"},
                "metric_type": {"S": "search_query"},
                "timestamp": {"S": timestamp},
                "value": {"N": str(results_count)},
                "metadata": {
                    "M": {
                        "query": {"S": query},
                        "results_returned": {"N": str(results_count)},
                        "response_time_ms": {"N": str(random.randint(50, 500))},
                        "requester_type": {"S": random.choice(["bot", "human", "mcp_agent"])},
                        "query_length": {"N": str(len(query))},
                        "filters_applied": {
                            "SS": random.sample(["content_type", "date_range", "category"], random.randint(1, 3))
                        },
                        "result_quality_score": {"N": str(round(random.uniform(0.5, 1.0), 2))},
                    }
                },
            }
            records.append(record)

        return records

    def validate_data_generation_only(self) -> bool:
        """
        Generate all mock data and validate without writing to DynamoDB.

        Returns:
            True if generation and validation successful, False otherwise
        """
        logger.info("Starting mock data generation and validation (no DynamoDB writes)")

        try:
            # Generate all data
            logger.info("Generating mock data...")
            content_records = self.generate_content_data()
            partner_records = self.generate_partner_data()

            # Extract IDs for referential integrity
            content_ids = [item["content_id"]["S"] for item in content_records]
            partner_ids = [item["partner_id"]["S"] for item in partner_records]

            job_records = self.generate_job_data(content_ids, partner_ids)
            access_log_records = self.generate_access_logs(partner_ids, content_ids)
            # Check for existing bot rules first (validation-only mode)
            bot_rules_records = []
            logger.info("Validation-only mode: generating mock bot rules for testing")
            bot_rules_records = self.generate_bot_rules_data()

            # Generate analytics data that conforms to bot rules patterns
            analytics_records = self.generate_analytics_data(content_ids, partner_ids, bot_rules_records)

            # Generate pricing data
            pricing_rules_records = self.generate_pricing_rules_data()
            pricing_transactions_records = self.generate_pricing_transactions_data(content_ids, partner_ids)

            # Validate generated data
            logger.info("Validating generated data...")
            validation_results = self.validate_generated_data(
                content_records,
                partner_records,
                job_records,
                access_log_records,
                analytics_records,
                bot_rules_records,
                pricing_rules_records,
                pricing_transactions_records,
            )

            # Check if validation passed
            validation_passed = all(result.is_valid for result in validation_results.values())

            if validation_passed:
                logger.info("✓ All validation checks passed. Data generation is working correctly.")
                self._log_generation_summary(
                    content_records,
                    partner_records,
                    job_records,
                    access_log_records,
                    analytics_records,
                    bot_rules_records,
                    pricing_rules_records,
                    pricing_transactions_records,
                )
            else:
                logger.error("✗ Data validation failed. Check the detailed report above.")

            return validation_passed

        except Exception as e:
            logger.error(f"Mock data validation failed: {e}")
            return False

    def generate_all_data_with_validation(self) -> bool:
        """
        Generate all mock data with comprehensive validation.

        Returns:
            True if generation and validation successful, False otherwise
        """
        logger.info("Starting mock data generation with validation")

        try:
            # Generate all data
            logger.info("Generating mock data...")
            content_records = self.generate_content_data()
            partner_records = self.generate_partner_data()

            # Extract IDs for referential integrity
            content_ids = [item["content_id"]["S"] for item in content_records]
            partner_ids = [item["partner_id"]["S"] for item in partner_records]

            job_records = self.generate_job_data(content_ids, partner_ids)
            access_log_records = self.generate_access_logs(partner_ids, content_ids)

            # Check for existing bot rules first
            existing_bot_rules = self.get_existing_bot_rules()
            bot_rules_records = []

            if existing_bot_rules:
                logger.info(f"Found {len(existing_bot_rules)} existing bot rules from deployment")
                bot_rules_records = existing_bot_rules
            else:
                logger.warning(
                    "No existing bot rules found. Generating mock bot rules (WAF sync may fail in real deployment)"
                )
                bot_rules_records = self.generate_bot_rules_data()

            # Generate analytics data that conforms to bot rules patterns
            analytics_records = self.generate_analytics_data(content_ids, partner_ids, bot_rules_records)

            # Validate generated data
            logger.info("Validating generated data...")
            validation_results = self.validate_generated_data(
                content_records, partner_records, job_records, access_log_records, analytics_records, bot_rules_records
            )

            # Check if validation passed
            validation_passed = all(result.is_valid for result in validation_results.values())

            if not validation_passed:
                logger.error("Data validation failed. Aborting data insertion.")
                return False

            logger.info("✓ All validation checks passed. Proceeding with data insertion.")

            # Clear existing data if requested
            if self.config.clear_existing:
                logger.info("Clearing existing data...")
                if not self.clear_all_tables():
                    logger.error("Failed to clear existing data")
                    return False

            # Insert validated data
            logger.info("Inserting validated data into DynamoDB...")
            success = True

            if not self.batch_write_items_chunked(self.TABLE_NAMES["content"], content_records):
                logger.error("Failed to insert content records")
                success = False

            if not self.batch_write_items_chunked(self.TABLE_NAMES["partners"], partner_records):
                logger.error("Failed to insert partner records")
                success = False

            if not self.batch_write_items_chunked(self.TABLE_NAMES["jobs"], job_records):
                logger.error("Failed to insert job records")
                success = False

            if not self.batch_write_items_chunked(self.TABLE_NAMES["access_logs"], access_log_records):
                logger.error("Failed to insert access log records")
                success = False

            if not self.batch_write_items_chunked(self.TABLE_NAMES["analytics"], analytics_records):
                logger.error("Failed to insert analytics records")
                success = False

            if not self.batch_write_items_chunked(self.TABLE_NAMES["bot_rules"], bot_rules_records):
                logger.error("Failed to insert bot rules records")
                success = False

            if success:
                logger.info("✓ Mock data generation with validation completed successfully")
                self._log_generation_summary(
                    content_records,
                    partner_records,
                    job_records,
                    access_log_records,
                    analytics_records,
                    bot_rules_records,
                )
            else:
                logger.error("✗ Mock data generation failed during insertion")

            return success

        except Exception as e:
            logger.error(f"Mock data generation with validation failed: {e}")
            return False

    def _log_generation_summary(
        self,
        content_records: List[Dict[str, Any]],
        partner_records: List[Dict[str, Any]],
        job_records: List[Dict[str, Any]],
        access_log_records: List[Dict[str, Any]],
        analytics_records: List[Dict[str, Any]] = None,
        bot_rules_records: List[Dict[str, Any]] = None,
        pricing_rules_records: List[Dict[str, Any]] = None,
        pricing_transactions_records: List[Dict[str, Any]] = None,
    ):
        """Log a summary of generated data."""
        logger.info("=" * 60)
        logger.info("MOCK DATA GENERATION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Content records: {len(content_records)}")
        logger.info(f"Partner records: {len(partner_records)}")
        logger.info(f"Job records: {len(job_records)}")
        logger.info(f"Access log records: {len(access_log_records)}")

        total = len(content_records) + len(partner_records) + len(job_records) + len(access_log_records)

        if analytics_records:
            logger.info(f"Analytics records: {len(analytics_records)}")
            total += len(analytics_records)

        if bot_rules_records:
            logger.info(f"Bot rules records: {len(bot_rules_records)}")
            total += len(bot_rules_records)

        if pricing_rules_records:
            logger.info(f"Pricing rules records: {len(pricing_rules_records)}")
            total += len(pricing_rules_records)

        if pricing_transactions_records:
            logger.info(f"Pricing transactions records: {len(pricing_transactions_records)}")
            total += len(pricing_transactions_records)

        logger.info(f"Total records: {total}")
        logger.info("=" * 60)

    def __repr__(self) -> str:
        """String representation of the generator."""
        return (
            f"MockDataGenerator(profile='{self.config.aws_profile}', "
            f"region='{self.config.aws_region}', "
            f"content_count={self.config.content_count})"
        )
