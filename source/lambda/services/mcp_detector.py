"""
MCP Request Detection Service

This module provides detection logic for identifying requests coming through
MCP (Model Context Protocol) servers and extracting MCP-specific metadata.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MCPRequestDetector:
    """
    Service for detecting MCP server requests and extracting MCP-specific metadata.
    """

    def __init__(self):
        """Initialize the MCP detector with known MCP patterns."""
        self.mcp_headers = [
            "x-mcp-server-id",
            "x-mcp-tool-name",
            "x-agent-id",
            "x-mcp-session-id",
            "x-agentcore-session-id",
            "x-bedrock-agent-id",
            "x-mcp-version",
        ]

        self.mcp_user_agents = [
            r"MCP-Server",
            r"AgentCore",
            r"Bedrock-Agent",
            r"AWS-Bedrock-Agent",
            r"MCP-Client",
            r"Model-Context-Protocol",
            r"IAB-Content-MCP",
        ]

        self.mcp_query_parameters = [
            "mcp_tool",
            "agent_id",
            "mcp_session",
            "agentcore_session",
            "bedrock_agent_id",
            "mcp_server_id",
        ]

        self.agent_type_patterns = {
            "claude": r"claude.*3.*sonnet|claude.*3.*haiku|claude.*3.*opus",
            "gpt": r"gpt-4|gpt-3\.5|openai",
            "bedrock": r"bedrock|amazon|aws",
            "llama": r"llama.*2|llama.*3|meta",
            "gemini": r"gemini|google|bard",
        }

    def is_mcp_request(self, request_data: Dict[str, Any]) -> bool:
        """
        Detect if request is coming through MCP server.

        Args:
            request_data: Dictionary containing request information

        Returns:
            bool: True if request appears to be from MCP server
        """
        try:
            # Check for MCP-specific headers
            if self._check_mcp_headers(request_data):
                return True

            # Check user agent for MCP patterns
            if self._check_mcp_user_agent(request_data):
                return True

            # Check for MCP-specific query parameters
            if self._check_mcp_query_parameters(request_data):
                return True

            # Check request body for MCP tool call structure
            if self._check_mcp_request_body(request_data):
                return True

            return False

        except Exception as e:
            logger.error(f"Error in MCP detection: {str(e)}")
            return False

    def extract_mcp_metadata(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract MCP-specific metadata from request.

        Args:
            request_data: Dictionary containing request information

        Returns:
            dict: MCP metadata information
        """
        try:
            headers = request_data.get("headers", {})
            query_params = request_data.get("queryStringParameters", {}) or {}

            metadata = {
                "server_id": self._extract_server_id(headers, query_params),
                "tool_name": self._extract_tool_name(headers, query_params, request_data),
                "agent_id": self._extract_agent_id(headers, query_params),
                "agent_type": self._extract_agent_type(headers, query_params),
                "session_id": self._extract_session_id(headers, query_params),
                "mcp_version": headers.get("x-mcp-version", "unknown"),
                "parameters": query_params,
                "request_source": self._identify_request_source(headers),
            }

            # Add AgentCore specific metadata if present
            if self._is_agentcore_request(headers):
                metadata.update(self._extract_agentcore_metadata(headers, query_params))

            return metadata

        except Exception as e:
            logger.error(f"Error extracting MCP metadata: {str(e)}")
            return {
                "server_id": "unknown_server",
                "tool_name": "unknown_tool",
                "agent_id": "unknown_agent",
                "agent_type": "unknown",
                "session_id": None,
                "parameters": {},
                "request_source": "unknown",
            }

    def extract_business_context(self, request_data: Dict[str, Any], response_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract business-relevant context from MCP requests.

        Args:
            request_data: Dictionary containing request information
            response_data: Dictionary containing response information

        Returns:
            dict: Business context information
        """
        try:
            return {
                "content_types_accessed": self._analyze_content_types(response_data),
                "query_intent": self._classify_query_intent(request_data.get("query", "")),
                "response_quality": self._assess_response_quality(response_data),
                "usage_pattern": self._identify_usage_pattern(request_data),
                "interaction_complexity": self._assess_interaction_complexity(request_data),
                "business_value": self._calculate_business_value(request_data, response_data),
            }

        except Exception as e:
            logger.error(f"Error extracting business context: {str(e)}")
            return {
                "content_types_accessed": [],
                "query_intent": "unknown",
                "response_quality": "unknown",
                "usage_pattern": "unknown",
                "interaction_complexity": "low",
                "business_value": 0.0,
            }

    def get_mcp_tool_info(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get detailed information about the MCP tool being used.

        Args:
            request_data: Dictionary containing request information

        Returns:
            dict: MCP tool information
        """
        try:
            tool_name = self._extract_tool_name(
                request_data.get("headers", {}), request_data.get("queryStringParameters", {}), request_data
            )

            return {
                "tool_name": tool_name,
                "tool_category": self._categorize_mcp_tool(tool_name),
                "expected_response_type": self._get_expected_response_type(tool_name),
                "business_impact": self._assess_tool_business_impact(tool_name),
                "usage_frequency": self._get_tool_usage_frequency(tool_name),
            }

        except Exception as e:
            logger.error(f"Error getting MCP tool info: {str(e)}")
            return {
                "tool_name": "unknown_tool",
                "tool_category": "unknown",
                "expected_response_type": "unknown",
                "business_impact": "low",
                "usage_frequency": "unknown",
            }

    def _check_mcp_headers(self, request_data: Dict[str, Any]) -> bool:
        """Check for MCP-specific headers."""
        headers = request_data.get("headers", {})

        # Convert header names to lowercase for case-insensitive comparison
        header_names = [h.lower() for h in headers.keys()]

        for mcp_header in self.mcp_headers:
            if mcp_header.lower() in header_names:
                return True

        return False

    def _check_mcp_user_agent(self, request_data: Dict[str, Any]) -> bool:
        """Check user agent for MCP patterns."""
        headers = request_data.get("headers", {})
        user_agent = headers.get("user-agent", "") or headers.get("User-Agent", "")

        for pattern in self.mcp_user_agents:
            if re.search(pattern, user_agent, re.IGNORECASE):
                return True

        return False

    def _check_mcp_query_parameters(self, request_data: Dict[str, Any]) -> bool:
        """Check for MCP-specific query parameters."""
        query_params = request_data.get("queryStringParameters", {}) or {}

        for param in self.mcp_query_parameters:
            if param in query_params:
                return True

        return False

    def _check_mcp_request_body(self, request_data: Dict[str, Any]) -> bool:
        """Check request body for MCP tool call structure."""
        body = request_data.get("body")
        if not body:
            return False

        try:
            # Try to parse as JSON and look for MCP tool call structure
            if isinstance(body, str):
                parsed_body = json.loads(body)
            else:
                parsed_body = body

            # Check for MCP tool call indicators
            mcp_indicators = ["tool_name", "mcp_tool", "agent_request", "tool_call"]
            return any(indicator in parsed_body for indicator in mcp_indicators)

        except (json.JSONDecodeError, TypeError):
            return False

    def _extract_server_id(self, headers: Dict[str, Any], query_params: Dict[str, Any]) -> str:
        """Extract MCP server ID."""
        # Check headers first
        for key, value in headers.items():
            if key.lower() == "x-mcp-server-id":
                return value

        # Check query parameters
        return query_params.get("mcp_server_id", "unknown_server")

    def _extract_tool_name(
        self, headers: Dict[str, Any], query_params: Dict[str, Any], request_data: Dict[str, Any]
    ) -> str:
        """Extract MCP tool name."""
        # Check headers
        for key, value in headers.items():
            if key.lower() == "x-mcp-tool-name":
                return value

        # Check query parameters
        if "mcp_tool" in query_params:
            return query_params["mcp_tool"]

        # Try to infer from endpoint path
        path = request_data.get("path", "") or request_data.get("rawPath", "")
        if path.rstrip("/").endswith("/query"):
            return "search_content"
        elif "/content/" in path:
            return "get_content"
        elif "/analytics" in path:
            return "get_analytics"

        return "unknown_tool"

    def _extract_agent_id(self, headers: Dict[str, Any], query_params: Dict[str, Any]) -> str:
        """Extract agent ID."""
        # Check headers
        for key, value in headers.items():
            if key.lower() in ["x-agent-id", "x-bedrock-agent-id"]:
                return value

        # Check query parameters
        return query_params.get("agent_id", query_params.get("bedrock_agent_id", "unknown_agent"))

    def _extract_agent_type(self, headers: Dict[str, Any], query_params: Dict[str, Any]) -> str:
        """Extract or infer agent type."""
        # Check headers
        for key, value in headers.items():
            if key.lower() == "x-agent-type":
                return value

        # Try to infer from user agent or other headers
        user_agent = headers.get("user-agent", "") or headers.get("User-Agent", "")

        for agent_type, pattern in self.agent_type_patterns.items():
            if re.search(pattern, user_agent, re.IGNORECASE):
                return agent_type

        return "unknown"

    def _extract_session_id(self, headers: Dict[str, Any], query_params: Dict[str, Any]) -> Optional[str]:
        """Extract session ID."""
        # Check headers
        for key, value in headers.items():
            if key.lower() in ["x-mcp-session-id", "x-agentcore-session-id", "x-session-id"]:
                return value

        # Check query parameters
        return query_params.get("mcp_session") or query_params.get("agentcore_session")

    def _identify_request_source(self, headers: Dict[str, Any]) -> str:
        """Identify the source of the MCP request."""
        user_agent = headers.get("user-agent", "") or headers.get("User-Agent", "")

        if "agentcore" in user_agent.lower():
            return "agentcore"
        elif "bedrock" in user_agent.lower():
            return "bedrock"
        elif "mcp-server" in user_agent.lower():
            return "mcp_server"
        else:
            return "unknown"

    def _is_agentcore_request(self, headers: Dict[str, Any]) -> bool:
        """Check if request is from AgentCore."""
        agentcore_headers = ["x-agentcore-session-id", "x-bedrock-agent-id"]
        header_names = [h.lower() for h in headers.keys()]

        return any(h.lower() in header_names for h in agentcore_headers)

    def _extract_agentcore_metadata(self, headers: Dict[str, Any], query_params: Dict[str, Any]) -> Dict[str, Any]:
        """Extract AgentCore-specific metadata."""
        return {
            "agentcore_session_id": headers.get("x-agentcore-session-id"),
            "bedrock_agent_id": headers.get("x-bedrock-agent-id"),
            "bedrock_agent_alias_id": headers.get("x-bedrock-agent-alias-id"),
            "bedrock_knowledge_base_id": headers.get("x-bedrock-knowledge-base-id"),
            "agentcore_request_id": headers.get("x-agentcore-request-id"),
        }

    def _analyze_content_types(self, response_data: Dict[str, Any]) -> List[str]:
        """Analyze content types from response data."""
        content_types = set()

        results = response_data.get("results", [])
        for result in results:
            if isinstance(result, dict) and "content_type" in result:
                content_types.add(result["content_type"])

        return list(content_types)

    def _classify_query_intent(self, query: str) -> str:
        """Classify the intent of the query."""
        query_lower = query.lower()

        if any(word in query_lower for word in ["research", "study", "analysis", "investigate"]):
            return "research"
        elif any(word in query_lower for word in ["how", "tutorial", "guide", "learn"]):
            return "learning"
        elif any(word in query_lower for word in ["find", "search", "look for", "locate"]):
            return "discovery"
        elif any(word in query_lower for word in ["compare", "versus", "vs", "difference"]):
            return "comparison"
        else:
            return "general"

    def _assess_response_quality(self, response_data: Dict[str, Any]) -> str:
        """Assess the quality of the response."""
        results_count = len(response_data.get("results", []))

        if results_count == 0:
            return "no_results"
        elif results_count < 5:
            return "limited"
        elif results_count < 20:
            return "good"
        else:
            return "comprehensive"

    def _identify_usage_pattern(self, request_data: Dict[str, Any]) -> str:
        """Identify usage pattern from request."""
        # This would analyze request timing, frequency, etc.
        # For now, return a simple classification
        query = request_data.get("query", "")

        if len(query) > 100:
            return "detailed_query"
        elif len(query) < 20:
            return "simple_query"
        else:
            return "standard_query"

    def _assess_interaction_complexity(self, request_data: Dict[str, Any]) -> str:
        """Assess the complexity of the interaction."""
        query = request_data.get("query", "")
        params = request_data.get("queryStringParameters", {}) or {}

        complexity_score = 0

        # Query length
        if len(query) > 50:
            complexity_score += 1

        # Number of parameters
        if len(params) > 3:
            complexity_score += 1

        # Presence of filters
        if any(key in params for key in ["content_type", "category", "date_range"]):
            complexity_score += 1

        if complexity_score >= 2:
            return "high"
        elif complexity_score == 1:
            return "medium"
        else:
            return "low"

    def _calculate_business_value(self, request_data: Dict[str, Any], response_data: Dict[str, Any]) -> float:
        """Calculate business value of the interaction."""
        # Simple calculation based on results returned
        results_count = len(response_data.get("results", []))
        return results_count * 0.1  # $0.10 per result

    def _categorize_mcp_tool(self, tool_name: str) -> str:
        """Categorize the MCP tool."""
        tool_categories = {
            "search_content": "content_discovery",
            "get_content": "content_retrieval",
            "list_content": "content_browsing",
            "get_analytics": "analytics",
            "get_usage_analytics": "analytics",
            "get_revenue_metrics": "business_intelligence",
        }

        return tool_categories.get(tool_name, "unknown")

    def _get_expected_response_type(self, tool_name: str) -> str:
        """Get expected response type for the tool."""
        response_types = {
            "search_content": "content_list",
            "get_content": "content_detail",
            "list_content": "content_list",
            "get_analytics": "metrics_data",
            "get_usage_analytics": "usage_data",
            "get_revenue_metrics": "financial_data",
        }

        return response_types.get(tool_name, "unknown")

    def _assess_tool_business_impact(self, tool_name: str) -> str:
        """Assess business impact of the tool."""
        high_impact_tools = ["search_content", "get_content"]
        medium_impact_tools = ["list_content", "get_analytics"]

        if tool_name in high_impact_tools:
            return "high"
        elif tool_name in medium_impact_tools:
            return "medium"
        else:
            return "low"

    def _get_tool_usage_frequency(self, tool_name: str) -> str:
        """Get expected usage frequency for the tool."""
        # This would be based on historical data
        # For now, return estimated frequencies
        frequent_tools = ["search_content"]
        moderate_tools = ["get_content", "list_content"]

        if tool_name in frequent_tools:
            return "high"
        elif tool_name in moderate_tools:
            return "medium"
        else:
            return "low"
