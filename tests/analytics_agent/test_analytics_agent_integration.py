#!/usr/bin/env python3
"""
End-to-End Integration Tests for Analytics Agent

Tests the complete integration chain:
UI → API Gateway → Analytics Lambda → AgentCore Analytics Agent → MCP Server → IAB APIs
"""

import pytest
import json
import os
import sys
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

# Add paths for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "ui_lambda"))

from analytics_handler import lambda_handler, extract_cognito_user_context, handle_analytics_agent_chat


class TestAnalyticsAgentIntegration:
    """Test end-to-end analytics agent integration."""

    @pytest.fixture
    def valid_cognito_event(self):
        """Create a valid API Gateway event with Cognito authentication."""
        return {
            "httpMethod": "POST",
            "path": "/analytics-agent/chat",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer valid_jwt_token"},
            "body": json.dumps(
                {
                    "message": "Show me my revenue analytics for this month",
                    "page_context": {
                        "page_type": "analytics",
                        "url_path": "/analytics",
                        "context_elements": [
                            {"name": "current_revenue", "value": "$5,420", "description": "Monthly revenue"},
                            {"name": "growth_rate", "value": "12.5%", "description": "Monthly growth"},
                        ],
                    },
                    "session_id": "integration_test_session",
                }
            ),
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:username": "test_publisher",
                        "email": "publisher@example.com",
                        "custom:publisher_id": "pub_test_001",
                        "custom:publisher_name": "Test Publisher",
                        "custom:access_level": "admin",
                        "cognito:groups": ["publishers", "admins"],
                        "sub": "test_user_sub_123",
                        "token_use": "access",
                        "auth_time": "1640995200",
                        "iss": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_test",
                        "exp": "1640998800",
                    }
                }
            },
            "queryStringParameters": None,
        }

    @pytest.fixture
    def mock_agentcore_response(self):
        """Mock AgentCore response."""
        return {
            "response": Mock(
                read=Mock(
                    return_value=json.dumps(
                        {
                            "response": "Based on your analytics data, I can see your revenue has grown 12.5% this month to $5,420. This is excellent progress! The growth is primarily driven by increased demand for your content. Would you like me to analyze which specific content pieces are driving this growth?",
                            "intent_analysis": {
                                "primary_intent": "revenue_analysis",
                                "confidence": 0.9,
                                "requires_data": True,
                            },
                            "suggested_actions": [
                                "Analyze top-performing content",
                                "Review revenue trends over time",
                                "Compare with previous months",
                                "Identify growth opportunities",
                            ],
                            "page_context": {"page_type": "analytics", "url_path": "/analytics"},
                            "user_context": "pub_test_001",
                            "session_id": "integration_test_session",
                            "model_used": "Claude 3.7 Sonnet via Strands Agent with MCP Tools",
                            "conversation_metadata": {
                                "requires_followup": False,
                                "data_driven": True,
                                "clarifying_questions": [],
                            },
                        }
                    ).encode("utf-8")
                )
            ),
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "test-request-id"},
        }

    def test_cognito_user_context_extraction(self, valid_cognito_event):
        """Test Cognito user context extraction."""
        user_context = extract_cognito_user_context(valid_cognito_event)

        assert user_context is not None
        assert user_context["cognito_username"] == "test_publisher"
        assert user_context["email"] == "publisher@example.com"
        assert user_context["publisher_id"] == "pub_test_001"
        assert user_context["publisher_name"] == "Test Publisher"
        assert user_context["access_level"] == "admin"
        assert "publishers" in user_context["groups"]
        assert "admins" in user_context["groups"]
        assert user_context["user_id"] == "test_user_sub_123"

    def test_cognito_user_context_missing_claims(self):
        """Test handling of missing Cognito claims."""
        event_no_claims = {"requestContext": {"authorizer": {}}}

        user_context = extract_cognito_user_context(event_no_claims)
        assert user_context is None

    def test_cognito_user_context_invalid_claims(self):
        """Test handling of invalid Cognito claims."""
        event_invalid_claims = {
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "token_use": "access"
                        # Missing required fields
                    }
                }
            }
        }

        user_context = extract_cognito_user_context(event_invalid_claims)
        assert user_context is None

    @patch.dict(
        os.environ,
        {"ANALYTICS_AGENT_ARN": "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_analytics_agent"},
    )
    @patch("analytics_handler.boto3.client")
    def test_agentcore_integration(self, mock_boto3_client, valid_cognito_event, mock_agentcore_response):
        """Test AgentCore integration."""
        # Mock boto3 bedrock-agentcore client
        mock_client = Mock()
        mock_client.invoke_agent_runtime.return_value = mock_agentcore_response
        mock_boto3_client.return_value = mock_client

        # Call the handler
        response = lambda_handler(valid_cognito_event, None)

        # Verify response
        assert response["statusCode"] == 200

        # Parse response body
        response_body = json.loads(response["body"])

        # Verify response structure
        assert "response" in response_body
        assert "intent_analysis" in response_body
        assert "suggested_actions" in response_body
        assert "page_context" in response_body
        assert "user_context" in response_body
        assert "session_id" in response_body
        assert "model_used" in response_body

        # Verify content
        assert "revenue has grown 12.5%" in response_body["response"]
        assert response_body["intent_analysis"]["primary_intent"] == "revenue_analysis"
        assert response_body["intent_analysis"]["confidence"] == 0.9
        assert len(response_body["suggested_actions"]) == 4
        assert response_body["session_id"] == "integration_test_session"
        assert response_body["user_context"] == "pub_test_001"

        # Verify AgentCore was called correctly
        mock_client.invoke_agent_runtime.assert_called_once()
        call_args = mock_client.invoke_agent_runtime.call_args

        assert (
            call_args[1]["agentRuntimeArn"]
            == "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_analytics_agent"
        )
        assert call_args[1]["contentType"] == "application/json"
        assert call_args[1]["accept"] == "application/json"

        # Verify payload structure
        payload = json.loads(call_args[1]["payload"])
        assert payload["tool_name"] == "analytics_chat"
        assert payload["client_type"] == "internal"
        assert payload["parameters"]["message"] == "Show me my revenue analytics for this month"
        assert payload["parameters"]["page_context"]["page_type"] == "analytics"
        assert payload["parameters"]["user_context"]["publisher_id"] == "pub_test_001"
        assert payload["parameters"]["session_id"] == "integration_test_session"

    @patch.dict(
        os.environ,
        {"ANALYTICS_AGENT_ARN": "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_analytics_agent"},
    )
    @patch("analytics_handler.boto3.client")
    def test_agentcore_error_handling(self, mock_boto3_client, valid_cognito_event):
        """Test AgentCore error handling."""
        # Mock boto3 client to raise exception
        mock_client = Mock()
        mock_client.invoke_agent_runtime.side_effect = Exception("AgentCore connection failed")
        mock_boto3_client.return_value = mock_client

        # Call the handler
        response = lambda_handler(valid_cognito_event, None)

        # Verify error response
        assert response["statusCode"] == 500

        response_body = json.loads(response["body"])
        assert "error" in response_body
        assert "AgentCore runtime error" in response_body["error"]
        assert "AgentCore connection failed" in response_body["message"]

    def test_missing_analytics_agent_arn(self, valid_cognito_event):
        """Test handling of missing Analytics Agent ARN."""
        # Ensure ANALYTICS_AGENT_ARN is not set
        if "ANALYTICS_AGENT_ARN" in os.environ:
            del os.environ["ANALYTICS_AGENT_ARN"]

        response = lambda_handler(valid_cognito_event, None)

        assert response["statusCode"] == 500
        response_body = json.loads(response["body"])
        assert "Configuration error" in response_body["error"]
        assert "Analytics agent ARN not configured" in response_body["message"]

    def test_missing_message_parameter(self, valid_cognito_event):
        """Test handling of missing message parameter."""
        # Remove message from request body
        body_data = json.loads(valid_cognito_event["body"])
        del body_data["message"]
        valid_cognito_event["body"] = json.dumps(body_data)

        response = lambda_handler(valid_cognito_event, None)

        assert response["statusCode"] == 400
        response_body = json.loads(response["body"])
        assert "Message is required" in response_body["error"]

    def test_unauthenticated_request(self):
        """Test handling of unauthenticated request."""
        unauthenticated_event = {
            "httpMethod": "POST",
            "path": "/analytics-agent/chat",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": "test"}),
            "requestContext": {},
        }

        response = lambda_handler(unauthenticated_event, None)

        assert response["statusCode"] == 401
        response_body = json.loads(response["body"])
        assert "Authentication required" in response_body["error"]

    def test_cors_preflight_request(self):
        """Test CORS preflight request handling."""
        cors_event = {"httpMethod": "OPTIONS", "path": "/analytics-agent/chat", "headers": {}, "body": None}

        response = lambda_handler(cors_event, None)

        assert response["statusCode"] == 200
        assert response["body"] == ""

        # Verify CORS headers
        headers = response["headers"]
        assert "Access-Control-Allow-Origin" in headers
        assert "Access-Control-Allow-Methods" in headers
        assert "Access-Control-Allow-Headers" in headers
        assert headers["Access-Control-Allow-Origin"] == "*"

    def test_invalid_json_body(self, valid_cognito_event):
        """Test handling of invalid JSON in request body."""
        valid_cognito_event["body"] = "invalid json"

        response = lambda_handler(valid_cognito_event, None)

        assert response["statusCode"] == 400
        response_body = json.loads(response["body"])
        assert "Invalid JSON" in response_body["error"]

    def test_page_context_processing(self, valid_cognito_event):
        """Test page context processing and enhancement."""
        # Extract the request data
        request_data = json.loads(valid_cognito_event["body"])
        headers = valid_cognito_event["headers"]

        # Mock the AgentCore call to focus on context processing
        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": "test_arn"}):
            with patch("analytics_handler.boto3.client") as mock_boto3:
                mock_client = Mock()
                mock_response = {
                    "response": Mock(read=Mock(return_value=json.dumps({"response": "Test response"}).encode("utf-8")))
                }
                mock_client.invoke_agent_runtime.return_value = mock_response
                mock_boto3.return_value = mock_client

                # Call the handler
                response = lambda_handler(valid_cognito_event, None)

                # Verify the payload sent to AgentCore includes processed context
                call_args = mock_client.invoke_agent_runtime.call_args
                payload = json.loads(call_args[1]["payload"])

                page_context = payload["parameters"]["page_context"]
                assert page_context["page_type"] == "analytics"
                assert page_context["url_path"] == "/analytics"
                assert "context_elements" in page_context
                assert "timestamp" in page_context
                assert len(page_context["context_elements"]) == 2

                # Verify context elements are preserved
                elements = page_context["context_elements"]
                revenue_element = next(e for e in elements if e["name"] == "current_revenue")
                assert revenue_element["value"] == "$5,420"
                assert revenue_element["description"] == "Monthly revenue"


class TestAnalyticsAgentIntegrationEdgeCases:
    """Test edge cases and error scenarios."""

    def test_large_message_handling(self):
        """Test handling of very large messages."""
        large_message = "A" * 10000  # 10KB message

        event = {
            "httpMethod": "POST",
            "path": "/analytics-agent/chat",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": large_message}),
            "requestContext": {
                "authorizer": {"claims": {"cognito:username": "test_user", "email": "test@example.com"}}
            },
        }

        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": "test_arn"}):
            with patch("analytics_handler.boto3.client") as mock_boto3:
                mock_client = Mock()
                mock_response = {
                    "response": Mock(
                        read=Mock(return_value=json.dumps({"response": "Processed large message"}).encode("utf-8"))
                    )
                }
                mock_client.invoke_agent_runtime.return_value = mock_response
                mock_boto3.return_value = mock_client

                response = lambda_handler(event, None)

                # Should handle large messages gracefully
                assert response["statusCode"] == 200

                # Verify the message was passed through
                call_args = mock_client.invoke_agent_runtime.call_args
                payload = json.loads(call_args[1]["payload"])
                assert len(payload["parameters"]["message"]) == 10000

    def test_special_characters_in_message(self):
        """Test handling of special characters in messages."""
        special_message = "Test with émojis 🚀 and spëcial chars: <>&\"'"

        event = {
            "httpMethod": "POST",
            "path": "/analytics-agent/chat",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": special_message}),
            "requestContext": {
                "authorizer": {"claims": {"cognito:username": "test_user", "email": "test@example.com"}}
            },
        }

        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": "test_arn"}):
            with patch("analytics_handler.boto3.client") as mock_boto3:
                mock_client = Mock()
                mock_response = {
                    "response": Mock(
                        read=Mock(return_value=json.dumps({"response": "Processed special characters"}).encode("utf-8"))
                    )
                }
                mock_client.invoke_agent_runtime.return_value = mock_response
                mock_boto3.return_value = mock_client

                response = lambda_handler(event, None)

                assert response["statusCode"] == 200

                # Verify special characters are preserved
                call_args = mock_client.invoke_agent_runtime.call_args
                payload = json.loads(call_args[1]["payload"])
                assert payload["parameters"]["message"] == special_message


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
