"""
Analytics Collection Service

This module provides comprehensive analytics collection for the Content Monetization API,
with special focus on bot access tracking through search/query endpoints.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

import boto3

logger = logging.getLogger(__name__)


@dataclass
class AnalyticsMetric:
    """Data model for analytics metrics stored in DynamoDB"""

    metric_date: str  # YYYY-MM-DD
    metric_type_timestamp: str  # metric_type_YYYY-MM-DDTHH:MM:SSZ
    metric_type: str  # api_requests, content_access, revenue, etc.
    value: Union[int, float]
    metadata: Dict[str, Any]
    timestamp: str  # ISO format timestamp for GSI


@dataclass
class BotAccessRecord:
    """Data model for bot access tracking"""

    metric_date: str  # YYYY-MM-DD (Partition Key)
    metric_type_timestamp: str  # bot_access_bot_id_YYYY-MM-DDTHH:MM:SSZ (Sort Key)
    metric_type: str  # bot_access
    bot_id: str
    access_method: str  # direct_api, mcp_server
    access_type: str  # search_query, direct_query, bulk_access
    content_accessed: List[str]
    query_details: Dict[str, Any]
    timestamp: str  # ISO format timestamp for GSI
    mcp_metadata: Optional[Dict[str, Any]] = None
    requester_metadata: Dict[str, Any] = None
    business_context: Dict[str, Any] = None
    revenue_impact: Decimal = Decimal("0.0")


@dataclass
class MCPBusinessMetric:
    """Data model for MCP business metrics (separate from AgentCore technical metrics)"""

    metric_date: str  # YYYY-MM-DD (Partition Key)
    metric_type_timestamp: str  # mcp_tool_usage_YYYY-MM-DDTHH:MM:SSZ (Sort Key)
    metric_type: str  # mcp_tool_usage
    tool_name: str
    agent_id: str
    usage_count: int
    content_accessed_count: int
    business_metrics: Dict[str, Any]
    technical_metrics: Dict[str, Any]
    agentcore_correlation_id: Optional[str] = None


class AnalyticsCollector:
    """
    Main analytics collection service for recording API usage, content access,
    and bot interaction metrics.
    """

    def __init__(self, table_name: str = None):
        """
        Initialize the analytics collector.

        Args:
            table_name: DynamoDB table name for analytics data
        """
        self.dynamodb = boto3.resource("dynamodb")
        self.table_name = table_name or self._get_analytics_table_name()
        self.table = self.dynamodb.Table(self.table_name)

    def _get_analytics_table_name(self) -> str:
        """Get analytics table name from environment or use default"""
        import os

        return os.environ.get("ANALYTICS_TABLE_NAME", "iab-analytics-table")

    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        return datetime.now(timezone.utc).isoformat()

    def _get_current_date(self) -> str:
        """Get current date in YYYY-MM-DD format"""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def record_api_access(self, endpoint: str, user_id: str, metadata: Dict[str, Any]) -> bool:
        """
        Record API usage metrics.

        Args:
            endpoint: API endpoint accessed
            user_id: User or bot identifier
            metadata: Additional request metadata

        Returns:
            bool: Success status
        """
        try:
            timestamp = self._get_current_timestamp()
            date = self._get_current_date()

            metric = AnalyticsMetric(
                metric_date=date,
                metric_type_timestamp=f"api_requests_{timestamp}",
                metric_type="api_requests",
                value=1,
                metadata={"endpoint": endpoint, "user_id": user_id, "timestamp": timestamp, **metadata},
                timestamp=timestamp,
            )

            # Convert float values to Decimal for DynamoDB
            item_dict = asdict(metric)
            item_dict = self._convert_floats_to_decimal(item_dict)
            self.table.put_item(Item=item_dict)
            logger.info(f"Recorded API access: {endpoint} by {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to record API access: {str(e)}")
            return False

    def record_content_access(self, content_id: str, access_type: str, requester_info: Dict[str, Any]) -> bool:
        """
        Track content usage patterns including bot access.

        Args:
            content_id: ID of accessed content
            access_type: Type of access (query, search, etc.)
            requester_info: Information about the requester

        Returns:
            bool: Success status
        """
        try:
            timestamp = self._get_current_timestamp()
            date = self._get_current_date()

            metric = AnalyticsMetric(
                metric_date=date,
                metric_type_timestamp=f"content_access_{timestamp}",
                metric_type="content_access",
                value=1,
                metadata={
                    "content_id": content_id,
                    "access_type": access_type,
                    "requester_info": requester_info,
                    "timestamp": timestamp,
                },
                timestamp=timestamp,
            )

            # Convert float values to Decimal for DynamoDB
            item_dict = asdict(metric)
            item_dict = self._convert_floats_to_decimal(item_dict)
            self.table.put_item(Item=item_dict)
            logger.info(f"Recorded content access: {content_id} via {access_type}")
            return True

        except Exception as e:
            logger.error(f"Failed to record content access: {str(e)}")
            return False

    def record_search_query(self, query: str, results_count: int, requester_info: Dict[str, Any]) -> bool:
        """
        Track search/query endpoint usage - primary bot access point.

        Args:
            query: Search query string
            results_count: Number of results returned
            requester_info: Information about the requester

        Returns:
            bool: Success status
        """
        try:
            timestamp = self._get_current_timestamp()
            date = self._get_current_date()

            metric = AnalyticsMetric(
                metric_date=date,
                metric_type_timestamp=f"search_query_{timestamp}",
                metric_type="search_query",
                value=results_count,
                metadata={
                    "query": query,
                    "results_count": results_count,
                    "requester_info": requester_info,
                    "timestamp": timestamp,
                },
                timestamp=timestamp,
            )

            # Convert float values to Decimal for DynamoDB
            item_dict = asdict(metric)
            item_dict = self._convert_floats_to_decimal(item_dict)
            self.table.put_item(Item=item_dict)
            logger.info(f"Recorded search query: '{query}' returned {results_count} results")
            return True

        except Exception as e:
            logger.error(f"Failed to record search query: {str(e)}")
            return False

    def record_mcp_tool_usage(
        self, tool_name: str, agent_id: str, content_accessed: List[str], metadata: Dict[str, Any]
    ) -> bool:
        """
        Track MCP server tool usage for business analytics.

        Args:
            tool_name: Name of the MCP tool used
            agent_id: ID of the agent using the tool
            content_accessed: List of content IDs accessed
            metadata: Additional MCP metadata

        Returns:
            bool: Success status
        """
        try:
            timestamp = self._get_current_timestamp()
            date = self._get_current_date()

            business_metric = MCPBusinessMetric(
                metric_date=date,
                metric_type_timestamp=f"mcp_tool_usage_{timestamp}",
                metric_type="mcp_tool_usage",
                tool_name=tool_name,
                agent_id=agent_id,
                usage_count=1,
                content_accessed_count=len(content_accessed),
                business_metrics={
                    "content_accessed": content_accessed,
                    "tool_parameters": metadata.get("parameters", {}),
                    "response_size": metadata.get("response_size", 0),
                },
                technical_metrics={
                    "response_time_ms": metadata.get("response_time_ms", 0),
                    "content_retrieval_time_ms": metadata.get("content_retrieval_time_ms", 0),
                    "processing_time_ms": metadata.get("processing_time_ms", 0),
                },
                agentcore_correlation_id=metadata.get("agentcore_correlation_id"),
            )

            # Convert float values to Decimal for DynamoDB
            item_dict = asdict(business_metric)
            item_dict = self._convert_floats_to_decimal(item_dict)
            self.table.put_item(Item=item_dict)
            logger.info(f"Recorded MCP tool usage: {tool_name} by {agent_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to record MCP tool usage: {str(e)}")
            return False

    def record_bot_access(self, bot_id: str, content_accessed: List[str], query_type: str, access_method: str) -> bool:
        """
        Track bot content access patterns (direct API or MCP).

        Args:
            bot_id: ID of the bot
            content_accessed: List of content IDs accessed
            query_type: Type of query (search_query, direct_query, etc.)
            access_method: Method of access (direct_api, mcp_server)

        Returns:
            bool: Success status
        """
        try:
            timestamp = self._get_current_timestamp()
            date = self._get_current_date()

            bot_record = BotAccessRecord(
                metric_date=date,
                metric_type_timestamp=f"bot_access_{bot_id}_{timestamp}",
                metric_type="bot_access",
                bot_id=bot_id,
                access_method=access_method,
                access_type=query_type,
                content_accessed=content_accessed,
                query_details={"timestamp": timestamp, "content_count": len(content_accessed)},
                timestamp=timestamp,
                revenue_impact=self._calculate_revenue_impact(content_accessed),
            )

            # Convert float values to Decimal for DynamoDB
            item_dict = asdict(bot_record)
            item_dict = self._convert_floats_to_decimal(item_dict)
            self.table.put_item(Item=item_dict)
            logger.info(f"Recorded bot access: {bot_id} accessed {len(content_accessed)} items via {access_method}")
            return True

        except Exception as e:
            logger.error(f"Failed to record bot access: {str(e)}")
            return False

    def record_business_metrics(self, metric_type: str, value: float, context: Dict[str, Any]) -> bool:
        """
        Record business-level metrics separate from AgentCore technical metrics.

        Args:
            metric_type: Type of business metric
            value: Metric value
            context: Business context information

        Returns:
            bool: Success status
        """
        try:
            timestamp = self._get_current_timestamp()
            date = self._get_current_date()

            metric = AnalyticsMetric(
                metric_date=date,
                metric_type_timestamp=f"{metric_type}_{timestamp}",
                metric_type=metric_type,
                value=value,
                metadata={"context": context, "timestamp": timestamp},
                timestamp=timestamp,
            )

            # Convert float values to Decimal for DynamoDB
            item_dict = asdict(metric)
            item_dict = self._convert_floats_to_decimal(item_dict)
            self.table.put_item(Item=item_dict)
            logger.info(f"Recorded business metric: {metric_type} = {value}")
            return True

        except Exception as e:
            logger.error(f"Failed to record business metric: {str(e)}")
            return False

    def aggregate_metrics(self, time_period: str) -> Dict[str, Any]:
        """
        Generate aggregated analytics data.

        Args:
            time_period: Time period for aggregation (day, week, month)

        Returns:
            dict: Aggregated metrics data
        """
        try:
            # Implementation for metrics aggregation
            # This would query the analytics table and aggregate data
            # For now, return a placeholder structure
            return {
                "time_period": time_period,
                "total_api_requests": 0,
                "total_content_access": 0,
                "bot_access_count": 0,
                "mcp_tool_usage": 0,
                "revenue_generated": 0.0,
            }

        except Exception as e:
            logger.error(f"Failed to aggregate metrics: {str(e)}")
            return {}

    def export_analytics_data(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Export analytics for reporting.

        Args:
            filters: Filters for data export

        Returns:
            dict: Exported analytics data
        """
        try:
            # Implementation for data export
            # This would query and format data for export
            return {"filters": filters, "data": [], "export_timestamp": self._get_current_timestamp()}

        except Exception as e:
            logger.error(f"Failed to export analytics data: {str(e)}")
            return {}

    def _calculate_revenue_impact(self, content_accessed: List[str]) -> Decimal:
        """
        Calculate revenue impact based on content access.

        Args:
            content_accessed: List of content IDs

        Returns:
            Decimal: Calculated revenue impact
        """
        # Simple calculation: $0.10 per content item accessed
        # This would be replaced with actual pricing logic
        return Decimal("0.10") * Decimal(str(len(content_accessed)))

    def _convert_floats_to_decimal(self, obj):
        """Convert float values to Decimal for DynamoDB compatibility."""
        if isinstance(obj, dict):
            return {k: self._convert_floats_to_decimal(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_floats_to_decimal(item) for item in obj]
        elif isinstance(obj, float):
            return Decimal(str(obj))
        else:
            return obj
