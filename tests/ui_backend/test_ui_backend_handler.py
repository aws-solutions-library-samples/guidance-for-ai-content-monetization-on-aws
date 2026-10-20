#!/usr/bin/env python3
"""
Unit tests for UI Backend Lambda Handler

Tests the UI Backend Lambda function that handles internal dashboard operations
with Cognito JWT authentication.
"""

import json
import pytest
import os
import sys
from unittest.mock import Mock, patch
from datetime import datetime, timezone

# Add the lambda directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "ui_backend"))

# Environment defaults required before importing the handler. Since task 5.3 flipped the
# ``/content*`` dispatcher entry to ``ui_backend/routes/content.py``, importing
# ``ui_backend.handler`` now eagerly loads ``services.content_service`` -> ``content_management``,
# which builds its DynamoDB resources / reads table names at module load (mirrors the sibling
# content_management unit and characterization tests). No AWS calls are made by these tests.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

# Import the handler
# NOTE (task 4.4): the inline ``handle_processing_status`` was deleted from ``ui_backend.handler``
# when the ``/processing/status`` dispatcher entry was flipped to ``ui_backend/routes/processing.py``
# (Requirement 16.3, mirroring the taxonomy flip in task 3.3). Its import and the two inline-handler
# tests that referenced it were removed with it; route equivalence for the processing domain is now
# enforced by ``tests/processing_status/test_processing_route_characterization.py``.
# NOTE (task 5.3): likewise, the inline ``handle_content_endpoints`` router and its inline mock
# content handlers (``handle_list_content``, ``handle_create_content``, ...) were deleted from
# ``ui_backend.handler`` when the ``/content*`` dispatcher entry was flipped to
# ``ui_backend/routes/content.py`` (Requirement 16.3). Their imports and the two inline-handler tests
# that exercised them were removed with them; content route equivalence + auth are now enforced by
# ``tests/content_management/test_content_route_characterization.py``.
# NOTE (task 8.5): likewise, the interim ``_route_analytics`` adapter, the inline
# ``handle_analytics_endpoints`` router, and the inline ``handle_analytics_*`` handlers (plus the
# ``handle_analytics_partners = handle_get_partners`` alias) were deleted from ``ui_backend.handler``
# when the ``/analytics/*`` dispatcher entry was flipped to ``ui_backend/routes/analytics.py``
# (Requirement 16.3). Their imports and the inline-handler tests that exercised them were removed
# with them; analytics route equivalence + auth are now enforced by
# ``tests/ui_backend/test_analytics_characterization.py``.
# NOTE (task 9.4): likewise, the interim ``_route_pricing`` adapter, the inline
# ``handle_pricing_endpoints`` router, and the inline pricing handlers (``handle_get_pricing_rules``,
# ``handle_get_pricing_rules_resolved``, ``handle_create_pricing_rules``,
# ``handle_update_pricing_rules``, ``handle_get_pricing_history``, ``handle_calculate_pricing``,
# ``handle_fix_pricing_versions``) were deleted from ``ui_backend.handler`` when the ``/pricing/*``
# dispatcher entry was flipped to ``ui_backend/routes/pricing.py`` (Requirement 16.3). Their imports
# and the two inline-handler tests that exercised them (``test_pricing_rules_get_endpoint`` /
# ``test_pricing_rules_create_endpoint``) were removed with them; pricing route equivalence + auth are
# now enforced by ``tests/ui_backend/test_pricing_route_characterization.py``. The dispatcher-routing
# test below (``test_lambda_handler_pricing_endpoints``) was repointed to the new delegate.
# NOTE (task 11.4): likewise, the interim ``_route_analytics_agent`` adapter, the inline
# ``handle_analytics_agent_endpoints`` router, and the inline ``handle_analytics_agent_chat`` handler
# (including its inline bedrock-agentcore ``invoke_agent_runtime`` call) were deleted from
# ``ui_backend.handler`` when the ``/analytics-agent/*`` dispatcher entry was flipped to
# ``ui_backend/routes/analytics_agent.py`` -> ``services.agent_runtime_client.AgentRuntimeClient``
# (Requirement 16.3, and Requirement 7.3 — zero inline ``invoke_agent_runtime`` calls remain). The
# ``handle_analytics_agent_chat`` import and the three inline-handler tests that exercised it
# (``test_analytics_agent_chat_endpoint`` / ``test_analytics_agent_chat_missing_message`` /
# ``test_analytics_agent_chat_missing_arn``) were removed with it; analytics-agent route equivalence +
# auth are now enforced by ``tests/ui_backend/test_analytics_agent_characterization.py``. The
# dispatcher-routing test below (``test_lambda_handler_analytics_agent_chat``) was repointed to the
# new route module + service.
from ui_backend.handler import (
    lambda_handler,
)


class TestUIBackendHandler:
    """Test cases for UI Backend Lambda handler"""

    def setup_method(self):
        """Set up test fixtures"""
        self.mock_context = Mock()
        self.mock_context.aws_request_id = "test-request-id"

        # Mock Cognito user context
        self.mock_user = {
            "user_id": "test-user-123",
            "email": "test@example.com",
            "publisher_id": "test-publisher",
            "publisher_name": "Test Publisher",
            "access_level": "admin",
        }

        # Base event structure
        self.base_event = {
            "httpMethod": "GET",
            "path": "/analytics/metrics",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer test-token"},
            "body": None,
            "queryStringParameters": None,
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:username": "test_user",
                        "email": "test@example.com",
                        "custom:publisher_id": "test-publisher",
                        "sub": "test-user-123",
                    }
                }
            },
        }

    def test_lambda_handler_analytics_metrics(self):
        """Test analytics metrics endpoint routing.

        Task 8.5: ``/analytics/*`` now dispatches to ``ui_backend/routes/analytics.py`` ->
        ``services.analytics_service.AnalyticsService``. Patching the service the route
        delegates to confirms the dispatcher routes the request end-to-end through the new
        route module + service.
        """
        event = self.base_event.copy()
        event["path"] = "/analytics/metrics"
        event["httpMethod"] = "GET"

        with patch("routes.analytics._service.get_metrics") as mock_svc:
            mock_svc.return_value = {"test": "data"}

            response = lambda_handler(event, self.mock_context)

            assert response["statusCode"] == 200
            mock_svc.assert_called_once()

    def test_lambda_handler_pricing_endpoints(self):
        """Test pricing endpoints routing.

        Task 9.4: ``/pricing/*`` now dispatches to ``ui_backend/routes/pricing.py`` ->
        ``services.pricing_manager.PricingVersionManager`` (the pricing single home). Patching the
        service the route delegates to confirms the dispatcher routes the request end-to-end through
        the new route module + service.
        """
        event = self.base_event.copy()
        event["path"] = "/pricing/rules"
        event["httpMethod"] = "GET"

        with patch("routes.pricing.PricingVersionManager") as mock_pm:
            mock_pm.return_value.get_pricing_rules_view.return_value = {
                "status": "success",
                "pricing_rule": {},
                "rule_set_id": "default-pricing",
            }

            response = lambda_handler(event, self.mock_context)

            assert response["statusCode"] == 200
            mock_pm.return_value.get_pricing_rules_view.assert_called_once()

    def test_lambda_handler_bot_control_endpoints(self):
        """Test bot control endpoints routing.

        Task 6.3: ``/bot-control/*`` now dispatches to ``ui_backend/routes/bot_control.py`` ->
        ``services.bot_control_service.BotControlService``, now the single home for the
        bot-control domain's logic. Patching ``BotControlService.handle_request`` confirms the
        dispatcher routes the request end-to-end through the new route module + service.
        """
        event = self.base_event.copy()
        event["path"] = "/bot-control/rules"
        event["httpMethod"] = "GET"

        with patch("services.bot_control_service.BotControlService.handle_request") as mock_handler:
            mock_handler.return_value = {"statusCode": 200, "body": '{"rules": []}'}

            response = lambda_handler(event, self.mock_context)

            assert response["statusCode"] == 200
            mock_handler.assert_called_once()

    def test_lambda_handler_content_endpoints(self):
        """Test content management endpoints routing.

        Task 5.3: ``/content*`` now dispatches to ``ui_backend/routes/content.py`` ->
        ``services.content_service.ContentService``, which is now the single home for the
        content domain's logic. Patching ``ContentService.handle_request`` confirms the
        dispatcher routes the request end-to-end through the new route module + service.
        """
        event = self.base_event.copy()
        event["path"] = "/content/test"  # Use a path that starts with /content/
        event["httpMethod"] = "GET"

        with patch("services.content_service.ContentService.handle_request") as mock_handler:
            mock_handler.return_value = {"statusCode": 200, "body": '{"items": []}'}

            response = lambda_handler(event, self.mock_context)

            assert response["statusCode"] == 200
            mock_handler.assert_called_once()

    def test_lambda_handler_internal_log_endpoint(self):
        """Test internal log endpoint routing"""
        event = self.base_event.copy()
        event["path"] = "/log"
        event["httpMethod"] = "POST"
        event["body"] = json.dumps({"test": "log data"})

        with patch("ui_backend.handler.handle_internal_log_request") as mock_handler:
            mock_handler.return_value = {"statusCode": 200, "body": '{"logged": true}'}

            response = lambda_handler(event, self.mock_context)

            assert response["statusCode"] == 200
            mock_handler.assert_called_once()

    def test_lambda_handler_analytics_agent_chat(self):
        """Test analytics agent chat endpoint routing.

        Task 11.4: ``/analytics-agent/*`` now dispatches to ``ui_backend/routes/analytics_agent.py``
        -> ``services.agent_runtime_client.AgentRuntimeClient`` (the Service_Layer single home for the
        bedrock-agentcore invoke). Patching the service ``invoke`` the route delegates to confirms the
        dispatcher routes the request end-to-end through the new route module + service (with the
        ``ANALYTICS_AGENT_ARN`` present so the route reaches the service).
        """
        event = self.base_event.copy()
        event["path"] = "/analytics-agent/chat"
        event["httpMethod"] = "POST"
        event["body"] = json.dumps({"message": "test message"})

        with patch.dict(
            os.environ,
            {"ANALYTICS_AGENT_ARN": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-agent"},
        ), patch("routes.analytics_agent._agent_runtime_client.invoke") as mock_invoke:
            mock_invoke.return_value = {"output": {"response": "test"}}

            response = lambda_handler(event, self.mock_context)

            assert response["statusCode"] == 200
            mock_invoke.assert_called_once()

    def test_lambda_handler_invalid_endpoint(self):
        """Test invalid endpoint returns 404"""
        event = self.base_event.copy()
        event["path"] = "/invalid/endpoint"

        response = lambda_handler(event, self.mock_context)

        assert response["statusCode"] == 404
        body = json.loads(response["body"])
        assert "Endpoint not found" in body["error"]

    def test_lambda_handler_invalid_json_body(self):
        """Test invalid JSON body returns 400"""
        event = self.base_event.copy()
        event["body"] = "invalid json"

        response = lambda_handler(event, self.mock_context)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "Invalid JSON" in body["error"]

    # NOTE (task 8.5): ``test_analytics_metrics_endpoint``, ``test_analytics_usage_endpoint``, and
    # ``test_analytics_revenue_endpoint`` were removed here. They exercised the inline analytics
    # handlers (``handle_analytics_metrics`` / ``handle_analytics_usage`` / ``handle_analytics_revenue``)
    # that were deleted when the ``/analytics/*`` dispatcher entry was flipped to
    # ``ui_backend/routes/analytics.py`` (Requirement 16.3). Analytics route equivalence and auth are
    # now enforced by ``tests/ui_backend/test_analytics_characterization.py``.

    # NOTE (task 9.4): ``test_pricing_rules_get_endpoint`` and ``test_pricing_rules_create_endpoint``
    # were removed here. They exercised the inline pricing handlers (``handle_get_pricing_rules`` /
    # ``handle_create_pricing_rules``) that were deleted when the ``/pricing/*`` dispatcher entry was
    # flipped to ``ui_backend/routes/pricing.py`` (Requirement 16.3). Pricing route equivalence and
    # auth are now enforced by ``tests/ui_backend/test_pricing_route_characterization.py``.

    # NOTE (task 5.3): ``test_content_list_endpoint`` and ``test_content_create_endpoint`` were
    # removed here. They exercised the inline mock content handlers (``handle_list_content`` /
    # ``handle_create_content``) that were deleted when the ``/content*`` dispatcher entry was
    # flipped to ``ui_backend/routes/content.py`` (Requirement 16.3). Content route equivalence and
    # auth are now enforced by ``tests/content_management/test_content_route_characterization.py``.

    # NOTE (task 11.4): ``test_analytics_agent_chat_endpoint``, ``test_analytics_agent_chat_missing_message``,
    # and ``test_analytics_agent_chat_missing_arn`` were removed here. They exercised the inline
    # ``handle_analytics_agent_chat`` handler (including its inline bedrock-agentcore
    # ``invoke_agent_runtime`` call) that was deleted when the ``/analytics-agent/*`` dispatcher entry
    # was flipped to ``ui_backend/routes/analytics_agent.py`` ->
    # ``services.agent_runtime_client.AgentRuntimeClient`` (Requirement 16.3, Requirement 7.3).
    # Analytics-agent route equivalence (success 200, message-required 400, ARN-not-configured 500,
    # unknown-subpath 404) and auth (401) are now enforced by
    # ``tests/ui_backend/test_analytics_agent_characterization.py``.

    def test_lambda_handler_exception_handling(self):
        """Test lambda handler top-level exception handling.

        Task 8.5: the inline ``handle_analytics_endpoints`` router is gone; the dispatcher now
        delegates matched routes via the ``_DISPATCH`` Route_Table. Forcing the resolved
        ``analytics`` dispatch entry to raise exercises the dispatcher's top-level
        try/except -> 500 ``Internal server error`` mapping (Requirement 6.4).
        """
        event = self.base_event.copy()
        event["path"] = "/analytics/metrics"

        with patch.dict(
            "ui_backend.handler._DISPATCH",
            {"analytics": Mock(side_effect=Exception("Test exception"))},
        ):
            response = lambda_handler(event, self.mock_context)

            assert response["statusCode"] == 500
            body = json.loads(response["body"])
            assert "Internal server error" in body["error"]


if __name__ == "__main__":
    pytest.main([__file__])
