#!/usr/bin/env python3
"""
Unit tests for analytics data generation functionality.

This module tests the analytics data generation methods in the mock data generator,
including bot access tracking, MCP server request simulation, and usage metrics.
"""

import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import json

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator


class TestAnalyticsDataGeneration:
    """Test analytics data generation functionality"""

    @pytest.fixture
    def config(self):
        """Create test configuration"""
        return GeneratorConfig(
            content_count=5,
            partner_count=3,
            access_log_count=20,
            job_count=10,
            date_range_months=1,
            aws_profile="test",
            aws_region="us-west-2",
        )

    @pytest.fixture
    def generator(self, config):
        """Create mock data generator with mocked AWS session"""
        with patch("boto3.Session"):
            return MockDataGenerator(config)

    @pytest.fixture
    def sample_content_ids(self):
        """Sample content IDs for testing"""
        return ["art_001", "rpt_002", "vid_003", "img_004", "doc_005"]

    @pytest.fixture
    def sample_partner_ids(self):
        """Sample partner IDs for testing"""
        return ["partner_001", "partner_002", "partner_003"]

    def test_generate_analytics_data_basic(self, generator, sample_content_ids, sample_partner_ids):
        """Test basic analytics data generation"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        # Should generate records for the specified date range
        assert len(analytics_records) > 0

        # Verify each record has required fields
        for record in analytics_records:
            assert "metric_date" in record
            assert "metric_type_timestamp" in record
            assert "metric_type" in record
            assert "timestamp" in record
            assert "value" in record
            assert "metadata" in record

            # Verify DynamoDB format
            assert "S" in record["metric_date"]
            assert "S" in record["metric_type_timestamp"]
            assert "S" in record["metric_type"]
            assert "S" in record["timestamp"]
            assert "N" in record["value"]
            assert "M" in record["metadata"]

    def test_generate_analytics_data_metric_types(self, generator, sample_content_ids, sample_partner_ids):
        """Test that analytics data includes various metric types"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        # Extract metric types
        metric_types = set()
        for record in analytics_records:
            metric_type = record["metric_type"]["S"]
            metric_types.add(metric_type)

        # Should include expected metric types
        expected_types = {
            "api_requests",
            "content_access",
            "bot_access",
            "mcp_tool_usage",
            "revenue_daily",
            "search_query",
        }

        # Should have multiple metric types
        assert len(metric_types) > 1

        # Should include some of the expected types
        assert len(metric_types.intersection(expected_types)) > 0

    def test_generate_analytics_data_date_range(self, generator, sample_content_ids, sample_partner_ids):
        """Test that analytics data covers the specified date range"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        # Extract dates
        dates = set()
        for record in analytics_records:
            date_str = record["metric_date"]["S"]
            dates.add(date_str)

        # Should have data for multiple days (for 1 month range)
        assert len(dates) > 1

        # Dates should be in YYYY-MM-DD format
        for date_str in dates:
            assert len(date_str) == 10
            assert date_str.count("-") == 2

            # Should be valid date
            datetime.strptime(date_str, "%Y-%m-%d")

    def test_generate_api_request_metrics(self, generator):
        """Test API request metrics generation"""
        from faker import Faker

        fake = Faker()

        records = generator._generate_api_request_metrics("2024-01-15", fake)

        # Should generate 24 hourly records
        assert len(records) == 24

        for record in records:
            # Verify structure
            assert record["metric_type"]["S"] == "api_requests"
            assert record["metric_date"]["S"] == "2024-01-15"

            # Verify timestamp format
            timestamp = record["timestamp"]["S"]
            assert timestamp.startswith("2024-01-15T")
            assert timestamp.endswith(":00:00Z")

            # Verify value is positive
            value = int(record["value"]["N"])
            assert value > 0

            # Verify metadata structure
            metadata = record["metadata"]["M"]
            assert "endpoint_breakdown" in metadata
            assert "response_time_avg_ms" in metadata
            assert "error_rate" in metadata

    def test_generate_content_access_metrics(self, generator, sample_content_ids):
        """Test content access metrics generation"""
        from faker import Faker

        fake = Faker()

        records = generator._generate_content_access_metrics("2024-01-15", sample_content_ids, fake)

        # Should generate multiple access events
        assert len(records) > 0

        for record in records:
            # Verify structure
            assert record["metric_type"]["S"] == "content_access"
            assert record["metric_date"]["S"] == "2024-01-15"

            # Verify content ID is from sample
            content_id = record["metadata"]["M"]["content_id"]["S"]
            assert content_id in sample_content_ids

            # Verify access method is valid
            access_method = record["metadata"]["M"]["access_method"]["S"]
            assert access_method in ["direct_api", "mcp_server", "ui_browse"]

            # Verify user type is valid
            user_type = record["metadata"]["M"]["user_type"]["S"]
            assert user_type in ["bot", "human", "mcp_agent"]

    def test_generate_bot_access_metrics(self, generator, sample_content_ids, sample_partner_ids):
        """Test bot access metrics generation"""
        from faker import Faker

        fake = Faker()

        records = generator._generate_bot_access_metrics("2024-01-15", sample_content_ids, sample_partner_ids, fake)

        # Should generate multiple bot access events
        assert len(records) > 0

        for record in records:
            # Verify structure
            assert record["metric_type"]["S"] == "bot_access"
            assert record["metric_date"]["S"] == "2024-01-15"

            # Verify bot ID is present
            bot_id = record["metadata"]["M"]["bot_id"]["S"]
            assert len(bot_id) > 0

            # Verify partner ID is from sample
            partner_id = record["metadata"]["M"]["partner_id"]["S"]
            assert partner_id in sample_partner_ids

            # Verify content accessed is from sample
            content_accessed = record["metadata"]["M"]["content_accessed"]["SS"]
            for content_id in content_accessed:
                assert content_id in sample_content_ids

            # Verify access method is valid
            access_method = record["metadata"]["M"]["access_method"]["S"]
            assert access_method in ["direct_api", "mcp_server"]

            # Verify revenue impact is present and positive
            revenue_impact = float(record["metadata"]["M"]["revenue_impact"]["N"])
            assert revenue_impact >= 0

    def test_generate_mcp_request_metrics(self, generator, sample_content_ids):
        """Test MCP server request metrics generation"""
        from faker import Faker

        fake = Faker()

        records = generator._generate_mcp_request_metrics("2024-01-15", sample_content_ids, fake)

        # Should generate multiple MCP events
        assert len(records) > 0

        for record in records:
            # Verify structure
            assert record["metric_type"]["S"] == "mcp_tool_usage"
            assert record["metric_date"]["S"] == "2024-01-15"

            # Verify tool name is valid
            tool_name = record["metadata"]["M"]["tool_name"]["S"]
            expected_tools = [
                "search_content",
                "get_content_details",
                "list_categories",
                "analyze_content_performance",
                "get_usage_analytics",
            ]
            assert tool_name in expected_tools

            # Verify agent information is present
            agent_id = record["metadata"]["M"]["agent_id"]["S"]
            assert len(agent_id) > 0

            agent_type = record["metadata"]["M"]["agent_type"]["S"]
            assert len(agent_type) > 0

            # Verify server ID
            server_id = record["metadata"]["M"]["server_id"]["S"]
            assert server_id == "iab-content-mcp-server"

            # Verify business context
            business_context = record["metadata"]["M"]["business_context"]["M"]
            assert "query_intent" in business_context
            assert "content_categories" in business_context
            assert "usage_pattern" in business_context

    def test_generate_revenue_metrics(self, generator):
        """Test revenue metrics generation"""
        from faker import Faker

        fake = Faker()

        records = generator._generate_revenue_metrics("2024-01-15", fake)

        # Should generate one daily revenue record
        assert len(records) == 1

        record = records[0]

        # Verify structure
        assert record["metric_type"]["S"] == "revenue_daily"
        assert record["metric_date"]["S"] == "2024-01-15"

        # Verify revenue value is positive
        revenue = float(record["value"]["N"])
        assert revenue > 0

        # Verify revenue breakdown
        breakdown = record["metadata"]["M"]["revenue_breakdown"]["M"]
        assert "content_access" in breakdown
        assert "api_usage" in breakdown
        assert "premium_features" in breakdown

        # Verify transaction information
        transaction_count = int(record["metadata"]["M"]["transaction_count"]["N"])
        assert transaction_count > 0

        avg_transaction = float(record["metadata"]["M"]["average_transaction_value"]["N"])
        assert avg_transaction > 0

    def test_generate_search_query_metrics(self, generator, sample_content_ids):
        """Test search query metrics generation"""
        from faker import Faker

        fake = Faker()

        records = generator._generate_search_query_metrics("2024-01-15", sample_content_ids, fake)

        # Should generate multiple search events
        assert len(records) > 0

        for record in records:
            # Verify structure
            assert record["metric_type"]["S"] == "search_query"
            assert record["metric_date"]["S"] == "2024-01-15"

            # Verify query information
            query = record["metadata"]["M"]["query"]["S"]
            assert len(query) > 0

            results_returned = int(record["metadata"]["M"]["results_returned"]["N"])
            assert results_returned > 0

            # Verify requester type is valid
            requester_type = record["metadata"]["M"]["requester_type"]["S"]
            assert requester_type in ["bot", "human", "mcp_agent"]

            # Verify response time is reasonable
            response_time = int(record["metadata"]["M"]["response_time_ms"]["N"])
            assert 0 < response_time < 10000  # Less than 10 seconds

            # Verify quality score is in valid range
            quality_score = float(record["metadata"]["M"]["result_quality_score"]["N"])
            assert 0.0 <= quality_score <= 1.0

    def test_analytics_data_referential_integrity(self, generator, sample_content_ids, sample_partner_ids):
        """Test that analytics data maintains referential integrity"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        # Check that all referenced content IDs are valid
        for record in analytics_records:
            metadata = record["metadata"]["M"]

            # Check content_id references
            if "content_id" in metadata:
                content_id = metadata["content_id"]["S"]
                assert content_id in sample_content_ids

            # Check partner_id references
            if "partner_id" in metadata:
                partner_id = metadata["partner_id"]["S"]
                assert partner_id in sample_partner_ids

            # Check content_accessed references
            if "content_accessed" in metadata:
                content_accessed = metadata["content_accessed"]["SS"]
                for content_id in content_accessed:
                    assert content_id in sample_content_ids

    def test_analytics_data_timestamp_consistency(self, generator, sample_content_ids, sample_partner_ids):
        """Test that analytics data has consistent timestamps"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        for record in analytics_records:
            metric_date = record["metric_date"]["S"]
            timestamp = record["timestamp"]["S"]

            # Extract date from timestamp
            timestamp_date = timestamp.split("T")[0]

            # Metric date should match timestamp date
            assert metric_date == timestamp_date

            # Timestamp should be valid ISO format
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

    def test_analytics_data_metric_type_timestamp_format(self, generator, sample_content_ids, sample_partner_ids):
        """Test that metric_type_timestamp follows expected format"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        for record in analytics_records:
            metric_type = record["metric_type"]["S"]
            metric_type_timestamp = record["metric_type_timestamp"]["S"]
            timestamp = record["timestamp"]["S"]

            # Should start with metric type
            assert metric_type_timestamp.startswith(metric_type)

            # Should contain the timestamp
            assert timestamp in metric_type_timestamp

    def test_analytics_data_value_types(self, generator, sample_content_ids, sample_partner_ids):
        """Test that analytics data values are appropriate types"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        for record in analytics_records:
            # Value should be numeric
            value = record["value"]["N"]
            float(value)  # Should not raise exception

            # Value should be non-negative for most metrics
            numeric_value = float(value)
            assert numeric_value >= 0

    def test_analytics_data_metadata_structure(self, generator, sample_content_ids, sample_partner_ids):
        """Test that analytics metadata has proper DynamoDB structure"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        for record in analytics_records:
            metadata = record["metadata"]["M"]

            # Metadata should be a map
            assert isinstance(metadata, dict)

            # All metadata values should have DynamoDB type indicators
            for key, value in metadata.items():
                assert isinstance(value, dict)

                # Should have at least one DynamoDB type key
                dynamodb_types = {"S", "N", "B", "SS", "NS", "BS", "M", "L", "NULL", "BOOL"}
                assert len(set(value.keys()).intersection(dynamodb_types)) > 0


class TestAnalyticsDataIntegration:
    """Test analytics data integration with existing generator"""

    @pytest.fixture
    def config(self):
        """Create test configuration"""
        return GeneratorConfig(
            content_count=10,
            partner_count=5,
            access_log_count=50,
            job_count=20,
            date_range_months=2,
            aws_profile="test",
            aws_region="us-west-2",
        )

    @pytest.fixture
    def generator(self, config):
        """Create mock data generator with mocked AWS session"""
        with patch("boto3.Session"):
            return MockDataGenerator(config)

    def test_analytics_table_name_in_table_names(self, generator):
        """Test that analytics table is included in TABLE_NAMES"""
        assert "analytics" in generator.TABLE_NAMES
        assert generator.TABLE_NAMES["analytics"] == "cm-analytics"

    def test_analytics_generation_with_existing_data(self, generator):
        """Test analytics generation works with existing content and partner data"""
        # Generate base data first
        content_records = generator.generate_content_data()
        partner_records = generator.generate_partner_data()

        # Extract IDs
        content_ids = [item["content_id"]["S"] for item in content_records]
        partner_ids = [item["partner_id"]["S"] for item in partner_records]

        # Generate analytics data
        analytics_records = generator.generate_analytics_data(content_ids, partner_ids)

        # Should generate analytics records
        assert len(analytics_records) > 0

        # Should reference existing content and partners
        referenced_content_ids = set()
        referenced_partner_ids = set()

        for record in analytics_records:
            metadata = record["metadata"]["M"]

            if "content_id" in metadata:
                referenced_content_ids.add(metadata["content_id"]["S"])

            if "partner_id" in metadata:
                referenced_partner_ids.add(metadata["partner_id"]["S"])

            if "content_accessed" in metadata:
                for content_id in metadata["content_accessed"]["SS"]:
                    referenced_content_ids.add(content_id)

        # Should reference some of the generated content and partners
        assert len(referenced_content_ids) > 0
        assert len(referenced_partner_ids) > 0

        # All references should be valid
        assert referenced_content_ids.issubset(set(content_ids))
        assert referenced_partner_ids.issubset(set(partner_ids))

    def test_analytics_data_volume_scales_with_date_range(self, generator):
        """Test that analytics data volume scales with date range"""
        content_ids = ["art_001", "art_002"]
        partner_ids = ["partner_001"]

        # Test with 1 month
        generator.config.date_range_months = 1
        records_1_month = generator.generate_analytics_data(content_ids, partner_ids)

        # Test with 2 months
        generator.config.date_range_months = 2
        records_2_months = generator.generate_analytics_data(content_ids, partner_ids)

        # Should generate more records for longer time period
        assert len(records_2_months) > len(records_1_month)

        # Should be roughly proportional (allowing for some variance)
        ratio = len(records_2_months) / len(records_1_month)
        assert 1.5 < ratio < 2.5  # Should be roughly 2x but allow variance
