#!/usr/bin/env python3
"""
Unit tests for Analytics Agent Memory Integration

Tests the AgentCore Memory integration for conversation persistence and context retrieval.
"""

import asyncio
import os
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Add the analytics_agent directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analytics_agent_strands import AnalyticsMemoryHooks, PublisherAnalyticsAgent, create_or_get_analytics_memory


class TestMemoryIntegration:
    """Test AgentCore Memory integration."""

    @pytest.fixture
    def mock_memory_client(self):
        """Create a mock memory client."""
        client = Mock()
        client.gmcp_client = Mock()
        client.gmcp_client.get_memory = Mock(return_value={"id": "test_memory_id"})
        client.get_memory_strategies = Mock(
            return_value=[
                {"type": "user_preference", "namespaces": ["analytics/publisher/{actorId}/preferences"]},
                {"type": "semantic", "namespaces": ["analytics/publisher/{actorId}/semantic"]},
            ]
        )
        client.retrieve_memories = Mock(
            return_value=[{"content": {"text": "User prefers revenue analytics over content metrics"}}]
        )
        client.create_event = Mock()
        client.create_memory_and_wait = Mock(return_value={"id": "new_memory_id"})
        return client

    @pytest.fixture
    def mock_mcp_client(self):
        """Create a mock MCP client."""
        client = Mock()
        client.server_url = "http://localhost:8000"
        client.health_check = AsyncMock(return_value={"status": "healthy"})
        client.close = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_memory_hooks_initialization(self, mock_memory_client):
        """Test memory hooks initialization."""
        memory_hooks = AnalyticsMemoryHooks(
            memory_id="test_memory_id", client=mock_memory_client, actor_id="test_actor", session_id="test_session"
        )

        assert memory_hooks.memory_id == "test_memory_id"
        assert memory_hooks.actor_id == "test_actor"
        assert memory_hooks.session_id == "test_session"
        assert memory_hooks.client == mock_memory_client

        # Check that namespaces were set up
        mock_memory_client.get_memory_strategies.assert_called_once_with("test_memory_id")
        assert "user_preference" in memory_hooks.namespaces
        assert "semantic" in memory_hooks.namespaces

    @pytest.mark.asyncio
    async def test_memory_hooks_without_agentcore(self):
        """Test memory hooks when AgentCore is not available."""
        with patch("analytics_agent_strands.AGENTCORE_MEMORY_AVAILABLE", False):
            memory_hooks = AnalyticsMemoryHooks(
                memory_id="test_memory_id", client=None, actor_id="test_actor", session_id="test_session"
            )

            # Should initialize but with empty namespaces
            assert memory_hooks.namespaces == {}

    @pytest.mark.asyncio
    async def test_retrieve_analytics_context(self, mock_memory_client):
        """Test retrieving analytics context from memory."""
        memory_hooks = AnalyticsMemoryHooks(
            memory_id="test_memory_id", client=mock_memory_client, actor_id="test_actor", session_id="test_session"
        )

        # Mock event with user message
        mock_event = Mock()
        mock_agent = Mock()
        mock_agent.messages = [{"role": "user", "content": [{"text": "Show me revenue analytics"}]}]
        mock_event.agent = mock_agent

        # Test context retrieval
        memory_hooks.retrieve_analytics_context(mock_event)

        # Check that memories were retrieved
        assert mock_memory_client.retrieve_memories.call_count == 2  # One for each namespace

        # Check that context was injected into the message
        updated_message = mock_agent.messages[0]["content"][0]["text"]
        assert "Analytics Context:" in updated_message
        assert "Show me revenue analytics" in updated_message

    @pytest.mark.asyncio
    async def test_save_analytics_interaction(self, mock_memory_client):
        """Test saving analytics interaction to memory."""
        memory_hooks = AnalyticsMemoryHooks(
            memory_id="test_memory_id", client=mock_memory_client, actor_id="test_actor", session_id="test_session"
        )

        # Mock event with conversation
        mock_event = Mock()
        mock_agent = Mock()
        mock_agent.messages = [
            {"role": "user", "content": [{"text": "Show me revenue analytics"}]},
            {"role": "assistant", "content": [{"text": "Here's your revenue data: $5,420 this month"}]},
        ]
        mock_event.agent = mock_agent

        # Test interaction saving
        memory_hooks.save_analytics_interaction(mock_event)

        # Check that event was created
        mock_memory_client.create_event.assert_called_once_with(
            memory_id="test_memory_id",
            actor_id="test_actor",
            session_id="test_session",
            messages=[
                ("Show me revenue analytics", "USER"),
                ("Here's your revenue data: $5,420 this month", "ASSISTANT"),
            ],
        )

    @pytest.mark.asyncio
    async def test_create_or_get_analytics_memory_existing(self, mock_memory_client):
        """Test getting existing analytics memory."""
        with patch("analytics_agent_strands.AGENTCORE_MEMORY_AVAILABLE", True):
            with patch("analytics_agent_strands.MemoryClient", return_value=mock_memory_client):
                with patch.dict(os.environ, {"ANALYTICS_MEMORY_ID": "existing_memory_id"}):
                    client, memory_id = create_or_get_analytics_memory()

                    assert client == mock_memory_client
                    assert memory_id == "existing_memory_id"
                    mock_memory_client.gmcp_client.get_memory.assert_called_once_with(memoryId="existing_memory_id")

    @pytest.mark.asyncio
    async def test_create_or_get_analytics_memory_new(self, mock_memory_client):
        """Test creating new analytics memory."""
        with patch("analytics_agent_strands.AGENTCORE_MEMORY_AVAILABLE", True):
            with patch("analytics_agent_strands.MemoryClient", return_value=mock_memory_client):
                with patch.dict(os.environ, {}, clear=True):
                    client, memory_id = create_or_get_analytics_memory()

                    assert client == mock_memory_client
                    assert memory_id == "new_memory_id"
                    mock_memory_client.create_memory_and_wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_or_get_analytics_memory_unavailable(self):
        """Test memory creation when AgentCore is unavailable."""
        with patch("analytics_agent_strands.AGENTCORE_MEMORY_AVAILABLE", False):
            client, memory_id = create_or_get_analytics_memory()

            assert client is None
            assert memory_id is None

    @pytest.mark.asyncio
    async def test_agent_with_memory_integration(self, mock_memory_client, mock_mcp_client):
        """Test agent initialization with memory integration."""
        with patch("analytics_agent_strands.AGENTCORE_MEMORY_AVAILABLE", True):
            with patch(
                "analytics_agent_strands.create_or_get_analytics_memory",
                return_value=(mock_memory_client, "test_memory_id"),
            ):
                with patch("analytics_agent_strands.MCPClient", return_value=mock_mcp_client):
                    with patch("analytics_agent_strands.BedrockModel"), patch("analytics_agent_strands.Agent"):
                        agent = PublisherAnalyticsAgent(
                            mcp_server_url="http://localhost:8000", actor_id="test_actor", session_id="test_session"
                        )

                        # Check memory integration
                        assert agent.memory_client == mock_memory_client
                        assert agent.memory_id == "test_memory_id"
                        assert agent.memory_hooks is not None
                        assert agent.actor_id == "test_actor"
                        assert agent.session_id == "test_session"

    @pytest.mark.asyncio
    async def test_agent_without_memory_integration(self, mock_mcp_client):
        """Test agent initialization without memory integration."""
        with patch("analytics_agent_strands.AGENTCORE_MEMORY_AVAILABLE", False):
            with patch("analytics_agent_strands.MCPClient", return_value=mock_mcp_client):
                with patch("analytics_agent_strands.BedrockModel"), patch("analytics_agent_strands.Agent"):
                    agent = PublisherAnalyticsAgent("http://localhost:8000")

                    # Check that memory is not integrated
                    assert agent.memory_client is None
                    assert agent.memory_id is None
                    assert agent.memory_hooks is None
                    assert agent.actor_id.startswith("publisher_")

    @pytest.mark.asyncio
    async def test_chat_with_memory_session_update(self, mock_memory_client, mock_mcp_client):
        """Test chat with memory session ID update."""
        with patch("analytics_agent_strands.AGENTCORE_MEMORY_AVAILABLE", True):
            with patch(
                "analytics_agent_strands.create_or_get_analytics_memory",
                return_value=(mock_memory_client, "test_memory_id"),
            ):
                with patch("analytics_agent_strands.MCPClient", return_value=mock_mcp_client):
                    with (
                        patch("analytics_agent_strands.BedrockModel"),
                        patch("analytics_agent_strands.Agent") as mock_agent_class,
                    ):
                        mock_agent_instance = Mock()
                        mock_agent_instance.return_value = "Test response"
                        mock_agent_class.return_value = mock_agent_instance

                        agent = PublisherAnalyticsAgent("http://localhost:8000")

                        # Test chat with session ID
                        response = await agent.chat("Test message", session_id="new_session_id")

                        # Check that session ID was updated in memory hooks
                        assert agent.memory_hooks.session_id == "new_session_id"
                        assert "new_session_id" in agent._session_greetings

    @pytest.mark.asyncio
    async def test_memory_hooks_register(self, mock_memory_client):
        """Test memory hooks registration."""
        memory_hooks = AnalyticsMemoryHooks(
            memory_id="test_memory_id", client=mock_memory_client, actor_id="test_actor", session_id="test_session"
        )

        mock_registry = Mock()
        memory_hooks.register_hooks(mock_registry)

        # Check that callbacks were registered
        assert mock_registry.add_callback.call_count == 2

        # Check the callback types
        calls = mock_registry.add_callback.call_args_list
        callback_types = [call[0][0] for call in calls]

        # Import the event types for comparison
        from strands.hooks import AfterInvocationEvent, MessageAddedEvent

        assert MessageAddedEvent in callback_types
        assert AfterInvocationEvent in callback_types


class TestMemoryErrorHandling:
    """Test memory error handling scenarios."""

    @pytest.mark.asyncio
    async def test_memory_retrieval_error(self, mock_memory_client):
        """Test handling of memory retrieval errors."""
        # Mock memory client to raise exception
        mock_memory_client.retrieve_memories = Mock(side_effect=Exception("Memory error"))
        mock_memory_client.get_memory_strategies = Mock(
            return_value=[{"type": "semantic", "namespaces": ["test/namespace"]}]
        )

        memory_hooks = AnalyticsMemoryHooks(
            memory_id="test_memory_id", client=mock_memory_client, actor_id="test_actor", session_id="test_session"
        )

        # Mock event
        mock_event = Mock()
        mock_agent = Mock()
        mock_agent.messages = [{"role": "user", "content": [{"text": "test"}]}]
        mock_event.agent = mock_agent

        # Should not raise exception
        memory_hooks.retrieve_analytics_context(mock_event)

        # Message should remain unchanged
        assert mock_agent.messages[0]["content"][0]["text"] == "test"

    @pytest.mark.asyncio
    async def test_memory_save_error(self, mock_memory_client):
        """Test handling of memory save errors."""
        # Mock memory client to raise exception
        mock_memory_client.create_event = Mock(side_effect=Exception("Save error"))
        mock_memory_client.get_memory_strategies = Mock(return_value=[])

        memory_hooks = AnalyticsMemoryHooks(
            memory_id="test_memory_id", client=mock_memory_client, actor_id="test_actor", session_id="test_session"
        )

        # Mock event
        mock_event = Mock()
        mock_agent = Mock()
        mock_agent.messages = [
            {"role": "user", "content": [{"text": "test"}]},
            {"role": "assistant", "content": [{"text": "response"}]},
        ]
        mock_event.agent = mock_agent

        # Should not raise exception
        memory_hooks.save_analytics_interaction(mock_event)


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
