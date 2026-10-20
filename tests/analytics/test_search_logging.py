"""
Unit tests for search endpoint bot access logging functionality.

Tests the integration of analytics collection with bot detection for search endpoints.
"""

import pytest
from unittest.mock import patch, ANY

# Import the modules to test
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.bot_access_logger import BotAccessLogger


class TestBotAccessLogger:
    """Test cases for BotAccessLogger class."""

    @pytest.fixture
    def mock_analytics_collector(self):
        """Mock analytics collector."""
        with patch("services.bot_access_logger.AnalyticsCollector") as mock:
            yield mock.return_value

    @pytest.fixture
    def mock_bot_detector(self):
        """Mock bot detector."""
        with patch("services.bot_access_logger.BotDetector") as mock:
            yield mock.return_value

    @pytest.fixture
    def mock_mcp_detector(self):
        """Mock MCP detector."""
        with patch("services.bot_access_logger.MCPRequestDetector") as mock:
            yield mock.return_value

    @pytest.fixture
    def bot_access_logger(self, mock_analytics_collector, mock_bot_detector, mock_mcp_detector):
        """Create BotAccessLogger instance with mocked dependencies."""
        return BotAccessLogger("test-analytics-table")

    def test_log_search_access_direct_bot(
        self, bot_access_logger, mock_bot_detector, mock_mcp_detector, mock_analytics_collector
    ):
        """Test logging direct bot access through search endpoint."""
        # Setup
        request_data = {
            "headers": {"user-agent": "GPTBot/1.0", "x-partner-key": "pk_test123"},
            "body": {"query": "test search query"},
            "path": "/search",
            "httpMethod": "POST",
        }

        response_data = {
            "results": [
                {"content_id": "art_001", "title": "Test Article"},
                {"content_id": "art_002", "title": "Another Article"},
            ],
            "content_ids": ["art_001", "art_002"],
            "total_results": 2,
        }

        # Configure mocks
        mock_mcp_detector.is_mcp_request.return_value = False
        mock_bot_detector.is_bot_request.return_value = True
        mock_bot_detector.extract_bot_id.return_value = "gptbot-1.0"
        mock_bot_detector.assess_bot_behavior.return_value = {
            "bot_type": "llm_assistant",
            "confidence_score": 0.9,
            "behavior_indicators": ["bot_user_agent", "api_key_auth"],
        }

        mock_analytics_collector.record_search_query.return_value = True
        mock_analytics_collector.record_api_access.return_value = True
        mock_analytics_collector.record_bot_access.return_value = True
        mock_analytics_collector.record_business_metrics.return_value = True

        # Execute
        result = bot_access_logger.log_search_access(request_data, response_data)

        # Verify
        assert result is True
        mock_analytics_collector.record_search_query.assert_called_once()
        mock_analytics_collector.record_api_access.assert_called_once()
        mock_analytics_collector.record_bot_access.assert_called_once()
        mock_analytics_collector.record_business_metrics.assert_called_once()

    def test_log_search_access_mcp_request(
        self, bot_access_logger, mock_bot_detector, mock_mcp_detector, mock_analytics_collector
    ):
        """Test logging MCP server access through search endpoint."""
        # Setup
        request_data = {
            "headers": {
                "user-agent": "MCP-Server/1.0",
                "x-mcp-server-id": "content-server-001",
                "x-agent-id": "claude-3-sonnet",
            },
            "body": {"query": "research artificial intelligence"},
            "path": "/search",
            "httpMethod": "POST",
        }

        response_data = {
            "results": [{"content_id": "art_003", "title": "AI Research Paper"}],
            "content_ids": ["art_003"],
            "total_results": 1,
        }

        # Configure mocks
        mock_mcp_detector.is_mcp_request.return_value = True
        mock_mcp_detector.extract_mcp_metadata.return_value = {
            "server_id": "content-server-001",
            "tool_name": "search_content",
            "agent_id": "claude-3-sonnet",
            "agent_type": "claude",
            "session_id": "session_123",
        }
        mock_mcp_detector.extract_business_context.return_value = {
            "content_types_accessed": ["article"],
            "query_intent": "research",
            "business_value": 0.1,
        }

        mock_analytics_collector.record_mcp_tool_usage.return_value = True
        mock_analytics_collector.record_bot_access.return_value = True
        mock_analytics_collector.record_business_metrics.return_value = True
        mock_analytics_collector.record_search_query.return_value = True
        mock_analytics_collector.record_api_access.return_value = True

        # Execute
        result = bot_access_logger.log_search_access(request_data, response_data)

        # Verify
        assert result is True
        mock_analytics_collector.record_mcp_tool_usage.assert_called_once()
        mock_analytics_collector.record_bot_access.assert_called_once()
        mock_analytics_collector.record_business_metrics.assert_called_once()
        mock_analytics_collector.record_search_query.assert_called_once()
        mock_analytics_collector.record_api_access.assert_called_once()

    def test_log_content_query_access(
        self, bot_access_logger, mock_bot_detector, mock_mcp_detector, mock_analytics_collector
    ):
        """Test logging content query access."""
        # Setup
        content_id = "art_001"
        request_data = {
            "headers": {"user-agent": "python-requests/2.28.0", "x-partner-key": "pk_test456"},
            "pathParameters": {"content_id": content_id},
            "path": f"/content/{content_id}",
            "httpMethod": "GET",
        }

        # Configure mocks
        mock_mcp_detector.is_mcp_request.return_value = False
        mock_bot_detector.is_bot_request.return_value = True
        mock_bot_detector.extract_bot_id.return_value = "api-client-bot"

        mock_analytics_collector.record_content_access.return_value = True
        mock_analytics_collector.record_bot_access.return_value = True

        # Execute
        result = bot_access_logger.log_content_query_access(content_id, request_data)

        # Verify
        assert result is True
        mock_analytics_collector.record_content_access.assert_called_once_with(
            content_id=content_id, access_type="query", requester_info=ANY
        )
        mock_analytics_collector.record_bot_access.assert_called_once()

    def test_log_request_start(self, bot_access_logger, mock_bot_detector, mock_mcp_detector):
        """Test logging request start with tracking metadata."""
        # Setup
        request_data = {"headers": {"user-agent": "TestBot/1.0"}, "body": {"query": "test query"}, "path": "/search"}
        query = "test query"

        # Configure mocks
        mock_mcp_detector.is_mcp_request.return_value = False
        mock_bot_detector.is_bot_request.return_value = True

        # Execute
        result = bot_access_logger.log_request_start(request_data, query)

        # Verify
        assert isinstance(result, dict)
        assert "request_id" in result
        assert "start_time" in result
        assert result["query"] == query
        assert result["is_mcp"] is False
        assert result["is_bot"] is True

    def test_extract_requester_info(self, bot_access_logger):
        """Requester identity comes from the resolved partner / native key id, never a header value."""
        # Setup: a legacy header is present but must be ignored; the key id is on the request context
        request_data = {
            "headers": {
                "user-agent": "TestBot/1.0",
                "x-forwarded-for": "192.168.1.100, 10.0.0.1",
                "x-partner-key": "pk_test789",
            },
            "requestContext": {"identity": {"sourceIp": "192.168.1.100", "apiKeyId": "k7f3a9"}},
            "auth_type": "partner_key",
            "authenticated_user": {"partner_id": "ptr_789", "api_key_id": "k7f3a9", "operation_id": "queryContent"},
        }

        # Execute
        result = bot_access_logger._extract_requester_info(request_data)

        # Verify
        assert result["ip_address"] == "192.168.1.100"
        assert result["user_agent"] == "TestBot/1.0"
        assert result["authentication_method"] == "api_key"
        assert result["partner_id"] == "ptr_789"
        assert result["api_key_id"] == "k7f3a9"
        assert result["operation_id"] == "queryContent"
        assert result["user_id"] == "partner_ptr_789"
        assert "pk_test" not in str(result.values()).replace(str(result["headers"]), "")

    def test_extract_requester_info_falls_back_to_key_id_without_resolved_partner(self, bot_access_logger):
        request_data = {
            "headers": {"user-agent": "TestBot/1.0"},
            "requestContext": {"identity": {"sourceIp": "192.168.1.100", "apiKeyId": "k7f3a9"}},
        }
        result = bot_access_logger._extract_requester_info(request_data)
        assert result["authentication_method"] == "api_key"
        assert result["partner_id"] is None
        assert result["api_key_id"] == "k7f3a9"
        assert result["user_id"] == "apikey_k7f3a9"

    def test_assess_query_complexity(self, bot_access_logger):
        """Test query complexity assessment."""
        # Test simple query
        simple_query = "test"
        simple_complexity = bot_access_logger._assess_query_complexity(simple_query)
        assert 0.0 <= simple_complexity <= 2.0

        # Test complex query
        complex_query = "artificial intelligence AND machine learning OR (deep learning NOT supervised)"
        complex_complexity = bot_access_logger._assess_query_complexity(complex_query)
        assert complex_complexity > simple_complexity
        assert complex_complexity <= 10.0

        # Test empty query
        empty_complexity = bot_access_logger._assess_query_complexity("")
        assert empty_complexity == 0.0


if __name__ == "__main__":
    pytest.main([__file__])
