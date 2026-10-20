"""
Integration tests for Analytics API endpoints

Tests the complete integration of analytics API endpoints with the analytics aggregation
service and real DynamoDB data.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import json
import boto3
from moto import mock_aws
import os
import sys
from datetime import datetime, timezone, timedelta

# Add lambda directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from analytics_api import AnalyticsAPIHandler
from analytics_aggregation import AnalyticsAggregator


@mock_aws
class TestAnalyticsAPIIntegration(unittest.TestCase):
    """Integration test cases for Analytics API with real AWS services"""

    def setUp(self):
        """Set up test fixtures with mocked AWS services"""
        # Set up environment variables
        os.environ["ANALYTICS_TABLE_NAME"] = "test-analytics-table"
        os.environ["ANALYTICS_LAMBDA_NAME"] = "test-analytics-aggregation"

        # Create DynamoDB table
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        self.create_analytics_table()

        # Create Lambda function
        self.lambda_client = boto3.client("lambda", region_name="us-west-2")
        self.create_analytics_lambda()

        # Initialize handler
        self.handler = AnalyticsAPIHandler("test-analytics-aggregation")

        # Seed test data
        self.seed_test_data()

    def create_analytics_table(self):
        """Create the analytics DynamoDB table for testing"""
        self.analytics_table = self.dynamodb.create_table(
            TableName="test-analytics-table",
            KeySchema=[
                {"AttributeName": "metric_date", "KeyType": "HASH"},
                {"AttributeName": "metric_type_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "metric_date", "AttributeType": "S"},
                {"AttributeName": "metric_type_timestamp", "AttributeType": "S"},
                {"AttributeName": "metric_type", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "MetricTypeIndex",
                    "KeySchema": [
                        {"AttributeName": "metric_type", "KeyType": "HASH"},
                        {"AttributeName": "metric_date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )

    def create_analytics_lambda(self):
        """Create the analytics aggregation Lambda function for testing"""
        # Skip Lambda creation for now - we'll mock the Lambda calls instead
        pass

    def seed_test_data(self):
        """Seed the analytics table with test data"""
        # Generate test data for the last 7 days
        base_date = datetime.now(timezone.utc).date()

        for i in range(7):
            date = (base_date - timedelta(days=i)).strftime("%Y-%m-%d")
            timestamp = f"{date}T10:00:00Z"

            # API requests metric
            self.analytics_table.put_item(
                Item={
                    "metric_date": date,
                    "metric_type_timestamp": f"api_requests_{timestamp}",
                    "metric_type": "api_requests",
                    "value": 1200 + (i * 50),
                    "timestamp": timestamp,
                    "metadata": {"endpoint": "/content/search", "response_time_ms": 150},
                }
            )

            # Bot access metric
            self.analytics_table.put_item(
                Item={
                    "metric_date": date,
                    "metric_type_timestamp": f"bot_access_{timestamp}",
                    "metric_type": "bot_access",
                    "bot_id": "openai-gpt",
                    "access_method": "mcp_server",
                    "content_accessed": ["content_123", "content_456"],
                    "timestamp": timestamp,
                }
            )

            # MCP tool usage metric
            self.analytics_table.put_item(
                Item={
                    "metric_date": date,
                    "metric_type_timestamp": f"mcp_tool_usage_{timestamp}",
                    "metric_type": "mcp_tool_usage",
                    "value": 1,
                    "tool_name": "search_content",
                    "agent_id": "bedrock-agent-123",
                    "content_accessed_count": 25,
                    "timestamp": timestamp,
                }
            )

    @patch("analytics_api.lambda_client")
    def test_usage_analytics_integration(self, mock_lambda_client):
        """Test complete usage analytics integration"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "period": "7 days",
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-07",
                        "dates": ["2024-01-01", "2024-01-02", "2024-01-03"],
                        "daily_api_requests": [1200, 1450, 1100],
                        "daily_revenue": [45.60, 52.20, 41.80],
                        "daily_bot_access": [120, 145, 110],
                        "daily_mcp_usage": [80, 95, 75],
                        "total_metrics": {
                            "total_requests": 3750,
                            "total_revenue": 139.60,
                            "total_bot_access": 375,
                            "total_mcp_usage": 250,
                            "average_daily_requests": 1250.0,
                            "average_daily_revenue": 46.53,
                        },
                        "content_type_distribution": {"article": 1500, "report": 800, "video": 450},
                        "revenue_by_content_type": {"article": 75.00, "report": 40.00, "video": 24.60},
                        "revenue_by_access_method": {"direct_api": 89.60, "mcp_server": 50.00},
                    }
                ),
            }
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        headers = {}
        query_params = {"date_range": "last_7_days"}

        result = self.handler.handle_usage_analytics(headers, query_params)

        # Verify successful response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])

        # Verify response structure
        self.assertIn("period", body)
        self.assertIn("summary", body)
        self.assertIn("daily_breakdown", body)
        self.assertIn("content_type_distribution", body)
        self.assertIn("access_method_breakdown", body)

        # Verify summary data
        summary = body["summary"]
        self.assertIsInstance(summary["total_requests"], int)
        self.assertIsInstance(summary["total_bot_access"], int)
        self.assertIsInstance(summary["total_mcp_usage"], int)

        # Verify daily breakdown structure
        daily = body["daily_breakdown"]
        self.assertIsInstance(daily["dates"], list)
        self.assertIsInstance(daily["api_requests"], list)
        self.assertIsInstance(daily["bot_access"], list)
        self.assertIsInstance(daily["mcp_usage"], list)

        # Verify data consistency
        self.assertEqual(len(daily["dates"]), len(daily["api_requests"]))
        self.assertEqual(len(daily["dates"]), len(daily["bot_access"]))

    @patch("analytics_api.lambda_client")
    def test_revenue_analytics_integration(self, mock_lambda_client):
        """Test complete revenue analytics integration"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "period": "Last 30 days",
                        "total_revenue": 2847.50,
                        "average_revenue_per_request": 0.076,
                        "projected_monthly_revenue": 2847.50,
                        "revenue_growth": {"growth_rate": 12.5, "trend": "growing"},
                        "revenue_by_content_type": {"article": 1247.50, "report": 890.25, "video": 709.75},
                        "revenue_by_access_method": {"direct_api": 1567.80, "mcp_server": 1279.70},
                        "daily_revenue_trend": [45.60, 52.20, 41.80],
                        "dates": ["2024-01-01", "2024-01-02", "2024-01-03"],
                        "top_revenue_sources": [],
                    }
                ),
            }
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        headers = {}
        query_params = {"time_period": "last_30_days", "breakdown_by": "content_type"}

        result = self.handler.handle_revenue_analytics(headers, query_params)

        # Verify successful response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])

        # Verify response structure
        self.assertIn("total_revenue", body)
        self.assertIn("average_revenue_per_request", body)
        self.assertIn("projected_monthly_revenue", body)
        self.assertIn("revenue_growth", body)
        self.assertIn("breakdown", body)
        self.assertIn("daily_revenue_trend", body)

        # Verify revenue data types
        self.assertIsInstance(body["total_revenue"], (int, float))
        self.assertIsInstance(body["average_revenue_per_request"], (int, float))
        self.assertIsInstance(body["projected_monthly_revenue"], (int, float))

        # Verify revenue growth structure
        growth = body["revenue_growth"]
        self.assertIn("growth_rate", growth)
        self.assertIn("trend", growth)

        # Verify breakdown data
        breakdown = body["breakdown"]
        self.assertIsInstance(breakdown, dict)

        # Verify daily trend data
        trend = body["daily_revenue_trend"]
        self.assertIn("dates", trend)
        self.assertIn("daily_revenue", trend)
        self.assertIsInstance(trend["dates"], list)
        self.assertIsInstance(trend["daily_revenue"], list)

    @patch("analytics_api.lambda_client")
    def test_trends_analytics_integration(self, mock_lambda_client):
        """Test complete trends analytics integration"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "period": "30 days",
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-30",
                        "dates": ["2024-01-01", "2024-01-02", "2024-01-03"],
                        "daily_api_requests": [1200, 1450, 1100],
                        "daily_revenue": [45.60, 52.20, 41.80],
                        "daily_bot_access": [120, 145, 110],
                        "daily_mcp_usage": [80, 95, 75],
                        "total_metrics": {
                            "total_requests": 3750,
                            "total_revenue": 139.60,
                            "total_bot_access": 375,
                            "total_mcp_usage": 250,
                            "average_daily_requests": 1250.0,
                            "average_daily_revenue": 46.53,
                        },
                        "content_type_distribution": {"article": 1500, "report": 800, "video": 450},
                        "revenue_by_content_type": {"article": 75.00, "report": 40.00, "video": 24.60},
                        "revenue_by_access_method": {"direct_api": 89.60, "mcp_server": 50.00},
                    }
                ),
            }
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        headers = {}
        query_params = {"days": "30", "metric_type": "all", "granularity": "daily"}

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify successful response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])

        # Verify response structure
        self.assertIn("period", body)
        self.assertIn("start_date", body)
        self.assertIn("end_date", body)
        self.assertIn("granularity", body)
        self.assertIn("metric_type", body)
        self.assertIn("trends", body)
        self.assertIn("summary_statistics", body)
        self.assertIn("trend_analysis", body)
        self.assertIn("content_insights", body)

        # Verify trends data structure
        trends = body["trends"]
        self.assertIn("dates", trends)
        self.assertIn("api_requests", trends)
        self.assertIn("revenue", trends)
        self.assertIn("bot_access", trends)
        self.assertIn("mcp_usage", trends)

        # Verify all trend arrays have same length
        dates_len = len(trends["dates"])
        self.assertEqual(len(trends["api_requests"]), dates_len)
        self.assertEqual(len(trends["revenue"]), dates_len)
        self.assertEqual(len(trends["bot_access"]), dates_len)
        self.assertEqual(len(trends["mcp_usage"]), dates_len)

        # Verify summary statistics
        summary = body["summary_statistics"]
        required_stats = [
            "total_requests",
            "total_revenue",
            "total_bot_access",
            "total_mcp_usage",
            "average_daily_requests",
            "average_daily_revenue",
        ]
        for stat in required_stats:
            self.assertIn(stat, summary)
            self.assertIsInstance(summary[stat], (int, float))

        # Verify trend analysis
        analysis = body["trend_analysis"]
        self.assertIn("request_trend", analysis)
        self.assertIn("revenue_trend", analysis)
        self.assertIn("volatility", analysis)

        # Verify content insights
        insights = body["content_insights"]
        self.assertIn("content_type_distribution", insights)
        self.assertIn("revenue_by_content_type", insights)
        self.assertIn("revenue_by_access_method", insights)

    def test_trends_analytics_filtered_metric_integration(self):
        """Test trends analytics with metric filtering"""
        headers = {}
        query_params = {"days": "7", "metric_type": "revenue", "granularity": "daily"}

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify successful response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])

        # Verify metric filtering worked
        self.assertEqual(body["metric_type"], "revenue")

        trends = body["trends"]
        # Should only have dates and revenue data
        self.assertIn("dates", trends)
        self.assertIn("revenue", trends)
        # Other metrics should be filtered out
        self.assertNotIn("api_requests", trends)
        self.assertNotIn("bot_access", trends)
        self.assertNotIn("mcp_usage", trends)

    def test_usage_analytics_with_content_type_filter_integration(self):
        """Test usage analytics with content type filtering"""
        headers = {}
        query_params = {"date_range": "last_7_days", "content_type": "article"}

        result = self.handler.handle_usage_analytics(headers, query_params)

        # Verify successful response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])

        # Verify filter was applied
        filters = body["filters_applied"]
        self.assertEqual(filters["content_type"], "article")

        # Verify content type distribution reflects filter
        content_dist = body["content_type_distribution"]
        if content_dist:  # May be empty if no article content in test data
            self.assertIn("article", content_dist)

    def test_revenue_analytics_access_method_breakdown_integration(self):
        """Test revenue analytics with access method breakdown"""
        headers = {}
        query_params = {"time_period": "last_7_days", "breakdown_by": "access_method"}

        result = self.handler.handle_revenue_analytics(headers, query_params)

        # Verify successful response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])

        # Verify breakdown type
        self.assertEqual(body["breakdown_by"], "access_method")

        # Verify breakdown contains access method data
        breakdown = body["breakdown"]
        # Should contain access method keys
        expected_methods = ["direct_api", "mcp_server"]
        for method in expected_methods:
            if method in breakdown:
                self.assertIsInstance(breakdown[method], (int, float))

    @patch("analytics_api.lambda_client")
    def test_analytics_aggregation_service_failure_integration(self, mock_lambda_client):
        """Test handling of analytics aggregation service failure"""
        # Mock Lambda service failure
        mock_lambda_client.invoke.side_effect = Exception("Lambda service unavailable")

        headers = {}
        query_params = {"date_range": "last_7_days"}

        result = self.handler.handle_usage_analytics(headers, query_params)

        # Verify error handling
        self.assertEqual(result["statusCode"], 500)

        body = json.loads(result["body"])
        self.assertIn("error", body)
        self.assertIn("Failed to retrieve usage analytics", body["error"])

    def test_invalid_parameters_integration(self):
        """Test handling of invalid parameters"""
        # Test invalid days parameter
        headers = {}
        query_params = {"days": "500"}  # Exceeds maximum

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify error response
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertIn("Maximum days allowed is 365", body["error"])

        # Test invalid granularity
        query_params = {"granularity": "weekly"}

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify error response
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertIn("Granularity must be", body["error"])

    def test_custom_date_range_parsing_integration(self):
        """Test custom date range parsing in integration context"""
        headers = {}
        query_params = {"date_range": "custom:2024-01-01:2024-01-15"}

        # This should parse to 15 days
        result = self.handler.handle_usage_analytics(headers, query_params)

        # Verify successful response (even if data is mocked)
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])

        # Verify the request was processed
        self.assertIn("period", body)
        self.assertIn("summary", body)

    def test_response_format_consistency_integration(self):
        """Test that all analytics endpoints return consistent response formats"""
        headers = {}

        # Test usage analytics response format
        usage_result = self.handler.handle_usage_analytics(headers, {"date_range": "last_7_days"})
        self.assertEqual(usage_result["statusCode"], 200)
        self.assertIn("Content-Type", usage_result["headers"])
        self.assertEqual(usage_result["headers"]["Content-Type"], "application/json")

        # Test revenue analytics response format
        revenue_result = self.handler.handle_revenue_analytics(headers, {"time_period": "last_7_days"})
        self.assertEqual(revenue_result["statusCode"], 200)
        self.assertIn("Content-Type", revenue_result["headers"])
        self.assertEqual(revenue_result["headers"]["Content-Type"], "application/json")

        # Test trends analytics response format
        trends_result = self.handler.handle_trends_analytics(headers, {"days": "7"})
        self.assertEqual(trends_result["statusCode"], 200)
        self.assertIn("Content-Type", trends_result["headers"])
        self.assertEqual(trends_result["headers"]["Content-Type"], "application/json")

        # Verify CORS headers are present
        for result in [usage_result, revenue_result, trends_result]:
            headers = result["headers"]
            self.assertIn("Access-Control-Allow-Origin", headers)
            self.assertIn("Access-Control-Allow-Headers", headers)
            self.assertIn("Access-Control-Allow-Methods", headers)

    def tearDown(self):
        """Clean up test resources"""
        # Clean up environment variables
        if "ANALYTICS_TABLE_NAME" in os.environ:
            del os.environ["ANALYTICS_TABLE_NAME"]
        if "ANALYTICS_LAMBDA_NAME" in os.environ:
            del os.environ["ANALYTICS_LAMBDA_NAME"]


if __name__ == "__main__":
    unittest.main()
