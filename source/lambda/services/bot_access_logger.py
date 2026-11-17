"""
Bot Access Logger Service

This module provides comprehensive bot access logging for search and query endpoints,
integrating analytics collection with bot detection and MCP request handling.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .analytics_collector import AnalyticsCollector
from .bot_detector import BotDetector
from .mcp_detector import MCPRequestDetector

logger = logging.getLogger(__name__)


class BotAccessLogger:
    """
    Service for logging bot access through search endpoints with comprehensive
    analytics collection for both direct API and MCP server requests.
    """

    def __init__(self, analytics_table_name: str = None):
        """
        Initialize the bot access logger.

        Args:
            analytics_table_name: DynamoDB table name for analytics data
        """
        if analytics_table_name is None:
            analytics_table_name = os.environ.get("ANALYTICS_TABLE_NAME", "cm-analytics")
        self.analytics_collector = AnalyticsCollector(analytics_table_name)
        self.bot_detector = BotDetector()
        self.mcp_detector = MCPRequestDetector()

    def log_search_access(self, request_data: Dict[str, Any], response_data: Dict[str, Any]) -> bool:
        """
        Log bot access through search endpoints (direct API or MCP).

        Args:
            request_data: Dictionary containing request information
            response_data: Dictionary containing response information

        Returns:
            bool: Success status
        """
        try:
            # Extract requester information
            requester_info = self._extract_requester_info(request_data)
            access_method = self._determine_access_method(request_data)

            success = True
            # Handle MCP server requests
            if self.mcp_detector.is_mcp_request(request_data):
                success = self._log_mcp_access(request_data, response_data, requester_info) and success

            # Handle direct bot requests
            elif self.bot_detector.is_bot_request(requester_info):
                success = self._log_direct_bot_access(request_data, response_data, requester_info) and success

            # Always record general search metrics
            success = self._log_general_search_metrics(request_data, response_data, requester_info) and success

            logger.info(f"Logged search access via {access_method}")
            return bool(success)

        except Exception as e:
            logger.error(f"Failed to log search access: {str(e)}")
            return False

    def log_content_query_access(self, content_id: str, request_data: Dict[str, Any]) -> bool:
        """
        Log bot access through content query endpoints.

        Args:
            content_id: ID of the content being accessed
            request_data: Dictionary containing request information

        Returns:
            bool: Success status
        """
        try:
            requester_info = self._extract_requester_info(request_data)
            access_method = self._determine_access_method(request_data)

            # Record content access
            self.analytics_collector.record_content_access(
                content_id=content_id, access_type="query", requester_info=requester_info
            )

            # Handle MCP requests
            if self.mcp_detector.is_mcp_request(request_data):
                mcp_metadata = self.mcp_detector.extract_mcp_metadata(request_data)
                self.analytics_collector.record_mcp_tool_usage(
                    tool_name="get_content",
                    agent_id=mcp_metadata.get("agent_id", "unknown_agent"),
                    content_accessed=[content_id],
                    metadata=mcp_metadata,
                )

            # Handle bot access (both direct and MCP)
            if self.bot_detector.is_bot_request(requester_info) or self.mcp_detector.is_mcp_request(request_data):
                bot_id = self._get_bot_id(request_data, requester_info)
                self.analytics_collector.record_bot_access(
                    bot_id=bot_id, content_accessed=[content_id], query_type="direct_query", access_method=access_method
                )

            logger.info(f"Logged content query access for {content_id} via {access_method}")
            return True

        except Exception as e:
            logger.error(f"Failed to log content query access: {str(e)}")
            return False

    def log_request_start(self, request_data: Dict[str, Any], query: str) -> Dict[str, Any]:
        """
        Log the start of a request and return tracking metadata.

        Args:
            request_data: Dictionary containing request information
            query: Search query string

        Returns:
            dict: Request tracking metadata
        """
        try:
            request_start_time = datetime.now(timezone.utc)
            requester_info = self._extract_requester_info(request_data)

            tracking_metadata = {
                "request_id": f"req_{request_start_time.strftime('%Y%m%d_%H%M%S')}_{hash(query) % 10000:04d}",
                "start_time": request_start_time.isoformat(),
                "query": query,
                "requester_info": requester_info,
                "is_mcp": self.mcp_detector.is_mcp_request(request_data),
                "is_bot": self.bot_detector.is_bot_request(requester_info),
            }

            logger.info(f"Started request tracking: {tracking_metadata['request_id']}")
            return tracking_metadata

        except Exception as e:
            logger.error(f"Failed to log request start: {str(e)}")
            return {}

    def _log_mcp_access(
        self, request_data: Dict[str, Any], response_data: Dict[str, Any], requester_info: Dict[str, Any]
    ) -> None:
        """Log MCP server access with business-level metrics. Returns True iff all writes succeeded."""
        try:
            mcp_metadata = self.mcp_detector.extract_mcp_metadata(request_data)
            business_context = self.mcp_detector.extract_business_context(request_data, response_data)

            # Record MCP tool usage
            tool_ok = self.analytics_collector.record_mcp_tool_usage(
                tool_name=mcp_metadata.get("tool_name", "search_content"),
                agent_id=mcp_metadata.get("agent_id", "unknown_agent"),
                content_accessed=response_data.get("content_ids", []),
                metadata={
                    "mcp_server_id": mcp_metadata.get("server_id"),
                    "tool_parameters": mcp_metadata.get("parameters"),
                    "response_size": len(response_data.get("results", [])),
                    "session_id": mcp_metadata.get("session_id"),
                    "agent_type": mcp_metadata.get("agent_type"),
                    "business_context": business_context,
                },
            )

            # Record as bot access with MCP method
            bot_ok = self.analytics_collector.record_bot_access(
                bot_id=mcp_metadata.get("agent_id", "unknown_agent"),
                content_accessed=response_data.get("content_ids", []),
                query_type="search",
                access_method="mcp_server",
            )

            # Record business metrics (separate from AgentCore technical metrics)
            biz_ok = self.analytics_collector.record_business_metrics(
                metric_type="mcp_content_access",
                value=len(response_data.get("content_ids", [])),
                context={
                    "agent_type": mcp_metadata.get("agent_type"),
                    "content_categories": self._extract_content_categories(response_data),
                    "query_complexity": self._assess_query_complexity(request_data.get("query", "")),
                    "business_context": business_context,
                },
            )

            logger.info(f"Logged MCP access for agent {mcp_metadata.get('agent_id')}")
            return bool(tool_ok and bot_ok and biz_ok)

        except Exception as e:
            logger.error(f"Failed to log MCP access: {str(e)}")
            return False

    def _log_direct_bot_access(
        self, request_data: Dict[str, Any], response_data: Dict[str, Any], requester_info: Dict[str, Any]
    ) -> None:
        """Log direct bot API access. Returns True iff all writes succeeded."""
        try:
            bot_id = self.bot_detector.extract_bot_id(requester_info)
            bot_assessment = self.bot_detector.assess_bot_behavior(requester_info)

            # Record bot access
            bot_ok = self.analytics_collector.record_bot_access(
                bot_id=bot_id,
                content_accessed=response_data.get("content_ids", []),
                query_type="search",
                access_method="direct_api",
            )

            # Record business metrics for direct bot access
            biz_ok = self.analytics_collector.record_business_metrics(
                metric_type="direct_bot_access",
                value=len(response_data.get("content_ids", [])),
                context={
                    "bot_type": bot_assessment.get("bot_type"),
                    "confidence_score": bot_assessment.get("confidence_score"),
                    "behavior_indicators": bot_assessment.get("behavior_indicators"),
                    "content_categories": self._extract_content_categories(response_data),
                },
            )

            logger.info(f"Logged direct bot access for {bot_id}")
            return bool(bot_ok and biz_ok)

        except Exception as e:
            logger.error(f"Failed to log direct bot access: {str(e)}")
            return False

    def _log_general_search_metrics(
        self, request_data: Dict[str, Any], response_data: Dict[str, Any], requester_info: Dict[str, Any]
    ) -> None:
        """Log general search metrics for all requests. Returns True iff all writes succeeded."""
        try:
            query = request_data.get("query", "")
            results_count = len(response_data.get("results", []))

            # Record search query metrics
            search_ok = self.analytics_collector.record_search_query(
                query=query, results_count=results_count, requester_info=requester_info
            )

            # Record API access metrics
            endpoint = request_data.get("path", "/query")
            user_id = requester_info.get("user_id", "anonymous")

            api_ok = self.analytics_collector.record_api_access(
                endpoint=endpoint,
                user_id=user_id,
                metadata={
                    "query_length": len(query),
                    "results_returned": results_count,
                    "response_time_ms": response_data.get("processing_time_ms", 0),
                    "is_bot": self.bot_detector.is_bot_request(requester_info),
                    "is_mcp": self.mcp_detector.is_mcp_request(request_data),
                    "user_agent": requester_info.get("user_agent", ""),
                    "ip_address": requester_info.get("ip_address", ""),
                },
            )

            logger.debug(f"Logged general search metrics for query: {query[:50]}...")
            return bool(search_ok and api_ok)

        except Exception as e:
            logger.error(f"Failed to log general search metrics: {str(e)}")
            return False

    def _extract_requester_info(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract requester information from request data.

        Partner identity comes from the resolved partner placed on ``request_data`` by the
        authentication decorators (``authenticated_user``) or, failing that, from the native API
        key id API Gateway placed on ``requestContext.identity.apiKeyId``. Key values are never
        read from headers and never logged.
        """
        try:
            headers = request_data.get("headers", {})
            request_context = request_data.get("requestContext", {})

            # Extract IP address
            ip_address = (
                headers.get("x-forwarded-for", "").split(",")[0].strip()
                or headers.get("x-real-ip")
                or request_context.get("identity", {}).get("sourceIp")
                or "unknown"
            )

            # Extract user agent
            user_agent = headers.get("user-agent") or headers.get("User-Agent", "")

            # Extract authentication information
            auth_header = headers.get("authorization") or headers.get("Authorization", "")
            partner = self._resolved_partner(request_data)
            api_key_id = partner.get("api_key_id") or request_context.get("identity", {}).get("apiKeyId")

            # Determine authentication method
            auth_method = "none"
            if api_key_id:
                auth_method = "api_key"
            elif auth_header.startswith("Bearer "):
                auth_method = "jwt_token"

            return {
                "ip_address": ip_address,
                "user_agent": user_agent,
                "authentication_method": auth_method,
                "partner_id": partner.get("partner_id"),
                "api_key_id": api_key_id,
                "operation_id": partner.get("operation_id"),
                "headers": headers,
                "user_id": self._extract_user_id(request_data),
            }

        except Exception as e:
            logger.error(f"Failed to extract requester info: {str(e)}")
            return {}

    @staticmethod
    def _resolved_partner(request_data: Dict[str, Any]) -> Dict[str, Any]:
        """The partner context the auth decorators resolved for this request, if any."""
        if request_data.get("auth_type") == "partner_key":
            return request_data.get("authenticated_user") or {}
        return {}

    def _extract_user_id(self, request_data: Dict[str, Any]) -> str:
        """Extract user ID from request data."""
        try:
            # Try to get from Cognito context
            request_context = request_data.get("requestContext", {})
            authorizer = request_context.get("authorizer", {})
            claims = authorizer.get("claims", {})

            if claims and "sub" in claims:
                return claims["sub"]

            # Fallback to the resolved partner, then the native key id, then IP
            headers = request_data.get("headers", {})
            partner_id = self._resolved_partner(request_data).get("partner_id")
            if partner_id:
                return f"partner_{partner_id}"
            api_key_id = request_context.get("identity", {}).get("apiKeyId")
            if api_key_id:
                return f"apikey_{api_key_id}"

            # Last resort: use IP address — extract it directly here (do NOT call back into
            # _extract_requester_info, which calls _extract_user_id) to avoid the mutual
            # recursion that raised "maximum recursion depth exceeded" (wave0 Req 13.1).
            request_context = request_data.get("requestContext", {})
            ip_address = (
                headers.get("x-forwarded-for", "").split(",")[0].strip()
                or headers.get("x-real-ip")
                or request_context.get("identity", {}).get("sourceIp")
                or "unknown"
            )
            return f"ip_{ip_address.replace('.', '_')}"

        except Exception as e:
            logger.error(f"Failed to extract user ID: {str(e)}")
            return "unknown_user"

    def _determine_access_method(self, request_data: Dict[str, Any]) -> str:
        """Determine the access method (direct_api, mcp_server, etc.)."""
        if self.mcp_detector.is_mcp_request(request_data):
            return "mcp_server"
        else:
            return "direct_api"

    def _get_bot_id(self, request_data: Dict[str, Any], requester_info: Dict[str, Any]) -> str:
        """Get bot ID from either MCP metadata or bot detection."""
        if self.mcp_detector.is_mcp_request(request_data):
            mcp_metadata = self.mcp_detector.extract_mcp_metadata(request_data)
            return mcp_metadata.get("agent_id", "unknown_agent")
        else:
            return self.bot_detector.extract_bot_id(requester_info)

    def _extract_content_categories(self, response_data: Dict[str, Any]) -> List[str]:
        """Extract content categories from response data."""
        try:
            categories = set()
            results = response_data.get("results", [])

            for result in results:
                if isinstance(result, dict):
                    # Extract from iab_categories
                    iab_categories = result.get("iab_categories", [])
                    for category in iab_categories:
                        if isinstance(category, dict) and "name" in category:
                            categories.add(category["name"])
                        elif isinstance(category, str):
                            categories.add(category)

                    # Extract from content_type
                    content_type = result.get("content_type")
                    if content_type:
                        categories.add(content_type)

            return list(categories)

        except Exception as e:
            logger.error(f"Failed to extract content categories: {str(e)}")
            return []

    def _assess_query_complexity(self, query: str) -> float:
        """Assess the complexity of a search query."""
        try:
            if not query:
                return 0.0

            complexity_score = 0.0

            # Length factor
            complexity_score += min(len(query) / 100.0, 1.0) * 3.0

            # Word count factor
            word_count = len(query.split())
            complexity_score += min(word_count / 10.0, 1.0) * 2.0

            # Special characters and operators
            special_chars = sum(1 for c in query if c in "()[]{}+-*?|&")
            complexity_score += min(special_chars / 5.0, 1.0) * 2.0

            # Boolean operators
            boolean_operators = ["AND", "OR", "NOT", "and", "or", "not"]
            operator_count = sum(1 for op in boolean_operators if op in query)
            complexity_score += min(operator_count / 3.0, 1.0) * 3.0

            return min(complexity_score, 10.0)

        except Exception as e:
            logger.error(f"Failed to assess query complexity: {str(e)}")
            return 0.0
