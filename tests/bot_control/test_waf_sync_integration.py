"""
Integration tests for WAF Sync Controller

Tests the WAF synchronization service integration scenarios with mocked AWS services.
These tests verify the controller works correctly with the expected AWS API patterns.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from decimal import Decimal
import time
from moto import mock_aws
from botocore.exceptions import ClientError

# Import the module under test
import sys
import os
import importlib.util

# Add the lambda directory to the path
lambda_dir = os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda")
sys.path.insert(0, lambda_dir)

# Import the WAF sync controller module
spec = importlib.util.spec_from_file_location(
    "waf_sync_controller", os.path.join(lambda_dir, "services", "waf_sync_controller.py")
)
waf_sync_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(waf_sync_module)

WAFSyncController = waf_sync_module.WAFSyncController
BotRule = waf_sync_module.BotRule
WAFSyncResult = waf_sync_module.WAFSyncResult


class TestWAFSyncControllerIntegration:
    """Integration test cases for WAF Sync Controller with mocked AWS services"""

    @pytest.fixture
    def mock_bot_rule(self):
        """Create a mock bot rule for testing"""
        return BotRule(
            rule_id="integration-test-rule-123",
            rule_name="IntegrationTestRule",
            description="Integration test bot rule",
            action="block",
            priority=50,
            conditions={"user_agent_patterns": ["integration-bot", "test-scraper"]},
            status="active",
            created_at="2024-01-15T10:00:00Z",
            updated_at="2024-01-15T10:00:00Z",
            waf_rule_id=None,
            last_synced=None,
            sync_status="pending",
        )

    @pytest.fixture
    def mock_web_acl_response(self):
        """Mock WebACL response from AWS WAF"""
        return {
            "WebACL": {
                "Name": "integration-test-webacl",
                "Id": "integration-test-webacl-id",
                "ARN": "arn:aws:wafv2:us-west-2:123456789012:regional/webacl/integration-test-webacl/integration-test-webacl-id",
                "DefaultAction": {"Allow": {}},
                "Rules": [
                    {
                        "Name": "ExistingIntegrationRule",
                        "Priority": 1,
                        "Statement": {
                            "ByteMatchStatement": {
                                "SearchString": "existing",
                                "FieldToMatch": {"SingleHeader": {"Name": "user-agent"}},
                                "TextTransformations": [{"Priority": 0, "Type": "LOWERCASE"}],
                                "PositionalConstraint": "CONTAINS",
                            }
                        },
                        "Action": {"Block": {}},
                        "VisibilityConfig": {
                            "SampledRequestsEnabled": True,
                            "CloudWatchMetricsEnabled": True,
                            "MetricName": "ExistingIntegrationRule",
                        },
                    }
                ],
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "IntegrationTestWebACL",
                },
                "LockToken": "integration-test-lock-token",
            }
        }

    def test_end_to_end_rule_creation_workflow(self, mock_bot_rule, mock_web_acl_response):
        """Test complete rule creation workflow with mocked AWS services"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            # Setup mocks
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock WAF responses for complete workflow
            mock_waf_client.list_web_acls.return_value = {
                "WebACLs": [{"Name": "integration-test-webacl", "Id": "integration-test-webacl-id"}]
            }
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            # Initialize controller
            controller = WAFSyncController(
                bot_rules_table_name="integration-test-bot-rules", web_acl_name="integration-test-webacl"
            )
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            # Test rule creation
            result = controller.create_waf_rule(mock_bot_rule)

            # Verify success
            assert result.success is True
            assert result.rule_id == mock_bot_rule.rule_id
            assert result.waf_rule_id == mock_bot_rule.rule_name
            assert result.sync_timestamp is not None

            # Verify WAF API calls were made in correct order
            mock_waf_client.list_web_acls.assert_called_once_with(Scope="REGIONAL", Limit=100)
            mock_waf_client.get_web_acl.assert_called_once()
            mock_waf_client.update_web_acl.assert_called_once()

            # Verify DynamoDB update was called
            mock_table.update_item.assert_called_once()

            # Verify the WAF rule was added to existing rules
            update_call_args = mock_waf_client.update_web_acl.call_args[1]
            assert len(update_call_args["Rules"]) == 2  # Original + new rule

            # Find the new rule in the update call
            new_rule = None
            for rule in update_call_args["Rules"]:
                if rule["Name"] == mock_bot_rule.rule_name:
                    new_rule = rule
                    break

            assert new_rule is not None
            assert new_rule["Priority"] == mock_bot_rule.priority
            assert new_rule["Action"] == {"Block": {}}

    def test_rule_update_integration_workflow(self, mock_bot_rule, mock_web_acl_response):
        """Test complete rule update workflow with mocked AWS services"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            # Setup mocks
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Set up bot rule as if it already exists in WAF
            mock_bot_rule.waf_rule_id = "IntegrationTestRule"

            # Modify mock WebACL to include the rule we're updating
            mock_web_acl_response["WebACL"]["Rules"].append(
                {
                    "Name": "IntegrationTestRule",
                    "Priority": 50,
                    "Statement": {
                        "ByteMatchStatement": {
                            "SearchString": "oldpattern",
                            "FieldToMatch": {"SingleHeader": {"Name": "user-agent"}},
                            "TextTransformations": [{"Priority": 0, "Type": "LOWERCASE"}],
                            "PositionalConstraint": "CONTAINS",
                        }
                    },
                    "Action": {"Block": {}},
                    "VisibilityConfig": {
                        "SampledRequestsEnabled": True,
                        "CloudWatchMetricsEnabled": True,
                        "MetricName": "IntegrationTestRule",
                    },
                }
            )

            # Mock WAF responses
            mock_waf_client.list_web_acls.return_value = {
                "WebACLs": [{"Name": "integration-test-webacl", "Id": "integration-test-webacl-id"}]
            }
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            # Initialize controller
            controller = WAFSyncController(
                bot_rules_table_name="integration-test-bot-rules", web_acl_name="integration-test-webacl"
            )
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            # Test rule update
            result = controller.update_waf_rule(mock_bot_rule)

            # Verify success
            assert result.success is True
            assert result.rule_id == mock_bot_rule.rule_id

            # Verify WAF update was called
            mock_waf_client.update_web_acl.assert_called_once()

            # Verify the rule was updated (not added)
            update_call_args = mock_waf_client.update_web_acl.call_args[1]
            assert len(update_call_args["Rules"]) == 2  # Original + updated rule (not 3)

            # Find the updated rule
            updated_rule = None
            for rule in update_call_args["Rules"]:
                if rule["Name"] == mock_bot_rule.rule_name:
                    updated_rule = rule
                    break

            assert updated_rule is not None
            assert updated_rule["Priority"] == mock_bot_rule.priority

            # Verify it has OR statement for multiple patterns
            assert "OrStatement" in updated_rule["Statement"]
            assert len(updated_rule["Statement"]["OrStatement"]["Statements"]) == 2

    def test_rule_deletion_integration_workflow(self, mock_bot_rule, mock_web_acl_response):
        """Test complete rule deletion workflow with mocked AWS services"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            # Setup mocks
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Set up bot rule as if it exists in WAF
            mock_bot_rule.waf_rule_id = "IntegrationTestRule"

            # Add the rule to mock WebACL
            mock_web_acl_response["WebACL"]["Rules"].append(
                {
                    "Name": "IntegrationTestRule",
                    "Priority": 50,
                    "Statement": {},
                    "Action": {"Block": {}},
                    "VisibilityConfig": {},
                }
            )

            # Mock WAF responses
            mock_waf_client.list_web_acls.return_value = {
                "WebACLs": [{"Name": "integration-test-webacl", "Id": "integration-test-webacl-id"}]
            }
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            # Initialize controller
            controller = WAFSyncController(
                bot_rules_table_name="integration-test-bot-rules", web_acl_name="integration-test-webacl"
            )
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            # Test rule deletion
            result = controller.delete_waf_rule(mock_bot_rule)

            # Verify success
            assert result.success is True
            assert result.rule_id == mock_bot_rule.rule_id

            # Verify WAF update was called (to remove the rule)
            mock_waf_client.update_web_acl.assert_called_once()

            # Verify the rule was removed
            update_call_args = mock_waf_client.update_web_acl.call_args[1]
            assert len(update_call_args["Rules"]) == 1  # Only original rule remains

            # Verify the remaining rule is the original one
            remaining_rule = update_call_args["Rules"][0]
            assert remaining_rule["Name"] == "ExistingIntegrationRule"

    def test_waf_state_synchronization_workflow(self, mock_web_acl_response):
        """Test WAF state synchronization workflow with mocked AWS services"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            # Setup mocks
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock DynamoDB scan response with multiple rules
            mock_table.scan.return_value = {
                "Items": [
                    {
                        "rule_id": "active-rule-1",
                        "rule_name": "ActiveRule1",
                        "description": "Active test rule 1",
                        "action": "block",
                        "priority": 50,
                        "conditions": {"user_agent_patterns": ["testbot1"]},
                        "status": "active",
                        "created_at": "2024-01-15T10:00:00Z",
                        "updated_at": "2024-01-15T10:00:00Z",
                        "sync_status": "pending",
                    },
                    {
                        "rule_id": "active-rule-2",
                        "rule_name": "ActiveRule2",
                        "description": "Active test rule 2",
                        "action": "block",
                        "priority": 60,
                        "conditions": {"user_agent_patterns": ["testbot2"]},
                        "status": "active",
                        "created_at": "2024-01-15T10:00:00Z",
                        "updated_at": "2024-01-15T10:00:00Z",
                        "sync_status": "pending",
                    },
                    {
                        "rule_id": "inactive-rule",
                        "rule_name": "InactiveRule",
                        "description": "Inactive test rule",
                        "action": "block",
                        "priority": 70,
                        "conditions": {"user_agent_patterns": ["oldbot"]},
                        "status": "inactive",
                        "created_at": "2024-01-15T10:00:00Z",
                        "updated_at": "2024-01-15T10:00:00Z",
                        "sync_status": "synced",
                    },
                ]
            }

            # Add an orphan rule to WAF (not in DynamoDB)
            mock_web_acl_response["WebACL"]["Rules"].append(
                {
                    "Name": "OrphanWAFRule",
                    "Priority": 99,
                    "Statement": {},
                    "Action": {"Block": {}},
                    "VisibilityConfig": {},
                }
            )

            # Mock WAF responses
            mock_waf_client.list_web_acls.return_value = {
                "WebACLs": [{"Name": "integration-test-webacl", "Id": "integration-test-webacl-id"}]
            }
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            # Initialize controller
            controller = WAFSyncController(
                bot_rules_table_name="integration-test-bot-rules", web_acl_name="integration-test-webacl"
            )
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            # Test synchronization
            result = controller.sync_waf_state()

            # Verify sync results structure
            assert "synced_rules" in result
            assert "missing_in_waf" in result
            assert "missing_in_dynamodb" in result
            assert "sync_errors" in result

            # Should have attempted to sync active rules
            assert len(result["synced_rules"]) >= 2

            # Should have detected the orphan rule
            assert "OrphanWAFRule" in result["missing_in_dynamodb"]

            # Verify WAF update calls were made for active rules
            assert mock_waf_client.update_web_acl.call_count >= 2

    def test_waf_metrics_integration_workflow(self):
        """Test WAF metrics retrieval integration workflow"""
        with patch("boto3.client") as mock_boto_client:
            mock_waf_client = Mock()
            mock_cloudwatch_client = Mock()

            def client_side_effect(service_name, region_name=None):
                if service_name == "wafv2":
                    return mock_waf_client
                elif service_name == "cloudwatch":
                    return mock_cloudwatch_client
                return Mock()

            mock_boto_client.side_effect = client_side_effect

            # Mock WAF WebACL info
            mock_waf_client.list_web_acls.return_value = {
                "WebACLs": [{"Name": "integration-test-webacl", "Id": "integration-test-webacl-id"}]
            }

            # Mock CloudWatch metrics
            mock_cloudwatch_client.get_metric_statistics.return_value = {
                "Datapoints": [
                    {"Sum": 100, "Timestamp": datetime.now(timezone.utc)},
                    {"Sum": 150, "Timestamp": datetime.now(timezone.utc)},
                ]
            }

            # Initialize controller
            controller = WAFSyncController(web_acl_name="integration-test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.cloudwatch = mock_cloudwatch_client

            # Test metrics retrieval
            metrics = controller.get_waf_metrics()

            # Verify metrics structure
            assert "blocked_requests" in metrics
            assert "allowed_requests" in metrics
            assert metrics["blocked_requests"]["total"] == 250
            assert metrics["allowed_requests"]["total"] == 250

            # Test rule-specific metrics
            rule_metrics = controller.get_waf_metrics(rule_name="TestRule")

            assert "blocked_requests" in rule_metrics
            assert "allowed_requests" in rule_metrics
            assert "rule_TestRule_blocked" in rule_metrics

            # Verify CloudWatch API calls
            assert mock_cloudwatch_client.get_metric_statistics.call_count >= 3

    def test_error_handling_integration_workflow(self, mock_bot_rule):
        """Test error handling integration workflow"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            # Setup mocks
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock WebACL not found scenario
            mock_waf_client.list_web_acls.return_value = {"WebACLs": []}

            # Initialize controller
            controller = WAFSyncController(
                bot_rules_table_name="integration-test-bot-rules", web_acl_name="nonexistent-webacl"
            )
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            # Test rule creation with missing WebACL
            result = controller.create_waf_rule(mock_bot_rule)

            # Should fail gracefully
            assert result.success is False
            assert "WebACL not found" in result.error_message

            # In this case, the error occurs before DynamoDB update since WebACL is not found
            # The error is returned immediately without attempting to update DynamoDB
            # This is the expected behavior for this type of error


if __name__ == "__main__":
    pytest.main([__file__])
