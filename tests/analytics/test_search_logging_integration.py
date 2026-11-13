"""
Integration tests for search endpoint bot access logging.

Tests bot access logging against Moto-backed DynamoDB without live AWS access.
"""

import pytest
import boto3
import os
from datetime import datetime, timezone

from moto import mock_aws

# Import the modules to test
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.bot_access_logger import BotAccessLogger


class TestBotAccessLoggingIntegration:
    """Integration tests for bot access logging with DynamoDB."""

    @pytest.fixture(autouse=True)
    def setup_dynamodb(self):
        """Set up mock DynamoDB table for testing."""
        # moto 5.x: start the AWS mock for the duration of each test (a class decorator
        # would not cover this fixture, which runs outside the wrapped test method).
        self._mock_aws = mock_aws()
        self._mock_aws.start()

        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        table = dynamodb.create_table(
            TableName="test-analytics-table",
            KeySchema=[
                {"AttributeName": "metric_date", "KeyType": "HASH"},
                {"AttributeName": "metric_type_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "metric_date", "AttributeType": "S"},
                {"AttributeName": "metric_type_timestamp", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "timestamp-index",
                    "KeySchema": [{"AttributeName": "timestamp", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Wait for table to be created
        table.wait_until_exists()

        self.table = table
        self.table_name = "test-analytics-table"

        # Set environment variable
        os.environ["ANALYTICS_TABLE_NAME"] = self.table_name

        yield

        # Clean up within the still-active mock, then stop it.
        try:
            self.table.delete()
        except Exception:
            pass
        if "ANALYTICS_TABLE_NAME" in os.environ:
            del os.environ["ANALYTICS_TABLE_NAME"]
        self._mock_aws.stop()

    def test_end_to_end_direct_bot_logging(self):
        """Test end-to-end logging for direct bot API access."""
        # Setup
        bot_access_logger = BotAccessLogger(self.table_name)

        request_data = {
            "headers": {"user-agent": "GPTBot/1.0", "x-partner-key": "pk_test123", "x-forwarded-for": "192.168.1.100"},
            "query": "artificial intelligence research",
            "body": {"query": "artificial intelligence research", "content_types": ["article"], "max_results": 10},
            "path": "/search",
            "httpMethod": "POST",
            "requestContext": {"identity": {"sourceIp": "192.168.1.100"}},
        }

        response_data = {
            "results": [
                {
                    "content_id": "art_001",
                    "title": "AI Research Breakthrough",
                    "content_type": "article",
                    "similarity_score": 0.95,
                },
                {
                    "content_id": "art_002",
                    "title": "Machine Learning Applications",
                    "content_type": "article",
                    "similarity_score": 0.87,
                },
            ],
            "content_ids": ["art_001", "art_002"],
            "total_results": 2,
            "processing_time_ms": 150,
        }

        # Execute
        result = bot_access_logger.log_search_access(request_data, response_data)

        # Verify logging success
        assert result is True

        # Verify data was written to DynamoDB
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check for search query metrics
        response = self.table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date)
            & boto3.dynamodb.conditions.Key("metric_type_timestamp").begins_with("search_query_")
        )
        assert len(response["Items"]) >= 1

        search_item = response["Items"][0]
        assert search_item["metric_type"] == "search_query"
        assert search_item["value"] == 2  # Number of results
        assert "artificial intelligence research" in search_item["metadata"]["query"]

        # Check for API access metrics
        response = self.table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date)
            & boto3.dynamodb.conditions.Key("metric_type_timestamp").begins_with("api_requests_")
        )
        assert len(response["Items"]) >= 1

        api_item = response["Items"][0]
        assert api_item["metric_type"] == "api_requests"
        assert api_item["metadata"]["endpoint"] == "/search"
        assert api_item["metadata"]["is_bot"] is True

        # Check for bot access metrics
        response = self.table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date)
            & boto3.dynamodb.conditions.Key("metric_type_timestamp").begins_with("bot_access_")
        )
        assert len(response["Items"]) >= 1

        bot_item = response["Items"][0]
        assert bot_item["metric_type"] == "bot_access"
        assert bot_item["access_method"] == "direct_api"
        assert bot_item["content_accessed"] == ["art_001", "art_002"]

    def test_end_to_end_mcp_logging(self):
        """Test end-to-end logging for MCP server access."""
        # Setup
        bot_access_logger = BotAccessLogger(self.table_name)

        request_data = {
            "headers": {
                "user-agent": "MCP-Server/1.0",
                "x-mcp-server-id": "content-server-001",
                "x-agent-id": "claude-3-sonnet",
                "x-mcp-tool-name": "search_content",
            },
            "body": {"query": "sustainable energy solutions", "similarity_threshold": 0.8},
            "path": "/search",
            "httpMethod": "POST",
        }

        response_data = {
            "results": [{"content_id": "art_003", "title": "Solar Energy Innovations", "content_type": "article"}],
            "content_ids": ["art_003"],
            "total_results": 1,
        }

        # Execute
        result = bot_access_logger.log_search_access(request_data, response_data)

        # Verify logging success
        assert result is True

        # Verify MCP tool usage was logged
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        response = self.table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date)
            & boto3.dynamodb.conditions.Key("metric_type_timestamp").begins_with("mcp_tool_usage_")
        )
        assert len(response["Items"]) >= 1

        mcp_item = response["Items"][0]
        assert mcp_item["metric_type"] == "mcp_tool_usage"
        assert mcp_item["tool_name"] == "search_content"
        assert mcp_item["agent_id"] == "claude-3-sonnet"
        assert mcp_item["content_accessed_count"] == 1

        # Verify bot access was logged with MCP method
        response = self.table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date)
            & boto3.dynamodb.conditions.Key("metric_type_timestamp").begins_with("bot_access_")
        )
        assert len(response["Items"]) >= 1

        bot_item = response["Items"][0]
        assert bot_item["access_method"] == "mcp_server"
        assert bot_item["bot_id"] == "claude-3-sonnet"

    def test_content_query_logging_integration(self):
        """Test content query logging integration."""
        # Setup
        bot_access_logger = BotAccessLogger(self.table_name)

        content_id = "art_005"
        request_data = {
            "headers": {"user-agent": "Anthropic-Claude/3.0", "authorization": "Bearer sk-ant-test123"},
            "pathParameters": {"content_id": content_id},
            "path": f"/content/{content_id}",
            "httpMethod": "GET",
            "requestContext": {"identity": {"sourceIp": "203.0.113.1"}},
        }

        # Execute
        result = bot_access_logger.log_content_query_access(content_id, request_data)

        # Verify logging success
        assert result is True

        # Verify content access was logged
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        response = self.table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date)
            & boto3.dynamodb.conditions.Key("metric_type_timestamp").begins_with("content_access_")
        )
        assert len(response["Items"]) >= 1

        content_item = response["Items"][0]
        assert content_item["metric_type"] == "content_access"
        assert content_item["metadata"]["content_id"] == content_id
        assert content_item["metadata"]["access_type"] == "query"

    def test_analytics_data_aggregation(self):
        """Test that logged data can be aggregated for analytics."""
        # Setup - log multiple requests
        bot_access_logger = BotAccessLogger(self.table_name)

        # Log several different types of requests
        requests = [
            {
                "type": "direct_bot",
                "request_data": {
                    "headers": {"user-agent": "GPTBot/1.0", "x-partner-key": "pk_test1"},
                    "body": {"query": "test query 1"},
                    "path": "/search",
                },
                "response_data": {
                    "results": [{"content_id": "art_001"}],
                    "content_ids": ["art_001"],
                    "total_results": 1,
                },
            },
            {
                "type": "mcp",
                "request_data": {
                    "headers": {
                        "user-agent": "MCP-Server/1.0",
                        "x-mcp-server-id": "server-001",
                        "x-agent-id": "claude-3",
                    },
                    "body": {"query": "test query 2"},
                    "path": "/search",
                },
                "response_data": {
                    "results": [{"content_id": "art_002"}, {"content_id": "art_003"}],
                    "content_ids": ["art_002", "art_003"],
                    "total_results": 2,
                },
            },
        ]

        # Log all requests
        for req in requests:
            result = bot_access_logger.log_search_access(req["request_data"], req["response_data"])
            assert result is True

        # Verify we can query and aggregate the data
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Get all metrics for today
        response = self.table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date)
        )

        items = response["Items"]
        assert len(items) >= 6  # At least 3 metrics per request (search_query, api_requests, bot_access)

        # Aggregate by metric type
        metrics_by_type = {}
        for item in items:
            metric_type = item["metric_type"]
            if metric_type not in metrics_by_type:
                metrics_by_type[metric_type] = []
            metrics_by_type[metric_type].append(item)

        # Verify we have the expected metric types
        expected_types = ["search_query", "api_requests", "bot_access"]
        for expected_type in expected_types:
            assert expected_type in metrics_by_type
            assert len(metrics_by_type[expected_type]) >= 2  # At least 2 requests logged

        # Verify MCP-specific metrics
        if "mcp_tool_usage" in metrics_by_type:
            mcp_items = metrics_by_type["mcp_tool_usage"]
            assert len(mcp_items) >= 1
            assert any(item["agent_id"] == "claude-3" for item in mcp_items)

    def test_error_handling_in_logging(self):
        """Test error handling in logging operations."""
        # Setup with invalid table name to trigger errors
        bot_access_logger = BotAccessLogger("non-existent-table")

        request_data = {"headers": {"user-agent": "TestBot/1.0"}, "body": {"query": "test"}, "path": "/search"}

        response_data = {"results": [], "content_ids": [], "total_results": 0}

        # Execute - should handle errors gracefully (does not crash; reports failure)
        result = bot_access_logger.log_search_access(request_data, response_data)

        # Verify error is handled gracefully (returns False but doesn't crash)
        assert result is False

    def teardown_method(self):
        """Cleanup is handled by the setup_dynamodb fixture (within the active mock)."""
        pass


class TestSearchLoggingPerformance:
    """Performance tests for search logging functionality."""

    @mock_aws
    def test_logging_performance_under_load(self):
        """Test logging performance with multiple concurrent requests."""
        # Setup
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        table = dynamodb.create_table(
            TableName="perf-test-analytics-table",
            KeySchema=[
                {"AttributeName": "metric_date", "KeyType": "HASH"},
                {"AttributeName": "metric_type_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "metric_date", "AttributeType": "S"},
                {"AttributeName": "metric_type_timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()

        bot_access_logger = BotAccessLogger("perf-test-analytics-table")

        # Generate multiple requests
        num_requests = 50
        start_time = datetime.now()

        for i in range(num_requests):
            request_data = {
                "headers": {"user-agent": f"TestBot/{i}", "x-partner-key": f"pk_test_{i}"},
                "body": {"query": f"test query {i}"},
                "path": "/search",
            }

            response_data = {
                "results": [{"content_id": f"art_{i:03d}"}],
                "content_ids": [f"art_{i:03d}"],
                "total_results": 1,
            }

            result = bot_access_logger.log_search_access(request_data, response_data)
            assert result is True

        end_time = datetime.now()
        total_time = (end_time - start_time).total_seconds()

        # Verify performance (should complete within reasonable time)
        assert total_time < 30.0  # Should complete within 30 seconds

        # Verify all requests were logged
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        response = table.query(KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(current_date))

        # Should have at least 3 metrics per request (search_query, api_requests, bot_access)
        assert len(response["Items"]) >= num_requests * 3

        # Clean up
        table.delete()


if __name__ == "__main__":
    pytest.main([__file__])
