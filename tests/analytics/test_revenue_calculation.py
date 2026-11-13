"""
Tests for revenue calculation with historical pricing accuracy

This test suite validates that the analytics aggregation uses the PricingVersionManager
for accurate historical pricing instead of hardcoded constants.
"""

import pytest
import boto3
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from moto import mock_aws
import sys
import os

# Add lambda directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from analytics_aggregation import AnalyticsAggregator


@mock_aws
class TestRevenueCalculation:
    """Test revenue calculation with historical pricing"""

    def setup_method(self, method):
        """Set up test environment"""
        # Create mock DynamoDB tables
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create analytics table
        self.analytics_table = self.dynamodb.create_table(
            TableName="test-analytics",
            KeySchema=[
                {"AttributeName": "metric_id", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "metric_id", "AttributeType": "S"},
                {"AttributeName": "date", "AttributeType": "S"},
                {"AttributeName": "partner_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "date-index",
                    "KeySchema": [
                        {"AttributeName": "date", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
                {
                    "IndexName": "partner-date-index",
                    "KeySchema": [
                        {"AttributeName": "partner_id", "KeyType": "HASH"},
                        {"AttributeName": "date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                },
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Create pricing rules table
        self.pricing_rules_table = self.dynamodb.create_table(
            TableName="test-pricing-rules",
            KeySchema=[
                {"AttributeName": "rule_set_id", "KeyType": "HASH"},
                {"AttributeName": "version_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "rule_set_id", "AttributeType": "S"},
                {"AttributeName": "version_timestamp", "AttributeType": "S"},
                {"AttributeName": "effective_date", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "effective-date-index",
                    "KeySchema": [
                        {"AttributeName": "rule_set_id", "KeyType": "HASH"},
                        {"AttributeName": "effective_date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Create pricing transactions table
        self.pricing_transactions_table = self.dynamodb.create_table(
            TableName="test-pricing-transactions",
            KeySchema=[
                {"AttributeName": "transaction_id", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "transaction_id", "AttributeType": "S"},
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Set environment variables
        os.environ["ANALYTICS_TABLE_NAME"] = "test-analytics"
        os.environ["PRICING_RULES_TABLE_NAME"] = "test-pricing-rules"
        os.environ["PRICING_TRANSACTIONS_TABLE_NAME"] = "test-pricing-transactions"

        # Initialize aggregator
        self.aggregator = AnalyticsAggregator()

        # Override the pricing manager with test table names
        from services.pricing_manager import PricingVersionManager

        self.aggregator.pricing_manager = PricingVersionManager(
            region_name="us-west-2",
            pricing_rules_table_name="test-pricing-rules",
            pricing_transactions_table_name="test-pricing-transactions",
        )

    def test_historical_pricing_lookup(self):
        """Test that historical pricing is looked up correctly"""
        # Add pricing rule to table
        pricing_rule = {
            "rule_set_id": "default-pricing",
            "version_timestamp": "2025-01-01T00:00:00Z",
            "effective_date": "2025-01-01",
            "status": "active",
            "pricing_rules": {
                "per_access": {
                    "article": "0.08",  # Different from fallback price
                    "report": "0.30",
                }
            },
            "created_by": "test",
            "created_at": "2025-01-01T00:00:00Z",
        }

        self.pricing_rules_table.put_item(Item=pricing_rule)

        # Test historical pricing lookup
        price = self.aggregator._get_historical_pricing(
            content_type="article", content_id="art_001", transaction_date="2025-01-01T12:00:00Z"
        )

        # Should return the historical price, not the fallback
        assert price == Decimal("0.08")

    def test_fallback_pricing_when_no_historical_data(self):
        """Test fallback to hardcoded pricing when no historical data exists"""
        # Don't add any pricing rules to table

        price = self.aggregator._get_historical_pricing(
            content_type="article", content_id="art_001", transaction_date="2025-01-01T12:00:00Z"
        )

        # Should return fallback price
        assert price == Decimal("0.05")

    def test_fallback_pricing_when_pricing_manager_unavailable(self):
        """Test fallback when PricingVersionManager is not available"""
        # Simulate pricing manager being unavailable
        self.aggregator.pricing_manager = None

        price = self.aggregator._get_historical_pricing(
            content_type="report", content_id="rpt_001", transaction_date="2025-01-01T12:00:00Z"
        )

        # Should return fallback price
        assert price == Decimal("0.25")

    def test_revenue_calculation_with_historical_pricing(self):
        """Test that revenue calculation uses historical pricing"""
        # Add pricing rule
        pricing_rule = {
            "rule_set_id": "default-pricing",
            "version_timestamp": "2025-01-01T00:00:00Z",
            "effective_date": "2025-01-01T00:00:00Z",
            "status": "active",
            "pricing_rules": {
                "per_access": {
                    "article": "0.10",  # Higher than fallback
                    "report": "0.50",
                }
            },
            "created_by": "test",
            "created_at": "2025-01-01T00:00:00Z",
        }

        self.pricing_rules_table.put_item(Item=pricing_rule)

        # Add analytics data with bot access
        analytics_item = {
            "metric_id": "test_metric_001",
            "date": "2025-01-01",
            "partner_id": "test_partner",
            "metric_type": "bot_access",
            "value": 1,
            "timestamp": "2025-01-01T12:00:00Z",
            "content_accessed": ["art_001", "rpt_001"],
            "access_method": "direct_api",
        }

        self.analytics_table.put_item(Item=analytics_item)

        # Aggregate metrics for the date
        result = self.aggregator.aggregate_daily_metrics("2025-01-01")

        # Revenue should be calculated using historical pricing
        # art_001 (article): $0.10, rpt_001 (report): $0.50 = $0.60 total
        expected_revenue = Decimal("0.60")
        actual_revenue = result["partner_metrics"]["test_partner"]["revenue_generated"]

        assert actual_revenue == expected_revenue

    def test_mcp_tool_historical_pricing(self):
        """Test MCP tool pricing uses historical data"""
        # Add pricing rule with MCP tool pricing
        pricing_rule = {
            "rule_set_id": "default-pricing",
            "version_timestamp": "2025-01-01T00:00:00Z",
            "effective_date": "2025-01-01T00:00:00Z",
            "status": "active",
            "pricing_rules": {
                "mcp_tools": {
                    "search_content": "0.03",  # Different from fallback
                    "get_content": "0.08",
                }
            },
            "created_by": "test",
            "created_at": "2025-01-01T00:00:00Z",
        }

        self.pricing_rules_table.put_item(Item=pricing_rule)

        # Test MCP tool pricing lookup
        price = self.aggregator._get_historical_mcp_pricing(
            tool_name="search_content", transaction_date="2025-01-01T12:00:00Z"
        )

        # Should return the historical price, not the fallback
        assert price == Decimal("0.03")

    def test_invalid_transaction_date_handling(self):
        """Test handling of invalid transaction dates"""
        # Test with invalid date format
        price = self.aggregator._get_historical_pricing(
            content_type="article", content_id="art_001", transaction_date="invalid-date"
        )

        # Should still return a valid price (fallback)
        assert price == Decimal("0.05")

    def test_missing_content_type_in_pricing_rules(self):
        """Test handling when content type is not in pricing rules"""
        # Add pricing rule without 'video' content type
        pricing_rule = {
            "rule_set_id": "default-pricing",
            "version_timestamp": "2025-01-01T00:00:00Z",
            "effective_date": "2025-01-01T00:00:00Z",
            "status": "active",
            "pricing_rules": {
                "per_access": {
                    "article": "0.08",
                    "report": "0.30",
                    # 'video' is missing
                }
            },
            "created_by": "test",
            "created_at": "2025-01-01T00:00:00Z",
        }

        self.pricing_rules_table.put_item(Item=pricing_rule)

        # Test pricing lookup for missing content type
        price = self.aggregator._get_historical_pricing(
            content_type="video", content_id="vid_001", transaction_date="2025-01-01T12:00:00Z"
        )

        # Should return fallback price for video
        assert price == Decimal("0.15")

    def test_pricing_version_precedence(self):
        """Test that the correct pricing version is used based on transaction date"""
        # Add older pricing rule
        old_pricing_rule = {
            "rule_set_id": "default-pricing",
            "version_timestamp": "2024-12-01T00:00:00Z",
            "effective_date": "2024-12-01T00:00:00Z",
            "status": "inactive",
            "pricing_rules": {
                "per_access": {
                    "article": "0.04"  # Old price
                }
            },
            "created_by": "test",
            "created_at": "2024-12-01T00:00:00Z",
        }

        # Add newer pricing rule
        new_pricing_rule = {
            "rule_set_id": "default-pricing",
            "version_timestamp": "2025-01-01T00:00:00Z",
            "effective_date": "2025-01-01T00:00:00Z",
            "status": "active",
            "pricing_rules": {
                "per_access": {
                    "article": "0.06"  # New price
                }
            },
            "created_by": "test",
            "created_at": "2025-01-01T00:00:00Z",
        }

        self.pricing_rules_table.put_item(Item=old_pricing_rule)
        self.pricing_rules_table.put_item(Item=new_pricing_rule)

        # Test transaction from old period
        old_price = self.aggregator._get_historical_pricing(
            content_type="article", content_id="art_001", transaction_date="2024-12-15T12:00:00Z"
        )

        # Test transaction from new period
        new_price = self.aggregator._get_historical_pricing(
            content_type="article", content_id="art_001", transaction_date="2025-01-15T12:00:00Z"
        )

        # Should use appropriate pricing for each period
        assert old_price == Decimal("0.04")
        assert new_price == Decimal("0.06")


if __name__ == "__main__":
    pytest.main([__file__])
