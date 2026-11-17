"""
Unit tests for analytics aggregation functionality.
"""

import pytest
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch, MagicMock
import boto3
from moto import mock_aws

# Import the module under test
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from analytics_aggregation import AnalyticsAggregator, lambda_handler


class TestAnalyticsAggregator:
    """Test cases for AnalyticsAggregator class"""

    def setup_method(self, method):
        """Set up test environment before each test"""
        # Start moto mock
        self.mock = mock_aws()
        self.mock.start()

        # Create mock DynamoDB table
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create analytics table
        self.table = self.dynamodb.create_table(
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
                    "IndexName": "metric-type-index",
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

        # Initialize aggregator with test table
        self.aggregator = AnalyticsAggregator("test-analytics-table")

        # Sample test data
        self.test_date = "2024-01-15"
        self.test_timestamp = "2024-01-15T10:00:00Z"

    def teardown_method(self, method):
        """Clean up after each test"""
        self.mock.stop()

    def _create_sample_analytics_data(self):
        """Create sample analytics data for testing"""
        sample_items = [
            {
                "metric_date": self.test_date,
                "metric_type_timestamp": f"api_requests_{self.test_timestamp}",
                "metric_type": "api_requests",
                "value": 100,
                "timestamp": self.test_timestamp,
                "metadata": {"endpoint": "/query", "user_id": "test_user"},
            },
            {
                "metric_date": self.test_date,
                "metric_type_timestamp": f"content_access_{self.test_timestamp}",
                "metric_type": "content_access",
                "value": 1,
                "timestamp": self.test_timestamp,
                "metadata": {"content_id": "art_001", "access_type": "query"},
            },
            {
                "metric_date": self.test_date,
                "metric_type_timestamp": f"bot_access_openai_{self.test_timestamp}",
                "metric_type": "bot_access",
                "bot_id": "openai-gpt",
                "access_method": "mcp_server",
                "access_type": "search_query",
                "content_accessed": ["art_001", "rpt_001"],
                "timestamp": self.test_timestamp,
                "revenue_impact": Decimal("0.30"),
            },
            {
                "metric_date": self.test_date,
                "metric_type_timestamp": f"mcp_tool_usage_{self.test_timestamp}",
                "metric_type": "mcp_tool_usage",
                "tool_name": "search_content",
                "agent_id": "bedrock-agent-123",
                "usage_count": 1,
                "content_accessed_count": 5,
                "timestamp": self.test_timestamp,
            },
        ]

        # Insert sample data
        for item in sample_items:
            self.table.put_item(Item=item)

        return sample_items

    def test_aggregate_daily_metrics_success(self):
        """Test successful daily metrics aggregation"""
        # Create sample data
        self._create_sample_analytics_data()

        # Aggregate metrics
        result = self.aggregator.aggregate_daily_metrics(self.test_date)

        # Verify results
        assert result["date"] == self.test_date
        assert result["api_requests"] == 100
        assert result["content_access"] == 1
        assert result["bot_access_count"] == 1
        assert result["mcp_tool_usage"] == 1
        assert result["unique_bots"] == 1
        assert result["unique_agents"] == 1
        assert result["revenue_generated"] > 0
        assert "hourly_distribution" in result
        assert "content_types_accessed" in result

    def test_aggregate_daily_metrics_empty_data(self):
        """Test daily metrics aggregation with no data"""
        result = self.aggregator.aggregate_daily_metrics("2024-01-01")

        # Verify empty results
        assert result["date"] == "2024-01-01"
        assert result["api_requests"] == 0
        assert result["content_access"] == 0
        assert result["bot_access_count"] == 0
        assert result["revenue_generated"] == 0.0

    def test_aggregate_date_range_metrics(self):
        """Test date range metrics aggregation"""
        # Create sample data
        self._create_sample_analytics_data()

        # Test single day range
        start_date = self.test_date
        end_date = self.test_date

        result = self.aggregator.aggregate_date_range_metrics(start_date, end_date)

        # Verify results
        assert result["start_date"] == start_date
        assert result["end_date"] == end_date
        assert result["total_api_requests"] == 100
        assert result["total_content_access"] == 1
        assert result["total_revenue"] > 0
        assert len(result["daily_breakdown"]) == 1

    def test_generate_usage_trends(self):
        """Test usage trends generation"""
        # Create sample data
        self._create_sample_analytics_data()

        # Generate trends for 7 days
        result = self.aggregator.generate_usage_trends(7)

        # Verify results structure
        assert result["period"] == "7 days"
        assert "start_date" in result
        assert "end_date" in result
        assert "daily_api_requests" in result
        assert "daily_revenue" in result
        assert "total_metrics" in result
        assert len(result["daily_api_requests"]) == 7
        assert len(result["daily_revenue"]) == 7

    def test_calculate_revenue_metrics(self):
        """Test revenue metrics calculation"""
        # Create sample data
        self._create_sample_analytics_data()

        # Calculate revenue metrics
        result = self.aggregator.calculate_revenue_metrics("7d")

        # Verify results
        assert result["period"] == "Last 7 days"
        assert "total_revenue" in result
        assert "average_revenue_per_request" in result
        assert "revenue_by_content_type" in result
        assert "revenue_by_access_method" in result
        assert "revenue_growth" in result
        assert "projected_monthly_revenue" in result

    def test_estimate_content_type(self):
        """Test content type estimation from content ID"""
        # Test different content ID patterns
        assert self.aggregator._estimate_content_type("art_001") == "article"
        assert self.aggregator._estimate_content_type("rpt_001") == "report"
        assert self.aggregator._estimate_content_type("vid_001") == "video"
        assert self.aggregator._estimate_content_type("doc_001") == "document"
        assert self.aggregator._estimate_content_type("unknown_001") == "article"  # Default

    def test_calculate_revenue_growth(self):
        """Test revenue growth calculation"""
        # Test with growing revenue
        daily_revenue = [10.0, 12.0, 15.0, 18.0, 20.0, 22.0, 25.0, 28.0, 30.0, 32.0, 35.0, 38.0, 40.0, 42.0]
        growth = self.aggregator._calculate_revenue_growth(daily_revenue)

        assert growth["trend"] == "growing"
        assert growth["growth_rate"] > 0
        assert "first_week_revenue" in growth
        assert "last_week_revenue" in growth

        # Test with stable revenue
        stable_revenue = [20.0] * 14
        growth = self.aggregator._calculate_revenue_growth(stable_revenue)
        assert growth["trend"] == "stable"
        assert growth["growth_rate"] == 0.0

    def test_identify_top_revenue_sources(self):
        """Test identification of top revenue sources"""
        trends_data = {
            "revenue_by_content_type": {"article": 100.0, "report": 200.0, "video": 50.0},
            "revenue_by_access_method": {"mcp_server": 250.0, "direct_api": 100.0},
        }

        sources = self.aggregator._identify_top_revenue_sources(trends_data)

        # Verify results
        assert len(sources) <= 5
        assert all("source" in s and "revenue" in s and "percentage" in s for s in sources)

        # Should be sorted by revenue (highest first)
        revenues = [s["revenue"] for s in sources]
        assert revenues == sorted(revenues, reverse=True)

    @patch("analytics_aggregation.logger")
    def test_error_handling(self, mock_logger):
        """Test error handling in aggregation methods"""
        # Test with invalid table name
        invalid_aggregator = AnalyticsAggregator("non-existent-table")

        result = invalid_aggregator.aggregate_daily_metrics("2024-01-01")

        # Should return error result
        assert "error" in result
        assert result["api_requests"] == 0
        assert result["revenue_generated"] == 0.0

        # Verify error was logged
        mock_logger.error.assert_called()


class TestLambdaHandler:
    """Test cases for Lambda handler function"""

    @patch("analytics_aggregation.AnalyticsAggregator")
    def test_lambda_handler_aggregate_daily(self, mock_aggregator_class):
        """Test Lambda handler for daily aggregation"""
        # Mock aggregator instance
        mock_aggregator = Mock()
        mock_aggregator.aggregate_daily_metrics.return_value = {
            "date": "2024-01-15",
            "api_requests": 100,
            "revenue_generated": 50.0,
        }
        mock_aggregator_class.return_value = mock_aggregator

        # Test event
        event = {"operation": "aggregate_daily", "parameters": {"date": "2024-01-15"}}

        # Call handler
        result = lambda_handler(event, {})

        # Verify response
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["date"] == "2024-01-15"
        assert body["api_requests"] == 100

        # Verify aggregator was called correctly
        mock_aggregator.aggregate_daily_metrics.assert_called_once_with("2024-01-15")

    @patch("analytics_aggregation.AnalyticsAggregator")
    def test_lambda_handler_usage_trends(self, mock_aggregator_class):
        """Test Lambda handler for usage trends"""
        # Mock aggregator instance
        mock_aggregator = Mock()
        mock_aggregator.generate_usage_trends.return_value = {
            "period": "30 days",
            "total_metrics": {"total_requests": 1000},
        }
        mock_aggregator_class.return_value = mock_aggregator

        # Test event
        event = {"operation": "usage_trends", "parameters": {"days": 30}}

        # Call handler
        result = lambda_handler(event, {})

        # Verify response
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["period"] == "30 days"

        # Verify aggregator was called correctly
        mock_aggregator.generate_usage_trends.assert_called_once_with(30)

    @patch("analytics_aggregation.AnalyticsAggregator")
    def test_lambda_handler_revenue_metrics(self, mock_aggregator_class):
        """Test Lambda handler for revenue metrics"""
        # Mock aggregator instance
        mock_aggregator = Mock()
        mock_aggregator.calculate_revenue_metrics.return_value = {"period": "Last 30 days", "total_revenue": 500.0}
        mock_aggregator_class.return_value = mock_aggregator

        # Test event
        event = {"operation": "revenue_metrics", "parameters": {"date_range": "30d"}}

        # Call handler
        result = lambda_handler(event, {})

        # Verify response
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total_revenue"] == 500.0

        # Verify aggregator was called correctly
        mock_aggregator.calculate_revenue_metrics.assert_called_once_with("30d")

    def test_lambda_handler_invalid_operation(self):
        """Test Lambda handler with invalid operation"""
        event = {"operation": "invalid_operation", "parameters": {}}

        result = lambda_handler(event, {})

        # Verify error response
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body
        assert "Unknown operation" in body["error"]

    def test_lambda_handler_missing_parameters(self):
        """Test Lambda handler with missing required parameters"""
        event = {
            "operation": "aggregate_range",
            "parameters": {
                "start_date": "2024-01-01"
                # Missing end_date
            },
        }

        result = lambda_handler(event, {})

        # Verify error response
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body
        assert "start_date and end_date are required" in body["error"]

    @patch("analytics_aggregation.AnalyticsAggregator")
    def test_lambda_handler_exception(self, mock_aggregator_class):
        """Test Lambda handler exception handling"""
        # Mock aggregator to raise exception
        mock_aggregator_class.side_effect = Exception("Test exception")

        event = {"operation": "usage_trends", "parameters": {}}

        result = lambda_handler(event, {})

        # Verify error response
        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "error" in body
        assert "Test exception" in body["error"]

    def test_lambda_handler_default_operation(self):
        """Test Lambda handler with default operation"""
        with patch("analytics_aggregation.AnalyticsAggregator") as mock_aggregator_class:
            mock_aggregator = Mock()
            mock_aggregator.generate_usage_trends.return_value = {"period": "30 days"}
            mock_aggregator_class.return_value = mock_aggregator

            # Event without operation (should default to usage_trends)
            event = {}

            result = lambda_handler(event, {})

            # Verify default operation was called
            assert result["statusCode"] == 200
            mock_aggregator.generate_usage_trends.assert_called_once_with(30)


class TestAnalyticsAggregatorIntegration:
    """Integration tests for AnalyticsAggregator with more complex scenarios"""

    def setup_method(self, method):
        """Set up test environment for integration tests"""
        # Start moto mock
        self.mock = mock_aws()
        self.mock.start()

        # Create mock DynamoDB table
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        self.table = self.dynamodb.create_table(
            TableName="integration-test-table",
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
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

        self.aggregator = AnalyticsAggregator("integration-test-table")

    def teardown_method(self, method):
        """Clean up after each test"""
        self.mock.stop()

    def test_multi_day_aggregation_with_realistic_data(self):
        """Test aggregation across multiple days with realistic data patterns"""
        # Create realistic multi-day data
        base_date = datetime(2024, 1, 15)

        for day_offset in range(7):  # 7 days of data
            current_date = base_date + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")

            # Simulate varying daily activity
            daily_requests = 100 + (day_offset * 20)  # Growing activity
            daily_bot_access = 10 + (day_offset * 2)

            for hour in range(24):
                timestamp = current_date.replace(hour=hour).isoformat() + "Z"

                # API requests (hourly distribution)
                hourly_requests = max(1, daily_requests // 24 + (hour % 3))  # Some variation
                self.table.put_item(
                    Item={
                        "metric_date": date_str,
                        "metric_type_timestamp": f"api_requests_{timestamp}",
                        "metric_type": "api_requests",
                        "value": hourly_requests,
                        "timestamp": timestamp,
                        "metadata": {"endpoint": "/query"},
                    }
                )

                # Bot access (less frequent)
                if hour % 4 == 0:  # Every 4 hours
                    self.table.put_item(
                        Item={
                            "metric_date": date_str,
                            "metric_type_timestamp": f"bot_access_bot_{hour}_{timestamp}",
                            "metric_type": "bot_access",
                            "bot_id": f"bot_{hour % 3}",  # 3 different bots
                            "access_method": "mcp_server" if hour % 2 == 0 else "direct_api",
                            "access_type": "search_query",
                            "content_accessed": [f"art_{hour}", f"rpt_{hour}"],
                            "timestamp": timestamp,
                            "revenue_impact": Decimal("0.30"),
                        }
                    )

        # Test date range aggregation
        start_date = "2024-01-15"
        end_date = "2024-01-21"

        result = self.aggregator.aggregate_date_range_metrics(start_date, end_date)

        # Verify comprehensive results
        assert result["start_date"] == start_date
        assert result["end_date"] == end_date
        assert result["total_api_requests"] > 0
        assert len(result["daily_breakdown"]) == 7

        # Verify growth pattern
        daily_requests = [day.get("api_requests", 0) for day in result["daily_breakdown"]]
        assert daily_requests[0] < daily_requests[-1]  # Should show growth

        # Test usage trends
        trends = self.aggregator.generate_usage_trends(7)
        assert trends["period"] == "7 days"
        assert len(trends["daily_api_requests"]) == 7
        assert trends["total_metrics"]["total_requests"] > 0

        # Verify revenue calculations
        revenue_metrics = self.aggregator.calculate_revenue_metrics("7d")
        assert revenue_metrics["total_revenue"] > 0
        assert revenue_metrics["average_revenue_per_request"] > 0
        assert "revenue_growth" in revenue_metrics
        assert revenue_metrics["revenue_growth"]["trend"] in ["growing", "stable", "declining"]


if __name__ == "__main__":
    pytest.main([__file__])
