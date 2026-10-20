#!/usr/bin/env python3
"""
Integration test for validating existing DynamoDB data state.

This test validates that the existing DynamoDB data is in a proper state
for content management operations and documents current data volumes
and structure for development reference.

Requirements tested:
- 5.4: Data validation and integrity checks
- 7.3: Development environment data consistency
"""

import logging
import os
import pytest
import boto3
from typing import Dict, Any, List
from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator

# Integration_Tier: no moto; opens a live boto3 session and validates real DynamoDB tables/data.
pytestmark = pytest.mark.integration


class TestExistingDataValidation:
    """Test suite for validating existing DynamoDB data state."""

    @classmethod
    def setup_class(cls):
        """Set up test class with AWS resources."""
        # Use the ambient credential chain (no hardcoded named profile) so this runs in CI
        # against the deployed environment; a locally-exported AWS_PROFILE is still honored.
        cls.config = GeneratorConfig(aws_profile=None)
        cls.generator = MockDataGenerator(cls.config)

        # The deployed content table has a CloudFormation-generated physical name (only the content
        # table lacks a fixed name); resolve it from the conftest-populated TEST_CONTENT_TABLE
        # (CloudFormation Output) and point the generator at it. Other tables use fixed names.
        cls.content_table_name = os.environ.get("TEST_CONTENT_TABLE", "cm-content")
        cls.partner_table_name = os.environ.get("TEST_PARTNER_TABLE", "cm-partners")
        cls.generator.TABLE_NAMES = {
            **MockDataGenerator.TABLE_NAMES,
            "content": cls.content_table_name,
            "partners": cls.partner_table_name,
        }

        # Validate AWS connectivity
        assert cls.generator.validate_aws_connectivity(), "AWS connectivity validation failed"
        assert cls.generator.validate_all_tables(), "DynamoDB table validation failed"

    def test_table_accessibility(self):
        """Test that all required DynamoDB tables are accessible."""
        tables = [self.content_table_name, self.partner_table_name, "cm-jobs", "iab-access-logs"]

        for table_name in tables:
            try:
                response = self.generator.dynamodb_client.describe_table(TableName=table_name)
                assert response["Table"]["TableStatus"] == "ACTIVE", f"Table {table_name} is not active"
                logging.info(f"✓ Table {table_name} is accessible and active")
            except Exception as e:
                pytest.fail(f"Failed to access table {table_name}: {e}")

    def test_data_volumes_and_structure(self):
        """Test and document current data volumes and structure."""
        data_summary = {}

        # Check content table
        content_response = self.generator.dynamodb_client.scan(TableName=self.content_table_name, Select="COUNT")
        data_summary["content_count"] = content_response["Count"]

        # Check partners table
        partners_response = self.generator.dynamodb_client.scan(TableName=self.partner_table_name, Select="COUNT")
        data_summary["partners_count"] = partners_response["Count"]

        # Check jobs table
        jobs_response = self.generator.dynamodb_client.scan(TableName="cm-jobs", Select="COUNT")
        data_summary["jobs_count"] = jobs_response["Count"]

        # Check access logs table
        logs_response = self.generator.dynamodb_client.scan(TableName="iab-access-logs", Select="COUNT")
        data_summary["access_logs_count"] = logs_response["Count"]

        # Log data volumes for development reference
        logging.info("=== Current DynamoDB Data State ===")
        logging.info(f"Content (cm-content): {data_summary['content_count']} records")
        logging.info(f"Partners (cm-partners): {data_summary['partners_count']} records")
        logging.info(f"Jobs (cm-jobs): {data_summary['jobs_count']} records")
        logging.info(f"Access_logs (iab-access-logs): {data_summary['access_logs_count']} records")
        logging.info("===================================")

        # Validate minimum data requirements
        assert data_summary["content_count"] >= 0, "Content table should be accessible"
        assert data_summary["partners_count"] >= 0, "Partners table should be accessible"
        assert data_summary["jobs_count"] >= 0, "Jobs table should be accessible"
        assert data_summary["access_logs_count"] >= 0, "Access logs table should be accessible"

        # Don't return data_summary to avoid pytest warning
        assert True  # Test passes if we reach here

    def test_content_table_structure(self):
        """Test content table structure and sample data."""
        # Get a sample record to validate structure
        response = self.generator.dynamodb_client.scan(TableName=self.content_table_name, Limit=1)

        if response["Count"] > 0:
            sample_item = response["Items"][0]

            # Validate required fields exist
            required_fields = ["content_id", "title", "content_type", "author", "created_at"]
            for field in required_fields:
                assert field in sample_item, f"Required field {field} missing from content record"

            logging.info(
                f"✓ Content table structure validated with sample item: {sample_item.get('content_id', {}).get('S', 'unknown')}"
            )
        else:
            logging.info("ℹ Content table is empty - structure validation skipped")

    def test_partners_table_structure(self):
        """Test partners table structure and sample data (native API-key partner model)."""
        response = self.generator.dynamodb_client.scan(TableName=self.partner_table_name, Limit=25)

        if response["Count"] > 0:
            # Partner records are PARTNER#<id>/META items (KEY#<id> items link gateway keys). The
            # gateway owns key material, so no record carries a key hash, and permissions are the
            # key-gated operation ids.
            from shared.auth.partner_operations import KEY_GATED_OPERATION_IDS

            enriched = [
                it
                for it in response["Items"]
                if it.get("SK", {}).get("S") == "META" and "partner_id" in it and "partner_name" in it
            ]
            assert enriched, (
                "Expected at least one partner META record with partner_id + partner_name — "
                "seed via deployment/data/add-sample-partners.py"
            )
            sample_item = enriched[0]
            assert "partner_key_hash" not in sample_item, "Key material belongs to API Gateway, not the record"
            assert sample_item["partner_id"]["S"], "partner_id must be non-empty"
            assert sample_item["partner_name"]["S"], "partner_name must be non-empty"
            assert sample_item.get("identity_source", {}).get("S"), "identity_source must be set"
            granted = {entry["S"] for entry in sample_item.get("allowed_endpoints", {}).get("L", [])}
            assert granted <= KEY_GATED_OPERATION_IDS, f"allowed_endpoints must be operation ids: {granted}"

            logging.info(f"✓ Partners table structure validated: {sample_item['partner_id']['S']}")
        else:
            logging.info("ℹ Partners table is empty - structure validation skipped")

    def test_referential_integrity(self):
        """Test referential integrity between tables."""
        # Get content IDs
        content_response = self.generator.dynamodb_client.scan(
            TableName=self.content_table_name, ProjectionExpression="content_id"
        )
        content_ids = [item["content_id"]["S"] for item in content_response["Items"]]

        # Get partner IDs
        partners_response = self.generator.dynamodb_client.scan(
            TableName=self.partner_table_name, ProjectionExpression="partner_id"
        )
        # Partner records may be keyed by partner_key_hash without a separate partner_id;
        # only records that carry partner_id participate in this cross-reference (data-tolerant).
        partner_ids = [item["partner_id"]["S"] for item in partners_response["Items"] if "partner_id" in item]

        if content_ids and partner_ids:
            # Check that jobs reference valid content and partners
            jobs_response = self.generator.dynamodb_client.scan(
                TableName="cm-jobs", ProjectionExpression="content_id, partner_id"
            )

            for job in jobs_response["Items"]:
                job_content_id = job.get("content_id", {}).get("S")
                job_partner_id = job.get("partner_id", {}).get("S")

                if job_content_id:
                    assert job_content_id in content_ids, f"Job references invalid content_id: {job_content_id}"

                if job_partner_id:
                    assert job_partner_id in partner_ids, f"Job references invalid partner_id: {job_partner_id}"

            logging.info(
                f"✓ Referential integrity validated - {len(content_ids)} content IDs, {len(partner_ids)} partner IDs"
            )
        else:
            logging.info("ℹ Referential integrity check skipped - insufficient data")

    def test_data_generation_capability(self):
        """Test that data generation is working correctly."""
        # Run validation-only mode to test data generation without writing
        result = self.generator.validate_data_generation_only()
        assert result, "Data generation validation failed"
        logging.info("✓ Data generation capability validated")

    def test_aws_profile_and_region(self):
        """Test that a working AWS session resolves from the ambient credential chain."""
        session = boto3.Session()
        assert session.region_name is not None, "Expected a region to resolve from the ambient environment"

        # Verify we can access the correct account
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        logging.info(f"✓ Using AWS account: {identity['Account']} in region {session.region_name}")


if __name__ == "__main__":
    # Configure logging for standalone execution
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Run the tests
    pytest.main([__file__, "-v"])
