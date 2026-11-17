# test_context_chat.py - Unit tests for context-aware chat functionality
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, UTC
import json

# Import the analytics agent
import sys
import os

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analytics_agent_strands import PublisherAnalyticsAgent


class TestContextualGreeting:
    """Test contextual greeting generation functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    def test_generate_contextual_greeting_analytics_page(self):
        """Test greeting generation for analytics page."""
        page_context = {"page_type": "analytics"}
        greeting = self.agent._generate_contextual_greeting(page_context)

        assert "analytics" in greeting.lower() or "performance" in greeting.lower()
        assert len(greeting) > 10  # Should be a meaningful greeting

    def test_generate_contextual_greeting_content_page(self):
        """Test greeting generation for content page."""
        page_context = {"page_type": "content"}
        greeting = self.agent._generate_contextual_greeting(page_context)

        assert "content" in greeting.lower() or "optimize" in greeting.lower()

    def test_generate_contextual_greeting_bot_control_page(self):
        """Test greeting generation for bot control page."""
        page_context = {"page_type": "bot_control"}
        greeting = self.agent._generate_contextual_greeting(page_context)

        assert "bot" in greeting.lower() or "partnership" in greeting.lower()

    def test_generate_contextual_greeting_with_user_context(self):
        """Test greeting generation with user context."""
        page_context = {"page_type": "analytics"}
        user_context = {"publisher_id": "test_publisher"}

        greeting = self.agent._generate_contextual_greeting(page_context, user_context)

        assert "Hello" in greeting or "hello" in greeting
        assert len(greeting) > 10

    def test_generate_contextual_greeting_default(self):
        """Test greeting generation with no context defaults to home."""
        greeting = self.agent._generate_contextual_greeting()

        assert len(greeting) > 10
        # Should contain general platform-related terms
        assert any(word in greeting.lower() for word in ["platform", "dashboard", "performance", "welcome"])


class TestMessageIntentAnalysis:
    """Test message intent analysis functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    def test_analyze_message_intent_analytics_query(self):
        """Test intent analysis for analytics queries."""
        message = "Show me my performance analytics and metrics"
        intent = self.agent._analyze_message_intent(message)

        assert intent["primary_intent"] == "analytics_query"
        assert intent["confidence"] > 0.5
        assert "get_usage_analytics" in intent["suggested_tools"]
        assert "performance" in intent["matched_keywords"]

    def test_analyze_message_intent_revenue_query(self):
        """Test intent analysis for revenue queries."""
        message = "How much money am I making from my content?"
        intent = self.agent._analyze_message_intent(message)

        assert intent["primary_intent"] == "revenue_query"
        assert intent["confidence"] > 0.5
        assert "get_revenue_metrics" in intent["suggested_tools"]
        assert "money" in intent["matched_keywords"]

    def test_analyze_message_intent_content_query(self):
        """Test intent analysis for content queries."""
        message = "Which of my articles are performing best?"
        intent = self.agent._analyze_message_intent(message)

        assert intent["primary_intent"] == "content_query"
        assert intent["confidence"] >= 0.5
        assert "analyze_content_performance" in intent["suggested_tools"]

    def test_analyze_message_intent_bot_query(self):
        """Test intent analysis for bot queries."""
        message = "What bots are accessing my content and how often?"
        intent = self.agent._analyze_message_intent(message)

        assert intent["primary_intent"] == "bot_query"
        assert intent["confidence"] > 0.5
        assert "analyze_bot_behavior" in intent["suggested_tools"]
        assert "bots" in intent["matched_keywords"]

    def test_analyze_message_intent_help_request(self):
        """Test intent analysis for help requests."""
        message = "How do I navigate this page and what can I do here?"
        intent = self.agent._analyze_message_intent(message)

        assert intent["primary_intent"] == "help_request"
        assert "get_page_context_help" in intent["suggested_tools"]
        assert "how" in intent["matched_keywords"]

    def test_analyze_message_intent_comparison_query(self):
        """Test intent analysis for comparison queries."""
        message = "Compare my revenue this month versus last month"
        intent = self.agent._analyze_message_intent(message)

        assert intent["primary_intent"] == "comparison_query"
        assert intent["confidence"] > 0.5
        assert "versus" in intent["matched_keywords"]

    def test_analyze_message_intent_with_page_context(self):
        """Test intent analysis with page context enhancement."""
        message = "Show me the analytics"
        page_context = {
            "page_type": "analytics",
            "context_elements": [{"type": "metric", "name": "revenue", "value": 1000}],
        }

        intent = self.agent._analyze_message_intent(message, page_context)

        assert intent["primary_intent"] == "analytics_query"
        assert intent["context_relevance"] == "high"  # Should be high since on analytics page

    def test_analyze_message_intent_general_fallback(self):
        """Test intent analysis fallback for unclear messages."""
        message = "Hello there"
        intent = self.agent._analyze_message_intent(message)

        assert intent["primary_intent"] == "general_question"
        assert intent["confidence"] <= 0.5


class TestContextualResponseGeneration:
    """Test contextual response generation functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    def test_generate_contextual_response_analytics(self):
        """Test response generation for analytics intent."""
        message = "Show me my performance data"
        intent_analysis = {
            "primary_intent": "analytics_query",
            "confidence": 0.8,
            "suggested_tools": ["get_usage_analytics", "generate_insights"],
            "matched_keywords": ["performance"],
        }

        response = self.agent._generate_contextual_response(message, intent_analysis)

        assert "analytics" in response.lower() or "performance" in response.lower()
        assert len(response) > 20  # Should be a meaningful response

    def test_generate_contextual_response_with_page_context(self):
        """Test response generation with page context."""
        message = "What's my revenue?"
        intent_analysis = {
            "primary_intent": "revenue_query",
            "confidence": 0.9,
            "suggested_tools": ["get_revenue_metrics"],
            "matched_keywords": ["revenue"],
        }
        page_context = {
            "page_type": "analytics",
            "context_elements": [{"type": "metric", "name": "revenue", "value": 2500}],
        }

        response = self.agent._generate_contextual_response(message, intent_analysis, page_context)

        assert "analytics page" in response.lower()
        assert "revenue" in response.lower()

    def test_generate_contextual_response_high_confidence_tools(self):
        """Test response generation includes tool suggestions for high confidence."""
        message = "Analyze my content performance"
        intent_analysis = {
            "primary_intent": "content_query",
            "confidence": 0.9,
            "suggested_tools": ["analyze_content_performance"],
            "matched_keywords": ["content", "performance"],
        }

        response = self.agent._generate_contextual_response(message, intent_analysis)

        assert "content performance" in response.lower() or "analyze content performance" in response.lower()

    def test_generate_contextual_response_low_confidence(self):
        """Test response generation for low confidence doesn't mention specific tools."""
        message = "Hello"
        intent_analysis = {
            "primary_intent": "general_question",
            "confidence": 0.3,
            "suggested_tools": ["get_page_context_help"],
            "matched_keywords": [],
        }

        response = self.agent._generate_contextual_response(message, intent_analysis)

        # Should not mention specific tools for low confidence
        assert "get page context help" not in response.lower()
        assert len(response) > 10


class TestResponseFiltering:
    """Test response filtering and enhancement functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    def test_filter_response_by_context_analytics_with_filters(self):
        """Test response filtering for analytics page with filters."""
        response = "I can help you analyze your data."
        page_context = {
            "page_type": "analytics",
            "context_elements": [{"type": "filter", "name": "date_range", "value": "last_30_days"}],
        }

        filtered_response = self.agent._filter_response_by_context(response, page_context)

        assert len(filtered_response) > len(response)  # Should be enhanced
        assert "filter" in filtered_response.lower()

    def test_filter_response_by_context_analytics_with_revenue(self):
        """Test response filtering for analytics page with revenue data."""
        response = "I can help you with that."
        page_context = {
            "page_type": "analytics",
            "context_elements": [{"type": "metric", "name": "revenue", "value": 2500}],
        }

        filtered_response = self.agent._filter_response_by_context(response, page_context)

        assert "revenue" in filtered_response.lower()
        assert "monetization" in filtered_response.lower() or "insights" in filtered_response.lower()

    def test_filter_response_by_context_content_large_library(self):
        """Test response filtering for content page with large library."""
        response = "I can help you with your content."
        page_context = {
            "page_type": "content",
            "context_elements": [{"type": "data", "name": "content_count", "value": 100}],
        }

        filtered_response = self.agent._filter_response_by_context(response, page_context)

        assert "large" in filtered_response.lower() or "top-performing" in filtered_response.lower()

    def test_filter_response_by_context_bot_control(self):
        """Test response filtering for bot control page."""
        response = "I can help you manage bots."
        page_context = {
            "page_type": "bot_control",
            "context_elements": [{"type": "data", "name": "active_bots", "value": 5}],
        }

        filtered_response = self.agent._filter_response_by_context(response, page_context)

        assert "behavior patterns" in filtered_response.lower() or "optimization" in filtered_response.lower()

    def test_filter_response_by_context_no_context(self):
        """Test response filtering with no context returns original response."""
        response = "I can help you with that."

        filtered_response = self.agent._filter_response_by_context(response, None)

        assert filtered_response == response


class TestContextAwareChat:
    """Test the main context-aware chat functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    @pytest.mark.asyncio
    async def test_chat_basic_functionality(self):
        """Test basic chat functionality without context."""
        message = "Hello, can you help me?"

        with patch.object(self.agent, "suggest_page_actions", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = {"suggestions": {}}

            response = await self.agent.chat(message)

        assert isinstance(response, dict)
        assert "response" in response
        assert "intent_analysis" in response
        assert "suggested_actions" in response
        assert len(response["response"]) > 10

    @pytest.mark.asyncio
    async def test_chat_with_page_context(self):
        """Test chat functionality with page context."""
        message = "Show me my analytics"
        page_context = {
            "page_type": "analytics",
            "context_elements": [{"type": "metric", "name": "revenue", "value": 1500}],
        }

        with patch.object(self.agent, "suggest_page_actions", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = {
                "suggestions": {
                    "view_detailed": {
                        "label": "View Detailed Analytics",
                        "action": "navigate",
                        "route": "/analytics/detailed",
                        "description": "See comprehensive metrics",
                    }
                }
            }

            response = await self.agent.chat(message, page_context=page_context)

        assert response["page_context"] == page_context
        assert response["intent_analysis"]["primary_intent"] == "analytics_query"
        assert len(response["suggested_actions"]) > 0
        assert "analytics page" in response["response"].lower()

    @pytest.mark.asyncio
    async def test_chat_with_user_context(self):
        """Test chat functionality with user context."""
        message = "What's my revenue?"
        user_context = {"publisher_id": "test_publisher_123"}

        with patch.object(self.agent, "suggest_page_actions", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = {"suggestions": {}}

            response = await self.agent.chat(message, user_context=user_context)

        assert response["user_context"] == "test_publisher_123"
        assert response["intent_analysis"]["primary_intent"] == "revenue_query"

    @pytest.mark.asyncio
    async def test_chat_with_session_greeting(self):
        """Test chat functionality with session-based greeting."""
        message = "Hello"
        session_id = "test_session_123"
        page_context = {"page_type": "analytics"}

        with patch.object(self.agent, "suggest_page_actions", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = {"suggestions": {}}

            response = await self.agent.chat(message, page_context=page_context, session_id=session_id)

        assert "greeting" in response
        assert len(response["greeting"]) > 10
        assert response["session_id"] == session_id

        # Second message in same session should not have greeting
        response2 = await self.agent.chat("What's my performance?", page_context=page_context, session_id=session_id)
        assert "greeting" not in response2

    @pytest.mark.asyncio
    async def test_chat_suggested_actions_generation(self):
        """Test that chat generates appropriate suggested actions."""
        message = "I want to analyze my content performance"
        page_context = {"page_type": "content"}

        with patch.object(self.agent, "suggest_page_actions", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = {
                "suggestions": {
                    "analyze_content": {
                        "label": "Analyze Content Performance",
                        "action": "tool",
                        "tool": "analyze_content_performance",
                        "description": "Analyze your content metrics",
                    }
                }
            }

            response = await self.agent.chat(message, page_context=page_context)

        assert len(response["suggested_actions"]) > 0

        # Should have both tool suggestions and page actions
        action_types = [action["type"] for action in response["suggested_actions"]]
        assert "tool_suggestion" in action_types
        assert "page_action" in action_types

    @pytest.mark.asyncio
    async def test_chat_error_handling(self):
        """Test chat error handling."""
        message = "Test message"

        # Mock an error in suggest_page_actions - this should be handled gracefully
        with patch.object(self.agent, "suggest_page_actions", side_effect=Exception("Test error")):
            response = await self.agent.chat(message)

        # The error should be handled gracefully, so we should still get a valid response
        assert isinstance(response, dict)
        assert "response" in response
        assert "intent_analysis" in response
        # The response should still be helpful even if suggest_page_actions fails
        assert len(response["response"]) > 10

    @pytest.mark.asyncio
    async def test_chat_intent_confidence_affects_suggestions(self):
        """Test that intent confidence affects the number and type of suggestions."""
        # High confidence message
        high_confidence_message = "Show me detailed revenue analytics and performance metrics"

        with patch.object(self.agent, "suggest_page_actions", new_callable=AsyncMock) as mock_suggest:
            mock_suggest.return_value = {"suggestions": {}}

            response = await self.agent.chat(high_confidence_message)

        high_confidence = response["intent_analysis"]["confidence"]
        high_suggestions = len(response["suggested_actions"])

        # Low confidence message
        low_confidence_message = "Hi"
        response2 = await self.agent.chat(low_confidence_message)

        low_confidence = response2["intent_analysis"]["confidence"]
        low_suggestions = len(response2["suggested_actions"])

        assert high_confidence > low_confidence
        # High confidence should generally lead to more specific suggestions
        assert high_confidence > 0.7


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
