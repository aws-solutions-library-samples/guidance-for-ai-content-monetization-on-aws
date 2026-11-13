"""
Unit tests for pricing data model and infrastructure.

Tests the versioned pricing configuration data model with effective dates,
audit trail, and pricing transactions table functionality.
"""

import pytest
import boto3
from moto import mock_aws
from datetime import datetime, timezone
import json
from decimal import Decimal


@mock_aws
class TestPricingModel:
    """Test pricing data model functionality."""

    def setup_method(self, method):
        """Set up test environment with mocked DynamoDB tables."""
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

    def test_pricing_rules_table_structure(self):
        """Test that pricing rules table has correct structure."""
        table_description = self.pricing_rules_table.meta.client.describe_table(TableName="cm-pricing-rules")

        # Verify primary key structure
        key_schema = table_description["Table"]["KeySchema"]
        assert len(key_schema) == 2
        assert key_schema[0]["AttributeName"] == "rule_set_id"
        assert key_schema[0]["KeyType"] == "HASH"
        assert key_schema[1]["AttributeName"] == "version_timestamp"
        assert key_schema[1]["KeyType"] == "RANGE"

        # Verify GSI structure
        gsi_list = table_description["Table"]["GlobalSecondaryIndexes"]
        assert len(gsi_list) == 2

        # Check effective-date-index
        effective_date_gsi = next(gsi for gsi in gsi_list if gsi["IndexName"] == "effective-date-index")
        assert effective_date_gsi["KeySchema"][0]["AttributeName"] == "effective_date"
        assert effective_date_gsi["KeySchema"][1]["AttributeName"] == "version_timestamp"

        # Check status-effective-index
        status_effective_gsi = next(gsi for gsi in gsi_list if gsi["IndexName"] == "status-effective-index")
        assert status_effective_gsi["KeySchema"][0]["AttributeName"] == "status"
        assert status_effective_gsi["KeySchema"][1]["AttributeName"] == "effective_date"

    def test_pricing_transactions_table_structure(self):
        """Test that pricing transactions table has correct structure."""
        table_description = self.pricing_transactions_table.meta.client.describe_table(
            TableName="cm-pricing-transactions"
        )

        # Verify primary key structure
        key_schema = table_description["Table"]["KeySchema"]
        assert len(key_schema) == 2
        assert key_schema[0]["AttributeName"] == "transaction_date"
        assert key_schema[0]["KeyType"] == "HASH"
        assert key_schema[1]["AttributeName"] == "transaction_id"
        assert key_schema[1]["KeyType"] == "RANGE"

        # Verify GSI structure
        gsi_list = table_description["Table"]["GlobalSecondaryIndexes"]
        assert len(gsi_list) == 2

        # Check content-id-index
        content_id_gsi = next(gsi for gsi in gsi_list if gsi["IndexName"] == "content-id-index")
        assert content_id_gsi["KeySchema"][0]["AttributeName"] == "content_id"
        assert content_id_gsi["KeySchema"][1]["AttributeName"] == "transaction_timestamp"

        # Check pricing-version-index
        pricing_version_gsi = next(gsi for gsi in gsi_list if gsi["IndexName"] == "pricing-version-index")
        assert pricing_version_gsi["KeySchema"][0]["AttributeName"] == "pricing_rule_version"
        assert pricing_version_gsi["KeySchema"][1]["AttributeName"] == "transaction_timestamp"

    def test_create_versioned_pricing_rule(self):
        """Test creating a versioned pricing rule with effective dates."""
        now = datetime.now(timezone.utc)
        version_timestamp = now.isoformat()
        effective_date = "2024-01-01"

        pricing_rule = {
            "rule_set_id": "default-pricing",
            "version_timestamp": version_timestamp,
            "effective_date": effective_date,
            "status": "active",
            "created_by": "test-user",
            "created_at": version_timestamp,
            "pricing_rules": {
                "per_access": {
                    "article": Decimal("0.01"),
                    "video": Decimal("0.05"),
                    "image": Decimal("0.002"),
                    "audio": Decimal("0.03"),
                    "document": Decimal("0.015"),
                },
                "subscription": {"monthly_rate": Decimal("100.00"), "annual_rate": Decimal("1000.00")},
            },
            "audit_trail": {"version": 1, "change_reason": "Initial pricing configuration", "previous_version": None},
        }

        # Store pricing rule
        self.pricing_rules_table.put_item(Item=pricing_rule)

        # Retrieve and verify
        response = self.pricing_rules_table.get_item(
            Key={"rule_set_id": "default-pricing", "version_timestamp": version_timestamp}
        )

        assert "Item" in response
        item = response["Item"]
        assert item["rule_set_id"] == "default-pricing"
        assert item["effective_date"] == effective_date
        assert item["status"] == "active"
        assert item["pricing_rules"]["per_access"]["article"] == Decimal("0.01")
        assert item["audit_trail"]["version"] == 1

    def test_create_pricing_transaction(self):
        """Test creating a pricing transaction with historical cost tracking."""
        now = datetime.now(timezone.utc)
        transaction_date = now.strftime("%Y-%m-%d")
        transaction_timestamp = now.isoformat()
        transaction_id = f"txn_{now.strftime('%Y%m%d_%H%M%S')}_001"

        transaction = {
            "transaction_date": transaction_date,
            "transaction_id": transaction_id,
            "transaction_timestamp": transaction_timestamp,
            "content_id": "content_123",
            "partner_id": "partner_abc",
            "access_type": "full",
            "content_type": "article",
            "pricing_rule_version": "default-pricing#2024-01-01T00:00:00Z",
            "cost_calculation": {
                "base_cost": Decimal("0.01"),
                "multiplier": Decimal("1.0"),
                "final_cost": Decimal("0.01"),
                "currency": "USD",
            },
            "metadata": {"user_agent": "test-bot/1.0", "ip_address": "192.168.1.1", "request_id": "req_123"},
        }

        # Store transaction
        self.pricing_transactions_table.put_item(Item=transaction)

        # Retrieve and verify
        response = self.pricing_transactions_table.get_item(
            Key={"transaction_date": transaction_date, "transaction_id": transaction_id}
        )

        assert "Item" in response
        item = response["Item"]
        assert item["content_id"] == "content_123"
        assert item["partner_id"] == "partner_abc"
        assert item["pricing_rule_version"] == "default-pricing#2024-01-01T00:00:00Z"
        assert item["cost_calculation"]["final_cost"] == Decimal("0.01")

    def test_query_active_pricing_rules(self):
        """Test querying active pricing rules by effective date."""
        now = datetime.now(timezone.utc)

        # Create multiple pricing rule versions
        rules = [
            {
                "rule_set_id": "default-pricing",
                "version_timestamp": "2024-01-01T00:00:00Z",
                "effective_date": "2024-01-01",
                "status": "active",
                "pricing_rules": {"per_access": {"article": Decimal("0.01")}},
            },
            {
                "rule_set_id": "default-pricing",
                "version_timestamp": "2024-02-01T00:00:00Z",
                "effective_date": "2024-02-01",
                "status": "active",
                "pricing_rules": {"per_access": {"article": Decimal("0.015")}},
            },
            {
                "rule_set_id": "default-pricing",
                "version_timestamp": "2024-03-01T00:00:00Z",
                "effective_date": "2024-03-01",
                "status": "draft",
                "pricing_rules": {"per_access": {"article": Decimal("0.02")}},
            },
        ]

        # Store all rules
        for rule in rules:
            self.pricing_rules_table.put_item(Item=rule)

        # Query active rules
        response = self.pricing_rules_table.query(
            IndexName="status-effective-index",
            KeyConditionExpression="#status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "active"},
        )

        assert response["Count"] == 2
        active_rules = response["Items"]

        # Verify active rules are returned
        effective_dates = [rule["effective_date"] for rule in active_rules]
        assert "2024-01-01" in effective_dates
        assert "2024-02-01" in effective_dates

    def test_query_transactions_by_content_id(self):
        """Test querying transactions by content ID."""
        now = datetime.now(timezone.utc)
        transaction_date = now.strftime("%Y-%m-%d")

        # Create multiple transactions for same content
        transactions = [
            {
                "transaction_date": transaction_date,
                "transaction_id": "txn_001",
                "transaction_timestamp": "2024-01-01T10:00:00Z",
                "content_id": "content_123",
                "partner_id": "partner_a",
                "cost_calculation": {"final_cost": Decimal("0.01")},
            },
            {
                "transaction_date": transaction_date,
                "transaction_id": "txn_002",
                "transaction_timestamp": "2024-01-01T11:00:00Z",
                "content_id": "content_123",
                "partner_id": "partner_b",
                "cost_calculation": {"final_cost": Decimal("0.015")},
            },
            {
                "transaction_date": transaction_date,
                "transaction_id": "txn_003",
                "transaction_timestamp": "2024-01-01T12:00:00Z",
                "content_id": "content_456",
                "partner_id": "partner_a",
                "cost_calculation": {"final_cost": Decimal("0.02")},
            },
        ]

        # Store all transactions
        for transaction in transactions:
            self.pricing_transactions_table.put_item(Item=transaction)

        # Query transactions for content_123
        response = self.pricing_transactions_table.query(
            IndexName="content-id-index",
            KeyConditionExpression="content_id = :content_id",
            ExpressionAttributeValues={":content_id": "content_123"},
        )

        assert response["Count"] == 2
        content_transactions = response["Items"]

        # Verify correct transactions returned
        partner_ids = [txn["partner_id"] for txn in content_transactions]
        assert "partner_a" in partner_ids
        assert "partner_b" in partner_ids

    def test_audit_trail_functionality(self):
        """Test audit trail for pricing rule versions."""
        now = datetime.now(timezone.utc)

        # Create initial version
        initial_rule = {
            "rule_set_id": "test-pricing",
            "version_timestamp": "2024-01-01T00:00:00Z",
            "effective_date": "2024-01-01",
            "status": "active",
            "created_by": "admin",
            "pricing_rules": {"per_access": {"article": Decimal("0.01")}},
            "audit_trail": {"version": 1, "change_reason": "Initial setup", "previous_version": None},
        }

        # Create updated version
        updated_rule = {
            "rule_set_id": "test-pricing",
            "version_timestamp": "2024-02-01T00:00:00Z",
            "effective_date": "2024-02-01",
            "status": "active",
            "created_by": "admin",
            "pricing_rules": {"per_access": {"article": Decimal("0.015")}},
            "audit_trail": {
                "version": 2,
                "change_reason": "Price increase due to market conditions",
                "previous_version": "2024-01-01T00:00:00Z",
            },
        }

        # Store both versions
        self.pricing_rules_table.put_item(Item=initial_rule)
        self.pricing_rules_table.put_item(Item=updated_rule)

        # Query all versions for rule set
        response = self.pricing_rules_table.query(
            KeyConditionExpression="rule_set_id = :rule_set_id",
            ExpressionAttributeValues={":rule_set_id": "test-pricing"},
            ScanIndexForward=True,  # Sort by version_timestamp ascending
        )

        assert response["Count"] == 2
        versions = response["Items"]

        # Verify audit trail
        assert versions[0]["audit_trail"]["version"] == 1
        assert versions[0]["audit_trail"]["previous_version"] is None
        assert versions[1]["audit_trail"]["version"] == 2
        assert versions[1]["audit_trail"]["previous_version"] == "2024-01-01T00:00:00Z"

    def test_pricing_rule_effective_date_validation(self):
        """Test that pricing rules can be queried by effective date range."""
        # Create rules with different effective dates
        rules = [
            {
                "rule_set_id": "seasonal-pricing",
                "version_timestamp": "2024-01-01T00:00:00Z",
                "effective_date": "2024-01-01",
                "status": "active",
            },
            {
                "rule_set_id": "seasonal-pricing",
                "version_timestamp": "2024-06-01T00:00:00Z",
                "effective_date": "2024-06-01",
                "status": "active",
            },
            {
                "rule_set_id": "seasonal-pricing",
                "version_timestamp": "2024-12-01T00:00:00Z",
                "effective_date": "2024-12-01",
                "status": "active",
            },
        ]

        for rule in rules:
            self.pricing_rules_table.put_item(Item=rule)

        # Query rules by specific effective date (DynamoDB doesn't support BETWEEN in KeyConditionExpression)
        response_jan = self.pricing_rules_table.query(
            IndexName="effective-date-index",
            KeyConditionExpression="effective_date = :date",
            ExpressionAttributeValues={":date": "2024-01-01"},
        )

        response_jun = self.pricing_rules_table.query(
            IndexName="effective-date-index",
            KeyConditionExpression="effective_date = :date",
            ExpressionAttributeValues={":date": "2024-06-01"},
        )

        response_dec = self.pricing_rules_table.query(
            IndexName="effective-date-index",
            KeyConditionExpression="effective_date = :date",
            ExpressionAttributeValues={":date": "2024-12-01"},
        )

        # Verify each query returns the expected rule
        assert response_jan["Count"] == 1
        assert response_jan["Items"][0]["effective_date"] == "2024-01-01"

        assert response_jun["Count"] == 1
        assert response_jun["Items"][0]["effective_date"] == "2024-06-01"

        assert response_dec["Count"] == 1
        assert response_dec["Items"][0]["effective_date"] == "2024-12-01"
