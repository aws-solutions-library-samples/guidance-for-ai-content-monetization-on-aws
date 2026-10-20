#!/usr/bin/env python3
"""
Unit tests for Analytics Agent MCP Integration

Tests the analytics agent integration with deployed resources and Cognito user context.
"""

import pytest
import asyncio
import os
import sys
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Add the analytics_agent directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "agent"))

from analytics_agent_strands import PublisherAnalyticsAgent


class TestAnalyticsAgentMCPIntegration:
    """Test analytics agent MCP integration with deployed resources."""

    @pytest.fixture
    def mock_bedrock_client(self):
        """Create a mock bedrock-agentcore client."""
        client = Mock()

        # Mock successful AgentCore response
        mock_response_body = Mock()
        mock_response_body.read.return_value = json.dumps(
            {
                "response": "Based on your analytics data, I can see strong revenue growth of 15% this month, reaching $5,420. Your healthcare content is performing exceptionally well.",
                "intent_analysis": {"primary_intent": "revenue_analysis", "confidence": 0.9, "requires_data": True},
                "suggested_actions": [
                    "Analyze healthcare content performance",
                    "Review revenue trends by content type",
                    "Optimize pricing strategy",
                ],
                "page_context": {"page_type": "analytics"},
                "user_context": "test_publisher",
                "session_id": "test_session",
                "model_used": "Claude 3.7 Sonnet via Strands Agent with MCP Tools",
                "conversation_metadata": {"requires_followup": False, "data_driven": True},
            }
        ).encode("utf-8")

        client.invoke_agent_runtime.return_value = {
            "response": mock_response_body,
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }

        return client

    @pytest.fixture
    def sample_user_context(self):
        """Create sample Cognito user context."""
        return {
            "cognito_username": "test_user",
            "email": "test@publisher.com",
            "publisher_id": "test_publisher",
            "publisher_name": "Test Publisher",
            "groups": ["publishers", "admin"],
            "access_level": "admin",
            "user_id": "test_user_id_123",
            "token_use": "access",
            "auth_time": "1640995200",
            "iss": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_test",
            "exp": "1640998800",
        }

    @pytest.fixture
    def sample_page_context(self):
        """Create sample page context."""
        return {
            "page_type": "analytics",
            "url_path": "/analytics/dashboard",
            "context_elements": [
                {"name": "current_revenue", "value": "$5,420", "description": "Monthly revenue"},
                {"name": "growth_rate", "value": "15%", "description": "Monthly growth"},
                {"name": "top_content", "value": "Healthcare AI", "description": "Top performing content"},
            ],
            "current_filters": {"time_period": "last_30_days"},
            "timestamp": "2025-01-15T10:00:00Z",
        }

    def test_client_initialization_with_arn(self, mock_bedrock_client):
        """Test client initialization with provided ARN."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            assert client.agent_arn == test_arn
            assert client.region == "us-west-2"
            assert client.bedrock_client == mock_bedrock_client

    def test_client_initialization_with_env_var(self, mock_bedrock_client):
        """Test client initialization with environment variable."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/env_agent"

        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": test_arn}):
            with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
                client = PublisherAnalyticsAgentClient()

                assert client.agent_arn == test_arn

    def test_client_initialization_missing_arn(self):
        """Test client initialization fails without ARN."""
        # Ensure no ARN in environment
        if "ANALYTICS_AGENT_ARN" in os.environ:
            del os.environ["ANALYTICS_AGENT_ARN"]

        with pytest.raises(ValueError, match="Analytics agent ARN must be provided"):
            PublisherAnalyticsAgentClient()

    @pytest.mark.asyncio
    async def test_chat_with_full_context(self, mock_bedrock_client, sample_user_context, sample_page_context):
        """Test chat with full user and page context."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            response = await client.chat(
                message="How is my revenue performing this month?",
                page_context=sample_page_context,
                user_context=sample_user_context,
                session_id="integration_test",
            )

            # Verify AgentCore was called correctly
            mock_bedrock_client.invoke_agent_runtime.assert_called_once()
            call_args = mock_bedrock_client.invoke_agent_runtime.call_args

            assert call_args[1]["agentRuntimeArn"] == test_arn
            assert call_args[1]["contentType"] == "application/json"
            assert call_args[1]["accept"] == "application/json"

            # Parse the payload
            payload = json.loads(call_args[1]["payload"])
            assert payload["prompt"] == "How is my revenue performing this month?"
            assert payload["page_context"] == sample_page_context
            assert payload["user_context"] == sample_user_context
            assert payload["session_id"] == "integration_test"
            assert payload["actor_id"] == "test_user_id_123"

            # Verify response structure
            assert "response" in response
            assert "intent_analysis" in response
            assert "suggested_actions" in response
            assert response["session_id"] == "integration_test"
            assert response["user_context"] == "test_publisher"
            assert "revenue growth of 15%" in response["response"]

    @pytest.mark.asyncio
    async def test_chat_with_minimal_context(self, mock_bedrock_client):
        """Test chat with minimal context."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            response = await client.chat("Hello")

            # Verify AgentCore was called
            mock_bedrock_client.invoke_agent_runtime.assert_called_once()
            call_args = mock_bedrock_client.invoke_agent_runtime.call_args

            payload = json.loads(call_args[1]["payload"])
            assert payload["prompt"] == "Hello"
            assert payload["page_context"] == {}
            assert payload["user_context"] == {}
            assert payload["actor_id"] == "backend_user"
            assert payload["session_id"].startswith("backend_session_")

            # Verify response
            assert "response" in response
            assert response["model_used"] == "Claude 3.7 Sonnet via Strands Agent with MCP Tools"

    @pytest.mark.asyncio
    async def test_chat_agentcore_error(self, mock_bedrock_client):
        """Test chat when AgentCore returns an error."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        # Mock AgentCore to raise an exception
        mock_bedrock_client.invoke_agent_runtime.side_effect = Exception("AgentCore connection failed")

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            response = await client.chat("Test error handling")

            # Verify error response
            assert "error" in response
            assert response["model_used"] == "error_fallback"
            assert "AgentCore connection failed" in response["error"]
            assert "I'm sorry, I encountered an error" in response["response"]

    @pytest.mark.asyncio
    async def test_chat_invalid_json_response(self, mock_bedrock_client):
        """Test chat when AgentCore returns invalid JSON."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        # Mock AgentCore to return invalid JSON
        mock_response_body = Mock()
        mock_response_body.read.return_value = b"Invalid JSON response from agent"
        mock_bedrock_client.invoke_agent_runtime.return_value = {"response": mock_response_body}

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            response = await client.chat("Test invalid JSON")

            # Verify response handles invalid JSON gracefully
            assert "response" in response
            assert response["response"] == "Invalid JSON response from agent"
            assert response["model_used"] == "AgentCore Analytics Agent (raw)"

    @pytest.mark.asyncio
    async def test_chat_no_response_body(self, mock_bedrock_client):
        """Test chat when AgentCore returns no response body."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        # Mock AgentCore to return no response body
        mock_bedrock_client.invoke_agent_runtime.return_value = {}

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            response = await client.chat("Test no response")

            # Verify error handling
            assert "error" in response
            assert response["model_used"] == "error_fallback"
            assert "No response received from AgentCore" in response["error"]

    def test_health_check_healthy(self, mock_bedrock_client):
        """Test health check when client is healthy."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            health = client.health_check()

            assert health["status"] == "healthy"
            assert health["agent_arn"] == test_arn
            assert health["region"] == "us-west-2"
            assert "timestamp" in health

    def test_health_check_unhealthy(self):
        """Test health check when client is unhealthy."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        # Mock boto3.client to raise an exception
        with patch("analytics_agent.boto3.client", side_effect=Exception("Client initialization failed")):
            with pytest.raises(Exception):
                PublisherAnalyticsAgentClient(agent_arn=test_arn)

    @pytest.mark.asyncio
    async def test_cognito_user_context_integration(self, mock_bedrock_client):
        """Test integration with Cognito user context extraction."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        # Simulate Cognito user context from API Gateway
        cognito_context = {
            "cognito_username": "publisher_admin",
            "email": "admin@example.com",
            "publisher_id": "sample_publisher",
            "publisher_name": "Sample Publisher",
            "groups": ["publishers", "admin"],
            "access_level": "admin",
            "user_id": "sample_admin_123",
        }

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            response = await client.chat(
                message="Show me our top performing content this quarter",
                page_context={"page_type": "analytics", "url_path": "/analytics/content"},
                user_context=cognito_context,
                session_id="cognito_test_session",
            )

            # Verify Cognito context was passed correctly
            call_args = mock_bedrock_client.invoke_agent_runtime.call_args
            payload = json.loads(call_args[1]["payload"])

            assert payload["user_context"] == cognito_context
            assert payload["actor_id"] == "sample_admin_123"
            assert payload["session_id"] == "cognito_test_session"

            # Verify response includes user context
            assert response["user_context"] == "sample_publisher"

    @pytest.mark.asyncio
    async def test_page_context_processing(self, mock_bedrock_client):
        """Test page context processing and forwarding."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        complex_page_context = {
            "page_type": "content",
            "url_path": "/content/management",
            "context_elements": [
                {"name": "total_content", "value": "1,247", "description": "Total content items"},
                {"name": "pending_review", "value": "23", "description": "Items pending review"},
                {"name": "top_category", "value": "Technology", "description": "Most popular category"},
            ],
            "current_filters": {"category": "Technology", "date_range": "last_7_days", "status": "published"},
            "user_actions": ["filter_applied", "sort_by_date"],
        }

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            client = PublisherAnalyticsAgentClient(agent_arn=test_arn)

            response = await client.chat(
                message="Help me understand my content performance",
                page_context=complex_page_context,
                session_id="page_context_test",
            )

            # Verify page context was passed correctly
            call_args = mock_bedrock_client.invoke_agent_runtime.call_args
            payload = json.loads(call_args[1]["payload"])

            assert payload["page_context"] == complex_page_context
            assert payload["page_context"]["page_type"] == "content"
            assert len(payload["page_context"]["context_elements"]) == 3
            assert payload["page_context"]["current_filters"]["category"] == "Technology"


class TestBackwardCompatibility:
    """Test backward compatibility with existing code."""

    def test_publisher_analytics_agent_alias(self):
        """Test that PublisherAnalyticsAgent is an alias for the client."""
        from analytics_agent_strands import PublisherAnalyticsAgent, PublisherAnalyticsAgentClient

        assert PublisherAnalyticsAgent == PublisherAnalyticsAgentClient

    @pytest.mark.asyncio
    async def test_existing_interface_compatibility(self, mock_bedrock_client):
        """Test that existing interface still works."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_agent"

        with patch("analytics_agent.boto3.client", return_value=mock_bedrock_client):
            # Test using the old class name
            from analytics_agent_strands import PublisherAnalyticsAgent

            agent = PublisherAnalyticsAgent(agent_arn=test_arn)

            # Test that the interface works
            response = await agent.chat("Test compatibility")

            assert "response" in response
            assert "intent_analysis" in response


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
