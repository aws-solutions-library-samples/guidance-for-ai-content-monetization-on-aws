"""
Unit tests for analytics DynamoDB table infrastructure.

Tests the analytics table creation, schema, and GSI configuration
as part of task 2.1 - Add analytics DynamoDB table to infrastructure.
"""

import pytest
import boto3
from moto import mock_aws
from aws_cdk import App, Stack
from aws_cdk.assertions import Template, Match
from infrastructure.content_monetization_stack import ContentMonetizationStack


class TestAnalyticsTableInfrastructure:
    """Test analytics table infrastructure components."""

    def test_analytics_table_creation(self):
        """Test that analytics table is created with correct configuration."""
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        template = Template.from_stack(stack)

        # Verify analytics table exists
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "TableName": "cm-analytics",
                "BillingMode": "PAY_PER_REQUEST",
                "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            },
        )

    def test_analytics_table_schema(self):
        """Test analytics table partition and sort key configuration."""
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        template = Template.from_stack(stack)

        # Verify table schema
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "AttributeDefinitions": [
                    {"AttributeName": "metric_date", "AttributeType": "S"},
                    {"AttributeName": "metric_type_timestamp", "AttributeType": "S"},
                    {"AttributeName": "metric_type", "AttributeType": "S"},
                    {"AttributeName": "timestamp", "AttributeType": "S"},
                ],
                "KeySchema": [
                    {"AttributeName": "metric_date", "KeyType": "HASH"},
                    {"AttributeName": "metric_type_timestamp", "KeyType": "RANGE"},
                ],
            },
        )

    def test_analytics_table_gsi(self):
        """Test analytics table GSI configuration."""
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        template = Template.from_stack(stack)

        # Verify GSI configuration
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "GlobalSecondaryIndexes": [
                    {
                        "IndexName": "metric-type-index",
                        "KeySchema": [
                            {"AttributeName": "metric_type", "KeyType": "HASH"},
                            {"AttributeName": "timestamp", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ]
            },
        )

    def test_bot_rules_table_creation(self):
        """Test that bot rules table is created with correct configuration."""
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        template = Template.from_stack(stack)

        # Verify bot rules table exists
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "TableName": "cm-bot-rules",
                "BillingMode": "PAY_PER_REQUEST",
                "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            },
        )

    def test_bot_rules_table_schema(self):
        """Test bot rules table partition key configuration."""
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        template = Template.from_stack(stack)

        # Verify table schema
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "AttributeDefinitions": [
                    {"AttributeName": "rule_id", "AttributeType": "S"},
                    {"AttributeName": "status", "AttributeType": "S"},
                    {"AttributeName": "priority", "AttributeType": "N"},
                ],
                "KeySchema": [{"AttributeName": "rule_id", "KeyType": "HASH"}],
            },
        )

    def test_bot_rules_table_gsi(self):
        """Test bot rules table GSI configuration."""
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        template = Template.from_stack(stack)

        # Verify GSI configuration for bot rules table
        template.has_resource_properties(
            "AWS::DynamoDB::Table",
            {
                "GlobalSecondaryIndexes": [
                    {
                        "IndexName": "status-priority-index",
                        "KeySchema": [
                            {"AttributeName": "status", "KeyType": "HASH"},
                            {"AttributeName": "priority", "KeyType": "RANGE"},
                        ],
                        "Projection": {"ProjectionType": "ALL"},
                    }
                ]
            },
        )

    def test_lambda_environment_variables(self):
        """Test that Lambda functions have access to new table names."""
        app = App()
        stack = ContentMonetizationStack(app, "TestStack")
        template = Template.from_stack(stack)

        # Verify Lambda functions have analytics table environment variable references
        # The values will be CloudFormation references, not literal strings
        template.has_resource_properties(
            "AWS::Lambda::Function",
            {
                "Environment": {
                    "Variables": {
                        "ANALYTICS_TABLE_NAME": {"Ref": Match.any_value()},
                        "BOT_RULES_TABLE_NAME": {"Ref": Match.any_value()},
                    }
                }
            },
        )

    @mock_aws
    def test_analytics_table_operations(self):
        """Test basic analytics table operations with mocked DynamoDB."""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        table = dynamodb.create_table(
            TableName="cm-analytics",
            KeySchema=[
                {"AttributeName": "metric_date", "KeyType": "HASH"},
                {"AttributeName": "metric_type_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "metric_date", "AttributeType": "S"},
                {"AttributeName": "metric_type_timestamp", "AttributeType": "S"},
                {"AttributeName": "metric_type", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "metric-type-index",
                    "KeySchema": [
                        {"AttributeName": "metric_type", "KeyType": "HASH"},
                        {"AttributeName": "timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Test putting an analytics record
        test_record = {
            "metric_date": "2024-01-15",
            "metric_type_timestamp": "api_requests_2024-01-15T10:00:00Z",
            "metric_type": "api_requests",
            "timestamp": "2024-01-15T10:00:00Z",
            "value": 1250,
            "metadata": {
                "endpoint": "/content/search",
                "user_id": "user123",
                "response_time_ms": 150,
                "is_bot": True,
                "bot_id": "openai-gpt",
            },
        }

        table.put_item(Item=test_record)

        # Test retrieving the record
        response = table.get_item(
            Key={"metric_date": "2024-01-15", "metric_type_timestamp": "api_requests_2024-01-15T10:00:00Z"}
        )

        assert "Item" in response
        assert response["Item"]["metric_type"] == "api_requests"
        assert response["Item"]["value"] == 1250

    @mock_aws
    def test_bot_rules_table_operations(self):
        """Test basic bot rules table operations with mocked DynamoDB."""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        table = dynamodb.create_table(
            TableName="cm-bot-rules",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "rule_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "priority", "AttributeType": "N"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-priority-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "priority", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Test putting a bot rule record
        test_rule = {
            "rule_id": "rule_001",
            "rule_name": "Block Malicious Bots",
            "description": "Block known malicious bot patterns",
            "action": "block",
            "priority": 100,
            "status": "active",
            "conditions": {"user_agent_patterns": ["BadBot/*", "MaliciousBot/*"]},
            "created_at": "2024-01-15T10:00:00Z",
            "updated_at": "2024-01-15T10:00:00Z",
            "sync_status": "pending",
        }

        table.put_item(Item=test_rule)

        # Test retrieving the record
        response = table.get_item(Key={"rule_id": "rule_001"})

        assert "Item" in response
        assert response["Item"]["rule_name"] == "Block Malicious Bots"
        assert response["Item"]["action"] == "block"
        assert response["Item"]["priority"] == 100


if __name__ == "__main__":
    pytest.main([__file__])
