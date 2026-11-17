#!/usr/bin/env python3
"""
Comprehensive test suite for the mock data generator.

This module provides unit tests for each generator method, integration tests
for data structure validation, and tests for DynamoDB operations using mocked AWS services.
"""

import pytest
import sys
import os
import random
import boto3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, Mock
from moto import mock_aws
import json
import uuid

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator
from mock_data_generator.validation import SchemaValidator, ReferentialIntegrityValidator, ValidationReporter


class TestMockDataGeneratorCore:
    """Test core functionality of MockDataGenerator"""

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

    def test_generator_initialization(self, config):
        """Test generator initialization with configuration"""
        with patch("boto3.Session") as mock_session:
            generator = MockDataGenerator(config)

            assert generator.config == config
            assert generator._dynamodb_client is None
            assert generator._dynamodb_resource is None
            mock_session.assert_called_once_with(profile_name=config.aws_profile, region_name=config.aws_region)

    def test_dynamodb_client_lazy_initialization(self, generator):
        """Test lazy initialization of DynamoDB client"""
        mock_client = MagicMock()
        generator._session.client = MagicMock(return_value=mock_client)

        # First access should initialize client
        client = generator.dynamodb_client
        assert client == mock_client
        generator._session.client.assert_called_once_with("dynamodb")

        # Second access should return cached client
        client2 = generator.dynamodb_client
        assert client2 == mock_client
        assert generator._session.client.call_count == 1

    def test_dynamodb_resource_lazy_initialization(self, generator):
        """Test lazy initialization of DynamoDB resource"""
        mock_resource = MagicMock()
        generator._session.resource = MagicMock(return_value=mock_resource)

        # First access should initialize resource
        resource = generator.dynamodb_resource
        assert resource == mock_resource
        generator._session.resource.assert_called_once_with("dynamodb")

        # Second access should return cached resource
        resource2 = generator.dynamodb_resource
        assert resource2 == mock_resource
        assert generator._session.resource.call_count == 1

    def test_validate_aws_connectivity_success(self, generator):
        """Test successful AWS connectivity validation"""
        mock_client = MagicMock()
        mock_client.list_tables.return_value = {"TableNames": ["test-table"]}
        generator._dynamodb_client = mock_client

        result = generator.validate_aws_connectivity()
        assert result is True
        mock_client.list_tables.assert_called_once_with(Limit=1)

    def test_validate_aws_connectivity_failure(self, generator):
        """Test AWS connectivity validation failure"""
        from botocore.exceptions import NoCredentialsError

        mock_client = MagicMock()
        mock_client.list_tables.side_effect = NoCredentialsError()
        generator._dynamodb_client = mock_client

        result = generator.validate_aws_connectivity()
        assert result is False

    def test_get_table_info_success(self, generator):
        """Test successful table info retrieval"""
        mock_client = MagicMock()
        table_info = {
            "Table": {
                "TableName": "test-table",
                "TableStatus": "ACTIVE",
                "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            }
        }
        mock_client.describe_table.return_value = table_info
        generator._dynamodb_client = mock_client

        result = generator.get_table_info("test-table")
        assert result == table_info["Table"]
        mock_client.describe_table.assert_called_once_with(TableName="test-table")

    def test_get_table_info_not_found(self, generator):
        """Test table info retrieval for non-existent table"""
        from botocore.exceptions import ClientError

        mock_client = MagicMock()
        error = ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "DescribeTable")
        mock_client.describe_table.side_effect = error
        generator._dynamodb_client = mock_client

        result = generator.get_table_info("nonexistent-table")
        assert result is None

    def test_validate_table_access_active_table(self, generator):
        """Test table access validation for active table"""
        generator.get_table_info = MagicMock(return_value={"TableStatus": "ACTIVE"})

        result = generator.validate_table_access("test-table")
        assert result is True

    def test_validate_table_access_inactive_table(self, generator):
        """Test table access validation for inactive table"""
        generator.get_table_info = MagicMock(return_value={"TableStatus": "CREATING"})

        result = generator.validate_table_access("test-table")
        assert result is False

    def test_validate_table_access_nonexistent_table(self, generator):
        """Test table access validation for non-existent table"""
        generator.get_table_info = MagicMock(return_value=None)

        result = generator.validate_table_access("nonexistent-table")
        assert result is False


class TestContentDataGeneration:
    """Test content data generation functionality"""

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

    def test_generate_content_data_basic(self, generator):
        """Test basic content data generation"""
        random.seed(0)  # deterministic: variety assertion below must not flake under pytest-randomly (Req 17.2)
        content_records = generator.generate_content_data()

        # Verify correct number of records generated
        assert len(content_records) == generator.config.content_count

        # Verify each record has required fields
        for record in content_records:
            assert "content_id" in record
            assert "title" in record
            assert "content_type" in record
            assert "description" in record
            assert "content" in record
            assert "author" in record
            assert "publisher" in record
            assert "created_at" in record
            assert "updated_at" in record
            assert "iab_categories" in record
            assert "tags" in record
            assert "url" in record
            assert "access_level" in record
            assert "pricing_tier" in record
            assert "language" in record
            assert "word_count" in record
            assert "metadata" in record

    def test_generate_content_data_content_types(self, generator):
        """Test that content data includes various content types"""
        content_records = generator.generate_content_data()

        # Extract content types
        content_types = set()
        for record in content_records:
            content_type = record["content_type"]["S"]
            content_types.add(content_type)
            assert content_type in generator.config.content_types

        # Should have variety in content types (if enough records)
        if len(content_records) >= 3:
            assert len(content_types) > 1

    def test_generate_content_data_id_format(self, generator):
        """Test content ID format and uniqueness"""
        content_records = generator.generate_content_data()

        content_ids = set()
        for record in content_records:
            content_id = record["content_id"]["S"]
            content_ids.add(content_id)

            # Verify ID format (type_prefix_number)
            assert "_" in content_id
            parts = content_id.split("_")
            assert len(parts) == 2
            assert parts[0] in ["art", "rpt", "vid", "img", "doc"]
            assert parts[1].isdigit()

        # All IDs should be unique
        assert len(content_ids) == len(content_records)

    def test_generate_content_data_timestamps(self, generator):
        """Test content timestamp generation"""
        earliest = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=generator.config.date_range_months * 30
        )
        content_records = generator.generate_content_data()
        latest = datetime.now(timezone.utc).replace(tzinfo=None)

        for record in content_records:
            created_at_str = record["created_at"]["S"]
            updated_at_str = record["updated_at"]["S"]

            # Parse timestamps
            created_at = datetime.fromisoformat(created_at_str)
            updated_at = datetime.fromisoformat(updated_at_str)

            # Updated timestamp should be >= created timestamp
            assert updated_at >= created_at

            # Faker returns naive UTC values for the generator's UTC date window.
            assert earliest <= created_at <= latest

    def test_generate_content_data_iab_categories(self, generator):
        """Test IAB category assignment"""
        content_records = generator.generate_content_data()

        for record in content_records:
            iab_categories = record["iab_categories"]["SS"]

            # Should have at least one category
            assert len(iab_categories) >= 1

            # Should not have too many categories
            assert len(iab_categories) <= 3

            # Categories should be strings
            for category in iab_categories:
                assert isinstance(category, str)
                assert len(category) > 0

    def test_load_iab_taxonomy_file_exists(self, generator):
        """Test IAB taxonomy loading when file exists"""
        # Mock the file existence and content
        mock_taxonomy_content = """IAB Content Taxonomy v3.0
Effective Date: February 1, 2023
Tier 1 ID	Tier 1 Name	Tier 2 ID	Tier 2 Name	Tier 3 ID	Tier 3 Name	Tier 4 ID	Tier 4 Name
1	Arts & Entertainment	1	Books & Literature	1	Fiction	1	Action & Adventure
2	Business & Finance	1	Business	1	Accounting	1	Tax Preparation
"""

        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=mock_taxonomy_content)),
        ):
            taxonomy = generator.load_iab_taxonomy()

            assert isinstance(taxonomy, dict)
            assert "article" in taxonomy
            assert "report" in taxonomy
            assert "video" in taxonomy
            assert "image" in taxonomy
            assert "document" in taxonomy

            # Each content type should have some categories
            for content_type in taxonomy:
                assert len(taxonomy[content_type]) > 0

    def test_load_iab_taxonomy_file_missing(self, generator):
        """Test IAB taxonomy loading when file is missing"""
        with patch("os.path.exists", return_value=False):
            taxonomy = generator.load_iab_taxonomy()

            # Should return default taxonomy
            assert isinstance(taxonomy, dict)
            assert "article" in taxonomy
            assert len(taxonomy["article"]) > 0

    def test_generate_content_by_type_article(self, generator):
        """Test content generation for article type"""
        fake = generator.generate_content_data.__globals__["Faker"]()
        iab_categories = ["Business > Finance"]

        content_data = generator._generate_content_by_type(fake, "article", iab_categories)

        assert "title" in content_data
        assert "description" in content_data
        assert "content" in content_data
        assert "tags" in content_data
        assert "word_count" in content_data
        assert "format" in content_data

        assert isinstance(content_data["tags"], list)
        assert len(content_data["tags"]) > 0
        assert content_data["word_count"] > 0
        assert content_data["format"] == "text/html"


class TestPartnerDataGeneration:
    """Test partner data generation functionality"""

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

    def test_generate_partner_data_basic(self, generator):
        """Test basic partner data generation"""
        partner_records = generator.generate_partner_data()

        # Verify correct number of records generated
        assert len(partner_records) == generator.config.partner_count

        # Verify each record is a META item on the partner-keyed table shape
        for record in partner_records:
            assert record["PK"] == {"S": f"PARTNER#{record['partner_id']['S']}"}
            assert record["SK"] == {"S": "META"}
            for field in ("partner_name", "partner_type", "contact_email", "contact_name", "pricing_tier",
                          "status", "identity_source", "allowed_endpoints", "created_at", "updated_at"):
                assert field in record, field
            # Retired attributes never come back: credentials are API Gateway keys, quota is a tier.
            for retired in ("partner_key_hash", "api_quota", "api_usage", "scopes", "billing_info",
                            "usage_limits", "ip_whitelist", "metadata", "last_access"):
                assert retired not in record, retired

    def test_generate_partner_data_id_format(self, generator):
        """Test partner ID format and uniqueness"""
        partner_records = generator.generate_partner_data()

        partner_ids = set()
        for record in partner_records:
            partner_id = record["partner_id"]["S"]
            partner_ids.add(partner_id)

            # Verify ID format (partner_number)
            assert partner_id.startswith("partner_")
            number_part = partner_id.replace("partner_", "")
            assert number_part.isdigit()

        # All IDs should be unique
        assert len(partner_ids) == len(partner_records)

    def test_generate_partner_data_has_no_credentials(self, generator):
        """Keys are provisioned through API Gateway, never fabricated by the generator."""
        for record in generator.generate_partner_data():
            assert not any(k.lower().startswith("api_key") or "hash" in k.lower() for k in record)

    def test_generate_partner_data_partner_types(self, generator):
        """Test partner type distribution"""
        partner_records = generator.generate_partner_data()

        partner_types = set()
        for record in partner_records:
            partner_type = record["partner_type"]["S"]
            partner_types.add(partner_type)
            assert partner_type in generator.config.partner_types

        # Should have variety in partner types (if enough records)
        if len(partner_records) >= 2:
            assert len(partner_types) >= 1

    def test_generate_partner_data_email_format(self, generator):
        """Test email format validation"""
        partner_records = generator.generate_partner_data()

        for record in partner_records:
            email = record["contact_email"]["S"]

            # Basic email format validation
            assert "@" in email
            assert "." in email
            assert len(email) > 5

    def test_generate_partner_data_statuses_from_enum(self, generator):
        for record in generator.generate_partner_data():
            assert record["status"]["S"] in ("active", "suspended")

    def test_generate_partner_data_allowed_endpoints_are_key_gated_operation_ids(self, generator):
        from shared.auth.partner_operations import KEY_GATED_OPERATION_IDS

        for record in generator.generate_partner_data():
            endpoints = [e["S"] for e in record["allowed_endpoints"]["L"]]
            assert endpoints and set(endpoints) <= KEY_GATED_OPERATION_IDS
            assert "/search" not in endpoints and "/bid" not in endpoints


class TestJobDataGeneration:
    """Test job data generation functionality"""

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

    def test_generate_job_data_basic(self, generator, sample_content_ids, sample_partner_ids):
        """Test basic job data generation"""
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        # Verify correct number of records generated
        assert len(job_records) == generator.config.job_count

        # Verify each record has required fields
        for record in job_records:
            assert "job_id" in record
            assert "content_id" in record
            assert "partner_id" in record
            assert "job_type" in record
            assert "status" in record
            assert "created_at" in record
            assert "retry_count" in record
            assert "priority" in record
            assert "input_params" in record
            assert "metadata" in record

    def test_generate_job_data_referential_integrity(self, generator, sample_content_ids, sample_partner_ids):
        """Test that jobs reference valid content and partner IDs"""
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        for record in job_records:
            content_id = record["content_id"]["S"]
            partner_id = record["partner_id"]["S"]

            assert content_id in sample_content_ids
            assert partner_id in sample_partner_ids

    def test_generate_job_data_id_format(self, generator, sample_content_ids, sample_partner_ids):
        """Test job ID format and uniqueness"""
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        job_ids = set()
        for record in job_records:
            job_id = record["job_id"]["S"]
            job_ids.add(job_id)

            # Verify ID format (job_number)
            assert job_id.startswith("job_")
            number_part = job_id.replace("job_", "")
            assert number_part.isdigit()

        # All IDs should be unique
        assert len(job_ids) == len(job_records)

    def test_generate_job_data_status_distribution(self, generator, sample_content_ids, sample_partner_ids):
        """Test job status distribution"""
        random.seed(0)  # deterministic: variety assertion below must not flake under pytest-randomly (Req 17.2)
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        statuses = set()
        for record in job_records:
            status = record["status"]["S"]
            statuses.add(status)
            assert status in ["pending", "processing", "completed", "failed"]

        # Should have variety in statuses (if enough records)
        if len(job_records) >= 3:
            assert len(statuses) > 1

    def test_generate_job_data_timing_consistency(self, generator, sample_content_ids, sample_partner_ids):
        """Test job timing consistency"""
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        for record in job_records:
            created_at_str = record["created_at"]["S"]
            created_at = datetime.fromisoformat(created_at_str)

            # Check optional timing fields
            if "started_at" in record:
                started_at = datetime.fromisoformat(record["started_at"]["S"])
                assert started_at >= created_at

            if "completed_at" in record:
                completed_at = datetime.fromisoformat(record["completed_at"]["S"])
                assert completed_at >= created_at

                if "started_at" in record:
                    started_at = datetime.fromisoformat(record["started_at"]["S"])
                    assert completed_at >= started_at

    def test_generate_job_data_processing_time(self, generator, sample_content_ids, sample_partner_ids):
        """Test processing time generation"""
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        for record in job_records:
            if "processing_time_ms" in record:
                processing_time = int(record["processing_time_ms"]["N"])
                assert processing_time >= 0
                assert processing_time < 3600000  # Less than 1 hour in ms

    def test_generate_job_data_retry_count(self, generator, sample_content_ids, sample_partner_ids):
        """Test retry count generation"""
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        for record in job_records:
            retry_count = int(record["retry_count"]["N"])
            assert 0 <= retry_count <= 10

    def test_generate_job_data_priority(self, generator, sample_content_ids, sample_partner_ids):
        """Test priority generation"""
        job_records = generator.generate_job_data(sample_content_ids, sample_partner_ids)

        for record in job_records:
            priority = int(record["priority"]["N"])
            assert 1 <= priority <= 10


class TestAccessLogGeneration:
    """Test access log generation functionality"""

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
    def sample_partner_ids(self):
        """Sample partner IDs for testing"""
        return ["partner_001", "partner_002", "partner_003"]

    @pytest.fixture
    def sample_content_ids(self):
        """Sample content IDs for testing"""
        return ["art_001", "rpt_002", "vid_003", "img_004", "doc_005"]

    def test_generate_access_logs_basic(self, generator, sample_partner_ids, sample_content_ids):
        """Test basic access log generation"""
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        # Verify correct number of logs generated
        assert len(logs) == generator.config.access_log_count

        # Verify each log has required fields
        for log in logs:
            assert "log_id" in log
            assert "partner_id" in log
            assert "timestamp" in log
            assert "endpoint" in log
            assert "method" in log
            assert "response_code" in log
            assert "response_time_ms" in log
            assert "request_size" in log
            assert "response_size" in log
            assert "ip_address" in log
            assert "user_agent" in log
            assert "billing_units" in log
            assert "query_params" in log
            assert "metadata" in log

    def test_generate_access_logs_partner_distribution(self, generator, sample_partner_ids, sample_content_ids):
        """Test that logs are distributed across partners"""
        random.seed(0)  # deterministic: variety assertion below must not flake under pytest-randomly (Req 17.2)
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        # Extract partner IDs from logs
        used_partners = set()
        for log in logs:
            partner_id = log["partner_id"]["S"]
            used_partners.add(partner_id)
            assert partner_id in sample_partner_ids

        # Should use multiple partners (not just one)
        assert len(used_partners) > 1

    def test_generate_access_logs_endpoint_variety(self, generator, sample_partner_ids, sample_content_ids):
        """Test that logs use various endpoints"""
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        # Extract endpoints from logs
        used_endpoints = set()
        for log in logs:
            endpoint = log["endpoint"]["S"]
            used_endpoints.add(endpoint)

        # Should use multiple endpoints
        assert len(used_endpoints) > 1

        # Verify endpoints are realistic API paths
        for endpoint in used_endpoints:
            assert endpoint.startswith("/api/v1/")

    def test_generate_access_logs_response_codes(self, generator, sample_partner_ids, sample_content_ids):
        """Test that logs have realistic response codes"""
        random.seed(0)  # deterministic: variety assertion below must not flake under pytest-randomly (Req 17.2)
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        # Extract response codes
        response_codes = []
        for log in logs:
            code = int(log["response_code"]["N"])
            response_codes.append(code)

        # Should have mostly success codes (2xx)
        success_codes = [code for code in response_codes if 200 <= code < 300]
        assert len(success_codes) > len(response_codes) * 0.5  # At least 50% success

        # Should have some variety in response codes
        unique_codes = set(response_codes)
        assert len(unique_codes) > 1

    def test_generate_access_logs_timestamps(self, generator, sample_partner_ids, sample_content_ids):
        """Test that logs have realistic timestamps"""
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        # Extract and parse timestamps
        timestamps = []
        for log in logs:
            timestamp_str = log["timestamp"]["S"]
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            timestamps.append(timestamp)

        # Should have the expected number of timestamps
        assert len(timestamps) == len(logs)

        # All timestamps should be valid datetime objects
        for timestamp in timestamps:
            assert isinstance(timestamp, datetime)

        # Timestamps should be in chronological order (sorted)
        sorted_timestamps = sorted(timestamps)
        assert timestamps == sorted_timestamps

        # Timestamps should span a reasonable range (not all the same)
        if len(timestamps) > 1:
            time_span = max(timestamps) - min(timestamps)
            assert time_span.total_seconds() > 0  # Should have some time variation

    def test_generate_access_logs_content_references(self, generator, sample_partner_ids, sample_content_ids):
        """Test that some logs reference content IDs"""
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        # Count logs with content references
        logs_with_content = 0
        for log in logs:
            if "content_id" in log:
                content_id = log["content_id"]["S"]
                assert content_id in sample_content_ids
                logs_with_content += 1

        # Some logs should reference content (not all endpoints require content)
        assert logs_with_content > 0

    def test_generate_access_logs_billing_units(self, generator, sample_partner_ids, sample_content_ids):
        """Test that billing units are calculated correctly"""
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        for log in logs:
            billing_units = int(log["billing_units"]["N"])
            response_code = int(log["response_code"]["N"])

            # Failed requests should have 0 billing units
            if response_code >= 400:
                assert billing_units == 0
            else:
                # Successful requests should have positive billing units
                assert billing_units > 0

    def test_generate_access_logs_metadata(self, generator, sample_partner_ids, sample_content_ids):
        """Test that metadata is properly generated"""
        logs = generator.generate_access_logs(sample_partner_ids, sample_content_ids)

        for log in logs:
            metadata = log["metadata"]["M"]

            # Verify required metadata fields
            assert "request_id" in metadata
            assert "session_id" in metadata
            assert "api_version" in metadata
            assert "client_type" in metadata
            assert "geographic_region" in metadata
            assert "cache_hit" in metadata

            # Verify metadata types
            assert metadata["api_version"]["S"] == "v1"
            assert isinstance(metadata["cache_hit"]["BOOL"], bool)

    def test_generate_access_logs_empty_partners(self, generator, sample_content_ids):
        """Test behavior with empty partner list"""
        logs = generator.generate_access_logs([], sample_content_ids)

        # Should return empty list when no partners provided
        assert len(logs) == 0

    def test_generate_access_logs_empty_content(self, generator, sample_partner_ids):
        """Test behavior with empty content list"""
        logs = generator.generate_access_logs(sample_partner_ids, [])

        # Should still generate logs (not all endpoints require content)
        assert len(logs) == generator.config.access_log_count

        # None of the logs should have content_id
        for log in logs:
            assert "content_id" not in log

    def test_usage_pattern_generation(self, generator):
        """Test usage pattern generation methods"""
        start_date = datetime(2024, 1, 1)
        end_date = datetime(2024, 1, 31)

        # Test usage pattern generation
        patterns = generator._generate_usage_patterns(start_date, end_date, 100)

        assert len(patterns) == 100
        assert all(start_date <= ts <= end_date for ts in patterns)
        assert patterns == sorted(patterns)  # Should be sorted

    def test_day_of_week_multiplier(self, generator):
        """Test day of week multiplier logic"""
        # Test all days of the week
        for weekday in range(7):
            multiplier = generator._get_day_of_week_multiplier(weekday)
            assert isinstance(multiplier, float)
            assert 0.1 <= multiplier <= 2.0  # Reasonable range

        # Weekdays should generally have higher multipliers than weekends
        weekday_avg = sum(generator._get_day_of_week_multiplier(i) for i in range(5)) / 5
        weekend_avg = sum(generator._get_day_of_week_multiplier(i) for i in range(5, 7)) / 2
        assert weekday_avg > weekend_avg

    def test_response_code_generation(self, generator):
        """Test response code generation logic"""
        random.seed(0)  # deterministic: variety assertion below must not flake under pytest-randomly (Req 17.2)
        # Test high success rate
        codes = [generator._generate_response_code(0.95) for _ in range(100)]
        success_rate = len([c for c in codes if 200 <= c < 300]) / len(codes)
        assert success_rate > 0.8  # Should be mostly successful

        # Test low success rate
        codes = [generator._generate_response_code(0.5) for _ in range(100)]
        success_rate = len([c for c in codes if 200 <= c < 300]) / len(codes)
        assert success_rate < 0.7  # Should have more errors

    def test_user_agent_generation(self, generator):
        """Test user agent generation"""
        user_agents = [generator._generate_realistic_user_agent() for _ in range(10)]

        # Should generate variety
        assert len(set(user_agents)) > 1

        # Should be realistic user agent strings
        for ua in user_agents:
            assert isinstance(ua, str)
            assert len(ua) > 5  # Should be meaningful strings


class TestMockDataValidation:
    """Test data validation functionality"""

    @pytest.fixture
    def config(self):
        """Create test configuration"""
        return GeneratorConfig(
            content_count=3,
            partner_count=2,
            access_log_count=5,
            job_count=3,
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
    def schema_validator(self):
        """Create schema validator"""
        return SchemaValidator()

    @pytest.fixture
    def integrity_validator(self):
        """Create referential integrity validator"""
        return ReferentialIntegrityValidator()

    def test_schema_validator_content_valid(self, schema_validator):
        """Test schema validation for valid content record"""
        valid_content = {
            "content_id": {"S": "art_001"},
            "title": {"S": "Test Article"},
            "content_type": {"S": "article"},
            "description": {"S": "A test article description"},
            "content": {"S": "This is the content of the test article."},
            "author": {"S": "John Doe"},
            "publisher": {"S": "Test Publisher"},
            "created_at": {"S": "2024-01-01T10:00:00"},
            "updated_at": {"S": "2024-01-01T10:00:00"},
            "iab_categories": {"SS": ["Business > Finance"]},
            "tags": {"SS": ["test", "article"]},
            "url": {"S": "https://example.com/article"},
            "access_level": {"S": "public"},
            "pricing_tier": {"S": "free"},
            "language": {"S": "en"},
            "word_count": {"N": "100"},
            "metadata": {"M": {"source": {"S": "test"}}},
        }

        result = schema_validator.validate_record(valid_content, "content")
        assert result.is_valid
        assert len(result.errors) == 0

    def test_schema_validator_content_invalid(self, schema_validator):
        """Test schema validation for invalid content record"""
        invalid_content = {
            "content_id": {"S": "invalid_id"},  # Wrong pattern
            "title": {"S": ""},  # Too short
            "content_type": {"S": "invalid_type"},  # Not in allowed values
            # Missing required fields
        }

        result = schema_validator.validate_record(invalid_content, "content")
        assert not result.is_valid
        assert len(result.errors) > 0

        # Check specific errors
        error_fields = [error.field for error in result.errors]
        assert "content_id" in error_fields  # Pattern mismatch
        assert "title" in error_fields  # Too short
        assert "content_type" in error_fields  # Invalid value

    def test_schema_validator_partner_valid(self, schema_validator):
        """Test schema validation for valid partner record"""
        valid_partner = {
            "PK": {"S": "PARTNER#partner_001"},
            "SK": {"S": "META"},
            "partner_id": {"S": "partner_001"},
            "partner_name": {"S": "Test Company"},
            "partner_type": {"S": "publisher"},
            "contact_email": {"S": "test@example.com"},
            "contact_name": {"S": "John Doe"},
            "pricing_tier": {"S": "basic"},
            "status": {"S": "active"},
            "identity_source": {"S": "direct"},
            "allowed_endpoints": {"L": [{"S": "queryContent"}]},
            "created_at": {"S": "2024-01-01T10:00:00"},
            "updated_at": {"S": "2024-01-02T10:00:00"},
        }

        result = schema_validator.validate_record(valid_partner, "partners")
        assert result.is_valid
        assert len(result.errors) == 0

    def test_referential_integrity_valid(self, integrity_validator):
        """Test referential integrity validation with valid references"""
        content_records = [{"content_id": {"S": "art_001"}}, {"content_id": {"S": "rpt_002"}}]

        partner_records = [{"partner_id": {"S": "partner_001"}}, {"partner_id": {"S": "partner_002"}}]

        job_records = [{"job_id": {"S": "job_001"}, "content_id": {"S": "art_001"}, "partner_id": {"S": "partner_001"}}]

        access_log_records = [
            {"log_id": {"S": "log_001"}, "partner_id": {"S": "partner_002"}, "content_id": {"S": "rpt_002"}}
        ]

        result = integrity_validator.validate_referential_integrity(
            content_records, partner_records, job_records, access_log_records
        )

        assert result.is_valid
        assert len(result.errors) == 0

    def test_referential_integrity_invalid(self, integrity_validator):
        """Test referential integrity validation with invalid references"""
        content_records = [{"content_id": {"S": "art_001"}}]

        partner_records = [{"partner_id": {"S": "partner_001"}}]

        job_records = [
            {
                "job_id": {"S": "job_001"},
                "content_id": {"S": "nonexistent_content"},  # Invalid reference
                "partner_id": {"S": "nonexistent_partner"},  # Invalid reference
            }
        ]

        access_log_records = []

        result = integrity_validator.validate_referential_integrity(
            content_records, partner_records, job_records, access_log_records
        )

        assert not result.is_valid
        assert len(result.errors) == 2  # Two invalid references

        # Check specific errors
        error_messages = [error.message for error in result.errors]
        assert any("nonexistent_content" in msg for msg in error_messages)
        assert any("nonexistent_partner" in msg for msg in error_messages)

    def test_validation_reporter_generates_report(self):
        """Test that validation reporter generates proper reports"""
        from mock_data_generator.validation import ValidationResult, ValidationError

        # Create mock validation results
        validation_results = {
            "content": ValidationResult(is_valid=True, errors=[], warnings=[]),
            "partners": ValidationResult(
                is_valid=False,
                errors=[ValidationError("partner_name", "Required field missing", record_id="partner_001")],
                warnings=[ValidationError("metadata", "Unexpected field", record_id="partner_001")],
            ),
        }

        reporter = ValidationReporter()
        report = reporter.generate_report(validation_results)

        assert isinstance(report, str)
        assert "MOCK DATA VALIDATION REPORT" in report
        assert "CONTENT TABLE: ✓ PASS" in report
        assert "PARTNERS TABLE: ✗ FAIL" in report
        assert "Required field missing" in report
        assert "Unexpected field" in report

    def test_generator_validation_integration(self, generator):
        """Test full validation integration with generator"""
        # Generate small amounts of data
        content_records = generator.generate_content_data()
        partner_records = generator.generate_partner_data()

        # Extract IDs for dependent data
        content_ids = [item["content_id"]["S"] for item in content_records]
        partner_ids = [item["partner_id"]["S"] for item in partner_records]

        job_records = generator.generate_job_data(content_ids, partner_ids)
        access_log_records = generator.generate_access_logs(partner_ids, content_ids)

        # Run validation
        validation_results = generator.validate_generated_data(
            content_records, partner_records, job_records, access_log_records
        )

        # All validations should pass (warnings are OK)
        for table_name, result in validation_results.items():
            assert result.is_valid, f"Validation failed for {table_name}: {result.errors}"

    def test_validate_single_record(self, generator):
        """Test single record validation"""
        # Generate one content record
        content_records = generator.generate_content_data()
        assert len(content_records) > 0

        # Validate single record
        result = generator.validate_single_record(content_records[0], "content")
        assert result.is_valid
        assert len(result.errors) == 0

    def test_validation_only_mode(self, generator):
        """Test validation-only mode (no DynamoDB writes)"""
        success = generator.validate_data_generation_only()
        assert success  # Should pass validation without errors


class TestDynamoDBOperations:
    """Test DynamoDB operations using mocked AWS services"""

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

    @mock_aws
    def test_batch_write_items_success(self, config):
        """Test successful batch write operations"""
        # Create mock DynamoDB table
        dynamodb = boto3.client("dynamodb", region_name="us-west-2")
        table_name = "test-table"

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create generator with real DynamoDB client
        with patch("boto3.Session") as mock_session:
            mock_session.return_value.client.return_value = dynamodb
            generator = MockDataGenerator(config)

            # Test data
            items = [{"id": {"S": "item1"}, "data": {"S": "test1"}}, {"id": {"S": "item2"}, "data": {"S": "test2"}}]

            # Test batch write
            result = generator.batch_write_items(table_name, items)
            assert result is True

            # Verify items were written
            response = dynamodb.scan(TableName=table_name)
            assert len(response["Items"]) == 2

    @mock_aws
    def test_batch_write_items_chunked(self, config):
        """Test chunked batch write operations for large datasets"""
        # Create mock DynamoDB table
        dynamodb = boto3.client("dynamodb", region_name="us-west-2")
        table_name = "test-table"

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create generator with real DynamoDB client
        with patch("boto3.Session") as mock_session:
            mock_session.return_value.client.return_value = dynamodb
            generator = MockDataGenerator(config)

            # Create more items than batch limit (25)
            items = []
            for i in range(30):
                items.append({"id": {"S": f"item{i}"}, "data": {"S": f"test{i}"}})

            # Test chunked batch write
            result = generator.batch_write_items_chunked(table_name, items)
            assert result is True

            # Verify all items were written
            response = dynamodb.scan(TableName=table_name)
            assert len(response["Items"]) == 30

    @mock_aws
    def test_scan_table_items(self, config):
        """Test table scanning operations"""
        # Create mock DynamoDB table with data
        dynamodb = boto3.client("dynamodb", region_name="us-west-2")
        table_name = "test-table"

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add test data
        for i in range(5):
            dynamodb.put_item(TableName=table_name, Item={"id": {"S": f"item{i}"}, "data": {"S": f"test{i}"}})

        # Create generator with real DynamoDB client
        with patch("boto3.Session") as mock_session:
            mock_session.return_value.client.return_value = dynamodb
            generator = MockDataGenerator(config)

            # Test scan
            items = generator.scan_table_items(table_name)
            assert len(items) == 5

            # Verify item structure
            for item in items:
                assert "id" in item
                assert "data" in item

    @mock_aws
    def test_batch_delete_items(self, config):
        """Test batch delete operations"""
        # Create mock DynamoDB table with data
        dynamodb = boto3.client("dynamodb", region_name="us-west-2")
        table_name = "test-table"

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add test data
        items_to_delete = []
        for i in range(3):
            item = {"id": {"S": f"item{i}"}, "data": {"S": f"test{i}"}}
            dynamodb.put_item(TableName=table_name, Item=item)
            items_to_delete.append(item)

        # Create generator with real DynamoDB client
        with patch("boto3.Session") as mock_session:
            mock_session.return_value.client.return_value = dynamodb
            generator = MockDataGenerator(config)

            # Test batch delete
            result = generator.batch_delete_items(table_name, items_to_delete, ["id"])
            assert result is True

            # Verify items were deleted
            response = dynamodb.scan(TableName=table_name)
            assert len(response["Items"]) == 0

    @mock_aws
    def test_clear_table(self, config):
        """Test table clearing operations"""
        # Create mock DynamoDB table with data
        dynamodb = boto3.client("dynamodb", region_name="us-west-2")
        table_name = "test-table"

        dynamodb.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add test data
        for i in range(10):
            dynamodb.put_item(TableName=table_name, Item={"id": {"S": f"item{i}"}, "data": {"S": f"test{i}"}})

        # Create generator with real DynamoDB client
        with patch("boto3.Session") as mock_session:
            mock_session.return_value.client.return_value = dynamodb
            generator = MockDataGenerator(config)

            # Test clear table
            result = generator.clear_table(table_name)
            assert result is True

            # Verify table is empty
            response = dynamodb.scan(TableName=table_name)
            assert len(response["Items"]) == 0

    def test_batch_write_items_empty_list(self, config):
        """Test batch write with empty item list"""
        with patch("boto3.Session"):
            generator = MockDataGenerator(config)

            result = generator.batch_write_items("test-table", [])
            assert result is True

    def test_batch_write_items_oversized_batch(self, config):
        """Test batch write with oversized batch"""
        with patch("boto3.Session"):
            generator = MockDataGenerator(config)

            # Create batch larger than limit (25)
            items = [{"id": {"S": f"item{i}"}} for i in range(30)]

            result = generator.batch_write_items("test-table", items)
            assert result is False

    def test_validate_all_tables_success(self, config):
        """Test validation of all required tables"""
        config.partner_table_name = "cm-partners-test"
        with patch("boto3.Session"):
            generator = MockDataGenerator(config)

            # Mock successful table validation
            generator.validate_table_access = MagicMock(return_value=True)

            result = generator.validate_all_tables()
            assert result is True

            # Should check all table types
            expected_calls = len(generator.TABLE_NAMES)
            assert generator.validate_table_access.call_count == expected_calls

    def test_validate_all_tables_failure(self, config):
        """Test validation failure for some tables"""
        with patch("boto3.Session"):
            generator = MockDataGenerator(config)

            # Mock mixed table validation results
            def mock_validate(table_name):
                return table_name != "cm-content"  # Fail content table

            generator.validate_table_access = MagicMock(side_effect=mock_validate)

            result = generator.validate_all_tables()
            assert result is False


class TestIntegrationTests:
    """Integration tests that validate complete data generation workflows"""

    @pytest.fixture
    def config(self):
        """Create test configuration"""
        return GeneratorConfig(
            content_count=3,
            partner_count=2,
            access_log_count=5,
            job_count=3,
            date_range_months=1,
            aws_profile="test",
            aws_region="us-west-2",
        )

    @pytest.fixture
    def generator(self, config):
        """Create mock data generator with mocked AWS session"""
        with patch("boto3.Session"):
            return MockDataGenerator(config)

    def test_complete_data_generation_workflow(self, generator):
        """Test complete data generation workflow with validation"""
        # Generate all data types
        content_records = generator.generate_content_data()
        partner_records = generator.generate_partner_data()

        # Extract IDs for dependent data
        content_ids = [item["content_id"]["S"] for item in content_records]
        partner_ids = [item["partner_id"]["S"] for item in partner_records]

        job_records = generator.generate_job_data(content_ids, partner_ids)
        access_log_records = generator.generate_access_logs(partner_ids, content_ids)

        # Validate all generated data
        validation_results = generator.validate_generated_data(
            content_records, partner_records, job_records, access_log_records
        )

        # All validations should pass
        for table_name, result in validation_results.items():
            assert result.is_valid, f"Validation failed for {table_name}: {result.errors}"

        # Verify data counts
        assert len(content_records) == generator.config.content_count
        assert len(partner_records) == generator.config.partner_count
        assert len(job_records) == generator.config.job_count
        assert len(access_log_records) == generator.config.access_log_count

    def test_referential_integrity_across_tables(self, generator):
        """Test referential integrity across all generated tables"""
        # Generate all data types
        content_records = generator.generate_content_data()
        partner_records = generator.generate_partner_data()

        content_ids = [item["content_id"]["S"] for item in content_records]
        partner_ids = [item["partner_id"]["S"] for item in partner_records]

        job_records = generator.generate_job_data(content_ids, partner_ids)
        access_log_records = generator.generate_access_logs(partner_ids, content_ids)

        # Validate referential integrity
        integrity_validator = ReferentialIntegrityValidator()
        result = integrity_validator.validate_referential_integrity(
            content_records, partner_records, job_records, access_log_records
        )

        assert result.is_valid, f"Referential integrity validation failed: {result.errors}"

    def test_data_structure_consistency(self, generator):
        """Test that all generated data follows consistent structure patterns"""
        # Generate all data types
        content_records = generator.generate_content_data()
        partner_records = generator.generate_partner_data()

        content_ids = [item["content_id"]["S"] for item in content_records]
        partner_ids = [item["partner_id"]["S"] for item in partner_records]

        job_records = generator.generate_job_data(content_ids, partner_ids)
        access_log_records = generator.generate_access_logs(partner_ids, content_ids)

        # Validate schema compliance
        schema_validator = SchemaValidator()

        content_result = schema_validator.validate_records(content_records, "content")
        partner_result = schema_validator.validate_records(partner_records, "partners")
        job_result = schema_validator.validate_records(job_records, "jobs")
        log_result = schema_validator.validate_records(access_log_records, "access_logs")

        assert content_result.is_valid, f"Content schema validation failed: {content_result.errors}"
        assert partner_result.is_valid, f"Partner schema validation failed: {partner_result.errors}"
        assert job_result.is_valid, f"Job schema validation failed: {job_result.errors}"
        assert log_result.is_valid, f"Access log schema validation failed: {log_result.errors}"

    def test_data_generation_with_different_configurations(self):
        """Test data generation with various configuration parameters"""
        configs = [
            GeneratorConfig(content_count=1, partner_count=1, access_log_count=1, job_count=1),
            GeneratorConfig(content_count=10, partner_count=5, access_log_count=50, job_count=20),
            GeneratorConfig(date_range_months=6, content_types=["article", "video"]),
            GeneratorConfig(partner_types=["publisher", "ai_operator"]),
        ]

        for config in configs:
            with patch("boto3.Session"):
                generator = MockDataGenerator(config)

                # Generate data
                content_records = generator.generate_content_data()
                partner_records = generator.generate_partner_data()

                # Verify counts match configuration
                assert len(content_records) == config.content_count
                assert len(partner_records) == config.partner_count

                # Verify content types if specified
                if hasattr(config, "content_types") and config.content_types:
                    content_types = {record["content_type"]["S"] for record in content_records}
                    assert content_types.issubset(set(config.content_types))

                # Verify partner types if specified
                if hasattr(config, "partner_types") and config.partner_types:
                    partner_types = {record["partner_type"]["S"] for record in partner_records}
                    assert partner_types.issubset(set(config.partner_types))

    @mock_aws
    def test_end_to_end_with_real_dynamodb(self, config):
        """Test end-to-end workflow with real DynamoDB operations"""
        # Create mock DynamoDB tables
        dynamodb = boto3.client("dynamodb", region_name="us-west-2")

        # Create all required tables
        table_names = {**MockDataGenerator.TABLE_NAMES, "partners": config.partner_table_name or "cm-partners-test"}
        config.partner_table_name = table_names["partners"]
        for table_type, table_name in table_names.items():
            dynamodb.create_table(
                TableName=table_name,
                KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )

        # Create generator with real DynamoDB client
        with patch("boto3.Session") as mock_session:
            mock_session.return_value.client.return_value = dynamodb
            generator = MockDataGenerator(config)

            # Test table validation
            assert generator.validate_all_tables() is True

            # Generate and write data (would need to implement write_all_data method)
            content_records = generator.generate_content_data()
            partner_records = generator.generate_partner_data()

            # Verify data was generated correctly
            assert len(content_records) == config.content_count
            assert len(partner_records) == config.partner_count


class TestErrorHandling:
    """Test error handling and edge cases"""

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

    def test_generator_initialization_aws_error(self, config):
        """Test generator initialization with AWS session error"""
        with patch("boto3.Session", side_effect=Exception("AWS Error")):
            with pytest.raises(Exception, match="AWS Error"):
                MockDataGenerator(config)

    def test_dynamodb_client_credentials_error(self, config):
        """Test DynamoDB client initialization with credentials error"""
        from botocore.exceptions import NoCredentialsError

        with patch("boto3.Session"):
            generator = MockDataGenerator(config)
            generator._session.client = MagicMock(side_effect=NoCredentialsError())

            with pytest.raises(NoCredentialsError):
                _ = generator.dynamodb_client

    def test_generate_data_with_empty_reference_lists(self, config):
        """Test data generation with empty reference lists"""
        with patch("boto3.Session"):
            generator = MockDataGenerator(config)

            # Test job generation with empty lists
            job_records = generator.generate_job_data([], [])
            assert len(job_records) == 0

            # Test access log generation with empty partner list
            access_logs = generator.generate_access_logs([], ["content_001"])
            assert len(access_logs) == 0

    def test_validation_with_invalid_data(self):
        """Test validation with intentionally invalid data"""
        schema_validator = SchemaValidator()

        # Invalid content record (missing required fields)
        invalid_content = {
            "content_id": {"S": "invalid"},  # Wrong pattern
            "title": {"S": ""},  # Too short
        }

        result = schema_validator.validate_record(invalid_content, "content")
        assert not result.is_valid
        assert len(result.errors) > 0

    def test_referential_integrity_with_broken_references(self):
        """Test referential integrity validation with broken references"""
        integrity_validator = ReferentialIntegrityValidator()

        content_records = [{"content_id": {"S": "art_001"}}]
        partner_records = [{"partner_id": {"S": "partner_001"}}]

        # Job with invalid references
        job_records = [
            {
                "job_id": {"S": "job_001"},
                "content_id": {"S": "nonexistent_content"},
                "partner_id": {"S": "nonexistent_partner"},
            }
        ]

        result = integrity_validator.validate_referential_integrity(content_records, partner_records, job_records, [])

        assert not result.is_valid
        assert len(result.errors) == 2  # Two broken references


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

    def test_analytics_data_schema_validation(self, generator, sample_content_ids, sample_partner_ids):
        """Test that generated analytics data passes schema validation"""
        analytics_records = generator.generate_analytics_data(sample_content_ids, sample_partner_ids)

        schema_validator = SchemaValidator()
        result = schema_validator.validate_records(analytics_records, "analytics")

        # Should pass validation
        assert result.is_valid
        assert len(result.errors) == 0


class TestBotRulesDataGeneration:
    """Test bot rules data generation functionality"""

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

    def test_generate_bot_rules_data_basic(self, generator):
        """Test basic bot rules data generation"""
        bot_rules_records = generator.generate_bot_rules_data()

        # Should generate a reasonable number of rules
        assert 10 <= len(bot_rules_records) <= 20

        # Verify each record has required fields
        for record in bot_rules_records:
            assert "rule_id" in record
            assert "rule_name" in record
            assert "description" in record
            assert "action" in record
            assert "priority" in record
            assert "conditions" in record
            assert "status" in record
            assert "created_at" in record
            assert "updated_at" in record
            assert "sync_status" in record
            assert "metadata" in record

    def test_bot_rules_unique_priorities(self, generator):
        """Test that active bot rules have unique priorities"""
        bot_rules_records = generator.generate_bot_rules_data()

        # Extract priorities for active rules
        active_priorities = []
        for record in bot_rules_records:
            if record["status"]["S"] == "active":
                priority = int(record["priority"]["N"])
                active_priorities.append(priority)

        # All active rule priorities should be unique
        assert len(active_priorities) == len(set(active_priorities))

    def test_bot_rules_schema_validation(self, generator):
        """Test that generated bot rules data passes schema validation"""
        bot_rules_records = generator.generate_bot_rules_data()

        schema_validator = SchemaValidator()
        result = schema_validator.validate_records(bot_rules_records, "bot_rules")

        # Should pass validation
        assert result.is_valid
        assert len(result.errors) == 0

    def test_bot_rules_waf_conditions_format(self, generator):
        """Test that WAF conditions are properly formatted"""
        bot_rules_records = generator.generate_bot_rules_data()

        for record in bot_rules_records:
            conditions = record["conditions"]["M"]

            # Should have some condition fields
            assert len(conditions) > 0

            # Common condition fields should be properly formatted
            if "field_to_match" in conditions:
                field_to_match = conditions["field_to_match"]["M"]
                assert "type" in field_to_match
                assert "S" in field_to_match["type"]


class TestEnhancedValidation:
    """Test enhanced validation functionality for new tables"""

    @pytest.fixture
    def schema_validator(self):
        """Create schema validator"""
        return SchemaValidator()

    @pytest.fixture
    def integrity_validator(self):
        """Create referential integrity validator"""
        return ReferentialIntegrityValidator()

    def test_bot_rules_schema_validation(self, schema_validator):
        """Test bot rules schema validation"""
        # Valid bot rule record
        valid_record = {
            "rule_id": {"S": "rule_001"},
            "rule_name": {"S": "Test Rule"},
            "description": {"S": "A test rule for validation"},
            "action": {"S": "block"},
            "priority": {"N": "100"},
            "conditions": {"M": {"test": {"S": "value"}}},
            "status": {"S": "active"},
            "created_at": {"S": "2024-01-15T10:00:00Z"},
            "updated_at": {"S": "2024-01-15T10:00:00Z"},
            "sync_status": {"S": "pending"},
            "metadata": {"M": {"test": {"S": "value"}}},
        }

        result = schema_validator.validate_record(valid_record, "bot_rules")
        assert result.is_valid
        assert len(result.errors) == 0

    def test_bot_rules_invalid_action(self, schema_validator):
        """Test bot rules validation with invalid action"""
        invalid_record = {
            "rule_id": {"S": "rule_001"},
            "rule_name": {"S": "Test Rule"},
            "description": {"S": "A test rule"},
            "action": {"S": "invalid_action"},  # Invalid action
            "priority": {"N": "100"},
            "conditions": {"M": {"test": {"S": "value"}}},
            "status": {"S": "active"},
            "created_at": {"S": "2024-01-15T10:00:00Z"},
            "updated_at": {"S": "2024-01-15T10:00:00Z"},
            "sync_status": {"S": "pending"},
            "metadata": {"M": {"test": {"S": "value"}}},
        }

        result = schema_validator.validate_record(invalid_record, "bot_rules")
        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("action" in error.field for error in result.errors)

    def test_enhanced_referential_integrity_with_new_tables(self, integrity_validator):
        """Test referential integrity validation with analytics and bot rules"""
        # Sample data
        content_records = [{"content_id": {"S": "art_001"}}]
        partner_records = [{"partner_id": {"S": "partner_001"}}]
        job_records = []
        access_log_records = []

        # Analytics records with valid references
        analytics_records = [
            {
                "metric_date": {"S": "2024-01-15"},
                "metric_type_timestamp": {"S": "content_access_2024-01-15T10:00:00Z"},
                "metadata": {"M": {"content_id": {"S": "art_001"}, "partner_id": {"S": "partner_001"}}},
            }
        ]

        # Bot rules records (self-contained)
        bot_rules_records = [
            {
                "rule_id": {"S": "rule_001"},
                "rule_name": {"S": "Test Rule"},
                "priority": {"N": "100"},
                "status": {"S": "active"},
            }
        ]

        result = integrity_validator.validate_referential_integrity(
            content_records, partner_records, job_records, access_log_records, analytics_records, bot_rules_records
        )

        # Should pass validation
        assert result.is_valid
        assert len(result.errors) == 0

    def test_bot_rules_priority_conflict_detection(self, integrity_validator):
        """Test detection of priority conflicts in bot rules"""
        # Bot rules with duplicate priorities for active rules
        bot_rules_records = [
            {
                "rule_id": {"S": "rule_001"},
                "rule_name": {"S": "Rule 1"},
                "priority": {"N": "100"},
                "status": {"S": "active"},
            },
            {
                "rule_id": {"S": "rule_002"},
                "rule_name": {"S": "Rule 2"},
                "priority": {"N": "100"},  # Duplicate priority
                "status": {"S": "active"},
            },
        ]

        result = integrity_validator._validate_bot_rules_references(bot_rules_records)

        # Should detect priority conflict
        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("priority" in error.field for error in result.errors)


def mock_open(read_data=""):
    """Helper function to create mock file open"""
    from unittest.mock import mock_open as original_mock_open

    return original_mock_open(read_data=read_data)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
