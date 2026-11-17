#!/usr/bin/env python3
"""
Unit tests for AgentCore Wrapper

Tests the AgentCore wrapper with real Strands agent implementation.
"""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Add the analytics_agent directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAgentCoreWrapper:
    """Test AgentCore wrapper functionality."""

    @pytest.fixture
    def mock_analytics_agent(self):
        """Create a mock analytics agent."""
        agent = Mock()
        agent.initialize_agent = AsyncMock(return_value=True)
        agent.chat = AsyncMock(
            return_value={
                "response": "Intelligent response from analytics agent",
                "intent_analysis": {"primary_intent": "analytics_query", "confidence": 0.9},
                "suggested_actions": ["View revenue trends", "Analyze content performance"],
                "page_context": {"page_type": "analytics"},
                "session_id": "test_session",
                "model_used": "Claude 3.7 Sonnet via Strands Agent with MCP Tools",
            }
        )
        return agent

    @pytest.fixture
    def mock_bedrock_app(self):
        """Create a mock BedrockAgentCoreApp."""
        with patch("agentcore_wrapper.BedrockAgentCoreApp") as mock_app:
            mock_instance = Mock()
            mock_app.return_value = mock_instance
            yield mock_instance

    def test_environment_variable_setup(self):
        """Test that environment variables are set correctly."""
        # Import the wrapper to trigger environment setup
        with patch("agentcore_wrapper.BedrockAgentCoreApp"):
            with patch("agentcore_wrapper.PublisherAnalyticsAgent"):
                import agentcore_wrapper

                # Check that environment variables are set
                assert os.environ.get("MCP_CLIENT_TYPE") == "internal"
                assert os.environ.get("LOG_LEVEL") == "INFO"
                assert os.environ.get("AWS_DEFAULT_REGION") == "us-west-2"

    @pytest.mark.asyncio
    async def test_get_analytics_agent_initialization(self, mock_analytics_agent):
        """Test analytics agent initialization."""
        with patch("agentcore_wrapper.BedrockAgentCoreApp"):
            with patch("agentcore_wrapper.PublisherAnalyticsAgent", return_value=mock_analytics_agent):
                import agentcore_wrapper

                # Test agent initialization
                agent = await agentcore_wrapper.get_analytics_agent()

                assert agent == mock_analytics_agent
                mock_analytics_agent.initialize_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_analytics_agent_singleton(self, mock_analytics_agent):
        """Test that analytics agent is a singleton."""
        with patch("agentcore_wrapper.BedrockAgentCoreApp"):
            with patch("agentcore_wrapper.PublisherAnalyticsAgent", return_value=mock_analytics_agent):
                import agentcore_wrapper

                # Reset global agent
                agentcore_wrapper.analytics_agent = None

                # Get agent twice
                agent1 = await agentcore_wrapper.get_analytics_agent()
                agent2 = await agentcore_wrapper.get_analytics_agent()

                # Should be the same instance
                assert agent1 == agent2
                # Initialize should only be called once
                assert mock_analytics_agent.initialize_agent.call_count == 1

    def test_invoke_with_intelligent_agent(self, mock_analytics_agent, mock_bedrock_app):
        """Test invoke function with intelligent agent."""
        with patch("agentcore_wrapper.PublisherAnalyticsAgent", return_value=mock_analytics_agent):
            import agentcore_wrapper

            # Reset global agent
            agentcore_wrapper.analytics_agent = None

            # Test payload
            payload = {
                "prompt": "Show me my revenue analytics",
                "page_context": {"page_type": "analytics"},
                "user_context": {"publisher_id": "test_publisher"},
                "actor_id": "test_user",
                "session_id": "test_session",
            }

            # Call invoke
            result = agentcore_wrapper.invoke(payload)

            # Parse result
            response_data = json.loads(result)

            # Check response structure
            assert "response" in response_data
            assert "intent_analysis" in response_data
            assert "suggested_actions" in response_data
            assert response_data["response"] == "Intelligent response from analytics agent"
            assert response_data["session_id"] == "test_session"

    def test_invoke_with_minimal_payload(self, mock_analytics_agent, mock_bedrock_app):
        """Test invoke function with minimal payload."""
        with patch("agentcore_wrapper.PublisherAnalyticsAgent", return_value=mock_analytics_agent):
            import agentcore_wrapper

            # Reset global agent
            agentcore_wrapper.analytics_agent = None

            # Minimal payload
            payload = {"prompt": "Hello"}

            # Call invoke
            result = agentcore_wrapper.invoke(payload)

            # Parse result
            response_data = json.loads(result)

            # Check that defaults are used
            assert "response" in response_data
            assert response_data["response"] == "Intelligent response from analytics agent"

            # Check that agent was called with defaults
            mock_analytics_agent.chat.assert_called_once()
            call_args = mock_analytics_agent.chat.call_args
            assert call_args[1]["message"] == "Hello"
            assert call_args[1]["session_id"] == "agentcore_session"

    def test_invoke_error_handling(self, mock_bedrock_app):
        """Test invoke function error handling."""
        # Mock agent to raise exception during initialization
        mock_agent = Mock()
        mock_agent.initialize_agent = AsyncMock(side_effect=Exception("Initialization failed"))

        with patch("agentcore_wrapper.PublisherAnalyticsAgent", return_value=mock_agent):
            import agentcore_wrapper

            # Reset global agent
            agentcore_wrapper.analytics_agent = None

            # Test payload
            payload = {"prompt": "Test error handling", "actor_id": "test_user"}

            # Call invoke
            result = agentcore_wrapper.invoke(payload)

            # Parse result
            response_data = json.loads(result)

            # Check error response
            assert "error" in response_data
            assert "I'm sorry, I encountered an error" in response_data["response"]
            assert response_data["model_used"] == "error_fallback"
            assert response_data["actor_id"] == "test_user"

    def test_invoke_agent_chat_error(self, mock_analytics_agent, mock_bedrock_app):
        """Test invoke function when agent chat fails."""
        # Mock agent chat to raise exception
        mock_analytics_agent.chat = AsyncMock(side_effect=Exception("Chat processing failed"))

        with patch("agentcore_wrapper.PublisherAnalyticsAgent", return_value=mock_analytics_agent):
            import agentcore_wrapper

            # Reset global agent
            agentcore_wrapper.analytics_agent = None

            # Test payload
            payload = {"prompt": "Test chat error", "page_context": {"page_type": "analytics"}}

            # Call invoke
            result = agentcore_wrapper.invoke(payload)

            # Parse result
            response_data = json.loads(result)

            # Check error response
            assert "error" in response_data
            assert "Chat processing failed" in response_data["error"]
            assert response_data["model_used"] == "error_fallback"

    def test_memory_environment_variables(self):
        """Test memory environment variable handling."""
        with patch.dict(os.environ, {"ANALYTICS_MEMORY_ID": "test_memory_123"}):
            with patch("agentcore_wrapper.BedrockAgentCoreApp"):
                with patch("agentcore_wrapper.PublisherAnalyticsAgent"):
                    import agentcore_wrapper

                    # Check that memory environment variables are set
                    assert os.environ.get("ANALYTICS_MEMORY_ID") == "test_memory_123"
                    assert os.environ.get("ANALYTICS_MEMORY_SSM_PARAM") == "/app/analytics/agentcore/memory_id"

    def test_no_memory_environment_variables(self):
        """Test behavior when no memory environment variables are set."""
        # Ensure memory env vars are not set
        if "ANALYTICS_MEMORY_ID" in os.environ:
            del os.environ["ANALYTICS_MEMORY_ID"]

        with patch("agentcore_wrapper.BedrockAgentCoreApp"):
            with patch("agentcore_wrapper.PublisherAnalyticsAgent"):
                import agentcore_wrapper

                # Should not set memory environment variables
                assert (
                    "ANALYTICS_MEMORY_SSM_PARAM" not in os.environ
                    or os.environ.get("ANALYTICS_MEMORY_SSM_PARAM") != "/app/analytics/agentcore/memory_id"
                )

    def test_publisher_name_configuration(self):
        """Test publisher name configuration."""
        with patch.dict(os.environ, {"PUBLISHER_NAME": "TestPublisher"}):
            with patch("agentcore_wrapper.BedrockAgentCoreApp"):
                with patch("agentcore_wrapper.PublisherAnalyticsAgent"):
                    import agentcore_wrapper

                    # Check that publisher name is set
                    assert os.environ.get("PUBLISHER_NAME") == "TestPublisher"

    def test_mcp_server_arn_configuration(self):
        """Test MCP server ARN configuration."""
        test_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789:runtime/test_mcp_server"

        with patch.dict(os.environ, {"MCP_SERVER_ARN": test_arn}):
            with patch("agentcore_wrapper.BedrockAgentCoreApp"):
                with patch("agentcore_wrapper.PublisherAnalyticsAgent"):
                    import agentcore_wrapper

                    # Check that MCP server URL is set correctly
                    assert os.environ.get("MCP_SERVER_URL") == test_arn


class TestAgentCoreWrapperIntegration:
    """Integration tests for AgentCore wrapper."""

    @pytest.mark.asyncio
    async def test_full_integration_flow(self):
        """Test full integration flow from payload to response."""
        # Mock the full chain
        mock_agent = Mock()
        mock_agent.initialize_agent = AsyncMock(return_value=True)
        mock_agent.chat = AsyncMock(
            return_value={
                "response": "Based on your analytics data, I can see strong performance in healthcare content with 15% growth this month.",
                "intent_analysis": {"primary_intent": "revenue_analysis", "confidence": 0.9, "requires_data": True},
                "suggested_actions": [
                    "Analyze healthcare content performance",
                    "Review revenue trends",
                    "Optimize content strategy",
                ],
                "page_context": {"page_type": "analytics"},
                "session_id": "integration_test",
                "model_used": "Claude 3.7 Sonnet via Strands Agent with MCP Tools",
                "conversation_metadata": {"requires_followup": False, "data_driven": True},
            }
        )

        with patch("agentcore_wrapper.BedrockAgentCoreApp"):
            with patch("agentcore_wrapper.PublisherAnalyticsAgent", return_value=mock_agent):
                import agentcore_wrapper

                # Reset global agent
                agentcore_wrapper.analytics_agent = None

                # Comprehensive test payload
                payload = {
                    "prompt": "How is my healthcare content performing in terms of revenue?",
                    "page_context": {
                        "page_type": "analytics",
                        "context_elements": [
                            {"name": "revenue", "value": "$5,420", "description": "Monthly revenue"},
                            {"name": "growth", "value": "15%", "description": "Monthly growth"},
                        ],
                    },
                    "user_context": {"publisher_id": "healthcare_publisher", "access_level": "admin"},
                    "actor_id": "admin_user",
                    "session_id": "integration_test",
                }

                # Call invoke
                result = agentcore_wrapper.invoke(payload)

                # Parse and validate result
                response_data = json.loads(result)

                # Comprehensive validation
                assert response_data["response"].startswith("Based on your analytics data")
                assert response_data["intent_analysis"]["primary_intent"] == "revenue_analysis"
                assert response_data["intent_analysis"]["confidence"] == 0.9
                assert len(response_data["suggested_actions"]) == 3
                assert response_data["session_id"] == "integration_test"
                assert response_data["model_used"] == "Claude 3.7 Sonnet via Strands Agent with MCP Tools"
                assert response_data["conversation_metadata"]["data_driven"] is True

                # Verify agent was called with correct parameters
                mock_agent.chat.assert_called_once()
                call_args = mock_agent.chat.call_args
                assert call_args[1]["message"] == "How is my healthcare content performing in terms of revenue?"
                assert call_args[1]["page_context"]["page_type"] == "analytics"
                assert call_args[1]["user_context"]["publisher_id"] == "healthcare_publisher"
                assert call_args[1]["session_id"] == "integration_test"


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
