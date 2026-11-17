"""
Test Analytics Agent MCP Client Integration

This test file verifies that the analytics agent can switch between mock and real MCP client
implementations based on environment variables, as specified in the requirements.

Tests both USE_MOCK_ANALYTICS and DEMO_MODE environment variable configurations.
"""

import pytest
import os
import json
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

# Import the analytics agent and MCP client
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "agent"))

from analytics_agent_strands import PublisherAnalyticsAgent
from agentcore_mcp_client import AgentCoreMCPClient


class TestMCPClientIntegration:
    """Test suite for MCP client integration functionality."""

    def setup_method(self):
        """Setup for each test method."""
        # Clear environment variables before each test
        for var in ["USE_MOCK_ANALYTICS", "DEMO_MODE", "MCP_SERVER_URL"]:
            if var in os.environ:
                del os.environ[var]

    def teardown_method(self):
        """Cleanup after each test method."""
        # Clear environment variables after each test
        for var in ["USE_MOCK_ANALYTICS", "DEMO_MODE", "MCP_SERVER_URL"]:
            if var in os.environ:
                del os.environ[var]

    @pytest.mark.asyncio
    async def test_analytics_agent_mock_mode_enabled(self):
        """Test analytics agent uses mock tools when USE_MOCK_ANALYTICS=true."""
        os.environ["USE_MOCK_ANALYTICS"] = "true"

        agent = PublisherAnalyticsAgent(
            mcp_server_url="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server",
            actor_id="test_user",
            session_id="test_session",
        )

        # Should not have MCP client in mock mode
        assert agent.mcp_client is None

        # Test that tools are mock versions
        tool_names = [tool.__name__ for tool in agent.tools]
        assert "get_usage_analytics_mock" in tool_names
        assert "get_revenue_metrics_mock" in tool_names
        assert "analyze_bot_behavior_mock" in tool_names
        assert "analyze_content_performance_mock" in tool_names

    @pytest.mark.asyncio
    async def test_analytics_agent_demo_mode_enabled(self):
        """Test analytics agent uses mock tools when DEMO_MODE=true."""
        os.environ["DEMO_MODE"] = "true"

        agent = PublisherAnalyticsAgent(
            mcp_server_url="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server",
            actor_id="test_user",
            session_id="test_session",
        )

        # Should not have MCP client in demo mode
        assert agent.mcp_client is None

        # Test that tools are mock versions
        tool_names = [tool.__name__ for tool in agent.tools]
        assert "get_usage_analytics_mock" in tool_names
        assert "get_revenue_metrics_mock" in tool_names
        assert "analyze_bot_behavior_mock" in tool_names
        assert "analyze_content_performance_mock" in tool_names

    @pytest.mark.asyncio
    @patch("analytics_agent_strands.AGENTCORE_MCP_AVAILABLE", True)
    @patch("analytics_agent_strands.AgentCoreMCPClient")
    async def test_analytics_agent_mcp_mode_enabled(self, mock_mcp_client_class):
        """Test analytics agent uses real MCP client when mock mode is disabled."""
        # Setup mock MCP client
        mock_mcp_client = AsyncMock()
        mock_mcp_client_class.return_value = mock_mcp_client

        # Ensure mock mode is disabled
        os.environ["USE_MOCK_ANALYTICS"] = "false"
        os.environ["DEMO_MODE"] = "false"

        agent = PublisherAnalyticsAgent(
            mcp_server_url="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server",
            actor_id="test_user",
            session_id="test_session",
        )

        # Should have MCP client in real mode
        assert agent.mcp_client is not None

        # Verify MCP client was created with correct ARN
        mock_mcp_client_class.assert_called_once_with(
            server_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server"
        )

        # Test that tools are real MCP versions
        tool_names = [tool.__name__ for tool in agent.tools]
        assert "get_usage_analytics" in tool_names
        assert "get_revenue_metrics" in tool_names
        assert "analyze_bot_behavior" in tool_names
        assert "analyze_content_performance" in tool_names

    @pytest.mark.asyncio
    async def test_analytics_agent_no_mcp_server_url_fallback(self):
        """Test analytics agent falls back to mock mode when no MCP server URL is provided."""
        os.environ["USE_MOCK_ANALYTICS"] = "false"
        os.environ["DEMO_MODE"] = "false"

        agent = PublisherAnalyticsAgent(
            mcp_server_url=None,  # No MCP server URL
            actor_id="test_user",
            session_id="test_session",
        )

        # Should fall back to mock mode
        assert agent.mcp_client is None

        # Test that tools are mock versions
        tool_names = [tool.__name__ for tool in agent.tools]
        assert "get_usage_analytics_mock" in tool_names
        assert "get_revenue_metrics_mock" in tool_names
        assert "analyze_bot_behavior_mock" in tool_names
        assert "analyze_content_performance_mock" in tool_names

    @pytest.mark.asyncio
    @patch("analytics_agent_strands.AGENTCORE_MCP_AVAILABLE", True)
    @patch("analytics_agent_strands.AgentCoreMCPClient")
    async def test_mcp_client_initialization_failure_fallback(self, mock_mcp_client_class):
        """Test analytics agent falls back to mock mode when MCP client initialization fails."""
        # Make MCP client initialization fail
        mock_mcp_client_class.side_effect = Exception("MCP client initialization failed")

        os.environ["USE_MOCK_ANALYTICS"] = "false"
        os.environ["DEMO_MODE"] = "false"

        agent = PublisherAnalyticsAgent(
            mcp_server_url="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server",
            actor_id="test_user",
            session_id="test_session",
        )

        # Should fall back to mock mode due to initialization failure
        assert agent.mcp_client is None

        # Test that tools are mock versions
        tool_names = [tool.__name__ for tool in agent.tools]
        assert "get_usage_analytics_mock" in tool_names
        assert "get_revenue_metrics_mock" in tool_names
        assert "analyze_bot_behavior_mock" in tool_names
        assert "analyze_content_performance_mock" in tool_names

    def test_environment_variable_case_insensitive(self):
        """Test that environment variables are case insensitive."""
        test_cases = ["true", "TRUE", "True", "tRuE"]

        for case in test_cases:
            # Clean up from previous iteration
            for var in ["USE_MOCK_ANALYTICS", "DEMO_MODE"]:
                if var in os.environ:
                    del os.environ[var]

            os.environ["USE_MOCK_ANALYTICS"] = case

            agent = PublisherAnalyticsAgent(
                mcp_server_url="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server",
                actor_id="test_user",
                session_id="test_session",
            )

            # Should use mock mode regardless of case
            assert agent.mcp_client is None

            # Test that tools are mock versions
            tool_names = [tool.__name__ for tool in agent.tools]
            assert "get_usage_analytics_mock" in tool_names

    @pytest.mark.asyncio
    async def test_mock_analytics_disabled_by_default(self):
        """Test that mock analytics is disabled by default when no environment variables are set."""
        # Ensure no mock environment variables are set
        for var in ["USE_MOCK_ANALYTICS", "DEMO_MODE"]:
            if var in os.environ:
                del os.environ[var]

        # Mock the MCP client to avoid actual connections
        with (
            patch("analytics_agent_strands.AGENTCORE_MCP_AVAILABLE", True),
            patch("analytics_agent_strands.AgentCoreMCPClient") as mock_mcp_client_class,
        ):
            mock_mcp_client = AsyncMock()
            mock_mcp_client_class.return_value = mock_mcp_client

            agent = PublisherAnalyticsAgent(
                mcp_server_url="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server",
                actor_id="test_user",
                session_id="test_session",
            )

            # Should use real MCP client by default
            assert agent.mcp_client is not None

            # Verify MCP client was created
            mock_mcp_client_class.assert_called_once()

            # Test that tools are real MCP versions
            tool_names = [tool.__name__ for tool in agent.tools]
            assert "get_usage_analytics" in tool_names
            assert "get_revenue_metrics" in tool_names
            assert "analyze_bot_behavior" in tool_names
            assert "analyze_content_performance" in tool_names


class TestAgentCoreMCPClient:
    """Test suite for AgentCoreMCPClient functionality."""

    def setup_method(self):
        """Setup for each test method."""
        # Clear environment variables before each test
        for var in ["AGENTCORE_AUTH_TOKEN", "AGENTCORE_TOKEN_SECRET_NAME"]:
            if var in os.environ:
                del os.environ[var]

    def teardown_method(self):
        """Cleanup after each test method."""
        # Clear environment variables after each test
        for var in ["AGENTCORE_AUTH_TOKEN", "AGENTCORE_TOKEN_SECRET_NAME"]:
            if var in os.environ:
                del os.environ[var]

    def test_mcp_client_initialization(self):
        """Test MCP client initialization with server ARN."""
        server_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server"

        client = AgentCoreMCPClient(server_arn=server_arn)

        assert client.server_arn == server_arn
        assert client.client_type == "internal"

    def test_mcp_client_initialization_from_env(self):
        """Test MCP client initialization from environment variable."""
        server_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/env-server"
        os.environ["MCP_SERVER_URL"] = server_arn

        client = AgentCoreMCPClient()

        assert client.server_arn == server_arn

    def test_mcp_client_initialization_no_arn_raises_error(self):
        """Test MCP client raises error when no ARN is provided."""
        # Clear environment variable
        if "MCP_SERVER_URL" in os.environ:
            del os.environ["MCP_SERVER_URL"]

        with pytest.raises(ValueError, match="MCP server ARN is required"):
            AgentCoreMCPClient()

    @pytest.mark.asyncio
    async def test_bearer_token_from_environment(self):
        """Test bearer token retrieval from environment variable."""
        test_token = "test-bearer-token-123"
        os.environ["AGENTCORE_AUTH_TOKEN"] = test_token

        client = AgentCoreMCPClient(server_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server")

        token = await client._get_bearer_token()
        assert token == test_token

    @pytest.mark.asyncio
    async def test_bearer_token_from_secrets_manager(self):
        """Test bearer token retrieval from Secrets Manager."""
        with patch("boto3.Session") as mock_session:
            # Setup mock Secrets Manager response
            mock_client = MagicMock()
            mock_session.return_value.client.return_value = mock_client
            mock_client.get_secret_value.return_value = {"SecretString": '{"token": "secret-bearer-token-456"}'}

            client = AgentCoreMCPClient(
                server_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server"
            )

            token = await client._get_bearer_token()
            assert token == "secret-bearer-token-456"

            # Verify Secrets Manager was called
            mock_client.get_secret_value.assert_called_once_with(SecretId="agentcore-auth-token")

    @pytest.mark.asyncio
    async def test_bearer_token_from_secrets_manager_plain_text(self):
        """Test bearer token retrieval from Secrets Manager with plain text secret."""
        with patch("boto3.Session") as mock_session:
            # Setup mock Secrets Manager response with plain text
            mock_client = MagicMock()
            mock_session.return_value.client.return_value = mock_client
            mock_client.get_secret_value.return_value = {"SecretString": "plain-text-token-789"}

            client = AgentCoreMCPClient(
                server_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server"
            )

            token = await client._get_bearer_token()
            assert token == "plain-text-token-789"

    @pytest.mark.asyncio
    async def test_bearer_token_fallback_to_placeholder(self):
        """Test bearer token falls back to placeholder when no token is available."""
        with patch("boto3.Session") as mock_session:
            # Mock Secrets Manager to raise an exception (simulating failure)
            mock_client = MagicMock()
            mock_session.return_value.client.return_value = mock_client
            mock_client.get_secret_value.side_effect = Exception("Secrets Manager unavailable")

            client = AgentCoreMCPClient(
                server_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server"
            )

            token = await client._get_bearer_token()
            assert token == "placeholder"

    @pytest.mark.asyncio
    @patch("agentcore_mcp_client.MCP_AVAILABLE", False)
    async def test_mcp_client_fallback_when_mcp_unavailable(self):
        """Test MCP client falls back to mock data when MCP libraries are unavailable."""
        client = AgentCoreMCPClient(server_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server")

        result = await client.call_tool("get_usage_analytics", date_range="last_7_days")

        # Should return mock data
        assert result["success"] is True
        assert "data" in result
        assert "MOCK DATA - AgentCore unavailable" in str(result["data"])

    @pytest.mark.asyncio
    async def test_mcp_client_health_check_mock_mode(self):
        """Test health check in mock mode."""
        with patch("agentcore_mcp_client.MCP_AVAILABLE", False):
            client = AgentCoreMCPClient(
                server_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server"
            )

            health = await client.health_check()

            assert health["status"] == "healthy"
            assert "Using mock data for local testing" in health["note"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
