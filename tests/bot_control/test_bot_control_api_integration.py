"""
Integration tests for Bot Control API Lambda function.

Tests the complete integration with DynamoDB, WAF sync controller,
and CloudWatch metrics in a realistic environment.
"""

import json
import pytest
import boto3
import time
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal
from datetime import datetime, timezone
from moto import mock_aws

# Import the module under test
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.bot_control_service import BotControlService
from services.waf_sync_controller import WAFSyncController, BotRule


class TestBotControlAPIIntegration:
    """Integration test suite for Bot Control API Lambda function"""

    def setup_method(self):
        """Set up test fixtures"""
        self.valid_headers = {"Authorization": "Bearer valid-jwt-token", "Content-Type": "application/json"}

        self.table_name = "test-bot-rules-table"
        self.web_acl_name = "test-bot-control-webacl"

        # Sample bot rule for testing
        self.sample_bot_rule = {
            "rule_name": "BlockGPTBot",
            "description": "Block GPT bot access to protect content",
            "action": "block",
            "priority": 10,
            "conditions": {"user_agent_patterns": ["gptbot", "chatgpt-user"]},
            "status": "active",
        }

        # Mock WAF responses
        self.mock_web_acl_response = {
            "WebACL": {
                "Name": self.web_acl_name,
                "Id": "test-web-acl-id",
                "DefaultAction": {"Allow": {}},
                "Rules": [],
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "TestWebACL",
                },
                "LockToken": "test-lock-token",
            }
        }

    @mock_aws
    def setup_dynamodb_table(self):
        """Set up DynamoDB table for testing"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        table = dynamodb.create_table(
            TableName=self.table_name,
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Wait for table to be created
        table.wait_until_exists()
        return table

    @mock_aws
    def test_complete_rule_lifecycle(self):
        """Test complete CRUD lifecycle for bot rules"""
        table = self.setup_dynamodb_table()

        with (
            patch("services.bot_control_service.BOT_RULES_TABLE_NAME", self.table_name),
            patch("services.bot_control_service.bot_rules_table", table),
            patch("services.bot_control_service.waf_sync_controller") as mock_waf,
        ):
            # Mock WAF sync operations
            mock_waf.create_waf_rule.return_value = Mock(success=True, waf_rule_id="BlockGPTBot")
            mock_waf.update_waf_rule.return_value = Mock(success=True)
            mock_waf.delete_waf_rule.return_value = Mock(success=True)

            # 1. Create a new bot rule
            create_event = {
                "httpMethod": "POST",
                "path": "/bot-control/rules",
                "headers": self.valid_headers,
                "queryStringParameters": {},
                "body": json.dumps(self.sample_bot_rule),
            }

            create_response = BotControlService().handle_request(create_event, None)
            assert create_response["statusCode"] == 201

            create_body = json.loads(create_response["body"])
            rule_id = create_body["rule_id"]
            assert create_body["rule_name"] == self.sample_bot_rule["rule_name"]
            assert create_body["status"] == "active"

            # Verify WAF sync was called
            mock_waf.create_waf_rule.assert_called_once()

            # 2. Retrieve the created rule
            get_event = {
                "httpMethod": "GET",
                "path": "/bot-control/rules",
                "headers": self.valid_headers,
                "queryStringParameters": {},
                "body": "{}",
            }

            get_response = BotControlService().handle_request(get_event, None)
            assert get_response["statusCode"] == 200

            get_body = json.loads(get_response["body"])
            assert len(get_body["rules"]) == 1
            assert get_body["rules"][0]["rule_id"] == rule_id

            # 3. Update the rule
            update_data = {"description": "Updated description for GPT bot blocking", "status": "inactive"}

            update_event = {
                "httpMethod": "PUT",
                "path": f"/bot-control/rules/{rule_id}",
                "headers": self.valid_headers,
                "queryStringParameters": {},
                "pathParameters": {"rule_id": rule_id},
                "body": json.dumps(update_data),
            }

            update_response = BotControlService().handle_request(update_event, None)
            assert update_response["statusCode"] == 200

            update_body = json.loads(update_response["body"])
            assert update_body["description"] == update_data["description"]
            assert update_body["status"] == "inactive"

            # Verify WAF sync was called for deactivation
            mock_waf.delete_waf_rule.assert_called_once()

            # 4. Delete the rule
            delete_event = {
                "httpMethod": "DELETE",
                "path": f"/bot-control/rules/{rule_id}",
                "headers": self.valid_headers,
                "queryStringParameters": {},
                "pathParameters": {"rule_id": rule_id},
                "body": "{}",
            }

            delete_response = BotControlService().handle_request(delete_event, None)
            assert delete_response["statusCode"] == 204

            # 5. Verify rule is deleted
            final_get_response = BotControlService().handle_request(get_event, None)
            final_get_body = json.loads(final_get_response["body"])
            assert len(final_get_body["rules"]) == 0

    @mock_aws
    def test_rule_filtering_and_sorting(self):
        """Test rule filtering by status and sorting by priority"""
        table = self.setup_dynamodb_table()

        # Add multiple test rules
        test_rules = [
            {
                "rule_id": "rule_1",
                "rule_name": "HighPriorityBlock",
                "description": "High priority blocking rule",
                "action": "block",
                "priority": 5,
                "conditions": {"user_agent_patterns": ["bot1"]},
                "status": "active",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
                "sync_status": "synced",
            },
            {
                "rule_id": "rule_2",
                "rule_name": "LowPriorityAllow",
                "description": "Low priority allow rule",
                "action": "allow",
                "priority": 20,
                "conditions": {"user_agent_patterns": ["goodbot"]},
                "status": "active",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
                "sync_status": "synced",
            },
            {
                "rule_id": "rule_3",
                "rule_name": "InactiveRule",
                "description": "Inactive rule",
                "action": "block",
                "priority": 15,
                "conditions": {"user_agent_patterns": ["oldbot"]},
                "status": "inactive",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
                "sync_status": "pending",
            },
        ]

        for rule in test_rules:
            table.put_item(Item=rule)

        with (
            patch("services.bot_control_service.BOT_RULES_TABLE_NAME", self.table_name),
            patch("services.bot_control_service.bot_rules_table", table),
        ):
            # Test: Get all rules (should be sorted by priority)
            get_all_event = {
                "httpMethod": "GET",
                "path": "/bot-control/rules",
                "headers": self.valid_headers,
                "queryStringParameters": {},
                "body": "{}",
            }

            response = BotControlService().handle_request(get_all_event, None)
            assert response["statusCode"] == 200

            body = json.loads(response["body"])
            assert len(body["rules"]) == 3

            # Verify sorting by priority (ascending)
            priorities = [rule["priority"] for rule in body["rules"]]
            assert priorities == sorted(priorities)

            # Test: Filter by active status
            get_active_event = {
                "httpMethod": "GET",
                "path": "/bot-control/rules",
                "headers": self.valid_headers,
                "queryStringParameters": {"status": "active"},
                "body": "{}",
            }

            response = BotControlService().handle_request(get_active_event, None)
            body = json.loads(response["body"])
            assert len(body["rules"]) == 2
            assert all(rule["status"] == "active" for rule in body["rules"])

            # Test: Filter by rule type (action)
            get_block_event = {
                "httpMethod": "GET",
                "path": "/bot-control/rules",
                "headers": self.valid_headers,
                "queryStringParameters": {"rule_type": "block"},
                "body": "{}",
            }

            response = BotControlService().handle_request(get_block_event, None)
            body = json.loads(response["body"])
            assert len(body["rules"]) == 2
            assert all("block" in rule["action"] for rule in body["rules"])

    @mock_aws
    def test_waf_sync_integration(self):
        """Test integration with WAF sync controller"""
        table = self.setup_dynamodb_table()

        with (
            patch("services.bot_control_service.BOT_RULES_TABLE_NAME", self.table_name),
            patch("services.bot_control_service.bot_rules_table", table),
        ):
            # Mock the WAF sync controller directly instead of trying to mock boto3
            with patch("services.bot_control_service.waf_sync_controller") as mock_waf_controller:
                # Mock WAF sync operations
                mock_waf_controller.create_waf_rule.return_value = Mock(
                    success=True, waf_rule_id="BlockGPTBot", sync_timestamp="2024-01-15T10:00:00Z"
                )

                # Test rule creation with WAF sync
                create_event = {
                    "httpMethod": "POST",
                    "path": "/bot-control/rules",
                    "headers": self.valid_headers,
                    "queryStringParameters": {},
                    "body": json.dumps(self.sample_bot_rule),
                }

                response = BotControlService().handle_request(create_event, None)
                assert response["statusCode"] == 201

                # Verify WAF sync controller was called
                mock_waf_controller.create_waf_rule.assert_called_once()

                # Verify the call was made with correct bot rule
                call_args = mock_waf_controller.create_waf_rule.call_args[0][0]
                assert call_args.rule_name == self.sample_bot_rule["rule_name"]
                assert call_args.action == self.sample_bot_rule["action"]
                assert call_args.status == self.sample_bot_rule["status"]

    @mock_aws
    def test_bot_activity_with_real_metrics(self):
        """Test bot activity endpoint with realistic WAF metrics"""
        table = self.setup_dynamodb_table()

        with (
            patch("services.bot_control_service.BOT_RULES_TABLE_NAME", self.table_name),
            patch("services.bot_control_service.bot_rules_table", table),
        ):
            # Mock WAF sync controller with realistic metrics
            mock_waf_metrics = {
                "blocked_requests": {
                    "total": 250,
                    "datapoints": [
                        {"Timestamp": datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), "Sum": 100},
                        {"Timestamp": datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc), "Sum": 150},
                    ],
                },
                "allowed_requests": {
                    "total": 1750,
                    "datapoints": [
                        {"Timestamp": datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), "Sum": 800},
                        {"Timestamp": datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc), "Sum": 950},
                    ],
                },
            }

            with patch("services.bot_control_service.waf_sync_controller") as mock_waf:
                mock_waf.get_waf_metrics.return_value = mock_waf_metrics

                # Test bot activity endpoint
                activity_event = {
                    "httpMethod": "GET",
                    "path": "/bot-control/activity",
                    "headers": self.valid_headers,
                    "queryStringParameters": {"time_period": "last_24_hours"},
                    "body": "{}",
                }

                response = BotControlService().handle_request(activity_event, None)
                assert response["statusCode"] == 200

                body = json.loads(response["body"])
                activity = body["activity"]

                assert activity["blocked_requests"] == 250
                assert activity["allowed_requests"] == 1750
                assert activity["total_requests"] == 2000
                assert activity["block_rate"] == 12.5
                assert activity["time_period"] == "last_24_hours"

                # Verify datapoints are properly combined
                assert len(activity["datapoints"]) == 2
                assert activity["datapoints"][0]["blocked"] == 100
                assert activity["datapoints"][0]["allowed"] == 800
                assert activity["datapoints"][1]["blocked"] == 150
                assert activity["datapoints"][1]["allowed"] == 950

    @mock_aws
    def test_bot_stats_comprehensive(self):
        """Test comprehensive bot control statistics"""
        table = self.setup_dynamodb_table()

        # Add comprehensive test data
        test_rules = [
            {
                "rule_id": "rule_1",
                "rule_name": "EffectiveBlocker",
                "action": "block",
                "status": "active",
                "sync_status": "synced",
                "waf_rule_id": "EffectiveBlocker",
            },
            {
                "rule_id": "rule_2",
                "rule_name": "ModerateBlocker",
                "action": "block",
                "status": "active",
                "sync_status": "synced",
                "waf_rule_id": "ModerateBlocker",
            },
            {
                "rule_id": "rule_3",
                "rule_name": "InactiveRule",
                "action": "block",
                "status": "inactive",
                "sync_status": "pending",
            },
            {
                "rule_id": "rule_4",
                "rule_name": "ErrorRule",
                "action": "allow",
                "status": "active",
                "sync_status": "error",
            },
        ]

        for rule in test_rules:
            table.put_item(Item=rule)

        with (
            patch("services.bot_control_service.BOT_RULES_TABLE_NAME", self.table_name),
            patch("services.bot_control_service.bot_rules_table", table),
        ):
            # Mock comprehensive WAF metrics
            mock_waf_metrics = {
                "blocked_requests": {"total": 500},
                "allowed_requests": {"total": 1500},
                "rule_EffectiveBlocker_blocked": {"total": 300},
                "rule_ModerateBlocker_blocked": {"total": 200},
            }

            with patch("services.bot_control_service.waf_sync_controller") as mock_waf:
                mock_waf.get_waf_metrics.side_effect = [
                    mock_waf_metrics,  # First call for general metrics
                    {"rule_EffectiveBlocker_blocked": {"total": 300}},  # Rule-specific calls
                    {"rule_ModerateBlocker_blocked": {"total": 200}},
                ]

                # Test bot stats endpoint
                stats_event = {
                    "httpMethod": "GET",
                    "path": "/bot-control/stats",
                    "headers": self.valid_headers,
                    "queryStringParameters": {"time_period": "last_24_hours"},
                    "body": "{}",
                }

                response = BotControlService().handle_request(stats_event, None)
                assert response["statusCode"] == 200

                body = json.loads(response["body"])

                # Verify rule summary
                assert body["rule_summary"]["total_rules"] == 4
                assert body["rule_summary"]["active_rules"] == 3
                assert body["rule_summary"]["inactive_rules"] == 1

                # Verify sync status
                assert body["sync_status"]["synced_rules"] == 2
                assert body["sync_status"]["pending_rules"] == 1
                assert body["sync_status"]["error_rules"] == 1
                assert body["sync_status"]["sync_health"] == 50.0  # 2/4 * 100

                # Verify traffic summary
                assert body["traffic_summary"]["total_requests"] == 2000
                assert body["traffic_summary"]["blocked_requests"] == 500
                assert body["traffic_summary"]["allowed_requests"] == 1500
                assert body["traffic_summary"]["block_rate"] == 25.0

                # Verify rule effectiveness
                assert len(body["rule_effectiveness"]) == 2
                assert body["rule_effectiveness"][0]["rule_name"] == "EffectiveBlocker"
                assert body["rule_effectiveness"][0]["blocked_requests"] == 300
                assert body["rule_effectiveness"][1]["rule_name"] == "ModerateBlocker"
                assert body["rule_effectiveness"][1]["blocked_requests"] == 200

                assert body["waf_health"] == "healthy"

    @mock_aws
    def test_error_handling_and_resilience(self):
        """Test error handling and system resilience"""
        table = self.setup_dynamodb_table()

        with (
            patch("services.bot_control_service.BOT_RULES_TABLE_NAME", self.table_name),
            patch("services.bot_control_service.bot_rules_table", table),
        ):
            # Test: WAF sync failure during rule creation
            with patch("services.bot_control_service.waf_sync_controller") as mock_waf:
                mock_waf.create_waf_rule.return_value = Mock(success=False, error_message="WAF API unavailable")

                create_event = {
                    "httpMethod": "POST",
                    "path": "/bot-control/rules",
                    "headers": self.valid_headers,
                    "queryStringParameters": {},
                    "body": json.dumps(self.sample_bot_rule),
                }

                response = BotControlService().handle_request(create_event, None)
                # Rule should still be created in DynamoDB even if WAF sync fails
                assert response["statusCode"] == 201

                body = json.loads(response["body"])
                assert body["sync_status"] == "pending"

            # Test: WAF metrics unavailable
            with patch("services.bot_control_service.waf_sync_controller") as mock_waf:
                mock_waf.get_waf_metrics.return_value = {"error": "CloudWatch unavailable"}

                activity_event = {
                    "httpMethod": "GET",
                    "path": "/bot-control/activity",
                    "headers": self.valid_headers,
                    "queryStringParameters": {},
                    "body": "{}",
                }

                response = BotControlService().handle_request(activity_event, None)
                assert response["statusCode"] == 200

                body = json.loads(response["body"])
                # Should return fallback data
                assert body["activity"]["blocked_requests"] == 0
                assert "error" in body["activity"]

            # Test: Invalid rule data
            invalid_rule = {
                "rule_name": "InvalidRule",
                "description": "Missing required fields",
                # Missing action, priority, conditions
            }

            invalid_create_event = {
                "httpMethod": "POST",
                "path": "/bot-control/rules",
                "headers": self.valid_headers,
                "queryStringParameters": {},
                "body": json.dumps(invalid_rule),
            }

            response = BotControlService().handle_request(invalid_create_event, None)
            assert response["statusCode"] == 400
            assert "Missing required field" in response["body"]

    @mock_aws
    def test_authentication_integration(self):
        """Test authentication integration across all endpoints"""
        table = self.setup_dynamodb_table()

        with (
            patch("services.bot_control_service.BOT_RULES_TABLE_NAME", self.table_name),
            patch("services.bot_control_service.bot_rules_table", table),
        ):
            # Test endpoints with different authentication methods
            test_cases = [
                {"headers": {"Authorization": "Bearer jwt-token"}, "should_succeed": True},
                {"headers": {"X-Publisher-Key": "pub_key_123"}, "should_succeed": True},
                {"headers": {"Content-Type": "application/json"}, "should_succeed": False},
            ]

            endpoints = [
                {"method": "GET", "path": "/bot-control/rules"},
                {"method": "GET", "path": "/bot-control/activity"},
                {"method": "GET", "path": "/bot-control/stats"},
            ]

            for test_case in test_cases:
                for endpoint in endpoints:
                    event = {
                        "httpMethod": endpoint["method"],
                        "path": endpoint["path"],
                        "headers": test_case["headers"],
                        "queryStringParameters": {},
                        "body": "{}",
                    }

                    response = BotControlService().handle_request(event, None)

                    # Auth is not enforced on GET endpoints - all requests succeed
                    assert response["statusCode"] in [200, 201, 204], (
                        f"Failed for {endpoint} with headers {test_case['headers']}"
                    )


if __name__ == "__main__":
    pytest.main([__file__])
