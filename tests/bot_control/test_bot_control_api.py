"""
Unit tests for Bot Control API Lambda function.

Tests the CRUD operations for bot control rules and WAF metrics retrieval
without requiring actual AWS resources.
"""

import json
import pytest
import boto3
from unittest.mock import Mock, patch, MagicMock
from decimal import Decimal
from datetime import datetime, timezone
from moto import mock_aws

# Import the module under test
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.bot_control_service import (
    BotControlService,
    handle_get_bot_rules,
    handle_create_bot_rule,
    handle_update_bot_rule,
    handle_delete_bot_rule,
    handle_get_bot_activity,
    handle_get_bot_stats,
    validate_authentication,
    create_success_response,
    create_error_response,
)


class TestBotControlAPI:
    """Test suite for Bot Control API Lambda function"""

    def setup_method(self):
        """Set up test fixtures"""
        self.valid_headers = {"Authorization": "Bearer valid-jwt-token", "Content-Type": "application/json"}

        self.valid_publisher_headers = {"X-Publisher-Key": "pub_test_key_123", "Content-Type": "application/json"}

        self.sample_bot_rule = {
            "rule_name": "BlockGPTBot",
            "description": "Block GPT bot access",
            "action": "block",
            "priority": 10,
            "conditions": {"user_agent_patterns": ["gptbot", "chatgpt"]},
            "status": "active",
        }

        self.sample_rule_response = {
            "rule_id": "rule_1234567890",
            "rule_name": "BlockGPTBot",
            "description": "Block GPT bot access",
            "action": "block",
            "priority": 10,
            "conditions": {"user_agent_patterns": ["gptbot", "chatgpt"]},
            "status": "active",
            "created_at": "2024-01-15T10:00:00+00:00",
            "updated_at": "2024-01-15T10:00:00+00:00",
            "sync_status": "pending",
        }

    def test_lambda_handler_get_rules_success(self):
        """Test successful GET /bot-control/rules request"""
        event = {
            "httpMethod": "GET",
            "path": "/bot-control/rules",
            "headers": self.valid_headers,
            "queryStringParameters": {},
            "body": "{}",
        }

        with patch("services.bot_control_service.handle_get_bot_rules") as mock_handler:
            mock_handler.return_value = create_success_response({"rules": []})

            response = BotControlService().handle_request(event, None)

            assert response["statusCode"] == 200
            mock_handler.assert_called_once_with(self.valid_headers, {})

    def test_lambda_handler_create_rule_success(self):
        """Test successful POST /bot-control/rules request"""
        event = {
            "httpMethod": "POST",
            "path": "/bot-control/rules",
            "headers": self.valid_headers,
            "queryStringParameters": {},
            "body": json.dumps(self.sample_bot_rule),
        }

        with patch("services.bot_control_service.handle_create_bot_rule") as mock_handler:
            mock_handler.return_value = create_success_response(self.sample_rule_response, 201)

            response = BotControlService().handle_request(event, None)

            assert response["statusCode"] == 201
            mock_handler.assert_called_once_with(self.valid_headers, self.sample_bot_rule)

    def test_lambda_handler_update_rule_success(self):
        """Test successful PUT /bot-control/rules/{rule_id} request"""
        rule_id = "rule_1234567890"
        event = {
            "httpMethod": "PUT",
            "path": f"/bot-control/rules/{rule_id}",
            "headers": self.valid_headers,
            "queryStringParameters": {},
            "pathParameters": {"rule_id": rule_id},
            "body": json.dumps({"status": "inactive"}),
        }

        with patch("services.bot_control_service.handle_update_bot_rule") as mock_handler:
            mock_handler.return_value = create_success_response(self.sample_rule_response)

            response = BotControlService().handle_request(event, None)

            assert response["statusCode"] == 200
            mock_handler.assert_called_once_with(self.valid_headers, rule_id, {"status": "inactive"})

    def test_lambda_handler_delete_rule_success(self):
        """Test successful DELETE /bot-control/rules/{rule_id} request"""
        rule_id = "rule_1234567890"
        event = {
            "httpMethod": "DELETE",
            "path": f"/bot-control/rules/{rule_id}",
            "headers": self.valid_headers,
            "queryStringParameters": {},
            "pathParameters": {"rule_id": rule_id},
            "body": "{}",
        }

        with patch("services.bot_control_service.handle_delete_bot_rule") as mock_handler:
            mock_handler.return_value = create_success_response({}, 204)

            response = BotControlService().handle_request(event, None)

            assert response["statusCode"] == 204
            mock_handler.assert_called_once_with(self.valid_headers, rule_id)

    def test_lambda_handler_invalid_method(self):
        """Test invalid HTTP method"""
        event = {
            "httpMethod": "PATCH",
            "path": "/bot-control/rules",
            "headers": self.valid_headers,
            "queryStringParameters": {},
            "body": "{}",
        }

        response = BotControlService().handle_request(event, None)

        assert response["statusCode"] == 405
        assert "Method not allowed" in response["body"]

    def test_lambda_handler_invalid_path(self):
        """Test invalid path"""
        event = {
            "httpMethod": "GET",
            "path": "/bot-control/invalid",
            "headers": self.valid_headers,
            "queryStringParameters": {},
            "body": "{}",
        }

        response = BotControlService().handle_request(event, None)

        assert response["statusCode"] == 404
        assert "Endpoint not found" in response["body"]

    def test_lambda_handler_invalid_json(self):
        """Test invalid JSON in request body"""
        event = {
            "httpMethod": "POST",
            "path": "/bot-control/rules",
            "headers": self.valid_headers,
            "queryStringParameters": {},
            "body": "invalid json",
        }

        response = BotControlService().handle_request(event, None)

        assert response["statusCode"] == 400
        assert "Invalid JSON" in response["body"]

    @mock_aws
    def test_handle_get_bot_rules_success(self):
        """Test successful retrieval of bot rules"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add test data
        table.put_item(
            Item={
                "rule_id": "rule_1",
                "rule_name": "TestRule1",
                "description": "Test rule 1",
                "action": "block",
                "priority": 10,
                "conditions": {"user_agent_patterns": ["bot1"]},
                "status": "active",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
                "sync_status": "synced",
            }
        )

        with patch("services.bot_control_service.bot_rules_table", table):
            response = handle_get_bot_rules(self.valid_headers, {})

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "rules" in body
        assert len(body["rules"]) == 1
        assert body["rules"][0]["rule_name"] == "TestRule1"

    @mock_aws
    def test_handle_get_bot_rules_with_filters(self):
        """Test retrieval of bot rules with status filter"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add test data
        table.put_item(
            Item={
                "rule_id": "rule_1",
                "rule_name": "ActiveRule",
                "description": "Active rule",
                "action": "block",
                "priority": 10,
                "conditions": {},
                "status": "active",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
            }
        )

        table.put_item(
            Item={
                "rule_id": "rule_2",
                "rule_name": "InactiveRule",
                "description": "Inactive rule",
                "action": "block",
                "priority": 20,
                "conditions": {},
                "status": "inactive",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
            }
        )

        with patch("services.bot_control_service.bot_rules_table", table):
            response = handle_get_bot_rules(self.valid_headers, {"status": "active"})

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert len(body["rules"]) == 1
        assert body["rules"][0]["status"] == "active"

    def test_handle_get_bot_rules_unauthorized(self):
        """Test access to bot rules without auth headers still works (auth is optional for GET)"""
        invalid_headers = {"Content-Type": "application/json"}

        with patch("services.bot_control_service.bot_rules_table") as mock_table:
            mock_table.scan.return_value = {"Items": []}
            response = handle_get_bot_rules(invalid_headers, {})

        assert response["statusCode"] == 200

    @mock_aws
    def test_handle_create_bot_rule_success(self):
        """Test successful creation of bot rule"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with patch("services.bot_control_service.bot_rules_table", table), patch("services.bot_control_service.waf_sync_controller") as mock_waf:
            mock_waf.create_waf_rule.return_value = Mock(success=True)

            response = handle_create_bot_rule(self.valid_headers, self.sample_bot_rule)

        assert response["statusCode"] == 201
        body = json.loads(response["body"])
        assert body["rule_name"] == self.sample_bot_rule["rule_name"]
        assert body["action"] == self.sample_bot_rule["action"]
        assert "rule_id" in body

    def test_handle_create_bot_rule_missing_fields(self):
        """Test creation with missing required fields"""
        incomplete_rule = {
            "rule_name": "TestRule",
            "description": "Test rule",
            # Missing action, priority, conditions
        }

        response = handle_create_bot_rule(self.valid_headers, incomplete_rule)

        assert response["statusCode"] == 400
        assert "Missing required field" in response["body"]

    def test_handle_create_bot_rule_invalid_action(self):
        """Test creation with invalid action"""
        invalid_rule = self.sample_bot_rule.copy()
        invalid_rule["action"] = "invalid_action"

        response = handle_create_bot_rule(self.valid_headers, invalid_rule)

        assert response["statusCode"] == 400
        assert "Invalid action" in response["body"]

    @mock_aws
    def test_handle_update_bot_rule_success(self):
        """Test successful update of bot rule"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add existing rule
        rule_id = "rule_1234567890"
        table.put_item(
            Item={
                "rule_id": rule_id,
                "rule_name": "TestRule",
                "description": "Test rule",
                "action": "block",
                "priority": 10,
                "conditions": {},
                "status": "active",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
            }
        )

        update_data = {"status": "inactive", "description": "Updated description"}

        with patch("services.bot_control_service.bot_rules_table", table), patch("services.bot_control_service.waf_sync_controller") as mock_waf:
            mock_waf.delete_waf_rule.return_value = Mock(success=True)

            response = handle_update_bot_rule(self.valid_headers, rule_id, update_data)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "inactive"
        assert body["description"] == "Updated description"

    @mock_aws
    def test_handle_update_bot_rule_not_found(self):
        """Test update of non-existent rule"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with patch("services.bot_control_service.bot_rules_table", table):
            response = handle_update_bot_rule(self.valid_headers, "nonexistent_rule", {"status": "inactive"})

        assert response["statusCode"] == 404
        assert "not found" in response["body"].lower()

    @mock_aws
    def test_handle_delete_bot_rule_success(self):
        """Test successful deletion of bot rule"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add existing rule
        rule_id = "rule_1234567890"
        table.put_item(
            Item={
                "rule_id": rule_id,
                "rule_name": "TestRule",
                "description": "Test rule",
                "action": "block",
                "priority": 10,
                "conditions": {},
                "status": "active",
                "created_at": "2024-01-15T10:00:00+00:00",
                "updated_at": "2024-01-15T10:00:00+00:00",
            }
        )

        with patch("services.bot_control_service.bot_rules_table", table), patch("services.bot_control_service.waf_sync_controller") as mock_waf:
            mock_waf.delete_waf_rule.return_value = Mock(success=True)

            response = handle_delete_bot_rule(self.valid_headers, rule_id)

        assert response["statusCode"] == 204

        # Verify rule was deleted
        try:
            table.get_item(Key={"rule_id": rule_id})["Item"]
            assert False, "Rule should have been deleted"
        except KeyError:
            pass  # Expected - rule was deleted

    @mock_aws
    def test_handle_delete_bot_rule_not_found(self):
        """Test deletion of non-existent rule"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        with patch("services.bot_control_service.bot_rules_table", table):
            response = handle_delete_bot_rule(self.valid_headers, "nonexistent_rule")

        assert response["statusCode"] == 404
        assert "not found" in response["body"].lower()

    def test_handle_get_bot_activity_success(self):
        """Test successful retrieval of bot activity"""
        mock_waf_metrics = {
            "blocked_requests": {
                "total": 150,
                "datapoints": [
                    {"Timestamp": datetime.now(timezone.utc), "Sum": 50},
                    {"Timestamp": datetime.now(timezone.utc), "Sum": 100},
                ],
            },
            "allowed_requests": {
                "total": 850,
                "datapoints": [
                    {"Timestamp": datetime.now(timezone.utc), "Sum": 400},
                    {"Timestamp": datetime.now(timezone.utc), "Sum": 450},
                ],
            },
        }

        with patch("services.bot_control_service.waf_sync_controller") as mock_waf:
            mock_waf.get_waf_metrics.return_value = mock_waf_metrics

            response = handle_get_bot_activity(self.valid_headers, {})

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "activity" in body
        assert body["activity"]["blocked_requests"] == 150
        assert body["activity"]["allowed_requests"] == 850
        assert body["activity"]["total_requests"] == 1000
        assert body["activity"]["block_rate"] == 15.0

    def test_handle_get_bot_activity_waf_error(self):
        """Test bot activity retrieval when WAF metrics fail"""
        with patch("services.bot_control_service.waf_sync_controller") as mock_waf:
            mock_waf.get_waf_metrics.return_value = {"error": "WAF unavailable"}

            response = handle_get_bot_activity(self.valid_headers, {})

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "activity" in body
        assert body["activity"]["blocked_requests"] == 0
        assert "error" in body["activity"]

    @mock_aws
    def test_handle_get_bot_stats_success(self):
        """Test successful retrieval of bot control statistics"""
        # Create mock DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        table = dynamodb.create_table(
            TableName="iab-bot-rules-table",
            KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Add test rules
        table.put_item(
            Item={
                "rule_id": "rule_1",
                "rule_name": "ActiveRule",
                "action": "block",
                "status": "active",
                "sync_status": "synced",
                "waf_rule_id": "ActiveRule",
            }
        )

        table.put_item(
            Item={
                "rule_id": "rule_2",
                "rule_name": "InactiveRule",
                "action": "block",
                "status": "inactive",
                "sync_status": "pending",
            }
        )

        mock_waf_metrics = {
            "blocked_requests": {"total": 100},
            "allowed_requests": {"total": 900},
            "rule_ActiveRule_blocked": {"total": 75},
        }

        with patch("services.bot_control_service.bot_rules_table", table), patch("services.bot_control_service.waf_sync_controller") as mock_waf:
            mock_waf.get_waf_metrics.return_value = mock_waf_metrics

            response = handle_get_bot_stats(self.valid_headers, {})

        assert response["statusCode"] == 200
        body = json.loads(response["body"])

        assert body["rule_summary"]["total_rules"] == 2
        assert body["rule_summary"]["active_rules"] == 1
        assert body["rule_summary"]["inactive_rules"] == 1

        assert body["sync_status"]["synced_rules"] == 1
        assert body["sync_status"]["pending_rules"] == 1

        assert body["traffic_summary"]["total_requests"] == 1000
        assert body["traffic_summary"]["blocked_requests"] == 100
        assert body["traffic_summary"]["block_rate"] == 10.0

        assert body["waf_health"] == "healthy"

    def test_validate_authentication_bearer_token(self):
        """Test authentication validation with Bearer token"""
        headers = {"Authorization": "Bearer jwt-token"}
        result = validate_authentication(headers)
        assert result["valid"] is True

    def test_validate_authentication_publisher_key(self):
        """Test authentication validation with Publisher key"""
        headers = {"X-Publisher-Key": "pub_key_123"}
        result = validate_authentication(headers)
        assert result["valid"] is True

    def test_validate_authentication_missing(self):
        """Test authentication validation with missing credentials"""
        headers = {"Content-Type": "application/json"}
        result = validate_authentication(headers)
        assert result["valid"] is False
        assert "authentication" in result["error"].lower()

    def test_create_success_response(self):
        """Test creation of success response"""
        data = {"message": "success"}
        response = create_success_response(data, 201)

        assert response["statusCode"] == 201
        assert "application/json" in response["headers"]["Content-Type"]
        # TM-02 (security-hardening-phase2): CORS origin is a CONFIGURED origin, never a
        # wildcard "*", and omitted (fail safe) when no allow-list is resolvable (this unit
        # env has no ALLOWED_ORIGINS / SSM).
        assert response["headers"].get("Access-Control-Allow-Origin") != "*"

        body = json.loads(response["body"])
        assert body["message"] == "success"

    def test_create_error_response(self):
        """Test creation of error response"""
        response = create_error_response(400, "Bad request")

        assert response["statusCode"] == 400
        assert "application/json" in response["headers"]["Content-Type"]

        body = json.loads(response["body"])
        assert body["error"]["code"] == 400
        assert body["error"]["message"] == "Bad request"

    def test_decimal_encoder(self):
        """Test DecimalEncoder for DynamoDB Decimal types"""
        from services.bot_control_service import DecimalEncoder

        data = {"count": Decimal("123.45")}
        json_str = json.dumps(data, cls=DecimalEncoder)
        parsed = json.loads(json_str)

        assert parsed["count"] == 123.45
        assert isinstance(parsed["count"], float)


if __name__ == "__main__":
    pytest.main([__file__])
