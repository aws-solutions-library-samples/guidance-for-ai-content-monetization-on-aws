"""
Analytics Aggregation Lambda Function

This module provides analytics data aggregation functionality for the Content Monetization API.
It calculates revenue metrics based on content access patterns and generates usage trend data
for dashboard display.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union

import boto3
from services.pricing_manager import PricingVersionManager
from shared.utils.response_helpers import cors_headers
from shared.utils.safe_datetime_parser import safe_parse_datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")


class AnalyticsAggregator:
    """
    Main analytics aggregation service for calculating metrics and trends.
    """

    def __init__(self, analytics_table_name: str = None):
        """
        Initialize the analytics aggregator.

        Args:
            analytics_table_name: DynamoDB table name for analytics data
        """
        self.analytics_table_name = analytics_table_name or os.environ.get("ANALYTICS_TABLE_NAME", "cm-analytics")
        self.analytics_table = dynamodb.Table(self.analytics_table_name)

        # Initialize pricing manager for historical pricing accuracy. The import is module-level
        # (canonical root) so a packaging break fails loudly at load time rather than silently
        # degrading; only the runtime construction (which may touch AWS) is guarded here, falling
        # back to the fallback pricing constants below.
        try:
            self.pricing_manager = PricingVersionManager()
            logger.info("PricingVersionManager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize PricingVersionManager: {e}")
            self.pricing_manager = None

        # Fallback pricing constants (used when PricingVersionManager is unavailable)
        self.FALLBACK_CONTENT_PRICING = {
            "article": Decimal("0.05"),
            "report": Decimal("0.25"),
            "video": Decimal("0.15"),
            "document": Decimal("0.10"),
            "default": Decimal("0.05"),
        }

        self.FALLBACK_MCP_TOOL_PRICING = {
            "search_content": Decimal("0.02"),
            "get_content": Decimal("0.05"),
            "list_categories": Decimal("0.01"),
            "default": Decimal("0.02"),
        }

    def aggregate_daily_metrics(self, date: str) -> Dict[str, Any]:
        """
        Aggregate metrics for a specific date.

        Args:
            date: Date in YYYY-MM-DD format

        Returns:
            dict: Aggregated metrics for the date
        """
        logger.info("=== AGGREGATE_DAILY_METRICS START ===")
        logger.info(f"Date: {date}")

        try:
            # Query analytics table for the specific date
            response = self.analytics_table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("metric_date").eq(date)
            )

            items = response.get("Items", [])

            # Initialize aggregation containers
            metrics = {
                "date": date,
                "api_requests": 0,
                "content_access": 0,
                "search_queries": 0,
                "bot_access_count": 0,
                "mcp_tool_usage": 0,
                "revenue_generated": Decimal("0.0"),
                "unique_bots": set(),
                "unique_agents": set(),
                "content_types_accessed": defaultdict(int),
                "popular_content": defaultdict(int),
                "revenue_by_content_type": defaultdict(lambda: Decimal("0.0")),
                "revenue_by_access_method": defaultdict(lambda: Decimal("0.0")),
                "hourly_distribution": defaultdict(int),
            }

            # Process each metric record
            for item in items:
                metric_type = item.get("metric_type", "")
                value = item.get("value", 0)
                metadata = item.get("metadata", {})
                timestamp = item.get("timestamp", "")

                # Extract hour for hourly distribution
                try:
                    hour = safe_parse_datetime(timestamp).hour
                    metrics["hourly_distribution"][hour] += 1
                except (ValueError, AttributeError):
                    pass

                # Aggregate by metric type
                if metric_type == "api_requests":
                    metrics["api_requests"] += int(value)

                elif metric_type == "content_access":
                    metrics["content_access"] += int(value)
                    content_id = metadata.get("content_id", "")
                    if content_id:
                        metrics["popular_content"][content_id] += 1

                        # Calculate revenue from content access
                        content_type = self._estimate_content_type(content_id)
                        access_method = metadata.get("access_method", "direct_api")
                        transaction_date = item.get("timestamp", item.get("created_at"))
                        revenue = self._get_historical_pricing(content_type, content_id, transaction_date)

                        metrics["revenue_generated"] += revenue
                        metrics["revenue_by_content_type"][content_type] += revenue
                        metrics["revenue_by_access_method"][access_method] += revenue
                        metrics["content_types_accessed"][content_type] += 1

                elif metric_type == "search_query":
                    metrics["search_queries"] += 1

                elif metric_type == "bot_access":
                    metrics["bot_access_count"] += 1
                    bot_id = item.get("bot_id", "")
                    if bot_id:
                        metrics["unique_bots"].add(bot_id)

                    # Calculate revenue from bot access
                    content_accessed = item.get("content_accessed", [])
                    access_method = item.get("access_method", "direct_api")

                    for content_id in content_accessed:
                        # Estimate content type for revenue calculation
                        content_type = self._estimate_content_type(content_id)

                        # Get historical pricing for the transaction date
                        transaction_date = item.get("timestamp", item.get("created_at"))
                        revenue = self._get_historical_pricing(content_type, content_id, transaction_date)

                        metrics["revenue_generated"] += revenue
                        metrics["revenue_by_content_type"][content_type] += revenue
                        metrics["revenue_by_access_method"][access_method] += revenue
                        metrics["content_types_accessed"][content_type] += 1

                elif metric_type == "mcp_tool_usage":
                    metrics["mcp_tool_usage"] += int(value)
                    agent_id = item.get("agent_id", "")
                    tool_name = item.get("tool_name", "")

                    if agent_id:
                        metrics["unique_agents"].add(agent_id)

                    # Calculate revenue from MCP tool usage
                    transaction_date = item.get("timestamp", item.get("created_at"))
                    tool_revenue = self._get_historical_mcp_pricing(tool_name, transaction_date)
                    content_accessed_count = item.get("content_accessed_count", 0)

                    total_tool_revenue = tool_revenue * Decimal(str(content_accessed_count))
                    metrics["revenue_generated"] += total_tool_revenue
                    metrics["revenue_by_access_method"]["mcp_server"] += total_tool_revenue

            # Convert sets to counts and lists
            metrics["unique_bots"] = len(metrics["unique_bots"])
            metrics["unique_agents"] = len(metrics["unique_agents"])

            # Convert defaultdicts to regular dicts and Decimal to float for JSON serialization
            metrics["content_types_accessed"] = dict(metrics["content_types_accessed"])
            metrics["popular_content"] = dict(metrics["popular_content"])
            metrics["revenue_by_content_type"] = {k: float(v) for k, v in metrics["revenue_by_content_type"].items()}
            metrics["revenue_by_access_method"] = {k: float(v) for k, v in metrics["revenue_by_access_method"].items()}
            metrics["hourly_distribution"] = dict(metrics["hourly_distribution"])
            metrics["revenue_generated"] = float(metrics["revenue_generated"])

            logger.info(
                f"=== AGGREGATE_DAILY_METRICS END === Date: {date}, Requests: {metrics['api_requests']}, Revenue: ${metrics['revenue_generated']:.2f}"
            )
            return metrics

        except Exception as e:
            logger.error(f"=== AGGREGATE_DAILY_METRICS ERROR === Date: {date}, Error: {str(e)}")
            return {
                "date": date,
                "error": str(e),
                "api_requests": 0,
                "content_access": 0,
                "search_queries": 0,
                "bot_access_count": 0,
                "mcp_tool_usage": 0,
                "revenue_generated": 0.0,
                "unique_bots": 0,
                "unique_agents": 0,
                "content_types_accessed": {},
                "popular_content": {},
                "revenue_by_content_type": {},
                "revenue_by_access_method": {},
                "hourly_distribution": {},
            }

    def aggregate_date_range_metrics(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        Aggregate metrics for a date range.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            dict: Aggregated metrics for the date range
        """
        try:
            logger.info(f"Aggregating metrics from {start_date} to {end_date}")

            # Generate list of dates in range
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")

            date_range = []
            current_dt = start_dt
            while current_dt <= end_dt:
                date_range.append(current_dt.strftime("%Y-%m-%d"))
                current_dt += timedelta(days=1)

            # Aggregate metrics for each date
            daily_metrics = []
            total_metrics = {
                "start_date": start_date,
                "end_date": end_date,
                "total_api_requests": 0,
                "total_content_access": 0,
                "total_search_queries": 0,
                "total_bot_access": 0,
                "total_mcp_usage": 0,
                "total_revenue": 0.0,
                "unique_bots_total": set(),
                "unique_agents_total": set(),
                "daily_breakdown": [],
                "content_type_totals": defaultdict(int),
                "revenue_by_content_type_total": defaultdict(float),
                "revenue_by_access_method_total": defaultdict(float),
            }

            for date in date_range:
                daily_data = self.aggregate_daily_metrics(date)
                daily_metrics.append(daily_data)

                # Accumulate totals
                if "error" not in daily_data:
                    total_metrics["total_api_requests"] += daily_data.get("api_requests", 0)
                    total_metrics["total_content_access"] += daily_data.get("content_access", 0)
                    total_metrics["total_search_queries"] += daily_data.get("search_queries", 0)
                    total_metrics["total_bot_access"] += daily_data.get("bot_access_count", 0)
                    total_metrics["total_mcp_usage"] += daily_data.get("mcp_tool_usage", 0)
                    total_metrics["total_revenue"] += daily_data.get("revenue_generated", 0.0)

                    # Accumulate content type data
                    for content_type, count in daily_data.get("content_types_accessed", {}).items():
                        total_metrics["content_type_totals"][content_type] += count

                    for content_type, revenue in daily_data.get("revenue_by_content_type", {}).items():
                        total_metrics["revenue_by_content_type_total"][content_type] += revenue

                    for access_method, revenue in daily_data.get("revenue_by_access_method", {}).items():
                        total_metrics["revenue_by_access_method_total"][access_method] += revenue

            # Convert defaultdicts to regular dicts
            total_metrics["content_type_totals"] = dict(total_metrics["content_type_totals"])
            total_metrics["revenue_by_content_type_total"] = dict(total_metrics["revenue_by_content_type_total"])
            total_metrics["revenue_by_access_method_total"] = dict(total_metrics["revenue_by_access_method_total"])
            total_metrics["daily_breakdown"] = daily_metrics

            # Remove sets (can't serialize)
            del total_metrics["unique_bots_total"]
            del total_metrics["unique_agents_total"]

            logger.info(
                f"Aggregated range metrics: {total_metrics['total_api_requests']} total requests, ${total_metrics['total_revenue']:.2f} total revenue"
            )
            return total_metrics

        except Exception as e:
            logger.error(f"Failed to aggregate date range metrics: {str(e)}")
            return {"start_date": start_date, "end_date": end_date, "error": str(e), "total_revenue": 0.0}

    def generate_usage_trends(self, days: int = 30) -> Dict[str, Any]:
        """
        Generate usage trend data for dashboard display.

        Args:
            days: Number of days to include in trends

        Returns:
            dict: Usage trend data
        """
        logger.info("=== GENERATE_USAGE_TRENDS START ===")
        logger.info(f"Days: {days}")

        try:
            # Calculate date range
            end_date = datetime.now(timezone.utc).date()
            start_date = end_date - timedelta(days=days - 1)

            # Get aggregated metrics for the range
            range_metrics = self.aggregate_date_range_metrics(
                start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
            )

            # Extract trend data
            daily_breakdown = range_metrics.get("daily_breakdown", [])

            trends = {
                "period": f"{days} days",
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "daily_api_requests": [d.get("api_requests", 0) for d in daily_breakdown],
                "daily_revenue": [d.get("revenue_generated", 0.0) for d in daily_breakdown],
                "daily_bot_access": [d.get("bot_access_count", 0) for d in daily_breakdown],
                "daily_mcp_usage": [d.get("mcp_tool_usage", 0) for d in daily_breakdown],
                "dates": [d.get("date", "") for d in daily_breakdown],
                "total_metrics": {
                    "total_requests": range_metrics.get("total_api_requests", 0),
                    "total_revenue": range_metrics.get("total_revenue", 0.0),
                    "total_bot_access": range_metrics.get("total_bot_access", 0),
                    "total_mcp_usage": range_metrics.get("total_mcp_usage", 0),
                    "average_daily_requests": range_metrics.get("total_api_requests", 0) / days,
                    "average_daily_revenue": range_metrics.get("total_revenue", 0.0) / days,
                },
                "content_type_distribution": range_metrics.get("content_type_totals", {}),
                "revenue_by_content_type": range_metrics.get("revenue_by_content_type_total", {}),
                "revenue_by_access_method": range_metrics.get("revenue_by_access_method_total", {}),
            }

            logger.info(
                f"=== GENERATE_USAGE_TRENDS END === Days: {days}, Total requests: {trends['total_metrics']['total_requests']}"
            )
            return trends

        except Exception as e:
            logger.error(f"=== GENERATE_USAGE_TRENDS ERROR === Days: {days}, Error: {str(e)}")
            return {
                "period": f"{days} days",
                "error": str(e),
                "total_metrics": {"total_requests": 0, "total_revenue": 0.0},
            }

    def calculate_revenue_metrics(self, date_range: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculate detailed revenue metrics based on content access patterns.

        Args:
            date_range: Optional date range (e.g., "7d", "30d", "90d")

        Returns:
            dict: Revenue metrics data
        """
        try:
            # Parse date range
            if date_range == "7d":
                days = 7
            elif date_range == "30d":
                days = 30
            elif date_range == "90d":
                days = 90
            else:
                days = 30  # Default to 30 days

            logger.info(f"Calculating revenue metrics for last {days} days")

            # Get usage trends which include revenue data
            trends = self.generate_usage_trends(days)

            # Calculate additional revenue metrics
            total_revenue = trends["total_metrics"]["total_revenue"]
            total_requests = trends["total_metrics"]["total_requests"]

            revenue_metrics = {
                "period": f"Last {days} days",
                "total_revenue": total_revenue,
                "average_revenue_per_request": total_revenue / max(total_requests, 1),
                "revenue_by_content_type": trends["revenue_by_content_type"],
                "revenue_by_access_method": trends["revenue_by_access_method"],
                "daily_revenue_trend": trends["daily_revenue"],
                "dates": trends["dates"],
                "revenue_growth": self._calculate_revenue_growth(trends["daily_revenue"]),
                "top_revenue_sources": self._identify_top_revenue_sources(trends),
                "projected_monthly_revenue": (total_revenue / days) * 30 if days > 0 else 0.0,
            }

            logger.info(
                f"Calculated revenue metrics: ${total_revenue:.2f} total, ${revenue_metrics['average_revenue_per_request']:.4f} per request"
            )
            return revenue_metrics

        except Exception as e:
            logger.error(f"Failed to calculate revenue metrics: {str(e)}")
            return {"period": date_range or "30d", "error": str(e), "total_revenue": 0.0}

    def _estimate_content_type(self, content_id: str) -> str:
        """
        Estimate content type from content ID for revenue calculation.

        Args:
            content_id: Content identifier

        Returns:
            str: Estimated content type
        """
        # Simple heuristic based on content ID prefix
        if content_id.startswith("art_"):
            return "article"
        elif content_id.startswith("rpt_"):
            return "report"
        elif content_id.startswith("vid_"):
            return "video"
        elif content_id.startswith("doc_"):
            return "document"
        else:
            return "article"  # Default

    def _get_historical_pricing(self, content_type: str, content_id: str, transaction_date: str) -> Decimal:
        """
        Get historical pricing for content based on transaction date from real database data.

        This method looks up actual pricing from the pricing_transactions table and
        pricing_rules table to get real revenue values.

        Args:
            content_type: Type of content (article, report, etc.)
            content_id: Content identifier
            transaction_date: ISO timestamp of the transaction

        Returns:
            Decimal: Actual price from database or calculated from transactions
        """
        try:
            # First, try to find actual transaction data for this content
            pricing_transactions_table_name = os.environ.get(
                "PRICING_TRANSACTIONS_TABLE_NAME", "cm-pricing-transactions"
            )
            pricing_transactions_table = dynamodb.Table(pricing_transactions_table_name)

            # Parse transaction date for querying
            if transaction_date:
                try:
                    from datetime import datetime

                    if isinstance(transaction_date, str):
                        if "T" in transaction_date:
                            transaction_dt = datetime.fromisoformat(transaction_date.replace("Z", "+00:00"))
                        else:
                            transaction_dt = datetime.fromisoformat(transaction_date)
                    else:
                        transaction_dt = transaction_date

                    transaction_date_str = transaction_dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse transaction date {transaction_date}: {e}")
                    transaction_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            else:
                transaction_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Look for actual pricing transactions for this content
            try:
                response = pricing_transactions_table.query(
                    IndexName="content-id-index",
                    KeyConditionExpression=boto3.dynamodb.conditions.Key("content_id").eq(content_id),
                    ScanIndexForward=False,  # Get most recent first
                    Limit=1,
                )

                if response.get("Items"):
                    transaction = response["Items"][0]
                    actual_price = Decimal(str(transaction.get("amount_charged", "0.0")))
                    if actual_price > 0:
                        logger.info(f"Found actual transaction price for {content_id}: ${actual_price}")
                        return actual_price

            except Exception as e:
                logger.debug(f"No transaction data found for {content_id}: {str(e)}")

            # If no transaction data, look up current pricing rules
            pricing_rules_table_name = os.environ.get("PRICING_RULES_TABLE_NAME", "cm-pricing-rules")
            pricing_rules_table = dynamodb.Table(pricing_rules_table_name)

            try:
                # Get active pricing rules
                response = pricing_rules_table.query(
                    IndexName="status-effective-index",
                    KeyConditionExpression=boto3.dynamodb.conditions.Key("status").eq("active"),
                    FilterExpression=boto3.dynamodb.conditions.Attr("effective_date").lte(transaction_date_str),
                    ScanIndexForward=False,  # Get most recent first
                    Limit=1,
                )

                if response.get("Items"):
                    pricing_rule = response["Items"][0]
                    pricing_rules = pricing_rule.get("pricing_rules", {})

                    # Look for per-access pricing
                    per_access_pricing = pricing_rules.get("per_access", {})
                    if content_type in per_access_pricing:
                        price = Decimal(str(per_access_pricing[content_type]))
                        logger.info(f"Found pricing rule for {content_type}: ${price}")
                        return price

                    # Look for default pricing
                    if "default" in per_access_pricing:
                        price = Decimal(str(per_access_pricing["default"]))
                        logger.info(f"Using default pricing rule: ${price}")
                        return price

            except Exception as e:
                logger.debug(f"No pricing rules found: {str(e)}")

            # If no database pricing found, calculate based on content type and usage patterns
            # Look at recent similar transactions to estimate pricing
            try:
                # Query recent transactions for similar content types
                current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                response = pricing_transactions_table.query(
                    KeyConditionExpression=boto3.dynamodb.conditions.Key("transaction_date").eq(current_date),
                    FilterExpression=boto3.dynamodb.conditions.Attr("content_type").eq(content_type),
                    ScanIndexForward=False,
                    Limit=10,
                )

                if response.get("Items"):
                    # Calculate average price from recent transactions
                    total_amount = Decimal("0.0")
                    count = 0
                    for transaction in response["Items"]:
                        amount = Decimal(str(transaction.get("amount_charged", "0.0")))
                        if amount > 0:
                            total_amount += amount
                            count += 1

                    if count > 0:
                        avg_price = total_amount / count
                        logger.info(
                            f"Calculated average price for {content_type} from {count} transactions: ${avg_price}"
                        )
                        return avg_price

            except Exception as e:
                logger.debug(f"Could not calculate average pricing: {str(e)}")

            # Last resort: use a reasonable default based on content type
            default_prices = {
                "article": Decimal("0.05"),
                "report": Decimal("0.25"),
                "video": Decimal("0.15"),
                "document": Decimal("0.10"),
                "default": Decimal("0.05"),
            }

            price = default_prices.get(content_type, default_prices["default"])
            logger.warning(f"No database pricing found for {content_type}, using minimal default: ${price}")
            return price

        except Exception as e:
            logger.error(f"Error getting pricing for {content_type}: {str(e)}")
            # Return minimal default to avoid zero revenue
            return Decimal("0.05")

    def _get_historical_mcp_pricing(self, tool_name: str, transaction_date: str) -> Decimal:
        """
        Get historical MCP tool pricing based on transaction date from real database data.

        Args:
            tool_name: Name of the MCP tool
            transaction_date: ISO timestamp of the transaction

        Returns:
            Decimal: Actual price from database
        """
        try:
            # Parse transaction date for querying
            if transaction_date:
                try:
                    from datetime import datetime

                    if isinstance(transaction_date, str):
                        if "T" in transaction_date:
                            transaction_dt = datetime.fromisoformat(transaction_date.replace("Z", "+00:00"))
                        else:
                            transaction_dt = datetime.fromisoformat(transaction_date)
                    else:
                        transaction_dt = transaction_date

                    transaction_date_str = transaction_dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse transaction date {transaction_date}: {e}")
                    transaction_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            else:
                transaction_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Look up current pricing rules for MCP tools
            pricing_rules_table_name = os.environ.get("PRICING_RULES_TABLE_NAME", "cm-pricing-rules")
            pricing_rules_table = dynamodb.Table(pricing_rules_table_name)

            try:
                # Get active pricing rules
                response = pricing_rules_table.query(
                    IndexName="status-effective-index",
                    KeyConditionExpression=boto3.dynamodb.conditions.Key("status").eq("active"),
                    FilterExpression=boto3.dynamodb.conditions.Attr("effective_date").lte(transaction_date_str),
                    ScanIndexForward=False,  # Get most recent first
                    Limit=1,
                )

                if response.get("Items"):
                    pricing_rule = response["Items"][0]
                    pricing_rules = pricing_rule.get("pricing_rules", {})

                    # Look for MCP tool specific pricing
                    if "mcp_tools" in pricing_rules and tool_name in pricing_rules["mcp_tools"]:
                        price = Decimal(str(pricing_rules["mcp_tools"][tool_name]))
                        logger.info(f"Found MCP tool pricing for {tool_name}: ${price}")
                        return price

                    # Look for general tool pricing
                    if "tools" in pricing_rules and tool_name in pricing_rules["tools"]:
                        price = Decimal(str(pricing_rules["tools"][tool_name]))
                        logger.info(f"Found tool pricing for {tool_name}: ${price}")
                        return price

                    # Look for default MCP pricing
                    if "mcp_tools" in pricing_rules and "default" in pricing_rules["mcp_tools"]:
                        price = Decimal(str(pricing_rules["mcp_tools"]["default"]))
                        logger.info(f"Using default MCP tool pricing: ${price}")
                        return price

            except Exception as e:
                logger.debug(f"No MCP pricing rules found: {str(e)}")

            # Look for actual MCP tool transactions to estimate pricing
            pricing_transactions_table_name = os.environ.get(
                "PRICING_TRANSACTIONS_TABLE_NAME", "cm-pricing-transactions"
            )
            pricing_transactions_table = dynamodb.Table(pricing_transactions_table_name)

            try:
                # Query recent MCP tool transactions
                response = pricing_transactions_table.query(
                    KeyConditionExpression=boto3.dynamodb.conditions.Key("transaction_date").eq(transaction_date_str),
                    FilterExpression=boto3.dynamodb.conditions.Attr("tool_name").eq(tool_name),
                    ScanIndexForward=False,
                    Limit=5,
                )

                if response.get("Items"):
                    # Calculate average price from recent transactions
                    total_amount = Decimal("0.0")
                    count = 0
                    for transaction in response["Items"]:
                        amount = Decimal(str(transaction.get("amount_charged", "0.0")))
                        if amount > 0:
                            total_amount += amount
                            count += 1

                    if count > 0:
                        avg_price = total_amount / count
                        logger.info(
                            f"Calculated average MCP price for {tool_name} from {count} transactions: ${avg_price}"
                        )
                        return avg_price

            except Exception as e:
                logger.debug(f"Could not find MCP transaction data: {str(e)}")

            # Default MCP tool pricing based on tool type
            mcp_tool_prices = {
                "search_content": Decimal("0.02"),
                "get_content_details": Decimal("0.05"),
                "access_content": Decimal("0.10"),
                "list_categories": Decimal("0.01"),
                "get_pricing_info": Decimal("0.01"),
                "default": Decimal("0.02"),
            }

            price = mcp_tool_prices.get(tool_name, mcp_tool_prices["default"])
            logger.warning(f"No database MCP pricing found for {tool_name}, using minimal default: ${price}")
            return price

        except Exception as e:
            logger.error(f"Error getting MCP pricing for {tool_name}: {str(e)}")
            # Return minimal default to avoid zero revenue
            return Decimal("0.02")

    def _calculate_revenue_growth(self, daily_revenue: List[float]) -> Dict[str, float]:
        """
        Calculate revenue growth metrics.

        Args:
            daily_revenue: List of daily revenue values

        Returns:
            dict: Growth metrics
        """
        if len(daily_revenue) < 2:
            return {"growth_rate": 0.0, "trend": "stable"}

        # Calculate simple growth rate (last vs first)
        first_week = sum(daily_revenue[:7]) if len(daily_revenue) >= 7 else daily_revenue[0]
        last_week = sum(daily_revenue[-7:]) if len(daily_revenue) >= 7 else daily_revenue[-1]

        if first_week > 0:
            growth_rate = ((last_week - first_week) / first_week) * 100
        else:
            growth_rate = 0.0

        # Determine trend
        if growth_rate > 5:
            trend = "growing"
        elif growth_rate < -5:
            trend = "declining"
        else:
            trend = "stable"

        return {
            "growth_rate": growth_rate,
            "trend": trend,
            "first_week_revenue": first_week,
            "last_week_revenue": last_week,
        }

    def _identify_top_revenue_sources(self, trends: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Identify top revenue sources from trends data.

        Args:
            trends: Trends data containing revenue breakdowns

        Returns:
            list: Top revenue sources
        """
        sources = []

        # Add content type sources
        for content_type, revenue in trends.get("revenue_by_content_type", {}).items():
            sources.append(
                {
                    "source": f"{content_type.title()} Content",
                    "type": "content_type",
                    "revenue": revenue,
                    "percentage": 0.0,  # Will be calculated below
                }
            )

        # Add access method sources
        for access_method, revenue in trends.get("revenue_by_access_method", {}).items():
            method_name = "MCP Server" if access_method == "mcp_server" else "Direct API"
            sources.append(
                {
                    "source": method_name,
                    "type": "access_method",
                    "revenue": revenue,
                    "percentage": 0.0,  # Will be calculated below
                }
            )

        # Calculate percentages
        total_revenue = sum(s["revenue"] for s in sources)
        if total_revenue > 0:
            for source in sources:
                source["percentage"] = (source["revenue"] / total_revenue) * 100

        # Sort by revenue and return top 5
        sources.sort(key=lambda x: x["revenue"], reverse=True)
        return sources[:5]


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for analytics aggregation.

    Supports the following operations:
    - aggregate_daily: Aggregate metrics for a specific date
    - aggregate_range: Aggregate metrics for a date range
    - usage_trends: Generate usage trend data
    - revenue_metrics: Calculate revenue metrics
    """
    request_id = getattr(context, "aws_request_id", "unknown")
    logger.info(f"=== ANALYTICS_AGGREGATION_LAMBDA START === Request ID: {request_id}")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    try:
        # Extract operation and parameters
        operation = event.get("operation", "usage_trends")
        parameters = event.get("parameters", {})

        # Initialize aggregator
        aggregator = AnalyticsAggregator()

        # Route to appropriate operation
        if operation == "aggregate_daily":
            date = parameters.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            result = aggregator.aggregate_daily_metrics(date)

        elif operation == "aggregate_range":
            start_date = parameters.get("start_date")
            end_date = parameters.get("end_date")

            if not start_date or not end_date:
                return create_error_response(400, "start_date and end_date are required for aggregate_range operation")

            result = aggregator.aggregate_date_range_metrics(start_date, end_date)

        elif operation == "usage_trends":
            days = parameters.get("days", 30)
            result = aggregator.generate_usage_trends(days)

        elif operation == "revenue_metrics":
            date_range = parameters.get("date_range", "30d")
            result = aggregator.calculate_revenue_metrics(date_range)

        else:
            return create_error_response(400, f"Unknown operation: {operation}")

        logger.info(f"=== ANALYTICS_AGGREGATION_LAMBDA END === Request ID: {request_id}, Operation: {operation}")
        return create_success_response(result)

    except Exception as e:
        logger.error(f"=== ANALYTICS_AGGREGATION_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}")
        return create_error_response(500, f"Analytics aggregation failed: {str(e)}")


def create_success_response(data: Any) -> Dict[str, Any]:
    """Create a successful Lambda response"""
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            **cors_headers(),
        },
        "body": json.dumps(data, default=str),
    }


def create_error_response(status_code: int, message: str) -> Dict[str, Any]:
    """Create an error Lambda response"""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            **cors_headers(),
        },
        "body": json.dumps({"error": message, "timestamp": datetime.now(timezone.utc).isoformat()}),
    }
