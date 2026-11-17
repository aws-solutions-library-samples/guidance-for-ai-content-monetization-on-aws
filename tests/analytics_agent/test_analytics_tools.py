# test_analytics_tools.py - Unit tests for analytics tools with Gateway integration
import pytest
import pytest_asyncio
import os
from unittest.mock import MagicMock, patch, AsyncMock
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analytics_agent_strands import PublisherAnalyticsAgent


class TestAnalyticsToolsWithGateway:
    """Test suite for analytics tools with Gateway integration."""

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables for testing."""
        with patch.dict(
            os.environ,
            {
                "IAB_API_BASE_URL": "https://test-api.example.com",
                "PUBLISHER_API_KEY": "test-key-123",
                "AGENTCORE_GATEWAY_URL": "https://test-gateway.example.com",
            },
        ):
            yield

    @pytest.fixture
    def agent(self, mock_env_vars):
        """Create a test agent instance."""
        with patch("analytics_agent.gateway_config.create_gateway_config") as mock_gateway:
            mock_gateway_manager = MagicMock()
            mock_gateway_manager.get_analytics_agent_tools_config.return_value = {
                "get_usage_analytics": {
                    "description": "Test tool",
                    "requires_user_context": True,
                    "access_level": "publisher",
                }
            }
            mock_gateway_manager.validate_gateway_config.return_value = {"valid": True, "errors": []}
            mock_gateway_manager.gateway_config = {"gateway_url": "https://test-gateway.example.com"}
            mock_gateway.return_value = mock_gateway_manager

            return PublisherAnalyticsAgent()

    @pytest.mark.asyncio
    async def test_get_usage_analytics_with_user_context(self, agent):
        """Test get_usage_analytics tool with user context from Gateway."""
        user_context = {"publisher_id": "test-publisher-123", "access_level": "admin", "user_id": "test-user"}

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "total_accesses": 2500,
            "unique_bots": 25,
            "revenue": 4200.75,
            "period": "last_7_days",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(agent.http_client, "get", return_value=mock_response) as mock_get:
            result = await agent.get_usage_analytics(
                date_range="last_7_days", content_type="articles", user_context=user_context
            )

            # Verify API call includes user context
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            params = call_args[1]["params"]

            assert params["publisher_id"] == "test-publisher-123"
            assert params["user_access_level"] == "admin"
            assert params["date_range"] == "last_7_days"
            assert params["content_type"] == "articles"

            # Verify result includes user context
            assert "user_context" in result
            assert result["user_context"]["publisher_id"] == "test-publisher-123"
            assert result["user_context"]["access_level"] == "admin"

    @pytest.mark.asyncio
    async def test_analyze_content_performance_with_user_context(self, agent):
        """Test analyze_content_performance tool with user context."""
        user_context = {"publisher_id": "test-publisher-456", "access_level": "editor", "user_id": "test-user-2"}

        content_ids = ["article_123", "video_456", "report_789"]

        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content_performance": [
                {"content_id": "article_123", "access_count": 300, "revenue": 200.00},
                {"content_id": "video_456", "access_count": 250, "revenue": 180.50},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(agent.http_client, "post", return_value=mock_response) as mock_post:
            result = await agent.analyze_content_performance(content_ids=content_ids, user_context=user_context)

            # Verify API call includes user context
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            json_data = call_args[1]["json"]

            assert json_data["content_ids"] == content_ids
            assert json_data["publisher_id"] == "test-publisher-456"
            assert json_data["user_access_level"] == "editor"

            # Verify result includes user context
            assert "user_context" in result
            assert result["user_context"]["publisher_id"] == "test-publisher-456"
            assert result["user_context"]["requested_content_count"] == 3

    @pytest.mark.asyncio
    async def test_execute_tool_via_gateway(self, agent):
        """Test executing tools through Gateway interface."""
        user_context = {"publisher_id": "gateway-publisher", "access_level": "admin"}

        parameters = {"date_range": "last_30_days", "content_type": "videos"}

        # Mock the tool execution
        with patch.object(agent, "get_usage_analytics", new_callable=AsyncMock) as mock_tool:
            mock_tool.return_value = {"success": True, "data": "test_data"}

            result = await agent.execute_tool_via_gateway(
                tool_name="get_usage_analytics", parameters=parameters, user_context=user_context
            )

            # Verify tool was called with user context
            mock_tool.assert_called_once()
            call_args = mock_tool.call_args[1]
            assert call_args["date_range"] == "last_30_days"
            assert call_args["content_type"] == "videos"
            assert call_args["user_context"] == user_context

            # Verify result
            assert result["success"] is True
            assert result["data"] == "test_data"

    @pytest.mark.asyncio
    async def test_execute_tool_via_gateway_missing_user_context(self, agent):
        """Test executing tool that requires user context without providing it."""
        parameters = {"date_range": "last_7_days"}

        result = await agent.execute_tool_via_gateway(
            tool_name="get_usage_analytics", parameters=parameters, user_context=None
        )

        # Should return error for missing user context
        assert "error" in result
        assert "requires user context" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_via_gateway_invalid_tool(self, agent):
        """Test executing non-existent tool through Gateway."""
        result = await agent.execute_tool_via_gateway(
            tool_name="non_existent_tool", parameters={}, user_context={"publisher_id": "test"}
        )

        # Should return error for invalid tool
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_chat_with_user_context(self, agent):
        """Test chat functionality with user context from Gateway."""
        page_context = {"page_type": "analytics", "current_data": {"revenue": 2500}}

        user_context = {"publisher_id": "chat-publisher", "user_id": "chat-user", "access_level": "viewer"}

        response = await agent.chat(
            message="Show me my revenue trends", page_context=page_context, user_context=user_context
        )

        # Verify response includes both page and user context
        assert "Show me my revenue trends" in response
        assert "analytics page" in response
        assert "chat-publisher" in response

    def test_get_gateway_tools_metadata(self, agent):
        """Test getting Gateway tools metadata."""
        metadata = agent.get_gateway_tools_metadata()

        # Verify metadata structure
        assert "total_tools" in metadata
        assert "tools" in metadata
        assert "gateway_config" in metadata

        # Verify tools information
        assert metadata["total_tools"] == 1  # From mock
        assert "get_usage_analytics" in metadata["tools"]

        tool_info = metadata["tools"]["get_usage_analytics"]
        assert tool_info["requires_user_context"] is True
        assert tool_info["access_level"] == "publisher"

    @pytest.mark.asyncio
    async def test_register_tools_with_gateway(self, agent):
        """Test registering tools with AgentCore Gateway."""
        result = await agent._register_tools_with_gateway()

        # Should succeed with mock configuration
        assert result is True

        # Verify gateway manager was called
        agent.gateway_manager.get_analytics_agent_tools_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_agent_initialization_with_gateway(self, agent):
        """Test agent initialization with Gateway integration."""
        result = await agent.initialize_agent()

        # Should succeed
        assert result is True

        # Verify Gateway validation was called
        agent.gateway_manager.validate_gateway_config.assert_called_once()


class TestAnalyticsToolsErrorHandling:
    """Test suite for analytics tools error handling."""

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables for testing."""
        with patch.dict(
            os.environ,
            {
                "IAB_API_BASE_URL": "https://test-api.example.com",
                "PUBLISHER_API_KEY": "test-key-123",
                "AGENTCORE_GATEWAY_URL": "https://test-gateway.example.com",
            },
        ):
            yield

    @pytest.fixture
    def agent(self, mock_env_vars):
        """Create a test agent instance."""
        with patch("analytics_agent.gateway_config.create_gateway_config") as mock_gateway:
            mock_gateway_manager = MagicMock()
            mock_gateway_manager.get_analytics_agent_tools_config.return_value = {
                "get_usage_analytics": {"description": "Test tool", "requires_user_context": True}
            }
            mock_gateway_manager.validate_gateway_config.return_value = {"valid": True, "errors": []}
            mock_gateway.return_value = mock_gateway_manager

            return PublisherAnalyticsAgent()

    @pytest.mark.asyncio
    async def test_get_usage_analytics_api_error_with_user_context(self, agent):
        """Test get_usage_analytics error handling with user context."""
        user_context = {"publisher_id": "error-test"}

        with patch.object(agent.http_client, "get", side_effect=Exception("API Error")):
            result = await agent.get_usage_analytics(user_context=user_context)

            # Should return mock data on error
            assert "error" in result
            assert "mock_data" in result
            assert result["mock_data"]["total_accesses"] == 1250

    @pytest.mark.asyncio
    async def test_execute_tool_via_gateway_exception(self, agent):
        """Test Gateway tool execution with exception."""
        with patch.object(agent, "get_usage_analytics", side_effect=Exception("Tool Error")):
            result = await agent.execute_tool_via_gateway(
                tool_name="get_usage_analytics",
                parameters={"date_range": "last_7_days"},
                user_context={"publisher_id": "test"},
            )

            # Should return error
            assert "error" in result
            assert "Tool Error" in result["error"]


class TestGatewayIntegrationMockResponses:
    """Test suite for Gateway integration with mock IAB API responses."""

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables for testing."""
        with patch.dict(
            os.environ,
            {
                "IAB_API_BASE_URL": "https://mock-iab-api.example.com",
                "PUBLISHER_API_KEY": "mock-key-123",
                "AGENTCORE_GATEWAY_URL": "https://mock-gateway.example.com",
            },
        ):
            yield

    @pytest.fixture
    def agent_with_mock_gateway(self, mock_env_vars):
        """Create agent with comprehensive Gateway mock."""
        with patch("analytics_agent.gateway_config.create_gateway_config") as mock_gateway:
            mock_gateway_manager = MagicMock()

            # Mock complete tools configuration
            mock_gateway_manager.get_analytics_agent_tools_config.return_value = {
                "get_usage_analytics": {
                    "description": "Retrieve content usage analytics data",
                    "requires_user_context": True,
                    "access_level": "publisher",
                    "iab_api_endpoint": "/analytics/usage",
                },
                "analyze_content_performance": {
                    "description": "Analyze performance metrics for specific content items",
                    "requires_user_context": True,
                    "access_level": "publisher",
                    "iab_api_endpoint": "/analytics/content-performance",
                },
                "get_page_context_help": {
                    "description": "Provide contextual help based on current page",
                    "requires_user_context": False,
                    "access_level": "user",
                    "local_processing": True,
                },
            }

            mock_gateway_manager.validate_gateway_config.return_value = {"valid": True, "errors": [], "warnings": []}

            mock_gateway_manager.gateway_config = {
                "gateway_url": "https://mock-gateway.example.com",
                "authentication": {"type": "cognito"},
                "tools": {"registration_mode": "automatic"},
            }

            mock_gateway.return_value = mock_gateway_manager

            return PublisherAnalyticsAgent()

    @pytest.mark.asyncio
    async def test_comprehensive_gateway_integration(self, agent_with_mock_gateway):
        """Test comprehensive Gateway integration with all tools."""
        agent = agent_with_mock_gateway

        # Test initialization
        init_result = await agent.initialize_agent()
        assert init_result is True

        # Test tools metadata
        metadata = agent.get_gateway_tools_metadata()
        assert metadata["total_tools"] == 3
        assert "get_usage_analytics" in metadata["tools"]
        assert "analyze_content_performance" in metadata["tools"]
        assert "get_page_context_help" in metadata["tools"]

        # Test tool execution with different user context requirements
        user_context = {"publisher_id": "comprehensive-test", "access_level": "admin"}

        # Test tool that requires user context
        with patch.object(agent, "get_usage_analytics", new_callable=AsyncMock) as mock_tool:
            mock_tool.return_value = {"success": True}

            result = await agent.execute_tool_via_gateway(
                "get_usage_analytics", {"date_range": "last_7_days"}, user_context
            )
            assert result["success"] is True

        # Test tool that doesn't require user context
        with patch.object(agent, "get_page_context_help", new_callable=AsyncMock) as mock_help_tool:
            mock_help_tool.return_value = {"help": "context help"}

            result = await agent.execute_tool_via_gateway("get_page_context_help", {"page_type": "analytics"}, None)
            # Should not error for missing user context
            assert "error" not in result or "requires user context" not in result.get("error", "")


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
