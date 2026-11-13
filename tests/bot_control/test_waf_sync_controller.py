"""
Unit tests for WAF Sync Controller

Tests the WAF synchronization service functionality including rule creation,
updates, deletion, and error handling.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone
from decimal import Decimal
import boto3
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


class TestWAFSyncController:
    """Test cases for WAF Sync Controller"""

    @pytest.fixture
    def mock_bot_rule(self):
        """Create a mock bot rule for testing"""
        return BotRule(
            rule_id="test-rule-123",
            rule_name="TestBotRule",
            description="Test bot rule for unit testing",
            action="block",
            priority=100,
            conditions={"user_agent_patterns": ["testbot", "scrapy"]},
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
                "Name": "test-webacl",
                "Id": "test-webacl-id",
                "ARN": "arn:aws:wafv2:us-west-2:123456789012:regional/webacl/test-webacl/test-webacl-id",
                "DefaultAction": {"Allow": {}},
                "Rules": [
                    {
                        "Name": "ExistingRule",
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
                            "MetricName": "ExistingRule",
                        },
                    }
                ],
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "TestWebACL",
                },
                "LockToken": "test-lock-token",
            }
        }

    @mock_aws
    def test_init_controller(self):
        """Test WAF sync controller initialization"""
        with patch.dict(os.environ, {"BOT_RULES_TABLE_NAME": "test-bot-rules", "WAF_WEB_ACL_NAME": "test-webacl"}):
            controller = WAFSyncController()

            assert controller.bot_rules_table_name == "test-bot-rules"
            assert controller.web_acl_name == "test-webacl"
            assert controller.web_acl_scope == "REGIONAL"
            assert controller.retry_config["max_attempts"] == 3

    @mock_aws
    def test_get_web_acl_info_success(self):
        """Test successful WebACL info retrieval"""
        # Create mock WAF client
        with patch("boto3.client") as mock_boto_client:
            mock_waf_client = Mock()
            mock_boto_client.return_value = mock_waf_client

            # Mock list_web_acls response
            mock_waf_client.list_web_acls.return_value = {
                "WebACLs": [
                    {
                        "Name": "test-webacl",
                        "Id": "test-webacl-id",
                        "ARN": "arn:aws:wafv2:us-west-2:123456789012:regional/webacl/test-webacl/test-webacl-id",
                    }
                ]
            }

            controller = WAFSyncController(web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client

            result = controller._get_web_acl_info()

            assert result is not None
            assert result["Name"] == "test-webacl"
            assert result["Id"] == "test-webacl-id"
            mock_waf_client.list_web_acls.assert_called_once_with(Scope="REGIONAL", Limit=100)

    @mock_aws
    def test_get_web_acl_info_not_found(self):
        """Test WebACL info retrieval when WebACL not found"""
        with patch("boto3.client") as mock_boto_client:
            mock_waf_client = Mock()
            mock_boto_client.return_value = mock_waf_client

            # Mock empty list_web_acls response
            mock_waf_client.list_web_acls.return_value = {"WebACLs": []}

            controller = WAFSyncController(web_acl_name="nonexistent-webacl")
            controller.wafv2_client = mock_waf_client

            result = controller._get_web_acl_info()

            assert result is None

    @mock_aws
    def test_create_waf_rule_success(self, mock_bot_rule, mock_web_acl_response):
        """Test successful WAF rule creation"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            # Setup mocks
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock WAF responses
            mock_waf_client.list_web_acls.return_value = {"WebACLs": [{"Name": "test-webacl", "Id": "test-webacl-id"}]}
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            controller = WAFSyncController(bot_rules_table_name="test-bot-rules", web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            # Test rule creation
            result = controller.create_waf_rule(mock_bot_rule)

            assert result.success is True
            assert result.rule_id == mock_bot_rule.rule_id
            assert result.waf_rule_id == mock_bot_rule.rule_name
            assert result.sync_timestamp is not None

            # Verify WAF client calls
            mock_waf_client.get_web_acl.assert_called_once()
            mock_waf_client.update_web_acl.assert_called_once()

            # Verify DynamoDB update
            mock_table.update_item.assert_called_once()

    @mock_aws
    def test_create_waf_rule_webacl_not_found(self, mock_bot_rule):
        """Test WAF rule creation when WebACL not found"""
        with patch("boto3.client") as mock_boto_client:
            mock_waf_client = Mock()
            mock_boto_client.return_value = mock_waf_client

            # Mock empty WebACL list
            mock_waf_client.list_web_acls.return_value = {"WebACLs": []}

            controller = WAFSyncController(web_acl_name="nonexistent-webacl")
            controller.wafv2_client = mock_waf_client

            result = controller.create_waf_rule(mock_bot_rule)

            assert result.success is False
            assert result.error_message == "WebACL not found"

    @mock_aws
    def test_create_waf_rule_api_error(self, mock_bot_rule, mock_web_acl_response):
        """Test WAF rule creation with API error"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock WAF responses with error
            mock_waf_client.list_web_acls.return_value = {"WebACLs": [{"Name": "test-webacl", "Id": "test-webacl-id"}]}
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.side_effect = ClientError(
                {"Error": {"Code": "WAFInvalidParameterException", "Message": "Invalid rule"}}, "UpdateWebACL"
            )

            controller = WAFSyncController(bot_rules_table_name="test-bot-rules", web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            result = controller.create_waf_rule(mock_bot_rule)

            assert result.success is False
            assert "Failed to create WAF rule" in result.error_message

            # Verify error status update in DynamoDB
            mock_table.update_item.assert_called_once()

    @mock_aws
    def test_update_waf_rule_success(self, mock_bot_rule, mock_web_acl_response):
        """Test successful WAF rule update"""
        # Set up bot rule as if it already exists in WAF
        mock_bot_rule.waf_rule_id = "TestBotRule"

        # Modify mock WebACL to include the rule we're updating
        mock_web_acl_response["WebACL"]["Rules"].append(
            {
                "Name": "TestBotRule",
                "Priority": 100,
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
                    "MetricName": "TestBotRule",
                },
            }
        )

        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock WAF responses
            mock_waf_client.list_web_acls.return_value = {"WebACLs": [{"Name": "test-webacl", "Id": "test-webacl-id"}]}
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            controller = WAFSyncController(bot_rules_table_name="test-bot-rules", web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            result = controller.update_waf_rule(mock_bot_rule)

            assert result.success is True
            assert result.rule_id == mock_bot_rule.rule_id

            # Verify WAF update was called
            mock_waf_client.update_web_acl.assert_called_once()

    @mock_aws
    def test_delete_waf_rule_success(self, mock_bot_rule, mock_web_acl_response):
        """Test successful WAF rule deletion"""
        # Set up bot rule as if it exists in WAF
        mock_bot_rule.waf_rule_id = "TestBotRule"

        # Add the rule to mock WebACL
        mock_web_acl_response["WebACL"]["Rules"].append(
            {"Name": "TestBotRule", "Priority": 100, "Statement": {}, "Action": {"Block": {}}, "VisibilityConfig": {}}
        )

        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock WAF responses
            mock_waf_client.list_web_acls.return_value = {"WebACLs": [{"Name": "test-webacl", "Id": "test-webacl-id"}]}
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            controller = WAFSyncController(bot_rules_table_name="test-bot-rules", web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            result = controller.delete_waf_rule(mock_bot_rule)

            assert result.success is True
            assert result.rule_id == mock_bot_rule.rule_id

            # Verify WAF update was called (to remove the rule)
            mock_waf_client.update_web_acl.assert_called_once()

    @mock_aws
    def test_delete_waf_rule_not_found(self, mock_bot_rule, mock_web_acl_response):
        """Test WAF rule deletion when rule doesn't exist"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock WAF responses (rule not in WebACL)
            mock_waf_client.list_web_acls.return_value = {"WebACLs": [{"Name": "test-webacl", "Id": "test-webacl-id"}]}
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response

            controller = WAFSyncController(bot_rules_table_name="test-bot-rules", web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            result = controller.delete_waf_rule(mock_bot_rule)

            # Should still succeed since rule doesn't exist
            assert result.success is True
            assert result.rule_id == mock_bot_rule.rule_id

            # WAF update should not be called since rule wasn't found
            mock_waf_client.update_web_acl.assert_not_called()

    @mock_aws
    def test_get_waf_metrics_success(self):
        """Test successful WAF metrics retrieval"""
        with patch("boto3.client") as mock_boto_client:
            mock_waf_client = Mock()
            mock_cloudwatch_client = Mock()

            def client_side_effect(service_name):
                if service_name == "wafv2":
                    return mock_waf_client
                elif service_name == "cloudwatch":
                    return mock_cloudwatch_client
                return Mock()

            mock_boto_client.side_effect = client_side_effect

            # Mock WAF WebACL info
            mock_waf_client.list_web_acls.return_value = {"WebACLs": [{"Name": "test-webacl", "Id": "test-webacl-id"}]}

            # Mock CloudWatch metrics
            mock_cloudwatch_client.get_metric_statistics.return_value = {
                "Datapoints": [
                    {"Sum": 100, "Timestamp": datetime.now(timezone.utc)},
                    {"Sum": 150, "Timestamp": datetime.now(timezone.utc)},
                ]
            }

            controller = WAFSyncController(web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.cloudwatch = mock_cloudwatch_client

            metrics = controller.get_waf_metrics()

            assert "blocked_requests" in metrics
            assert "allowed_requests" in metrics
            assert metrics["blocked_requests"]["total"] == 250
            assert metrics["allowed_requests"]["total"] == 250

    @mock_aws
    def test_sync_waf_state_success(self, mock_web_acl_response):
        """Test successful WAF state synchronization"""
        with patch("boto3.client") as mock_boto_client, patch("boto3.resource") as mock_boto_resource:
            mock_waf_client = Mock()
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_client.return_value = mock_waf_client
            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            # Mock DynamoDB scan response
            mock_table.scan.return_value = {
                "Items": [
                    {
                        "rule_id": "rule-1",
                        "rule_name": "ActiveRule",
                        "description": "Active test rule",
                        "action": "block",
                        "priority": 50,
                        "conditions": {"user_agent_patterns": ["testbot"]},
                        "status": "active",
                        "created_at": "2024-01-15T10:00:00Z",
                        "updated_at": "2024-01-15T10:00:00Z",
                        "sync_status": "pending",
                    },
                    {
                        "rule_id": "rule-2",
                        "rule_name": "InactiveRule",
                        "description": "Inactive test rule",
                        "action": "block",
                        "priority": 60,
                        "conditions": {"user_agent_patterns": ["oldbot"]},
                        "status": "inactive",
                        "created_at": "2024-01-15T10:00:00Z",
                        "updated_at": "2024-01-15T10:00:00Z",
                        "sync_status": "synced",
                    },
                ]
            }

            # Mock WAF responses
            mock_waf_client.list_web_acls.return_value = {"WebACLs": [{"Name": "test-webacl", "Id": "test-webacl-id"}]}
            mock_waf_client.get_web_acl.return_value = mock_web_acl_response
            mock_waf_client.update_web_acl.return_value = {}

            controller = WAFSyncController(bot_rules_table_name="test-bot-rules", web_acl_name="test-webacl")
            controller.wafv2_client = mock_waf_client
            controller.bot_rules_table = mock_table

            result = controller.sync_waf_state()

            assert "synced_rules" in result
            assert "missing_in_waf" in result
            assert "missing_in_dynamodb" in result
            assert "sync_errors" in result

            # Should have attempted to create the active rule
            assert len(result["synced_rules"]) >= 1

    def test_convert_bot_rule_to_waf_rule_single_pattern(self, mock_bot_rule):
        """Test conversion of bot rule to WAF rule format with single pattern"""
        controller = WAFSyncController()

        # Test with single user agent pattern
        mock_bot_rule.conditions = {"user_agent_patterns": ["testbot"]}

        waf_rule = controller._convert_bot_rule_to_waf_rule(mock_bot_rule)

        assert waf_rule["Name"] == mock_bot_rule.rule_name
        assert waf_rule["Priority"] == mock_bot_rule.priority
        assert "ByteMatchStatement" in waf_rule["Statement"]
        assert waf_rule["Statement"]["ByteMatchStatement"]["SearchString"] == "testbot"
        assert waf_rule["Action"] == {"Block": {}}

    def test_convert_bot_rule_to_waf_rule_multiple_patterns(self, mock_bot_rule):
        """Test conversion of bot rule to WAF rule format with multiple patterns"""
        controller = WAFSyncController()

        # Test with multiple user agent patterns
        mock_bot_rule.conditions = {"user_agent_patterns": ["testbot", "scrapy", "curl"]}

        waf_rule = controller._convert_bot_rule_to_waf_rule(mock_bot_rule)

        assert waf_rule["Name"] == mock_bot_rule.rule_name
        assert "OrStatement" in waf_rule["Statement"]
        assert len(waf_rule["Statement"]["OrStatement"]["Statements"]) == 3

    def test_convert_bot_rule_to_waf_rule_allow_action(self, mock_bot_rule):
        """Test conversion with allow action"""
        controller = WAFSyncController()

        mock_bot_rule.action = "allow"

        waf_rule = controller._convert_bot_rule_to_waf_rule(mock_bot_rule)

        assert waf_rule["Action"] == {"Allow": {}}

    def test_convert_bot_rule_to_waf_rule_count_action(self, mock_bot_rule):
        """Test conversion with count action"""
        controller = WAFSyncController()

        mock_bot_rule.action = "count"

        waf_rule = controller._convert_bot_rule_to_waf_rule(mock_bot_rule)

        assert waf_rule["Action"] == {"Count": {}}

    @mock_aws
    def test_update_bot_rule_sync_status(self):
        """Test updating bot rule sync status in DynamoDB"""
        with patch("boto3.resource") as mock_boto_resource:
            mock_dynamodb_resource = Mock()
            mock_table = Mock()

            mock_boto_resource.return_value = mock_dynamodb_resource
            mock_dynamodb_resource.Table.return_value = mock_table

            controller = WAFSyncController(bot_rules_table_name="test-bot-rules")
            controller.bot_rules_table = mock_table

            controller._update_bot_rule_sync_status(
                rule_id="test-rule-123",
                waf_rule_id="TestWAFRule",
                sync_status="synced",
                last_synced="2024-01-15T10:00:00Z",
            )

            # Verify DynamoDB update was called
            mock_table.update_item.assert_called_once()
            call_args = mock_table.update_item.call_args

            assert call_args[1]["Key"] == {"rule_id": "test-rule-123"}
            assert ":waf_rule_id" in call_args[1]["ExpressionAttributeValues"]
            assert ":sync_status" in call_args[1]["ExpressionAttributeValues"]
            assert ":last_synced" in call_args[1]["ExpressionAttributeValues"]

    def test_retry_operation_success(self):
        """Test retry operation with successful execution"""
        controller = WAFSyncController()

        mock_func = Mock(return_value="success")

        result = controller._retry_operation(mock_func, "arg1", kwarg1="value1")

        assert result == "success"
        mock_func.assert_called_once_with("arg1", kwarg1="value1")

    def test_retry_operation_with_retries(self):
        """Test retry operation with failures then success"""
        controller = WAFSyncController()

        mock_func = Mock()
        mock_func.side_effect = [
            ClientError({"Error": {"Code": "Throttling"}}, "TestOperation"),
            ClientError({"Error": {"Code": "Throttling"}}, "TestOperation"),
            "success",
        ]

        with patch("time.sleep"):  # Mock sleep to speed up test
            result = controller._retry_operation(mock_func)

        assert result == "success"
        assert mock_func.call_count == 3

    def test_retry_operation_max_attempts_exceeded(self):
        """Test retry operation when max attempts exceeded"""
        controller = WAFSyncController()

        mock_func = Mock()
        mock_func.side_effect = ClientError({"Error": {"Code": "Throttling"}}, "TestOperation")

        with patch("time.sleep"):  # Mock sleep to speed up test
            with pytest.raises(ClientError):
                controller._retry_operation(mock_func)

        assert mock_func.call_count == 3  # max_attempts


if __name__ == "__main__":
    pytest.main([__file__])
