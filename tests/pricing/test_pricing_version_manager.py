"""
Unit tests for PricingVersionManager service.

Tests pricing rule versioning with effective dates, historical tracking,
and transaction cost calculation functionality.
"""

import pytest
import boto3
import os
from moto import mock_aws
from datetime import datetime, timezone
from decimal import Decimal
import sys
import os

# Add lambda directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "../../source/lambda"))

from services.pricing_manager import PricingVersionManager


@mock_aws
class TestPricingVersionManager:
    """Test PricingVersionManager functionality."""

    def setup_method(self, method):
        """Set up test environment with mocked DynamoDB tables."""
        # Wire the manager to this test's own moto tables regardless of any co-run test that set
        # a placeholder PRICING_*_TABLE_NAME at collection time (wave0 Req 16/18 isolation).
        os.environ["PRICING_RULES_TABLE_NAME"] = "cm-pricing-rules"
        os.environ["PRICING_TRANSACTIONS_TABLE_NAME"] = "cm-pricing-transactions"
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create pricing rules table
        self.pricing_rules_table = self.dynamodb.create_table(
            TableName="cm-pricing-rules",
            KeySchema=[
                {"AttributeName": "rule_set_id", "KeyType": "HASH"},
                {"AttributeName": "version_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "rule_set_id", "AttributeType": "S"},
                {"AttributeName": "version_timestamp", "AttributeType": "S"},
                {"AttributeName": "effective_date", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "effective-date-index",
                    "KeySchema": [
                        {"AttributeName": "effective_date", "KeyType": "HASH"},
                        {"AttributeName": "version_timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "status-effective-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "effective_date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Create pricing transactions table
        self.pricing_transactions_table = self.dynamodb.create_table(
            TableName="cm-pricing-transactions",
            KeySchema=[
                {"AttributeName": "transaction_date", "KeyType": "HASH"},
                {"AttributeName": "transaction_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "transaction_date", "AttributeType": "S"},
                {"AttributeName": "transaction_id", "AttributeType": "S"},
                {"AttributeName": "content_id", "AttributeType": "S"},
                {"AttributeName": "transaction_timestamp", "AttributeType": "S"},
                {"AttributeName": "pricing_rule_version", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "content-id-index",
                    "KeySchema": [
                        {"AttributeName": "content_id", "KeyType": "HASH"},
                        {"AttributeName": "transaction_timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "pricing-version-index",
                    "KeySchema": [
                        {"AttributeName": "pricing_rule_version", "KeyType": "HASH"},
                        {"AttributeName": "transaction_timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Initialize the pricing manager
        self.pricing_manager = PricingVersionManager(region_name="us-west-2")

    def test_create_pricing_rule_version(self):
        """Test creating a new pricing rule version."""
        pricing_rules = {
            "per_access": {"article": Decimal("0.01"), "video": Decimal("0.05"), "image": Decimal("0.002")},
            "subscription": {"monthly_rate": Decimal("100.00")},
        }

        result = self.pricing_manager.create_pricing_rule_version(
            rule_set_id="test-pricing",
            pricing_rules=pricing_rules,
            effective_date="2024-01-01",
            created_by="test-user",
            change_reason="Initial setup",
        )

        assert result["rule_set_id"] == "test-pricing"
        assert result["effective_date"] == "2024-01-01"
        assert result["status"] == "draft"
        assert result["created_by"] == "test-user"
        assert result["audit_trail"]["version"] == 1
        assert result["audit_trail"]["change_reason"] == "Initial setup"
        assert result["pricing_rules"]["per_access"]["article"] == Decimal("0.01")

    def test_calculate_transaction_cost_with_fallback(self):
        """Test calculating transaction cost with fallback pricing."""
        # Calculate cost without any active pricing rules (should use fallback)
        cost_calculation = self.pricing_manager.calculate_transaction_cost(
            content_type="video",
            access_type="full",
            transaction_timestamp="2024-01-15T10:00:00Z",
            partner_id="test-partner",
            content_id="content-456",
        )

        assert cost_calculation["base_cost"] == Decimal("0.05")  # Default video cost
        assert cost_calculation["multiplier"] == Decimal("1.0")
        assert cost_calculation["final_cost"] == Decimal("0.05")
        assert cost_calculation["pricing_version"] == "default-fallback"
