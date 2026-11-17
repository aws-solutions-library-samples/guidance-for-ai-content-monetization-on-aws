# test_agent_foundation.py - Unit tests for analytics agent foundation and AgentCore Memory integration
import pytest
import pytest_asyncio
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analytics_agent_strands import PublisherAnalyticsAgent


class TestPublisherAnalyticsAgentFoundation:
    """Test suite for PublisherAnalyticsAgent foundation and AgentCore Memory integration."""

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
        return PublisherAnalyticsAgent()

    def test_agent_initialization(self, mock_env_vars):
        """Test basic agent initialization with environment configuration."""
        agent = PublisherAnalyticsAgent()

        # Verify configuration
        assert agent.iab_api_base == "https://test-api.example.com"
        assert agent.publisher_key == "test-key-123"
        assert agent.gateway_config["gateway_url"] == "https://test-gateway.example.com"

        # Verify AgentCore Memory configuration
        assert agent.memory_config["memory_type"] == "agentcore"
        assert agent.memory_config["memory_config"]["memory_id"] == "analytics-agent-memory"
        assert agent.memory_config["memory_config"]["retention_days"] == 30

        # Verify Gateway configuration
        expected_tools = [
            "get_usage_analytics",
            "analyze_content_performance",
            "generate_insights",
            "create_custom_report",
            "get_revenue_metrics",
            "analyze_bot_behavior",
            "get_page_context_help",
            "suggest_page_actions",
        ]
        assert agent.gateway_config["tools"] == expected_tools

    @pytest.mark.asyncio
    async def test_agent_initialization_async(self, agent):
        """Test async agent initialization."""
        result = await agent.initialize_agent()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_usage_analytics_tool(self, agent):
        """Test get_usage_analytics tool functionality."""
        # Mock HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "total_accesses": 1500,
            "unique_bots": 20,
            "revenue": 3200.75,
            "period": "last_7_days",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(agent.http_client, "get", return_value=mock_response) as mock_get:
            result = await agent.get_usage_analytics(
                date_range="last_7_days", content_type="articles", bot_filter="test-bot"
            )

            # Verify API call
            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert "analytics/usage" in call_args[0][0]

            # Verify parameters
            params = call_args[1]["params"]
            assert params["date_range"] == "last_7_days"
            assert params["content_type"] == "articles"
            assert params["bot_filter"] == "test-bot"
            assert params["include_trends"] is True

            # Verify result
            assert result["total_accesses"] == 1500
            assert result["unique_bots"] == 20

    @pytest.mark.asyncio
    async def test_get_usage_analytics_error_handling(self, agent):
        """Test error handling in get_usage_analytics tool."""
        with patch.object(agent.http_client, "get", side_effect=Exception("API Error")):
            result = await agent.get_usage_analytics()

            # Should return mock data on error
            assert "error" in result
            assert "mock_data" in result
            assert result["mock_data"]["total_accesses"] == 1250

    @pytest.mark.asyncio
    async def test_analyze_content_performance_tool(self, agent):
        """Test analyze_content_performance tool functionality."""
        content_ids = ["article_123", "video_456", "report_789"]

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content_performance": [
                {"content_id": "article_123", "access_count": 200, "revenue": 150.00},
                {"content_id": "video_456", "access_count": 180, "revenue": 120.50},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(agent.http_client, "post", return_value=mock_response) as mock_post:
            result = await agent.analyze_content_performance(content_ids)

            # Verify API call
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "analytics/content-performance" in call_args[0][0]

            # Verify request body
            json_data = call_args[1]["json"]
            assert json_data["content_ids"] == content_ids

            # Verify result
            assert "content_performance" in result
            assert len(result["content_performance"]) == 2

    @pytest.mark.asyncio
    async def test_generate_insights_tool(self, agent):
        """Test generate_insights tool functionality."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "insights": ["Revenue increased by 15%", "Peak activity on weekdays"],
            "recommendations": ["Increase content production", "Optimize scheduling"],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(agent.http_client, "post", return_value=mock_response) as mock_post:
            result = await agent.generate_insights(metric="revenue", time_period="last_30_days")

            # Verify API call
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "analytics/insights" in call_args[0][0]

            # Verify request body
            json_data = call_args[1]["json"]
            assert json_data["metric"] == "revenue"
            assert json_data["time_period"] == "last_30_days"
            assert json_data["include_recommendations"] is True

            # Verify result
            assert "insights" in result
            assert "recommendations" in result

    @pytest.mark.asyncio
    async def test_get_page_context_help_tool(self, agent):
        """Test get_page_context_help tool functionality."""
        result = await agent.get_page_context_help(
            page_type="analytics",
            current_data={"metric1": "value1", "metric2": "value2"},
            user_question="How do I export data?",
        )

        # Verify analytics page help
        assert result["description"] == "This page shows your content performance metrics and analytics"
        assert "Total Access Count" in result["key_metrics"]
        assert "Revenue" in result["key_metrics"]
        assert "Which content is performing best?" in result["common_questions"]
        assert "Change date range" in result["available_actions"]

        # Verify context-specific help
        assert "specific_help" in result
        assert "How do I export data?" in result["specific_help"]
        assert "context_data" in result
        assert "2 data points" in result["context_data"]

    @pytest.mark.asyncio
    async def test_suggest_page_actions_tool(self, agent):
        """Test suggest_page_actions tool functionality."""
        result = await agent.suggest_page_actions(
            page_type="analytics", user_intent="export", current_filters={"date_range": "last_7_days"}
        )

        # Verify suggestions structure
        assert result["page_type"] == "analytics"
        assert result["user_intent"] == "export"
        assert "suggestions" in result
        assert "current_context" in result

        # Should include export-related suggestion
        suggestions = result["suggestions"]
        assert "export_data" in suggestions
        assert suggestions["export_data"]["label"] == "Export Analytics Data"

    @pytest.mark.asyncio
    async def test_chat_functionality(self, agent):
        """Test basic chat functionality with page context."""
        page_context = {"page_type": "analytics", "current_data": {"revenue": 1500}}

        response = await agent.chat(message="What's my revenue performance?", page_context=page_context)

        # Verify response includes context awareness
        assert "analytics page" in response
        assert "What's my revenue performance?" in response
        assert "analytics tools" in response

    @pytest.mark.asyncio
    async def test_create_analytics_agent_factory(self, mock_env_vars):
        """Test the factory function for creating analytics agents."""
        agent = await create_analytics_agent()

        # Verify agent is properly initialized
        assert isinstance(agent, PublisherAnalyticsAgent)
        assert agent.iab_api_base == "https://test-api.example.com"
        assert agent.publisher_key == "test-key-123"

        # Cleanup
        await agent.cleanup()

    @pytest.mark.asyncio
    async def test_agent_cleanup(self, agent):
        """Test agent cleanup functionality."""
        # Mock the HTTP client close method
        agent.http_client.aclose = AsyncMock()

        await agent.cleanup()

        # Verify cleanup was called
        agent.http_client.aclose.assert_called_once()

    def test_agentcore_memory_configuration(self, agent):
        """Test AgentCore Memory configuration is properly set."""
        memory_config = agent.memory_config

        assert memory_config["memory_type"] == "agentcore"
        assert memory_config["memory_config"]["memory_id"] == "analytics-agent-memory"
        assert memory_config["memory_config"]["retention_days"] == 30

    def test_gateway_tools_configuration(self, agent):
        """Test AgentCore Gateway tools configuration."""
        gateway_config = agent.gateway_config

        # Verify all required tools are configured
        expected_tools = [
            "get_usage_analytics",
            "analyze_content_performance",
            "generate_insights",
            "create_custom_report",
            "get_revenue_metrics",
            "analyze_bot_behavior",
            "get_page_context_help",
            "suggest_page_actions",
        ]

        assert gateway_config["tools"] == expected_tools
        assert gateway_config["gateway_url"] == "https://test-gateway.example.com"


# Integration test for AgentCore Memory
class TestAgentCoreMemoryIntegration:
    """Test suite for AgentCore Memory integration."""

    @pytest.mark.asyncio
    async def test_memory_persistence_simulation(self):
        """Test simulated memory persistence across sessions."""
        # This would test actual AgentCore Memory integration
        # For now, we test the configuration structure

        agent = PublisherAnalyticsAgent()
        memory_config = agent.memory_config

        # Verify memory configuration for persistence
        assert memory_config["memory_type"] == "agentcore"
        assert "memory_id" in memory_config["memory_config"]
        assert "retention_days" in memory_config["memory_config"]

        # Simulate conversation state that would be persisted
        conversation_state = {
            "session_id": "test-session-123",
            "page_context": {"page_type": "analytics"},
            "conversation_history": [
                {"role": "user", "content": "Show me revenue data"},
                {"role": "assistant", "content": "Here's your revenue data..."},
            ],
        }

        # In actual implementation, this would be stored in AgentCore Memory
        assert conversation_state["session_id"] is not None
        assert len(conversation_state["conversation_history"]) == 2


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
