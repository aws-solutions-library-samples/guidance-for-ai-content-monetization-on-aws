"""
Unit tests for Analytics API endpoints

Tests the analytics API handlers that integrate with the analytics aggregation service
to provide usage, revenue, and trends data.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import json
from datetime import datetime, timezone
import sys
import os

# Add lambda directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from analytics_api import AnalyticsAPIHandler


class TestAnalyticsAPIHandler(unittest.TestCase):
    """Test cases for AnalyticsAPIHandler"""

    def setUp(self):
        """Set up test fixtures"""
        self.handler = AnalyticsAPIHandler("test-analytics-lambda")

        # Mock analytics aggregation response
        self.mock_trends_response = {
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

        self.mock_revenue_response = {
            "period": "Last 30 days",
            "total_revenue": 2847.50,
            "average_revenue_per_request": 0.076,
            "projected_monthly_revenue": 2847.50,
            "revenue_growth": {
                "growth_rate": 12.5,
                "trend": "growing",
                "first_week_revenue": 320.50,
                "last_week_revenue": 380.75,
            },
            "revenue_by_content_type": {"article": 1247.50, "report": 890.25, "video": 709.75},
            "revenue_by_access_method": {"direct_api": 1567.80, "mcp_server": 1279.70},
            "daily_revenue_trend": [45.60, 52.20, 41.80],
            "dates": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "top_revenue_sources": [
                {"source": "Article Content", "type": "content_type", "revenue": 1247.50, "percentage": 43.8}
            ],
        }

    @patch("analytics_api.lambda_client")
    def test_handle_usage_analytics_success(self, mock_lambda_client):
        """Test successful usage analytics request"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {"statusCode": 200, "body": json.dumps(self.mock_trends_response)}
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        # Test request
        headers = {}
        query_params = {"date_range": "last_30_days"}

        result = self.handler.handle_usage_analytics(headers, query_params)

        # Verify response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])
        self.assertEqual(body["period"], "30 days")
        self.assertIn("summary", body)
        self.assertIn("daily_breakdown", body)
        self.assertIn("content_type_distribution", body)

        # Verify summary data
        summary = body["summary"]
        self.assertEqual(summary["total_requests"], 3750)
        self.assertEqual(summary["total_bot_access"], 375)
        self.assertEqual(summary["total_mcp_usage"], 250)

        # Verify daily breakdown
        daily = body["daily_breakdown"]
        self.assertEqual(len(daily["dates"]), 3)
        self.assertEqual(len(daily["api_requests"]), 3)
        self.assertEqual(daily["api_requests"][0], 1200)

    @patch("analytics_api.lambda_client")
    def test_handle_usage_analytics_with_filters(self, mock_lambda_client):
        """Test usage analytics with content type and bot filters"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {"statusCode": 200, "body": json.dumps(self.mock_trends_response)}
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        # Test request with filters
        headers = {}
        query_params = {"date_range": "last_7_days", "content_type": "article", "bot_filter": "openai-gpt"}

        result = self.handler.handle_usage_analytics(headers, query_params)

        # Verify response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])
        filters = body["filters_applied"]
        self.assertEqual(filters["content_type"], "article")
        self.assertEqual(filters["bot_filter"], "openai-gpt")

        # Verify content type filtering
        content_dist = body["content_type_distribution"]
        self.assertIn("article", content_dist)
        # Other content types should be filtered out
        self.assertNotIn("report", content_dist)

    @patch("analytics_api.lambda_client")
    def test_handle_revenue_analytics_success(self, mock_lambda_client):
        """Test successful revenue analytics request"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {"statusCode": 200, "body": json.dumps(self.mock_revenue_response)}
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        # Test request
        headers = {}
        query_params = {"time_period": "last_30_days", "breakdown_by": "content_type"}

        result = self.handler.handle_revenue_analytics(headers, query_params)

        # Verify response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])
        self.assertEqual(body["total_revenue"], 2847.50)
        self.assertEqual(body["breakdown_by"], "content_type")
        self.assertIn("revenue_growth", body)
        self.assertIn("daily_revenue_trend", body)
        self.assertIn("top_revenue_sources", body)

        # Verify revenue growth data
        growth = body["revenue_growth"]
        self.assertEqual(growth["growth_rate"], 12.5)
        self.assertEqual(growth["trend"], "growing")

        # Verify breakdown data
        breakdown = body["breakdown"]
        self.assertIn("article", breakdown)
        self.assertEqual(breakdown["article"], 1247.50)

    @patch("analytics_api.lambda_client")
    def test_handle_revenue_analytics_access_method_breakdown(self, mock_lambda_client):
        """Test revenue analytics with access method breakdown"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {"statusCode": 200, "body": json.dumps(self.mock_revenue_response)}
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        # Test request with access method breakdown
        headers = {}
        query_params = {"time_period": "last_7_days", "breakdown_by": "access_method"}

        result = self.handler.handle_revenue_analytics(headers, query_params)

        # Verify response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])
        self.assertEqual(body["breakdown_by"], "access_method")

        # Verify breakdown shows access methods
        breakdown = body["breakdown"]
        self.assertIn("direct_api", breakdown)
        self.assertIn("mcp_server", breakdown)
        self.assertEqual(breakdown["direct_api"], 1567.80)
        self.assertEqual(breakdown["mcp_server"], 1279.70)

    @patch("analytics_api.lambda_client")
    def test_handle_trends_analytics_success(self, mock_lambda_client):
        """Test successful trends analytics request"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {"statusCode": 200, "body": json.dumps(self.mock_trends_response)}
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        # Test request
        headers = {}
        query_params = {"days": "30", "metric_type": "all", "granularity": "daily"}

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])
        self.assertEqual(body["period"], "30 days")
        self.assertEqual(body["granularity"], "daily")
        self.assertEqual(body["metric_type"], "all")

        # Verify trends data
        trends = body["trends"]
        self.assertIn("dates", trends)
        self.assertIn("api_requests", trends)
        self.assertIn("revenue", trends)
        self.assertIn("bot_access", trends)
        self.assertIn("mcp_usage", trends)

        # Verify summary statistics
        summary = body["summary_statistics"]
        self.assertEqual(summary["total_requests"], 3750)
        self.assertEqual(summary["total_revenue"], 139.60)

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

    @patch("analytics_api.lambda_client")
    def test_handle_trends_analytics_filtered_metric(self, mock_lambda_client):
        """Test trends analytics with specific metric type filter"""
        # Mock Lambda response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {"statusCode": 200, "body": json.dumps(self.mock_trends_response)}
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        # Test request with revenue metric filter
        headers = {}
        query_params = {"days": "7", "metric_type": "revenue", "granularity": "daily"}

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify response
        self.assertEqual(result["statusCode"], 200)

        body = json.loads(result["body"])
        self.assertEqual(body["metric_type"], "revenue")

        # Verify only revenue data is included in trends
        trends = body["trends"]
        self.assertIn("dates", trends)
        self.assertIn("revenue", trends)
        # Other metrics should be filtered out
        self.assertNotIn("api_requests", trends)
        self.assertNotIn("bot_access", trends)
        self.assertNotIn("mcp_usage", trends)

    def test_handle_trends_analytics_invalid_days(self):
        """Test trends analytics with invalid days parameter"""
        headers = {}
        query_params = {"days": "500"}  # Exceeds maximum

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify error response
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertIn("Maximum days allowed is 365", body["error"])

    def test_handle_trends_analytics_invalid_granularity(self):
        """Test trends analytics with invalid granularity parameter"""
        headers = {}
        query_params = {"granularity": "weekly"}  # Invalid granularity

        result = self.handler.handle_trends_analytics(headers, query_params)

        # Verify error response
        self.assertEqual(result["statusCode"], 400)
        body = json.loads(result["body"])
        self.assertIn("Granularity must be", body["error"])

    @patch("analytics_api.lambda_client")
    def test_analytics_aggregation_failure(self, mock_lambda_client):
        """Test handling of analytics aggregation service failure"""
        # Mock Lambda failure response
        mock_response = {"StatusCode": 200, "Payload": Mock()}
        mock_response["Payload"].read.return_value = json.dumps(
            {"statusCode": 500, "body": json.dumps({"error": "Analytics aggregation failed"})}
        ).encode()
        mock_lambda_client.invoke.return_value = mock_response

        # Test request
        headers = {}
        query_params = {}

        result = self.handler.handle_usage_analytics(headers, query_params)

        # Verify error response
        self.assertEqual(result["statusCode"], 500)
        body = json.loads(result["body"])
        self.assertIn("Failed to retrieve usage analytics", body["error"])

    @patch("analytics_api.lambda_client")
    def test_lambda_invoke_exception(self, mock_lambda_client):
        """Test handling of Lambda invoke exception"""
        # Mock Lambda invoke exception
        mock_lambda_client.invoke.side_effect = Exception("Lambda invoke failed")

        # Test request
        headers = {}
        query_params = {}

        result = self.handler.handle_revenue_analytics(headers, query_params)

        # Verify error response
        self.assertEqual(result["statusCode"], 500)
        body = json.loads(result["body"])
        self.assertIn("Failed to retrieve revenue analytics", body["error"])

    def test_parse_date_range(self):
        """Test date range parsing functionality"""
        # Test standard date ranges
        self.assertEqual(self.handler._parse_date_range("last_7_days"), 7)
        self.assertEqual(self.handler._parse_date_range("last_30_days"), 30)
        self.assertEqual(self.handler._parse_date_range("last_90_days"), 90)

        # Test custom date range
        custom_range = "custom:2024-01-01:2024-01-15"
        self.assertEqual(self.handler._parse_date_range(custom_range), 15)

        # Test invalid date range
        self.assertEqual(self.handler._parse_date_range("invalid_range"), 30)

    def test_convert_time_period_to_date_range(self):
        """Test time period conversion functionality"""
        self.assertEqual(self.handler._convert_time_period_to_date_range("last_7_days"), "7d")
        self.assertEqual(self.handler._convert_time_period_to_date_range("last_30_days"), "30d")
        self.assertEqual(self.handler._convert_time_period_to_date_range("last_90_days"), "90d")
        self.assertEqual(self.handler._convert_time_period_to_date_range("unknown"), "30d")

    def test_format_revenue_breakdown(self):
        """Test revenue breakdown formatting"""
        revenue_data = {
            "revenue_by_content_type": {"article": 100.0, "report": 200.0},
            "revenue_by_access_method": {"direct_api": 150.0, "mcp_server": 150.0},
        }

        # Test content type breakdown
        result = self.handler._format_revenue_breakdown(revenue_data, "content_type")
        self.assertEqual(result, {"article": 100.0, "report": 200.0})

        # Test access method breakdown
        result = self.handler._format_revenue_breakdown(revenue_data, "access_method")
        self.assertEqual(result, {"direct_api": 150.0, "mcp_server": 150.0})

        # Test bot_partner breakdown (should use access method)
        result = self.handler._format_revenue_breakdown(revenue_data, "bot_partner")
        self.assertEqual(result, {"direct_api": 150.0, "mcp_server": 150.0})

        # Test geographic breakdown (not implemented)
        result = self.handler._format_revenue_breakdown(revenue_data, "geographic")
        self.assertEqual(result, {})

    def test_analyze_trends(self):
        """Test trend analysis functionality"""
        trends_data = {
            "daily_api_requests": [100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230],
            "daily_revenue": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0, 21.0, 22.0, 23.0],
            "dates": [
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
                "2024-01-06",
                "2024-01-07",
                "2024-01-08",
                "2024-01-09",
                "2024-01-10",
                "2024-01-11",
                "2024-01-12",
                "2024-01-13",
                "2024-01-14",
            ],
        }

        analysis = self.handler._analyze_trends(trends_data)

        # Verify analysis results
        self.assertEqual(analysis["request_trend"], "increasing")
        self.assertEqual(analysis["revenue_trend"], "increasing")
        self.assertIn("volatility", analysis)
        self.assertIn("peak_day", analysis)
        self.assertIn("growth_rate", analysis)

        # Peak day should be the last day (highest value)
        self.assertEqual(analysis["peak_day"], "2024-01-14")

    def test_analyze_trends_insufficient_data(self):
        """Test trend analysis with insufficient data"""
        trends_data = {"daily_api_requests": [100], "daily_revenue": [10.0]}

        analysis = self.handler._analyze_trends(trends_data)

        # Should handle insufficient data gracefully
        self.assertEqual(analysis["request_trend"], "insufficient_data")
        self.assertEqual(analysis["revenue_trend"], "insufficient_data")

    def test_calculate_growth_rate(self):
        """Test growth rate calculation"""
        # Test positive growth
        daily_values = [100, 110, 120, 130]
        growth_rate = self.handler._calculate_growth_rate(daily_values)
        self.assertEqual(growth_rate, 30.0)  # (130-100)/100 * 100

        # Test negative growth
        daily_values = [100, 90, 80, 70]
        growth_rate = self.handler._calculate_growth_rate(daily_values)
        self.assertEqual(growth_rate, -30.0)

        # Test insufficient data
        daily_values = [100]
        growth_rate = self.handler._calculate_growth_rate(daily_values)
        self.assertEqual(growth_rate, 0.0)

    def test_filter_trends_by_metric(self):
        """Test filtering trends data by metric type"""
        base_trends_data = {
            "trends": {
                "dates": ["2024-01-01", "2024-01-02"],
                "api_requests": [100, 110],
                "revenue": [10.0, 11.0],
                "bot_access": [20, 22],
                "mcp_usage": [15, 17],
            }
        }

        # Test requests filter
        trends_data = base_trends_data.copy()
        filtered = self.handler._filter_trends_by_metric(trends_data, "requests")
        self.assertIn("api_requests", filtered["trends"])
        self.assertNotIn("revenue", filtered["trends"])
        self.assertNotIn("bot_access", filtered["trends"])

        # Test revenue filter
        trends_data = base_trends_data.copy()
        filtered = self.handler._filter_trends_by_metric(trends_data, "revenue")
        self.assertIn("revenue", filtered["trends"])
        self.assertNotIn("api_requests", filtered["trends"])

        # Test bot activity filter
        trends_data = base_trends_data.copy()
        filtered = self.handler._filter_trends_by_metric(trends_data, "bot_activity")
        self.assertIn("bot_access", filtered["trends"])
        self.assertIn("mcp_usage", filtered["trends"])
        self.assertNotIn("api_requests", filtered["trends"])


if __name__ == "__main__":
    unittest.main()
