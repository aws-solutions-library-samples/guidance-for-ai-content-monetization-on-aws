"""
Unit tests for MCPRequestDetector service.
"""

import pytest
import json
import sys
import os

# Add lambda directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.mcp_detector import MCPRequestDetector


@pytest.fixture
def mcp_detector():
    """Create an MCPRequestDetector instance for testing."""
    return MCPRequestDetector()


class TestMCPRequestDetector:
    """Test cases for MCPRequestDetector class."""

    def test_initialization(self, mcp_detector):
        """Test MCPRequestDetector initialization."""
        assert isinstance(mcp_detector.mcp_headers, list)
        assert len(mcp_detector.mcp_headers) > 0
        assert isinstance(mcp_detector.mcp_user_agents, list)
        assert isinstance(mcp_detector.mcp_query_parameters, list)
        assert isinstance(mcp_detector.agent_type_patterns, dict)

    def test_is_mcp_request_with_mcp_headers(self, mcp_detector):
        """Test MCP request detection with MCP headers."""
        request_data = {
            "headers": {
                "x-mcp-server-id": "iab-content-mcp-server",
                "x-agent-id": "bedrock-agent-123",
                "content-type": "application/json",
            }
        }

        result = mcp_detector.is_mcp_request(request_data)
        assert result is True

    def test_is_mcp_request_with_user_agent(self, mcp_detector):
        """Test MCP request detection with MCP user agent."""
        request_data = {"headers": {"user-agent": "MCP-Server/1.0", "content-type": "application/json"}}

        result = mcp_detector.is_mcp_request(request_data)
        assert result is True

    def test_is_mcp_request_with_query_params(self, mcp_detector):
        """Test MCP request detection with MCP query parameters."""
        request_data = {
            "headers": {"user-agent": "CustomClient/1.0"},
            "queryStringParameters": {"mcp_tool": "search_content", "agent_id": "bedrock-agent-456"},
        }

        result = mcp_detector.is_mcp_request(request_data)
        assert result is True

    def test_is_mcp_request_with_request_body(self, mcp_detector):
        """Test MCP request detection with MCP request body."""
        request_data = {
            "headers": {"user-agent": "CustomClient/1.0"},
            "body": json.dumps({"tool_name": "search_content", "parameters": {"query": "test query"}}),
        }

        result = mcp_detector.is_mcp_request(request_data)
        assert result is True

    def test_is_mcp_request_non_mcp(self, mcp_detector):
        """Test MCP request detection with non-MCP request."""
        request_data = {
            "headers": {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "accept": "text/html"},
            "queryStringParameters": {"q": "search query", "limit": "10"},
        }

        result = mcp_detector.is_mcp_request(request_data)
        assert result is False

    def test_extract_mcp_metadata_complete(self, mcp_detector):
        """Test complete MCP metadata extraction."""
        request_data = {
            "headers": {
                "x-mcp-server-id": "iab-content-mcp-server",
                "x-mcp-tool-name": "search_content",
                "x-agent-id": "bedrock-agent-123",
                "x-agent-type": "claude-3-sonnet",
                "x-session-id": "session-abc123",
                "x-mcp-version": "1.0",
                "user-agent": "AgentCore/1.0",
            },
            "queryStringParameters": {"query": "machine learning", "limit": "10"},
        }

        metadata = mcp_detector.extract_mcp_metadata(request_data)

        assert metadata["server_id"] == "iab-content-mcp-server"
        assert metadata["tool_name"] == "search_content"
        assert metadata["agent_id"] == "bedrock-agent-123"
        assert metadata["agent_type"] == "claude-3-sonnet"
        assert metadata["session_id"] == "session-abc123"
        assert metadata["mcp_version"] == "1.0"
        assert metadata["request_source"] == "agentcore"
        assert isinstance(metadata["parameters"], dict)

    def test_extract_mcp_metadata_minimal(self, mcp_detector):
        """Test MCP metadata extraction with minimal data."""
        request_data = {"headers": {}, "queryStringParameters": {}}

        metadata = mcp_detector.extract_mcp_metadata(request_data)

        assert metadata["server_id"] == "unknown_server"
        assert metadata["tool_name"] == "unknown_tool"
        assert metadata["agent_id"] == "unknown_agent"
        assert metadata["agent_type"] == "unknown"
        assert metadata["session_id"] is None
        assert metadata["request_source"] == "unknown"

    def test_extract_mcp_metadata_from_path(self, mcp_detector):
        """Test MCP metadata extraction inferring tool from path."""
        request_data = {"headers": {}, "queryStringParameters": {}, "path": "/query"}

        metadata = mcp_detector.extract_mcp_metadata(request_data)
        assert metadata["tool_name"] == "search_content"

        # Test different paths
        request_data["path"] = "/content/123"
        metadata = mcp_detector.extract_mcp_metadata(request_data)
        assert metadata["tool_name"] == "get_content"

        request_data["path"] = "/analytics/usage"
        metadata = mcp_detector.extract_mcp_metadata(request_data)
        assert metadata["tool_name"] == "get_analytics"

    def test_extract_business_context(self, mcp_detector):
        """Test business context extraction."""
        request_data = {
            "query": "machine learning research papers",
            "queryStringParameters": {"content_type": "article", "category": "technology"},
        }

        response_data = {
            "results": [
                {"content_id": "1", "content_type": "article"},
                {"content_id": "2", "content_type": "research_paper"},
                {"content_id": "3", "content_type": "article"},
            ]
        }

        context = mcp_detector.extract_business_context(request_data, response_data)

        assert isinstance(context, dict)
        assert "content_types_accessed" in context
        assert "query_intent" in context
        assert "response_quality" in context
        assert "usage_pattern" in context
        assert "interaction_complexity" in context
        assert "business_value" in context

        assert "article" in context["content_types_accessed"]
        assert context["query_intent"] == "research"
        assert context["response_quality"] == "limited"  # 3 results (< 5)

    def test_get_mcp_tool_info(self, mcp_detector):
        """Test MCP tool information extraction."""
        request_data = {"headers": {"x-mcp-tool-name": "search_content"}}

        tool_info = mcp_detector.get_mcp_tool_info(request_data)

        assert isinstance(tool_info, dict)
        assert tool_info["tool_name"] == "search_content"
        assert tool_info["tool_category"] == "content_discovery"
        assert tool_info["expected_response_type"] == "content_list"
        assert tool_info["business_impact"] == "high"
        assert tool_info["usage_frequency"] == "high"

    def test_check_mcp_headers_case_insensitive(self, mcp_detector):
        """Test MCP header checking is case insensitive."""
        request_data = {
            "headers": {
                "X-MCP-Server-ID": "test-server",  # Different case
                "Content-Type": "application/json",
            }
        }

        result = mcp_detector._check_mcp_headers(request_data)
        assert result is True

    def test_check_mcp_user_agent_patterns(self, mcp_detector):
        """Test MCP user agent pattern checking."""
        # Test AgentCore user agent
        request_data = {"headers": {"user-agent": "AgentCore/1.0 (AWS Bedrock)"}}
        assert mcp_detector._check_mcp_user_agent(request_data) is True

        # Test Bedrock Agent user agent
        request_data = {
            "headers": {
                "User-Agent": "Bedrock-Agent/2.0"  # Different case header
            }
        }
        assert mcp_detector._check_mcp_user_agent(request_data) is True

        # Test non-MCP user agent
        request_data = {"headers": {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}}
        assert mcp_detector._check_mcp_user_agent(request_data) is False

    def test_check_mcp_request_body_json(self, mcp_detector):
        """Test MCP request body checking with JSON."""
        # Test with MCP tool call structure
        request_data = {"body": json.dumps({"tool_name": "search_content", "parameters": {"query": "test"}})}
        assert mcp_detector._check_mcp_request_body(request_data) is True

        # Test with non-MCP JSON
        request_data = {"body": json.dumps({"search_query": "test", "limit": 10})}
        assert mcp_detector._check_mcp_request_body(request_data) is False

        # Test with invalid JSON
        request_data = {"body": "invalid json"}
        assert mcp_detector._check_mcp_request_body(request_data) is False

        # Test with no body
        request_data = {}
        assert mcp_detector._check_mcp_request_body(request_data) is False

    def test_extract_agent_type_patterns(self, mcp_detector):
        """Test agent type extraction from patterns."""
        # Test Claude detection
        headers = {"user-agent": "Claude-3-Sonnet Agent/1.0"}
        query_params = {}
        agent_type = mcp_detector._extract_agent_type(headers, query_params)
        assert agent_type == "claude"

        # Test GPT detection
        headers = {"user-agent": "GPT-4 Client/1.0"}
        agent_type = mcp_detector._extract_agent_type(headers, query_params)
        assert agent_type == "gpt"

        # Test Bedrock detection
        headers = {"user-agent": "AWS-Bedrock-Agent/1.0"}
        agent_type = mcp_detector._extract_agent_type(headers, query_params)
        assert agent_type == "bedrock"

        # Test unknown agent
        headers = {"user-agent": "CustomAgent/1.0"}
        agent_type = mcp_detector._extract_agent_type(headers, query_params)
        assert agent_type == "unknown"

    def test_is_agentcore_request(self, mcp_detector):
        """Test AgentCore request detection."""
        # Test with AgentCore headers
        headers = {"x-agentcore-session-id": "session-123", "x-bedrock-agent-id": "agent-456"}
        assert mcp_detector._is_agentcore_request(headers) is True

        # Test without AgentCore headers
        headers = {"x-mcp-server-id": "server-123"}
        assert mcp_detector._is_agentcore_request(headers) is False

    def test_extract_agentcore_metadata(self, mcp_detector):
        """Test AgentCore metadata extraction."""
        headers = {
            "x-agentcore-session-id": "session-123",
            "x-bedrock-agent-id": "agent-456",
            "x-bedrock-agent-alias-id": "alias-789",
            "x-bedrock-knowledge-base-id": "kb-abc",
            "x-agentcore-request-id": "req-def",
        }
        query_params = {}

        metadata = mcp_detector._extract_agentcore_metadata(headers, query_params)

        assert metadata["agentcore_session_id"] == "session-123"
        assert metadata["bedrock_agent_id"] == "agent-456"
        assert metadata["bedrock_agent_alias_id"] == "alias-789"
        assert metadata["bedrock_knowledge_base_id"] == "kb-abc"
        assert metadata["agentcore_request_id"] == "req-def"

    def test_analyze_content_types(self, mcp_detector):
        """Test content type analysis from response."""
        response_data = {
            "results": [
                {"content_id": "1", "content_type": "article"},
                {"content_id": "2", "content_type": "video"},
                {"content_id": "3", "content_type": "article"},
                {"content_id": "4", "content_type": "document"},
            ]
        }

        content_types = mcp_detector._analyze_content_types(response_data)

        assert isinstance(content_types, list)
        assert "article" in content_types
        assert "video" in content_types
        assert "document" in content_types
        assert len(content_types) == 3  # Unique types only

    def test_classify_query_intent(self, mcp_detector):
        """Test query intent classification."""
        # Research intent
        assert mcp_detector._classify_query_intent("research machine learning algorithms") == "research"

        # Learning intent
        assert mcp_detector._classify_query_intent("how to implement neural networks") == "learning"

        # Discovery intent
        assert mcp_detector._classify_query_intent("find articles about AI") == "discovery"

        # Comparison intent
        assert mcp_detector._classify_query_intent("compare GPT vs Claude models") == "comparison"

        # General intent
        assert mcp_detector._classify_query_intent("artificial intelligence") == "general"

    def test_assess_response_quality(self, mcp_detector):
        """Test response quality assessment."""
        # No results
        response_data = {"results": []}
        assert mcp_detector._assess_response_quality(response_data) == "no_results"

        # Limited results
        response_data = {"results": [{"id": "1"}, {"id": "2"}]}
        assert mcp_detector._assess_response_quality(response_data) == "limited"

        # Good results
        response_data = {"results": [{"id": str(i)} for i in range(10)]}
        assert mcp_detector._assess_response_quality(response_data) == "good"

        # Comprehensive results
        response_data = {"results": [{"id": str(i)} for i in range(25)]}
        assert mcp_detector._assess_response_quality(response_data) == "comprehensive"

    def test_identify_usage_pattern(self, mcp_detector):
        """Test usage pattern identification."""
        # Detailed query
        request_data = {"query": "a" * 150}  # Long query
        assert mcp_detector._identify_usage_pattern(request_data) == "detailed_query"

        # Simple query
        request_data = {"query": "AI"}  # Short query
        assert mcp_detector._identify_usage_pattern(request_data) == "simple_query"

        # Standard query
        request_data = {"query": "machine learning algorithms"}  # Medium query
        assert mcp_detector._identify_usage_pattern(request_data) == "standard_query"

    def test_assess_interaction_complexity(self, mcp_detector):
        """Test interaction complexity assessment."""
        # High complexity
        request_data = {
            "query": "a" * 100,  # Long query
            "queryStringParameters": {
                "content_type": "article",
                "category": "technology",
                "date_range": "2024-01-01:2024-12-31",
                "limit": "10",
            },
        }
        assert mcp_detector._assess_interaction_complexity(request_data) == "high"

        # Medium complexity
        request_data = {
            "query": "machine learning",  # Medium query
            "queryStringParameters": {"content_type": "article", "limit": "10"},
        }
        assert mcp_detector._assess_interaction_complexity(request_data) == "medium"

        # Low complexity
        request_data = {
            "query": "AI",  # Short query
            "queryStringParameters": {"limit": "10"},
        }
        assert mcp_detector._assess_interaction_complexity(request_data) == "low"

    def test_calculate_business_value(self, mcp_detector):
        """Test business value calculation."""
        request_data = {"query": "test query"}
        response_data = {"results": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}

        value = mcp_detector._calculate_business_value(request_data, response_data)
        assert abs(value - 0.3) < 0.001  # 3 results * $0.10 each (allow for floating point precision)

    def test_categorize_mcp_tool(self, mcp_detector):
        """Test MCP tool categorization."""
        assert mcp_detector._categorize_mcp_tool("search_content") == "content_discovery"
        assert mcp_detector._categorize_mcp_tool("get_content") == "content_retrieval"
        assert mcp_detector._categorize_mcp_tool("list_content") == "content_browsing"
        assert mcp_detector._categorize_mcp_tool("get_analytics") == "analytics"
        assert mcp_detector._categorize_mcp_tool("unknown_tool") == "unknown"

    def test_get_expected_response_type(self, mcp_detector):
        """Test expected response type determination."""
        assert mcp_detector._get_expected_response_type("search_content") == "content_list"
        assert mcp_detector._get_expected_response_type("get_content") == "content_detail"
        assert mcp_detector._get_expected_response_type("get_analytics") == "metrics_data"
        assert mcp_detector._get_expected_response_type("unknown_tool") == "unknown"

    def test_assess_tool_business_impact(self, mcp_detector):
        """Test tool business impact assessment."""
        assert mcp_detector._assess_tool_business_impact("search_content") == "high"
        assert mcp_detector._assess_tool_business_impact("get_content") == "high"
        assert mcp_detector._assess_tool_business_impact("list_content") == "medium"
        assert mcp_detector._assess_tool_business_impact("unknown_tool") == "low"

    def test_get_tool_usage_frequency(self, mcp_detector):
        """Test tool usage frequency estimation."""
        assert mcp_detector._get_tool_usage_frequency("search_content") == "high"
        assert mcp_detector._get_tool_usage_frequency("get_content") == "medium"
        assert mcp_detector._get_tool_usage_frequency("list_content") == "medium"
        assert mcp_detector._get_tool_usage_frequency("unknown_tool") == "low"

    def test_error_handling(self, mcp_detector):
        """Test error handling in MCP detection methods."""
        # Test with None input
        assert mcp_detector.is_mcp_request(None) is False

        metadata = mcp_detector.extract_mcp_metadata(None)
        assert metadata["server_id"] == "unknown_server"

        context = mcp_detector.extract_business_context(None, None)
        assert context["query_intent"] == "unknown"

        tool_info = mcp_detector.get_mcp_tool_info(None)
        assert tool_info["tool_name"] == "unknown_tool"
