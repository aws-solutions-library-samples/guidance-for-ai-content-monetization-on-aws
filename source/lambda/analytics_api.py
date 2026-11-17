"""
Analytics API Endpoints

This module provides the analytics API endpoints that integrate with the analytics aggregation
service to provide real-time usage, revenue, and trend data for the Content Monetization API.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import boto3
from shared.utils.response_helpers import cors_headers
from shared.utils.safe_datetime_parser import safe_parse_datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
lambda_client = boto3.client("lambda")


class AnalyticsAPIHandler:
    """
    Analytics API handler that integrates with the analytics aggregation service.
    """

    def __init__(self, analytics_lambda_name: str = None):
        """
        Initialize the analytics API handler.

        Args:
            analytics_lambda_name: Name of the analytics aggregation Lambda function
        """
        self.analytics_lambda_name = analytics_lambda_name or os.environ.get(
            "ANALYTICS_LAMBDA_NAME", "iab-llm-analytics-aggregation"
        )

    def handle_usage_analytics(self, headers: Dict[str, str], query_params: Dict[str, str]) -> Dict[str, Any]:
        """
        Handle /analytics/usage endpoint - Usage metrics and patterns

        Args:
            headers: Request headers
            query_params: Query parameters

        Returns:
            dict: API response with usage analytics data
        """
        try:
            logger.info("=== HANDLE_USAGE_ANALYTICS START ===")
            logger.info(f"Query params: {query_params}")

            # Extract query parameters
            date_range = query_params.get("date_range", "last_7_days")
            content_type = query_params.get("content_type")
            bot_filter = query_params.get("bot_filter")

            logger.info(
                f"Processing usage analytics: date_range={date_range}, content_type={content_type}, bot_filter={bot_filter}"
            )

            # Parse date range
            days = self._parse_date_range(date_range)

            # Call analytics aggregation service
            aggregation_request = {"operation": "usage_trends", "parameters": {"days": days}}

            aggregation_response = self._call_analytics_aggregation(aggregation_request)

            if not aggregation_response.get("success"):
                logger.error(f"Analytics aggregation failed: {aggregation_response.get('error')}")
                return self._create_error_response(500, "Failed to retrieve usage analytics")

            trends_data = aggregation_response.get("data", {})

            # Transform data for usage analytics response
            dates = trends_data.get("dates", [])
            api_requests = trends_data.get("daily_api_requests", [])
            revenue = trends_data.get("daily_revenue", [])
            bot_access = trends_data.get("daily_bot_access", [])

            # Transform to UI-expected format
            usage_data = []
            for i, date in enumerate(dates):
                usage_data.append(
                    {
                        "date": date,
                        "requests": api_requests[i] if i < len(api_requests) else 0,
                        "revenue": revenue[i] if i < len(revenue) else 0.0,
                        "blocked": bot_access[i] if i < len(bot_access) else 0,
                    }
                )

            usage_analytics = {
                "period": trends_data.get("period", f"{days} days"),
                "start_date": trends_data.get("start_date"),
                "end_date": trends_data.get("end_date"),
                "usage_data": usage_data,
                "summary": {
                    "total_requests": trends_data.get("total_metrics", {}).get("total_requests", 0),
                    "total_bot_access": trends_data.get("total_metrics", {}).get("total_bot_access", 0),
                    "total_mcp_usage": trends_data.get("total_metrics", {}).get("total_mcp_usage", 0),
                    "average_daily_requests": trends_data.get("total_metrics", {}).get("average_daily_requests", 0),
                    "unique_content_accessed": len(trends_data.get("content_type_distribution", {})),
                },
                "daily_breakdown": {
                    "dates": dates,
                    "api_requests": api_requests,
                    "bot_access": bot_access,
                    "mcp_usage": trends_data.get("daily_mcp_usage", []),
                },
                "content_type_distribution": trends_data.get("content_type_distribution", {}),
                "access_method_breakdown": trends_data.get("revenue_by_access_method", {}),
                "filters_applied": {"date_range": date_range, "content_type": content_type, "bot_filter": bot_filter},
            }

            # Apply filters if specified
            if content_type or bot_filter:
                usage_analytics = self._apply_usage_filters(usage_analytics, content_type, bot_filter)

            logger.info("=== HANDLE_USAGE_ANALYTICS SUCCESS ===")
            return self._create_success_response(usage_analytics)

        except Exception as e:
            logger.error(f"=== HANDLE_USAGE_ANALYTICS ERROR === {str(e)}", exc_info=True)
            return self._create_error_response(500, f"Failed to retrieve usage analytics: {str(e)}")

    def handle_revenue_analytics(self, headers: Dict[str, str], query_params: Dict[str, str]) -> Dict[str, Any]:
        """
        Handle /analytics/revenue endpoint - Revenue metrics and breakdowns

        Args:
            headers: Request headers
            query_params: Query parameters

        Returns:
            dict: API response with revenue analytics data
        """
        try:
            logger.info("=== HANDLE_REVENUE_ANALYTICS START ===")
            logger.info(f"Query params: {query_params}")

            # Extract query parameters
            time_period = query_params.get("time_period", "last_30_days")
            breakdown_by = query_params.get("breakdown_by", "content_type")

            logger.info(f"Processing revenue analytics: time_period={time_period}, breakdown_by={breakdown_by}")

            # Parse time period to date range format
            date_range = self._convert_time_period_to_date_range(time_period)

            # Call analytics aggregation service
            aggregation_request = {"operation": "revenue_metrics", "parameters": {"date_range": date_range}}

            aggregation_response = self._call_analytics_aggregation(aggregation_request)

            if not aggregation_response.get("success"):
                logger.error(f"Revenue analytics aggregation failed: {aggregation_response.get('error')}")
                return self._create_error_response(500, "Failed to retrieve revenue analytics")

            revenue_data = aggregation_response.get("data", {})

            # Transform data for revenue analytics response
            revenue_by_content_type = revenue_data.get("revenue_by_content_type", {})

            # Format categories for UI
            categories = []
            colors = ["#0073bb", "#037f0c", "#ff9900", "#8b5cf6", "#06b6d4", "#e91e63", "#ff5722"]
            color_index = 0

            for content_type, revenue in revenue_by_content_type.items():
                # Estimate requests based on revenue (rough calculation)
                estimated_requests = int(revenue * 20) if revenue > 0 else 0

                categories.append(
                    {
                        "category": content_type.replace("_", " ").title(),
                        "revenue": float(revenue),
                        "requests": estimated_requests,
                        "color": colors[color_index % len(colors)],
                    }
                )
                color_index += 1

            revenue_analytics = {
                "period": revenue_data.get("period", time_period),
                "total_revenue": revenue_data.get("total_revenue", 0.0),
                "average_revenue_per_request": revenue_data.get("average_revenue_per_request", 0.0),
                "projected_monthly_revenue": revenue_data.get("projected_monthly_revenue", 0.0),
                "revenue_growth": revenue_data.get("revenue_growth", {}),
                "categories": categories,  # Add categories for UI
                "breakdown": self._format_revenue_breakdown(revenue_data, breakdown_by),
                "daily_revenue_trend": {
                    "dates": revenue_data.get("dates", []),
                    "daily_revenue": revenue_data.get("daily_revenue_trend", []),
                },
                "top_revenue_sources": revenue_data.get("top_revenue_sources", []),
                "breakdown_by": breakdown_by,
            }

            logger.info("=== HANDLE_REVENUE_ANALYTICS SUCCESS ===")
            return self._create_success_response(revenue_analytics)

        except Exception as e:
            logger.error(f"=== HANDLE_REVENUE_ANALYTICS ERROR === {str(e)}", exc_info=True)
            return self._create_error_response(500, f"Failed to retrieve revenue analytics: {str(e)}")

    def handle_trends_analytics(self, headers: Dict[str, str], query_params: Dict[str, str]) -> Dict[str, Any]:
        """
        Handle /analytics/trends endpoint - Time-series trend data

        Args:
            headers: Request headers
            query_params: Query parameters

        Returns:
            dict: API response with trends analytics data
        """
        try:
            logger.info("=== HANDLE_TRENDS_ANALYTICS START ===")
            logger.info(f"Query params: {query_params}")

            # Extract query parameters
            days = int(query_params.get("days", 30))
            metric_type = query_params.get("metric_type", "all")
            granularity = query_params.get("granularity", "daily")

            logger.info(
                f"Processing trends analytics: days={days}, metric_type={metric_type}, granularity={granularity}"
            )

            # Validate parameters
            if days > 365:
                return self._create_error_response(400, "Maximum days allowed is 365")

            if granularity not in ["daily", "hourly"]:
                return self._create_error_response(400, "Granularity must be 'daily' or 'hourly'")

            # Call analytics aggregation service
            aggregation_request = {"operation": "usage_trends", "parameters": {"days": days}}

            aggregation_response = self._call_analytics_aggregation(aggregation_request)

            if not aggregation_response.get("success"):
                logger.error(f"Trends analytics aggregation failed: {aggregation_response.get('error')}")
                return self._create_error_response(500, "Failed to retrieve trends analytics")

            trends_data = aggregation_response.get("data", {})

            # Transform data for trends analytics response
            trends_analytics = {
                "period": trends_data.get("period", f"{days} days"),
                "start_date": trends_data.get("start_date"),
                "end_date": trends_data.get("end_date"),
                "granularity": granularity,
                "metric_type": metric_type,
                "trends": {
                    "dates": trends_data.get("dates", []),
                    "api_requests": trends_data.get("daily_api_requests", []),
                    "revenue": trends_data.get("daily_revenue", []),
                    "bot_access": trends_data.get("daily_bot_access", []),
                    "mcp_usage": trends_data.get("daily_mcp_usage", []),
                },
                "summary_statistics": {
                    "total_requests": trends_data.get("total_metrics", {}).get("total_requests", 0),
                    "total_revenue": trends_data.get("total_metrics", {}).get("total_revenue", 0.0),
                    "total_bot_access": trends_data.get("total_metrics", {}).get("total_bot_access", 0),
                    "total_mcp_usage": trends_data.get("total_metrics", {}).get("total_mcp_usage", 0),
                    "average_daily_requests": trends_data.get("total_metrics", {}).get("average_daily_requests", 0),
                    "average_daily_revenue": trends_data.get("total_metrics", {}).get("average_daily_revenue", 0.0),
                },
                "trend_analysis": self._analyze_trends(trends_data),
                "content_insights": {
                    "content_type_distribution": trends_data.get("content_type_distribution", {}),
                    "revenue_by_content_type": trends_data.get("revenue_by_content_type", {}),
                    "revenue_by_access_method": trends_data.get("revenue_by_access_method", {}),
                },
            }

            # Filter by metric type if specified
            if metric_type != "all":
                trends_analytics = self._filter_trends_by_metric(trends_analytics, metric_type)

            logger.info("=== HANDLE_TRENDS_ANALYTICS SUCCESS ===")
            return self._create_success_response(trends_analytics)

        except Exception as e:
            logger.error(f"=== HANDLE_TRENDS_ANALYTICS ERROR === {str(e)}", exc_info=True)
            return self._create_error_response(500, f"Failed to retrieve trends analytics: {str(e)}")

    def _call_analytics_aggregation(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call the analytics aggregation Lambda function.

        Args:
            request: Request payload for analytics aggregation

        Returns:
            dict: Response from analytics aggregation service
        """
        try:
            logger.info(f"Calling analytics aggregation with request: {json.dumps(request)}")

            response = lambda_client.invoke(
                FunctionName=self.analytics_lambda_name, InvocationType="RequestResponse", Payload=json.dumps(request)
            )

            payload = json.loads(response["Payload"].read())

            if response.get("StatusCode") == 200 and payload.get("statusCode") == 200:
                body = json.loads(payload.get("body", "{}"))
                return {"success": True, "data": body}
            else:
                error_msg = payload.get("body", "Unknown error")
                if isinstance(error_msg, str):
                    try:
                        error_data = json.loads(error_msg)
                        error_msg = error_data.get("error", error_msg)
                    except json.JSONDecodeError:
                        pass

                return {"success": False, "error": error_msg}

        except Exception as e:
            logger.error(f"Failed to call analytics aggregation: {str(e)}")
            return {"success": False, "error": str(e)}

    def _parse_date_range(self, date_range: str) -> int:
        """
        Parse date range parameter to number of days.

        Args:
            date_range: Date range string (e.g., 'last_7_days', 'last_30_days', 'custom:2024-01-01:2024-01-31')

        Returns:
            int: Number of days
        """
        if date_range == "last_7_days":
            return 7
        elif date_range == "last_30_days":
            return 30
        elif date_range == "last_90_days":
            return 90
        elif date_range.startswith("custom:"):
            # Parse custom date range
            try:
                _, start_date, end_date = date_range.split(":")
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                return (end_dt - start_dt).days + 1
            except (ValueError, IndexError):
                logger.warning(f"Invalid custom date range: {date_range}, defaulting to 30 days")
                return 30
        else:
            logger.warning(f"Unknown date range: {date_range}, defaulting to 30 days")
            return 30

    def _convert_time_period_to_date_range(self, time_period: str) -> str:
        """
        Convert time period to date range format for analytics aggregation.

        Args:
            time_period: Time period string

        Returns:
            str: Date range format for analytics aggregation
        """
        if time_period == "last_7_days":
            return "7d"
        elif time_period == "last_30_days":
            return "30d"
        elif time_period == "last_90_days":
            return "90d"
        else:
            return "30d"  # Default

    def _format_revenue_breakdown(self, revenue_data: Dict[str, Any], breakdown_by: str) -> Dict[str, Any]:
        """
        Format revenue breakdown based on the requested breakdown type.

        Args:
            revenue_data: Revenue data from analytics aggregation
            breakdown_by: Type of breakdown requested

        Returns:
            dict: Formatted revenue breakdown
        """
        if breakdown_by == "content_type":
            return revenue_data.get("revenue_by_content_type", {})
        elif breakdown_by == "bot_partner" or breakdown_by == "access_method":
            return revenue_data.get("revenue_by_access_method", {})
        elif breakdown_by == "geographic":
            # Geographic breakdown not implemented yet, return empty
            return {}
        else:
            # Default to content type
            return revenue_data.get("revenue_by_content_type", {})

    def _apply_usage_filters(
        self, usage_data: Dict[str, Any], content_type: Optional[str], bot_filter: Optional[str]
    ) -> Dict[str, Any]:
        """
        Apply filters to usage analytics data.

        Args:
            usage_data: Usage analytics data
            content_type: Content type filter
            bot_filter: Bot filter

        Returns:
            dict: Filtered usage data
        """
        # For now, just add filter information to the response
        # In a full implementation, you would re-query the data with filters
        if content_type:
            usage_data["filters_applied"]["content_type"] = content_type
            # Filter content_type_distribution
            if content_type in usage_data["content_type_distribution"]:
                usage_data["content_type_distribution"] = {
                    content_type: usage_data["content_type_distribution"][content_type]
                }
            else:
                usage_data["content_type_distribution"] = {}

        if bot_filter:
            usage_data["filters_applied"]["bot_filter"] = bot_filter
            # In a full implementation, you would filter the data by bot

        return usage_data

    def _analyze_trends(self, trends_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze trends data to provide insights.

        Args:
            trends_data: Trends data from analytics aggregation

        Returns:
            dict: Trend analysis insights
        """
        daily_requests = trends_data.get("daily_api_requests", [])
        daily_revenue = trends_data.get("daily_revenue", [])

        if not daily_requests or not daily_revenue:
            return {"request_trend": "insufficient_data", "revenue_trend": "insufficient_data", "volatility": "unknown"}

        # Simple trend analysis
        if len(daily_requests) >= 7:
            first_week_avg = sum(daily_requests[:7]) / 7
            last_week_avg = sum(daily_requests[-7:]) / 7

            if last_week_avg > first_week_avg * 1.1:
                request_trend = "increasing"
            elif last_week_avg < first_week_avg * 0.9:
                request_trend = "decreasing"
            else:
                request_trend = "stable"
        else:
            request_trend = "insufficient_data"

        # Revenue trend analysis
        if len(daily_revenue) >= 7:
            first_week_revenue = sum(daily_revenue[:7]) / 7
            last_week_revenue = sum(daily_revenue[-7:]) / 7

            if last_week_revenue > first_week_revenue * 1.1:
                revenue_trend = "increasing"
            elif last_week_revenue < first_week_revenue * 0.9:
                revenue_trend = "decreasing"
            else:
                revenue_trend = "stable"
        else:
            revenue_trend = "insufficient_data"

        # Calculate volatility (coefficient of variation)
        if daily_requests:
            avg_requests = sum(daily_requests) / len(daily_requests)
            if avg_requests > 0:
                variance = sum((x - avg_requests) ** 2 for x in daily_requests) / len(daily_requests)
                std_dev = variance**0.5
                cv = std_dev / avg_requests

                if cv < 0.1:
                    volatility = "low"
                elif cv < 0.3:
                    volatility = "medium"
                else:
                    volatility = "high"
            else:
                volatility = "unknown"
        else:
            volatility = "unknown"

        return {
            "request_trend": request_trend,
            "revenue_trend": revenue_trend,
            "volatility": volatility,
            "peak_day": self._find_peak_day(trends_data),
            "growth_rate": self._calculate_growth_rate(daily_requests),
        }

    def _find_peak_day(self, trends_data: Dict[str, Any]) -> Optional[str]:
        """
        Find the day with peak activity.

        Args:
            trends_data: Trends data

        Returns:
            str: Date of peak activity or None
        """
        daily_requests = trends_data.get("daily_api_requests", [])
        dates = trends_data.get("dates", [])

        if daily_requests and dates and len(daily_requests) == len(dates):
            max_index = daily_requests.index(max(daily_requests))
            return dates[max_index]

        return None

    def _calculate_growth_rate(self, daily_values: List[float]) -> float:
        """
        Calculate growth rate from daily values.

        Args:
            daily_values: List of daily values

        Returns:
            float: Growth rate percentage
        """
        if len(daily_values) < 2:
            return 0.0

        first_value = daily_values[0] if daily_values[0] > 0 else 1
        last_value = daily_values[-1]

        growth_rate = ((last_value - first_value) / first_value) * 100
        return round(growth_rate, 2)

    def _filter_trends_by_metric(self, trends_data: Dict[str, Any], metric_type: str) -> Dict[str, Any]:
        """
        Filter trends data by specific metric type.

        Args:
            trends_data: Full trends data
            metric_type: Specific metric to focus on

        Returns:
            dict: Filtered trends data
        """
        # Create a copy to avoid modifying the original data
        import copy

        filtered_data = copy.deepcopy(trends_data)

        if metric_type == "requests":
            # Keep only request-related data
            filtered_data["trends"] = {
                "dates": filtered_data["trends"]["dates"],
                "api_requests": filtered_data["trends"]["api_requests"],
            }
        elif metric_type == "revenue":
            # Keep only revenue-related data
            filtered_data["trends"] = {
                "dates": filtered_data["trends"]["dates"],
                "revenue": filtered_data["trends"]["revenue"],
            }
        elif metric_type == "bot_activity":
            # Keep only bot-related data
            filtered_data["trends"] = {
                "dates": filtered_data["trends"]["dates"],
                "bot_access": filtered_data["trends"]["bot_access"],
                "mcp_usage": filtered_data["trends"]["mcp_usage"],
            }

        return filtered_data

    def _create_success_response(self, data: Any) -> Dict[str, Any]:
        """Create a successful API response"""
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
                **cors_headers(),
            },
            "body": json.dumps(data, default=str),
        }

    def _create_error_response(self, status_code: int, message: str) -> Dict[str, Any]:
        """Create an error API response"""
        return {
            "statusCode": status_code,
            "headers": {
                "Content-Type": "application/json",
                # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
                **cors_headers(),
            },
            "body": json.dumps({"error": message, "timestamp": datetime.now(timezone.utc).isoformat()}),
        }


# Global instance for use in Lambda handler
analytics_api_handler = AnalyticsAPIHandler()
