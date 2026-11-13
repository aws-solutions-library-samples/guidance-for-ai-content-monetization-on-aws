"""
Unit tests for AnalyticsCollector service.
"""

import pytest
import boto3
from moto import mock_aws
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch, MagicMock
import sys
import os

# Add lambda directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.analytics_collector import AnalyticsCollector, AnalyticsMetric, BotAccessRecord, MCPBusinessMetric


@pytest.fixture
def mock_dynamodb_table():
    """Create a mock DynamoDB table for testing."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create analytics table
        table = dynamodb.create_table(
            TableName="test-analytics-table",
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
                    "IndexName": "MetricTypeIndex",
                    "KeySchema": [
                        {"AttributeName": "metric_type", "KeyType": "HASH"},
                        {"AttributeName": "timestamp", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        # Wait for table to be created
        table.wait_until_exists()
        yield table


@pytest.fixture
def analytics_collector(mock_dynamodb_table):
    """Create an AnalyticsCollector instance with mock table."""
    # Ensure the collector uses the same table name as the fixture
    collector = AnalyticsCollector(table_name="test-analytics-table")
    # Verify the table exists
    collector.table = mock_dynamodb_table
    return collector


class TestAnalyticsCollector:
    """Test cases for AnalyticsCollector class."""

    def test_initialization(self, analytics_collector):
        """Test AnalyticsCollector initialization."""
        assert analytics_collector.table_name == "test-analytics-table"
        assert analytics_collector.table is not None
        assert analytics_collector.dynamodb is not None

    def test_get_current_timestamp(self, analytics_collector):
        """Test timestamp generation."""
        timestamp = analytics_collector._get_current_timestamp()
        assert isinstance(timestamp, str)
        assert "T" in timestamp  # ISO format
        assert timestamp.endswith("Z") or "+" in timestamp  # UTC timezone

    def test_get_current_date(self, analytics_collector):
        """Test date generation."""
        date = analytics_collector._get_current_date()
        assert isinstance(date, str)
        assert len(date) == 10  # YYYY-MM-DD format
        assert date.count("-") == 2

    def test_record_api_access_success(self, analytics_collector):
        """Test successful API access recording."""
        endpoint = "/content/search"
        user_id = "test_user"
        metadata = {"response_time_ms": 150, "status_code": 200, "user_agent": "TestBot/1.0"}

        result = analytics_collector.record_api_access(endpoint, user_id, metadata)

        assert result is True

    def test_record_api_access_failure(self, analytics_collector):
        """Test API access recording with DynamoDB error."""
        with patch.object(analytics_collector.table, "put_item", side_effect=Exception("DynamoDB error")):
            result = analytics_collector.record_api_access("/test", "user", {})
            assert result is False

    def test_record_content_access_success(self, analytics_collector):
        """Test successful content access recording."""
        content_id = "content_123"
        access_type = "search_result"
        requester_info = {"user_id": "bot_user", "ip_address": "192.168.1.100", "user_agent": "OpenAI-GPT/1.0"}

        result = analytics_collector.record_content_access(content_id, access_type, requester_info)

        assert result is True

    def test_record_search_query_success(self, analytics_collector):
        """Test successful search query recording."""
        query = "machine learning algorithms"
        results_count = 25
        requester_info = {"bot_id": "openai-gpt", "user_agent": "OpenAI-GPT/1.0", "authentication_method": "api_key"}

        result = analytics_collector.record_search_query(query, results_count, requester_info)

        assert result is True

    def test_record_mcp_tool_usage_success(self, analytics_collector):
        """Test successful MCP tool usage recording."""
        tool_name = "search_content"
        agent_id = "bedrock-agent-123"
        content_accessed = ["content_1", "content_2", "content_3"]
        metadata = {
            "server_id": "iab-content-mcp-server",
            "parameters": {"query": "test query", "limit": 10},
            "response_size": 3,
            "response_time_ms": 150,
        }

        result = analytics_collector.record_mcp_tool_usage(tool_name, agent_id, content_accessed, metadata)

        assert result is True

    def test_record_bot_access_success(self, analytics_collector):
        """Test successful bot access recording."""
        bot_id = "claude-3-sonnet"
        content_accessed = ["content_456", "content_789"]
        query_type = "search_query"
        access_method = "mcp_server"

        result = analytics_collector.record_bot_access(bot_id, content_accessed, query_type, access_method)

        assert result is True

    def test_record_business_metrics_success(self, analytics_collector):
        """Test successful business metrics recording."""
        metric_type = "revenue_generated"
        value = 15.75
        context = {"content_category": "technology", "access_method": "mcp_server", "agent_type": "claude-3-sonnet"}

        result = analytics_collector.record_business_metrics(metric_type, value, context)

        assert result is True

    def test_calculate_revenue_impact(self, analytics_collector):
        """Test revenue impact calculation."""
        content_list = ["content_1", "content_2", "content_3"]
        revenue = analytics_collector._calculate_revenue_impact(content_list)

        assert revenue == Decimal("0.30")  # 3 items * $0.10 each

        # Test empty list
        empty_revenue = analytics_collector._calculate_revenue_impact([])
        assert empty_revenue == Decimal("0.0")

    def test_aggregate_metrics_placeholder(self, analytics_collector):
        """Test metrics aggregation placeholder."""
        result = analytics_collector.aggregate_metrics("day")

        assert isinstance(result, dict)
        assert "time_period" in result
        assert result["time_period"] == "day"
        assert "total_api_requests" in result
        assert "total_content_access" in result
        assert "bot_access_count" in result

    def test_export_analytics_data_placeholder(self, analytics_collector):
        """Test analytics data export placeholder."""
        filters = {"date_range": "2024-01-01:2024-01-31", "metric_type": "api_requests"}
        result = analytics_collector.export_analytics_data(filters)

        assert isinstance(result, dict)
        assert "filters" in result
        assert result["filters"] == filters
        assert "data" in result
        assert "export_timestamp" in result


class TestAnalyticsMetric:
    """Test cases for AnalyticsMetric dataclass."""

    def test_analytics_metric_creation(self):
        """Test AnalyticsMetric creation."""
        metric = AnalyticsMetric(
            metric_date="2024-01-15",
            metric_type_timestamp="api_requests_2024-01-15T10:00:00Z",
            metric_type="api_requests",
            value=1,
            metadata={"endpoint": "/search", "user_id": "test_user"},
            timestamp="2024-01-15T10:00:00Z",
        )

        assert metric.metric_date == "2024-01-15"
        assert metric.metric_type == "api_requests"
        assert metric.value == 1
        assert isinstance(metric.metadata, dict)


class TestBotAccessRecord:
    """Test cases for BotAccessRecord dataclass."""

    def test_bot_access_record_creation(self):
        """Test BotAccessRecord creation."""
        record = BotAccessRecord(
            metric_date="2024-01-15",
            metric_type_timestamp="bot_access_openai-gpt_2024-01-15T10:00:00Z",
            metric_type="bot_access",
            bot_id="openai-gpt",
            access_method="direct_api",
            access_type="search_query",
            content_accessed=["content_1", "content_2"],
            query_details={"query": "test", "results_count": 2},
            timestamp="2024-01-15T10:00:00Z",
            revenue_impact=Decimal("0.20"),
        )

        assert record.bot_id == "openai-gpt"
        assert record.access_method == "direct_api"
        assert len(record.content_accessed) == 2
        assert record.revenue_impact == Decimal("0.20")


class TestMCPBusinessMetric:
    """Test cases for MCPBusinessMetric dataclass."""

    def test_mcp_business_metric_creation(self):
        """Test MCPBusinessMetric creation."""
        metric = MCPBusinessMetric(
            metric_date="2024-01-15",
            metric_type_timestamp="mcp_tool_usage_2024-01-15T10:00:00Z",
            metric_type="mcp_tool_usage",
            tool_name="search_content",
            agent_id="bedrock-agent-123",
            usage_count=1,
            content_accessed_count=5,
            business_metrics={"revenue_generated": 0.50},
            technical_metrics={"response_time_ms": 150},
        )

        assert metric.tool_name == "search_content"
        assert metric.agent_id == "bedrock-agent-123"
        assert metric.content_accessed_count == 5
        assert isinstance(metric.business_metrics, dict)
        assert isinstance(metric.technical_metrics, dict)


class TestAnalyticsCollectorIntegration:
    """Integration tests for AnalyticsCollector."""

    def test_full_analytics_workflow(self, analytics_collector):
        """Test complete analytics collection workflow."""
        # Record API access
        api_result = analytics_collector.record_api_access("/content/search", "test_bot", {"response_time_ms": 100})
        assert api_result is True

        # Record content access
        content_result = analytics_collector.record_content_access(
            "content_123", "search_result", {"bot_id": "test_bot", "user_agent": "TestBot/1.0"}
        )
        assert content_result is True

        # Record search query
        search_result = analytics_collector.record_search_query("test query", 10, {"bot_id": "test_bot"})
        assert search_result is True

        # Record MCP tool usage
        mcp_result = analytics_collector.record_mcp_tool_usage(
            "search_content", "test_agent", ["content_1", "content_2"], {"server_id": "test_server"}
        )
        assert mcp_result is True

        # Record bot access
        bot_result = analytics_collector.record_bot_access("test_bot", ["content_1"], "search_query", "direct_api")
        assert bot_result is True

    @patch.dict(os.environ, {"ANALYTICS_TABLE_NAME": "custom-analytics-table"})
    def test_custom_table_name_from_environment(self):
        """Test using custom table name from environment variable."""
        collector = AnalyticsCollector()
        assert collector.table_name == "custom-analytics-table"

    def test_error_handling_in_all_methods(self, analytics_collector):
        """Test error handling across all recording methods."""
        with patch.object(analytics_collector.table, "put_item", side_effect=Exception("DynamoDB error")):
            # All methods should return False on error
            assert analytics_collector.record_api_access("/test", "user", {}) is False
            assert analytics_collector.record_content_access("content", "type", {}) is False
            assert analytics_collector.record_search_query("query", 1, {}) is False
            assert analytics_collector.record_mcp_tool_usage("tool", "agent", [], {}) is False
            assert analytics_collector.record_bot_access("bot", [], "type", "method") is False
            assert analytics_collector.record_business_metrics("metric", 1.0, {}) is False
