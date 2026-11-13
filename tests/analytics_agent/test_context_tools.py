# test_context_tools.py - Unit tests for context-aware analytics tools
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


class TestPageContextDetection:
    """Test page type detection and context extraction functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    def test_detect_page_type_from_url_analytics(self):
        """Test page type detection for analytics URLs."""
        assert self.agent._detect_page_type("/analytics") == "analytics"
        assert self.agent._detect_page_type("/dashboard") == "analytics"
        assert self.agent._detect_page_type("/analytics/detailed") == "analytics"

    def test_detect_page_type_from_url_content(self):
        """Test page type detection for content URLs."""
        assert self.agent._detect_page_type("/content") == "content"
        assert self.agent._detect_page_type("/upload") == "content"
        assert self.agent._detect_page_type("/content/upload") == "content"

    def test_detect_page_type_from_url_bot_control(self):
        """Test page type detection for bot control URLs."""
        assert self.agent._detect_page_type("/bot-control") == "bot_control"
        assert self.agent._detect_page_type("/bots") == "bot_control"
        assert self.agent._detect_page_type("/bot-control/pricing") == "bot_control"

    def test_detect_page_type_from_url_system(self):
        """Test page type detection for system URLs."""
        assert self.agent._detect_page_type("/system") == "system"
        assert self.agent._detect_page_type("/settings") == "system"
        assert self.agent._detect_page_type("/system/users") == "system"

    def test_detect_page_type_from_url_home(self):
        """Test page type detection for home URLs."""
        assert self.agent._detect_page_type("/") == "home"
        assert self.agent._detect_page_type("/home") == "home"

    def test_detect_page_type_from_data_fallback(self):
        """Test page type detection using data fallback when URL is unclear."""
        # Analytics data
        analytics_data = {"analytics": True, "metrics": {"revenue": 1000}}
        assert self.agent._detect_page_type("/unknown", analytics_data) == "analytics"

        # Content data
        content_data = {"content": True, "upload": {"status": "complete"}}
        assert self.agent._detect_page_type("/unknown", content_data) == "content"

        # Bot data
        bot_data = {"bot": True, "partner": {"name": "TestBot"}}
        assert self.agent._detect_page_type("/unknown", bot_data) == "bot_control"

    def test_detect_page_type_unknown(self):
        """Test page type detection returns unknown for unrecognized patterns."""
        assert self.agent._detect_page_type("/unknown-page") == "unknown"
        assert self.agent._detect_page_type("/random/path", {}) == "unknown"


class TestPageContextExtraction:
    """Test page context extraction functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    def test_extract_page_context_basic(self):
        """Test basic page context extraction."""
        context = self.agent._extract_page_context("analytics", None)

        assert context["page_type"] == "analytics"
        assert "timestamp" in context
        assert context["data_available"] is False
        assert context["context_elements"] == []

    def test_extract_page_context_with_data(self):
        """Test page context extraction with data."""
        test_data = {"revenue": 1000, "date_range": "last_7_days"}
        context = self.agent._extract_page_context("analytics", test_data)

        assert context["page_type"] == "analytics"
        assert context["data_available"] is True
        assert len(context["context_elements"]) > 0

    def test_extract_analytics_context(self):
        """Test analytics page context extraction."""
        data = {"date_range": "last_30_days", "content_type": "articles", "revenue": 2500.50, "access_count": 1500}

        elements = self.agent._extract_analytics_context(data)

        # Check for expected context elements
        element_names = [elem["name"] for elem in elements]
        assert "date_range" in element_names
        assert "content_type" in element_names
        assert "revenue" in element_names
        assert "access_count" in element_names

        # Check element structure
        date_element = next(elem for elem in elements if elem["name"] == "date_range")
        assert date_element["type"] == "filter"
        assert date_element["value"] == "last_30_days"
        assert "description" in date_element

    def test_extract_content_context(self):
        """Test content page context extraction."""
        data = {
            "content_list": [{"id": 1}, {"id": 2}, {"id": 3}],
            "upload_status": "processing",
            "processing_status": "complete",
        }

        elements = self.agent._extract_content_context(data)

        # Check for expected context elements
        element_names = [elem["name"] for elem in elements]
        assert "content_count" in element_names
        assert "upload_status" in element_names
        assert "processing_status" in element_names

        # Check content count calculation
        count_element = next(elem for elem in elements if elem["name"] == "content_count")
        assert count_element["value"] == 3

    def test_extract_bot_control_context(self):
        """Test bot control page context extraction."""
        data = {
            "active_bots": [{"id": 1}, {"id": 2}],
            "access_rules": [{"rule": 1}, {"rule": 2}, {"rule": 3}],
            "pricing_config": {"model": "per_access"},
        }

        elements = self.agent._extract_bot_control_context(data)

        # Check for expected context elements
        element_names = [elem["name"] for elem in elements]
        assert "active_bots" in element_names
        assert "access_rules" in element_names
        assert "pricing_config" in element_names

        # Check bot count calculation
        bots_element = next(elem for elem in elements if elem["name"] == "active_bots")
        assert bots_element["value"] == 2

    def test_extract_system_context(self):
        """Test system page context extraction."""
        data = {"system_status": "healthy", "users": [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}], "user_count": 4}

        elements = self.agent._extract_system_context(data)

        # Check for expected context elements
        element_names = [elem["name"] for elem in elements]
        assert "system_status" in element_names
        assert "user_count" in element_names

        # Check user count
        user_element = next(elem for elem in elements if elem["name"] == "user_count")
        assert user_element["value"] == 4

    def test_extract_home_context(self):
        """Test home page context extraction."""
        data = {"total_revenue": 5000, "total_content": 150, "active_bots": 12, "alerts": [{"id": 1}, {"id": 2}]}

        elements = self.agent._extract_home_context(data)

        # Check for expected context elements
        element_names = [elem["name"] for elem in elements]
        assert "total_revenue" in element_names
        assert "total_content" in element_names
        assert "active_bots" in element_names
        assert "alerts" in element_names

        # Check alert count calculation
        alerts_element = next(elem for elem in elements if elem["name"] == "alerts")
        assert alerts_element["value"] == 2


class TestGetPageContextHelp:
    """Test the get_page_context_help tool functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    @pytest.mark.asyncio
    async def test_get_page_context_help_basic(self):
        """Test basic page context help functionality."""
        result = await self.agent.get_page_context_help("analytics")

        assert result["page_context"]["page_type"] == "analytics"
        assert "description" in result
        assert "key_metrics" in result
        assert "common_questions" in result
        assert "available_actions" in result
        assert "contextual_tips" in result

    @pytest.mark.asyncio
    async def test_get_page_context_help_with_data(self):
        """Test page context help with current data."""
        current_data = {"revenue": 2500, "date_range": "last_30_days", "content_type": "articles"}

        result = await self.agent.get_page_context_help("analytics", current_data=current_data)

        assert result["page_context"]["data_available"] is True
        assert len(result["page_context"]["context_elements"]) > 0
        assert "contextual_recommendations" in result

    @pytest.mark.asyncio
    async def test_get_page_context_help_with_question(self):
        """Test page context help with specific user question."""
        result = await self.agent.get_page_context_help("analytics", user_question="How do I see my revenue trends?")

        assert "specific_help" in result
        assert result["specific_help"]["question"] == "How do I see my revenue trends?"
        assert len(result["specific_help"]["recommendations"]) > 0

    @pytest.mark.asyncio
    async def test_get_page_context_help_url_detection(self):
        """Test page context help with URL path detection."""
        result = await self.agent.get_page_context_help("unknown", url_path="/analytics/detailed")

        # Should detect analytics page type from URL
        assert result["page_context"]["page_type"] == "analytics"

    @pytest.mark.asyncio
    async def test_get_page_context_help_all_page_types(self):
        """Test page context help for all supported page types."""
        page_types = ["analytics", "content", "bot_control", "system", "home"]

        for page_type in page_types:
            result = await self.agent.get_page_context_help(page_type)

            assert result["page_context"]["page_type"] == page_type
            assert "description" in result
            assert "available_actions" in result

    @pytest.mark.asyncio
    async def test_generate_specific_help_revenue_question(self):
        """Test specific help generation for revenue questions."""
        page_context = {"page_type": "analytics", "context_elements": []}

        help_info = self.agent._generate_specific_help("How much revenue am I making?", "analytics", page_context)

        assert help_info["question"] == "How much revenue am I making?"
        assert len(help_info["recommendations"]) > 0

        # Should recommend revenue-related actions
        revenue_rec = next(
            (rec for rec in help_info["recommendations"] if "revenue" in rec["description"].lower()), None
        )
        assert revenue_rec is not None

    @pytest.mark.asyncio
    async def test_generate_contextual_recommendations(self):
        """Test contextual recommendations generation."""
        page_context = {
            "context_elements": [
                {"type": "metric", "name": "revenue", "value": 1000, "description": "Current revenue data"}
            ]
        }

        recommendations = self.agent._generate_contextual_recommendations("analytics", page_context)

        assert len(recommendations) > 0

        # Should have revenue-related recommendation
        revenue_rec = next((rec for rec in recommendations if "revenue" in rec["title"].lower()), None)
        assert revenue_rec is not None
        assert revenue_rec["action"] == "generate_insights"


class TestSuggestPageActions:
    """Test the suggest_page_actions tool functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.agent = PublisherAnalyticsAgent()

    @pytest.mark.asyncio
    async def test_suggest_page_actions_basic(self):
        """Test basic page actions suggestion."""
        result = await self.agent.suggest_page_actions("analytics", "I want to analyze my performance")

        assert result["page_type"] == "analytics"
        assert result["user_intent"] == "I want to analyze my performance"
        assert "suggestions" in result
        assert len(result["suggestions"]) > 0
        assert "suggestion_categories" in result

    @pytest.mark.asyncio
    async def test_suggest_page_actions_with_context(self):
        """Test page actions suggestion with context data."""
        current_data = {"content_list": [{"id": 1}, {"id": 2}], "revenue": 1500}

        result = await self.agent.suggest_page_actions(
            "content", "I want to optimize my content", current_data=current_data
        )

        assert result["page_type"] == "content"
        assert "page_context" in result
        assert result["page_context"]["data_available"] is True

    @pytest.mark.asyncio
    async def test_suggest_page_actions_with_filters(self):
        """Test page actions suggestion with current filters."""
        current_filters = {"date_range": "last_30_days", "content_type": "articles"}

        result = await self.agent.suggest_page_actions(
            "analytics", "I want to export data", current_filters=current_filters
        )

        assert "current_filters" in result
        assert "filter_suggestions" in result
        assert len(result["filter_suggestions"]) > 0

    @pytest.mark.asyncio
    async def test_filter_suggestions_by_intent(self):
        """Test filtering suggestions based on user intent."""
        suggestions = {
            "analyze_data": {
                "label": "Analyze Data",
                "description": "Analyze your performance data",
                "category": "analysis",
                "priority": "high",
            },
            "export_data": {
                "label": "Export Data",
                "description": "Export your analytics data",
                "category": "export",
                "priority": "medium",
            },
        }

        # Test intent matching
        filtered = self.agent._filter_suggestions_by_intent(suggestions, "analyze performance")
        assert "analyze_data" in filtered

        filtered = self.agent._filter_suggestions_by_intent(suggestions, "export data")
        assert "export_data" in filtered

    @pytest.mark.asyncio
    async def test_check_intent_semantic_match(self):
        """Test semantic matching between intent and suggestions."""
        suggestion = {"description": "Review and examine your data insights", "category": "analysis"}

        # Test semantic matches
        assert self.agent._check_intent_semantic_match("analyze", suggestion) is True
        assert self.agent._check_intent_semantic_match("examine", suggestion) is False  # Not in keywords
        assert self.agent._check_intent_semantic_match("download", suggestion) is False

    @pytest.mark.asyncio
    async def test_filter_suggestions_by_context(self):
        """Test filtering suggestions based on page context."""
        suggestions = {
            "analyze_content": {"label": "Analyze Content", "requirements": ["content_list"], "category": "analysis"},
            "view_stats": {"label": "View Stats", "requirements": [], "category": "monitoring"},
        }

        page_context = {"context_elements": []}

        # Without content data
        filtered = self.agent._filter_suggestions_by_context(suggestions, page_context, None)
        assert "analyze_content" not in filtered  # Requires content_list
        assert "view_stats" in filtered

        # With content data
        current_data = {"content_list": [{"id": 1}]}
        filtered = self.agent._filter_suggestions_by_context(suggestions, page_context, current_data)
        assert "analyze_content" in filtered
        assert "view_stats" in filtered

    @pytest.mark.asyncio
    async def test_prioritize_suggestions(self):
        """Test suggestion prioritization."""
        suggestions = {
            "low_priority": {"label": "Low Priority", "description": "Low priority action", "priority": "low"},
            "high_priority": {
                "label": "High Priority",
                "description": "High priority action with relevant keywords",
                "priority": "high",
            },
            "medium_priority": {
                "label": "Medium Priority",
                "description": "Medium priority action",
                "priority": "medium",
            },
        }

        page_context = {"context_elements": []}
        prioritized = self.agent._prioritize_suggestions(suggestions, "relevant", page_context)

        # Should be ordered by priority (high first)
        suggestion_keys = list(prioritized.keys())
        assert suggestion_keys[0] == "high_priority"

    @pytest.mark.asyncio
    async def test_calculate_relevance_score(self):
        """Test relevance score calculation."""
        suggestion = {
            "label": "Analyze Performance",
            "description": "Analyze your content performance data",
            "category": "analysis",
        }

        page_context = {"context_elements": [{"name": "performance", "type": "metric"}]}

        # Test intent matching
        score1 = self.agent._calculate_relevance_score(suggestion, "analyze", page_context)
        score2 = self.agent._calculate_relevance_score(suggestion, "unrelated", page_context)

        assert score1 > score2

    @pytest.mark.asyncio
    async def test_generate_filter_suggestions(self):
        """Test filter suggestions generation."""
        current_filters = {"date_range": "last_7_days"}

        filter_suggestions = self.agent._generate_filter_suggestions(current_filters, "analytics")

        assert len(filter_suggestions) > 0

        # Should suggest modifying existing filter
        modify_suggestion = next((fs for fs in filter_suggestions if fs["type"] == "modify_filter"), None)
        assert modify_suggestion is not None
        assert modify_suggestion["filter"] == "date_range"

        # Should suggest adding new filter
        add_suggestion = next((fs for fs in filter_suggestions if fs["type"] == "add_filter"), None)
        assert add_suggestion is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
