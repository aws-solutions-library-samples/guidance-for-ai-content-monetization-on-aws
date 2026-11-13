"""
Integration tests for analytics aggregation functionality.

These tests verify the analytics aggregation Lambda function works correctly
with real AWS services and data flows.
"""

import pytest
import json
import boto3
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import patch, Mock
from moto import mock_aws

# Import the module under test
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from analytics_aggregation import AnalyticsAggregator, lambda_handler
from services.analytics_collector import AnalyticsCollector


class TestAnalyticsAggregationIntegration:
    """Integration tests for analytics aggregation with real data flows"""

    def setup_method(self, method):
        """Set up integration test environment"""
        # Start moto mock
        self.mock = mock_aws()
        self.mock.start()

        # Create mock DynamoDB table that matches production schema
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        self.analytics_table = self.dynamodb.create_table(
            TableName="cm-analytics-integration-test",
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
                    "ProvisionedThroughput": {"ReadCapacityUnits": 10, "WriteCapacityUnits": 10},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 10, "WriteCapacityUnits": 10},
        )

        # Initialize components
        self.analytics_collector = AnalyticsCollector("cm-analytics-integration-test")
        self.analytics_aggregator = AnalyticsAggregator("cm-analytics-integration-test")

        # Test data
        self.test_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def teardown_method(self, method):
        """Clean up after each test"""
        self.mock.stop()

    def test_end_to_end_analytics_flow(self):
        """Test complete analytics flow from collection to aggregation"""
        # Step 1: Simulate analytics data collection

        # Simulate API requests
        for i in range(10):
            self.analytics_collector.record_api_access(
                endpoint="/query",
                user_id=f"user_{i}",
                metadata={"response_time_ms": 150 + i * 10, "request_size": 1024, "response_size": 2048},
            )

        # Simulate content access
        content_ids = ["art_001", "art_002", "rpt_001", "vid_001"]
        for content_id in content_ids:
            self.analytics_collector.record_content_access(
                content_id=content_id,
                access_type="query",
                requester_info={"user_id": "test_user", "ip_address": "192.168.1.100", "user_agent": "TestBot/1.0"},
            )

        # Simulate search queries
        search_queries = [
            "machine learning algorithms",
            "artificial intelligence trends",
            "data science best practices",
            "neural network architectures",
        ]
        for query in search_queries:
            self.analytics_collector.record_search_query(
                query=query,
                results_count=25,
                requester_info={"user_id": "search_user", "bot_id": "openai-gpt", "is_bot": True},
            )

        # Simulate bot access
        for i in range(5):
            self.analytics_collector.record_bot_access(
                bot_id=f"bot_{i % 2}",  # 2 different bots
                content_accessed=[f"art_{i}", f"rpt_{i}"],
                query_type="search_query",
                access_method="mcp_server" if i % 2 == 0 else "direct_api",
            )

        # Simulate MCP tool usage
        for i in range(3):
            self.analytics_collector.record_mcp_tool_usage(
                tool_name="search_content",
                agent_id=f"bedrock_agent_{i}",
                content_accessed=[f"art_{i}", f"rpt_{i}", f"vid_{i}"],
                metadata={
                    "response_time_ms": 200 + i * 50,
                    "content_retrieval_time_ms": 50,
                    "processing_time_ms": 150 + i * 50,
                    "agentcore_correlation_id": f"ac_session_{i}",
                },
            )

        # Step 2: Aggregate the collected data
        daily_metrics = self.analytics_aggregator.aggregate_daily_metrics(self.test_date)

        # Step 3: Verify aggregated results
        assert daily_metrics["date"] == self.test_date
        assert daily_metrics["api_requests"] == 10
        assert daily_metrics["content_access"] == 4
        assert daily_metrics["search_queries"] == 4
        assert daily_metrics["bot_access_count"] == 5
        assert daily_metrics["mcp_tool_usage"] == 3
        assert daily_metrics["unique_bots"] == 2
        assert daily_metrics["unique_agents"] == 3
        assert daily_metrics["revenue_generated"] > 0

        # Verify content type distribution
        assert "content_types_accessed" in daily_metrics
        assert daily_metrics["content_types_accessed"]["article"] > 0

        # Verify revenue calculations
        assert "revenue_by_content_type" in daily_metrics
        assert "revenue_by_access_method" in daily_metrics

        # Step 4: Test usage trends generation
        trends = self.analytics_aggregator.generate_usage_trends(1)  # Single day

        assert trends["period"] == "1 days"
        assert trends["total_metrics"]["total_requests"] == 10
        assert trends["total_metrics"]["total_bot_access"] == 5
        assert trends["total_metrics"]["total_mcp_usage"] == 3
        assert len(trends["daily_api_requests"]) == 1
        assert trends["daily_api_requests"][0] == 10

    def test_lambda_handler_integration_with_real_data(self):
        """Test Lambda handler integration with realistic data scenarios"""
        # Create realistic analytics data for multiple days
        base_date = datetime.now(timezone.utc) - timedelta(days=7)

        for day_offset in range(7):
            current_date = base_date + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")

            # Simulate daily patterns with weekend variations
            is_weekend = current_date.weekday() >= 5
            daily_multiplier = 0.6 if is_weekend else 1.0

            # Create varied daily activity
            daily_api_requests = int(100 * daily_multiplier)
            daily_bot_access = int(20 * daily_multiplier)
            daily_mcp_usage = int(15 * daily_multiplier)

            # Insert API request metrics
            for hour in range(24):
                timestamp = current_date.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat() + "Z"

                # Business hours have more activity
                hour_multiplier = 1.5 if 9 <= hour <= 17 else 0.5
                hourly_requests = max(1, int((daily_api_requests / 24) * hour_multiplier))

                self.analytics_table.put_item(
                    Item={
                        "metric_date": date_str,
                        "metric_type_timestamp": f"api_requests_{timestamp}",
                        "metric_type": "api_requests",
                        "value": hourly_requests,
                        "timestamp": timestamp,
                        "metadata": {"endpoint": "/query", "hour": hour, "is_business_hour": 9 <= hour <= 17},
                    }
                )

            # Insert bot access data
            for bot_num in range(daily_bot_access):
                timestamp = (
                    current_date.replace(
                        hour=bot_num % 24, minute=(bot_num * 3) % 60, second=0, microsecond=0
                    ).isoformat()
                    + "Z"
                )

                self.analytics_table.put_item(
                    Item={
                        "metric_date": date_str,
                        "metric_type_timestamp": f"bot_access_bot_{bot_num}_{timestamp}",
                        "metric_type": "bot_access",
                        "bot_id": f"bot_{bot_num % 3}",  # 3 different bots
                        "access_method": "mcp_server" if bot_num % 2 == 0 else "direct_api",
                        "access_type": "search_query",
                        "content_accessed": [f"art_{bot_num}", f"rpt_{bot_num % 5}"],
                        "timestamp": timestamp,
                        "revenue_impact": Decimal("0.30"),
                    }
                )

            # Insert MCP tool usage data
            for mcp_num in range(daily_mcp_usage):
                timestamp = (
                    current_date.replace(
                        hour=(mcp_num * 2) % 24, minute=(mcp_num * 5) % 60, second=0, microsecond=0
                    ).isoformat()
                    + "Z"
                )

                self.analytics_table.put_item(
                    Item={
                        "metric_date": date_str,
                        "metric_type_timestamp": f"mcp_tool_usage_{timestamp}",
                        "metric_type": "mcp_tool_usage",
                        "tool_name": "search_content",
                        "agent_id": f"bedrock_agent_{mcp_num % 2}",
                        "usage_count": 1,
                        "content_accessed_count": 3 + (mcp_num % 5),
                        "timestamp": timestamp,
                    }
                )

        # Test Lambda handler with different operations

        # Test 1: Daily aggregation
        event_daily = {
            "operation": "aggregate_daily",
            "parameters": {"date": (base_date + timedelta(days=3)).strftime("%Y-%m-%d")},
        }

        result_daily = lambda_handler(event_daily, {})
        assert result_daily["statusCode"] == 200

        daily_body = json.loads(result_daily["body"])
        assert daily_body["api_requests"] > 0
        assert daily_body["bot_access_count"] > 0
        assert daily_body["revenue_generated"] > 0

        # Test 2: Date range aggregation
        event_range = {
            "operation": "aggregate_range",
            "parameters": {
                "start_date": base_date.strftime("%Y-%m-%d"),
                "end_date": (base_date + timedelta(days=6)).strftime("%Y-%m-%d"),
            },
        }

        result_range = lambda_handler(event_range, {})
        assert result_range["statusCode"] == 200

        range_body = json.loads(result_range["body"])
        assert range_body["total_api_requests"] > 0
        assert len(range_body["daily_breakdown"]) == 7
        assert range_body["total_revenue"] > 0

        # Test 3: Usage trends
        event_trends = {"operation": "usage_trends", "parameters": {"days": 7}}

        result_trends = lambda_handler(event_trends, {})
        assert result_trends["statusCode"] == 200

        trends_body = json.loads(result_trends["body"])
        assert trends_body["period"] == "7 days"
        assert len(trends_body["daily_api_requests"]) == 7
        assert trends_body["total_metrics"]["total_requests"] > 0

        # Verify weekend effect (should show lower activity on weekends)
        daily_requests = trends_body["daily_api_requests"]
        weekend_avg = (daily_requests[5] + daily_requests[6]) / 2  # Sat, Sun
        weekday_avg = sum(daily_requests[:5]) / 5  # Mon-Fri
        assert weekend_avg < weekday_avg  # Weekend should be lower

        # Test 4: Revenue metrics
        event_revenue = {"operation": "revenue_metrics", "parameters": {"date_range": "7d"}}

        result_revenue = lambda_handler(event_revenue, {})
        assert result_revenue["statusCode"] == 200

        revenue_body = json.loads(result_revenue["body"])
        assert revenue_body["total_revenue"] > 0
        assert revenue_body["average_revenue_per_request"] > 0
        assert "revenue_growth" in revenue_body
        assert "projected_monthly_revenue" in revenue_body

    def test_performance_with_large_dataset(self):
        """Test aggregation performance with larger datasets"""
        # Create a larger dataset (simulate 30 days of data)
        base_date = datetime.now(timezone.utc) - timedelta(days=30)

        # Insert data for 30 days (reduced volume for test performance)
        for day_offset in range(30):
            current_date = base_date + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")

            # Insert fewer records per day for performance
            for hour in [0, 6, 12, 18]:  # 4 times per day
                timestamp = current_date.replace(hour=hour).isoformat() + "Z"

                # API requests
                self.analytics_table.put_item(
                    Item={
                        "metric_date": date_str,
                        "metric_type_timestamp": f"api_requests_{timestamp}",
                        "metric_type": "api_requests",
                        "value": 25,  # 100 requests per day total
                        "timestamp": timestamp,
                        "metadata": {"endpoint": "/query"},
                    }
                )

                # Bot access
                self.analytics_table.put_item(
                    Item={
                        "metric_date": date_str,
                        "metric_type_timestamp": f"bot_access_{hour}_{timestamp}",
                        "metric_type": "bot_access",
                        "bot_id": f"bot_{hour % 2}",
                        "access_method": "mcp_server",
                        "access_type": "search_query",
                        "content_accessed": ["art_001", "rpt_001"],
                        "timestamp": timestamp,
                        "revenue_impact": Decimal("0.30"),
                    }
                )

        # Test 30-day aggregation performance
        start_time = datetime.now()

        trends = self.analytics_aggregator.generate_usage_trends(30)

        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()

        # Verify results
        assert trends["period"] == "30 days"
        assert len(trends["daily_api_requests"]) == 30
        assert trends["total_metrics"]["total_requests"] == 30 * 100  # 100 per day

        # Performance assertion (should complete within reasonable time)
        assert processing_time < 10.0  # Should complete within 10 seconds

        print(f"30-day aggregation completed in {processing_time:.2f} seconds")

    def test_error_handling_and_recovery(self):
        """Test error handling and recovery scenarios"""
        # Test 1: Aggregation with corrupted data
        corrupted_date = "2024-01-01"

        # Insert some valid data
        self.analytics_table.put_item(
            Item={
                "metric_date": corrupted_date,
                "metric_type_timestamp": "api_requests_2024-01-01T10:00:00Z",
                "metric_type": "api_requests",
                "value": 100,
                "timestamp": "2024-01-01T10:00:00Z",
                "metadata": {"endpoint": "/query"},
            }
        )

        # Insert corrupted data (missing required fields)
        self.analytics_table.put_item(
            Item={
                "metric_date": corrupted_date,
                "metric_type_timestamp": "corrupted_data_2024-01-01T11:00:00Z",
                "metric_type": "unknown_type",
                "value": "invalid_value",  # Should be numeric
                "timestamp": "invalid_timestamp",
            }
        )

        # Aggregation should handle corrupted data gracefully
        result = self.analytics_aggregator.aggregate_daily_metrics(corrupted_date)

        # Should still return valid results for good data
        assert result["date"] == corrupted_date
        assert result["api_requests"] == 100
        assert "error" not in result  # Should not fail completely

        # Test 2: Lambda handler with invalid parameters
        event_invalid = {"operation": "aggregate_range", "parameters": {"start_date": "invalid-date-format"}}

        result_invalid = lambda_handler(event_invalid, {})
        assert result_invalid["statusCode"] == 400

        error_body = json.loads(result_invalid["body"])
        assert "error" in error_body

    def test_real_time_aggregation_simulation(self):
        """Test aggregation with real-time data simulation"""
        current_time = datetime.now(timezone.utc)
        current_date = current_time.strftime("%Y-%m-%d")

        # Simulate real-time data collection over several hours
        for hour_offset in range(6):  # 6 hours of data
            hour_time = current_time - timedelta(hours=hour_offset)
            timestamp = hour_time.isoformat() + "Z"

            # Simulate varying hourly activity
            hourly_activity = 20 + (hour_offset * 5)  # Increasing activity

            # API requests
            self.analytics_collector.record_api_access(
                endpoint="/query",
                user_id=f"realtime_user_{hour_offset}",
                metadata={"response_time_ms": 100 + hour_offset * 10, "hour_offset": hour_offset},
            )

            # Bot access with different patterns
            if hour_offset % 2 == 0:  # Every other hour
                self.analytics_collector.record_bot_access(
                    bot_id=f"realtime_bot_{hour_offset}",
                    content_accessed=[f"art_{hour_offset}", f"rpt_{hour_offset}"],
                    query_type="search_query",
                    access_method="mcp_server",
                )

            # MCP tool usage
            if hour_offset % 3 == 0:  # Every third hour
                self.analytics_collector.record_mcp_tool_usage(
                    tool_name="search_content",
                    agent_id=f"realtime_agent_{hour_offset}",
                    content_accessed=[f"content_{i}" for i in range(hour_offset + 1)],
                    metadata={"response_time_ms": 150 + hour_offset * 20, "real_time_test": True},
                )

        # Aggregate real-time data
        real_time_metrics = self.analytics_aggregator.aggregate_daily_metrics(current_date)

        # Verify real-time aggregation
        assert real_time_metrics["date"] == current_date
        assert real_time_metrics["api_requests"] >= 6  # At least one per hour
        assert real_time_metrics["bot_access_count"] >= 3  # Every other hour
        assert real_time_metrics["mcp_tool_usage"] >= 2  # Every third hour

        # Test hourly distribution
        hourly_dist = real_time_metrics["hourly_distribution"]
        assert len(hourly_dist) > 0  # Should have some hourly data

        # Generate trends including real-time data
        real_time_trends = self.analytics_aggregator.generate_usage_trends(1)

        assert real_time_trends["total_metrics"]["total_requests"] >= 6
        assert real_time_trends["total_metrics"]["total_bot_access"] >= 3


if __name__ == "__main__":
    pytest.main([__file__])
