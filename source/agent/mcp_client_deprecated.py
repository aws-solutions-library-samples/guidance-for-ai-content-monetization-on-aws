"""
MCP Client for Analytics Agent

This module provides an MCP client that communicates with the integrated MCP server
to access analytics tools instead of calling them directly.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from strands.tools import tool

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for communicating with the integrated MCP server."""

    def __init__(self, server_url: str = None):
        """
        Initialize MCP client.

        Args:
            server_url: URL of the MCP server (defaults to localhost:8000)
        """
        self.server_url = server_url or os.getenv("MCP_SERVER_URL", "http://localhost:8000")
        self.client_type = "internal"  # Grants access to analytics tools
        self.http_client = httpx.AsyncClient(timeout=30.0)
        logger.info(f"MCP Client initialized for server: {self.server_url}")

    async def call_tool(self, tool_name: str, **kwargs) -> Dict[str, Any]:
        """
        Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            **kwargs: Tool parameters

        Returns:
            Tool response data
        """
        try:
            # Prepare the request payload
            payload = {"client_type": self.client_type, "tool_name": tool_name, "parameters": kwargs}

            # Make the HTTP request to the MCP server
            response = await self.http_client.post(
                f"{self.server_url}/tools/{tool_name}", json=payload, headers={"Content-Type": "application/json"}
            )

            if response.status_code == 200:
                result = response.json()
                logger.debug(f"MCP tool call successful: {tool_name}")
                return result
            else:
                error_msg = f"MCP server returned status {response.status_code}: {response.text}"
                logger.error(f"MCP tool call failed: {error_msg}")
                return {"success": False, "error": error_msg, "error_code": f"MCP_SERVER_ERROR_{response.status_code}"}

        except httpx.ConnectError as e:
            error_msg = f"Failed to connect to MCP server at {self.server_url}: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg, "error_code": "MCP_CONNECTION_ERROR"}
        except Exception as e:
            error_msg = f"Unexpected error calling MCP tool {tool_name}: {str(e)}"
            logger.error(error_msg)
            return {"success": False, "error": error_msg, "error_code": "MCP_CLIENT_ERROR"}

    async def get_usage_analytics(
        self, date_range: str = "last_7_days", content_type: Optional[str] = None, bot_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get usage analytics via MCP server."""
        return await self.call_tool(
            "get_usage_analytics", date_range=date_range, content_type=content_type, bot_filter=bot_filter
        )

    async def analyze_content_performance(self, content_ids: List[str]) -> Dict[str, Any]:
        """Analyze content performance via MCP server."""
        return await self.call_tool("analyze_content_performance", content_ids=content_ids)

    async def generate_insights(self, metric: str, time_period: str = "last_30_days") -> Dict[str, Any]:
        """Generate insights via MCP server."""
        return await self.call_tool("generate_insights", metric=metric, time_period=time_period)

    async def get_revenue_metrics(
        self, time_period: str = "last_30_days", breakdown_by: str = "content_type"
    ) -> Dict[str, Any]:
        """Get revenue metrics via MCP server."""
        return await self.call_tool("get_revenue_metrics", time_period=time_period, breakdown_by=breakdown_by)

    async def analyze_bot_behavior(
        self, bot_id: Optional[str] = None, time_period: str = "last_7_days"
    ) -> Dict[str, Any]:
        """Analyze bot behavior via MCP server."""
        return await self.call_tool("analyze_bot_behavior", bot_id=bot_id, time_period=time_period)

    async def get_page_context_help(
        self,
        page_type: str,
        current_data: Optional[Dict[str, Any]] = None,
        user_question: Optional[str] = None,
        url_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get page context help via MCP server."""
        return await self.call_tool(
            "get_page_context_help",
            page_type=page_type,
            current_data=current_data,
            user_question=user_question,
            url_path=url_path,
        )

    async def suggest_page_actions(
        self,
        page_type: str,
        user_intent: str,
        current_filters: Optional[Dict[str, Any]] = None,
        current_data: Optional[Dict[str, Any]] = None,
        url_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Suggest page actions via MCP server."""
        return await self.call_tool(
            "suggest_page_actions",
            page_type=page_type,
            user_intent=user_intent,
            current_filters=current_filters,
            current_data=current_data,
            url_path=url_path,
        )

    async def list_available_tools(self) -> Dict[str, Any]:
        """List tools available to this client type."""
        try:
            response = await self.http_client.get(f"{self.server_url}/tools", params={"client_type": self.client_type})

            if response.status_code == 200:
                return response.json()
            else:
                return {"success": False, "error": f"Failed to list tools: {response.status_code}", "tools": []}
        except Exception as e:
            logger.error(f"Failed to list available tools: {str(e)}")
            return {"success": False, "error": str(e), "tools": []}

    async def health_check(self) -> Dict[str, Any]:
        """Check MCP server health."""
        try:
            response = await self.http_client.get(f"{self.server_url}/health")

            if response.status_code == 200:
                return response.json()
            else:
                return {"status": "unhealthy", "error": f"Server returned {response.status_code}"}
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return {"status": "unhealthy", "error": str(e)}

    async def close(self):
        """Close the HTTP client."""
        await self.http_client.aclose()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


def create_mcp_tool(mcp_client: MCPClient, tool_name: str, description: str):
    """Create a properly decorated Strands tool that calls MCP server."""

    @tool
    async def mcp_tool(**kwargs):
        """Call the MCP tool with the given parameters."""
        result = await mcp_client.call_tool(tool_name, **kwargs)

        # Extract the actual data from the MCP response
        if result.get("success"):
            return result.get("data", result)
        else:
            # Return error information
            return {
                "error": result.get("error", "Unknown MCP error"),
                "error_code": result.get("error_code", "MCP_ERROR"),
            }

    # Set the tool name and description
    mcp_tool.__name__ = tool_name
    mcp_tool.__doc__ = description

    return mcp_tool


def create_mcp_tools(mcp_client: MCPClient) -> List:
    """
    Create MCP tools for use with Strands Agent.

    Args:
        mcp_client: The MCP client instance

    Returns:
        List of properly decorated tools that can be used by Strands Agent
    """
    tool_definitions = [
        ("get_usage_analytics", "Retrieve content usage analytics data from the IAB API"),
        ("analyze_content_performance", "Analyze performance metrics for specific content items"),
        ("generate_insights", "Generate business insights from analytics data"),
        ("get_revenue_metrics", "Get detailed revenue and monetization metrics"),
        ("analyze_bot_behavior", "Analyze AI bot usage patterns and behavior"),
        ("get_page_context_help", "Provide contextual help based on the current page"),
        ("suggest_page_actions", "Suggest actions based on page context and user intent"),
    ]

    return [create_mcp_tool(mcp_client, tool_name, description) for tool_name, description in tool_definitions]
