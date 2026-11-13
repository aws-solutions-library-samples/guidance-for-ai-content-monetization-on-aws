"""
Analytics domain service (Service_Layer single home for the analytics domain).

This module is the canonical ``services/*`` home for the ui_backend analytics domain
(Requirements 1.1, 4.3, 7.4, 8.1). It holds the business logic and all AWS/data-store
access for the ``/analytics/*`` endpoints that was previously implemented inline in
``ui_backend/handler.py`` (``handle_analytics_*``): the on-demand, synchronous invoke of
the shared Analytics_Aggregation_Engine (``analytics_aggregation`` Lambda) and the direct
DynamoDB reads for access logs. This is the historically-intended consolidation target
(backend-integration-real-data task 3.4); ``analytics_api.py`` is retired separately after
a confirmatory diff (task 8.2).

Behavior-preserving (Requirement 2 / 8.1): each method reproduces the pre-refactor inline
handler's data-building — including the real/mock aggregation fallbacks and the mock
demo payloads for revenue/trends/revenue-categories — byte-for-byte. The route module
(``ui_backend/routes/analytics.py``) applies Cognito auth and wraps these payloads with
the shared ``create_success_response`` / ``create_error_response`` helpers, exactly as the
monolith did.

Single source of truth for the aggregation function name (Requirement 8.6): the
Analytics_Aggregation_Engine function name is resolved from the single
``DEFAULT_ANALYTICS_LAMBDA_NAME`` constant via :meth:`AnalyticsService.aggregation_function_name`
— there is no duplicate name literal in this module. The ``/analytics/partners`` route is an
alias of ``/partners`` and is served by ``services.partners_service`` (the partners single
home), so this service owns no partner DynamoDB access.

Imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``sys.path`` manipulation (Requirements 5.1,
15.2).
"""

import json
import logging
import os
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import boto3

logger = logging.getLogger(__name__)

# Single definition of the Analytics_Aggregation_Engine function name default
# (Requirement 8.6). This mirrors the pre-refactor inline BFF behavior, which resolved
# ``os.environ.get("ANALYTICS_LAMBDA_NAME", "iab-analytics-aggregation")``. The real
# function name is injected into the ui_backend Lambda environment at deploy time; the
# default is the behavior-preserving fallback when the env var is absent.
DEFAULT_ANALYTICS_LAMBDA_NAME = "iab-analytics-aggregation"


class AnalyticsService:
    """Single home for ui_backend analytics business logic and data access.

    Public methods return the analytics *data* payloads (plain dicts). The route module
    applies auth and wraps them in the shared success/error response helpers, so the
    externally observable status/body/CORS are identical to the pre-refactor inline
    handlers (Requirement 2).
    """

    def aggregation_function_name(self) -> str:
        """Resolve the Analytics_Aggregation_Engine function name from a single source.

        Returns the ``ANALYTICS_LAMBDA_NAME`` environment variable when set, otherwise the
        single ``DEFAULT_ANALYTICS_LAMBDA_NAME`` default (Requirement 8.6). This is the only
        place the aggregation function name is resolved in this module.
        """
        return os.environ.get("ANALYTICS_LAMBDA_NAME", DEFAULT_ANALYTICS_LAMBDA_NAME)

    # ------------------------------------------------------------------
    # /analytics/metrics
    # ------------------------------------------------------------------
    def get_metrics(self, publisher_id: str) -> Dict[str, Any]:
        """Build the ``/analytics/metrics`` payload (aggregation invoke + fallback).

        Invokes the shared aggregation engine (``usage_trends`` over 30 days) and
        transforms the result into the UI metrics shape, falling back to the mock payload
        when the aggregation call fails or returns a non-200 — preserving the inline
        handler's semantics exactly.
        """
        try:
            logger.info("=== ANALYTICS METRICS START ===")
            logger.info(f"Publisher ID: {publisher_id}")

            lambda_client = boto3.client("lambda")

            # Get usage trends for last 30 days to calculate metrics
            aggregation_request = {
                "operation": "usage_trends",
                "parameters": {"days": 30, "publisher_id": publisher_id},
            }

            analytics_lambda_name = self.aggregation_function_name()
            logger.info(f"Invoking analytics lambda: {analytics_lambda_name}")
            logger.info(f"Request payload: {json.dumps(aggregation_request)}")

            response = lambda_client.invoke(
                FunctionName=analytics_lambda_name,
                InvocationType="RequestResponse",
                Payload=json.dumps(aggregation_request),
            )

            logger.info(f"Analytics lambda response status: {response.get('StatusCode')}")

            payload = json.loads(response["Payload"].read())
            logger.info(f"Analytics lambda payload: {json.dumps(payload, default=str)[:500]}...")

            if response.get("StatusCode") == 200 and payload.get("statusCode") == 200:
                body = json.loads(payload.get("body", "{}"))
                total_metrics = body.get("total_metrics", {})
                logger.info(f"Total metrics from aggregation: {total_metrics}")

                # Transform to expected UI metrics format
                metrics_data = {
                    "total_requests": total_metrics.get("total_requests", 15420),
                    "unique_partners": 12,  # This would come from partners table count
                    "content_accessed": 1247,  # This would come from content table count
                    "revenue_generated": total_metrics.get("total_revenue", 2840.50),
                    "total_bot_access": total_metrics.get("total_bot_access", 342),
                    "total_mcp_usage": total_metrics.get("total_mcp_usage", 45),
                    "publisher_id": publisher_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                logger.info("=== ANALYTICS METRICS SUCCESS ===")
            else:
                logger.warning(
                    f"Analytics aggregation failed with status: {response.get('StatusCode')}, "
                    f"payload status: {payload.get('statusCode')}"
                )
                # Fallback to mock data if aggregation fails
                metrics_data = {
                    "total_requests": 15420,
                    "unique_partners": 12,
                    "content_accessed": 1247,
                    "revenue_generated": 2840.50,
                    "total_bot_access": 342,
                    "total_mcp_usage": 45,
                    "publisher_id": publisher_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                logger.info("=== ANALYTICS METRICS FALLBACK ===")
        except Exception as agg_error:
            logger.error(f"=== ANALYTICS METRICS ERROR === {str(agg_error)}")
            logger.error(f"Error type: {type(agg_error).__name__}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            # Fallback to mock data
            metrics_data = {
                "total_requests": 15420,
                "unique_partners": 12,
                "content_accessed": 1247,
                "revenue_generated": 2840.50,
                "total_bot_access": 342,
                "total_mcp_usage": 45,
                "publisher_id": publisher_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            logger.info("=== ANALYTICS METRICS ERROR FALLBACK ===")

        return metrics_data

    # ------------------------------------------------------------------
    # /analytics/usage
    # ------------------------------------------------------------------
    def get_usage(self, publisher_id: str, query_params: Dict[str, str]) -> Dict[str, Any]:
        """Build the ``/analytics/usage`` payload (aggregation invoke + fallback).

        Parses the ``range`` query parameter to a day count, invokes the aggregation
        engine, and transforms daily series into the UI usage shape, falling back to the
        mock payload when the aggregation call fails or returns a non-200.
        """
        time_range = query_params.get("range", "last_7_days")

        # Parse time range to get number of days
        days = 7  # default
        if "last_" in time_range and "_day" in time_range:
            try:
                days = int(time_range.split("_")[1])
            except (ValueError, IndexError):
                days = 7

        # Call real analytics aggregation service
        try:
            lambda_client = boto3.client("lambda")

            aggregation_request = {
                "operation": "usage_trends",
                "parameters": {"days": days, "publisher_id": publisher_id},
            }

            response = lambda_client.invoke(
                FunctionName=self.aggregation_function_name(),
                InvocationType="RequestResponse",
                Payload=json.dumps(aggregation_request),
            )

            payload = json.loads(response["Payload"].read())

            if response.get("StatusCode") == 200 and payload.get("statusCode") == 200:
                body = json.loads(payload.get("body", "{}"))

                # Extract daily data and transform to UI format
                dates = body.get("dates", [])
                daily_api_requests = body.get("daily_api_requests", [])
                daily_revenue = body.get("daily_revenue", [])
                daily_bot_access = body.get("daily_bot_access", [])

                usage_data_list = []
                for i, date in enumerate(dates):
                    usage_data_list.append(
                        {
                            "date": date,
                            "requests": daily_api_requests[i] if i < len(daily_api_requests) else 0,
                            "revenue": daily_revenue[i] if i < len(daily_revenue) else 0.0,
                            "blocked": daily_bot_access[i] if i < len(daily_bot_access) else 0,
                        }
                    )

                usage_data = {
                    "usage_data": usage_data_list,
                    "publisher_id": publisher_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            else:
                # Fallback to mock data if aggregation fails
                usage_data = {
                    "usage_data": [
                        {"date": "2025-01-15", "requests": 1234, "revenue": 67.89, "blocked": 45},
                        {"date": "2025-01-14", "requests": 1456, "revenue": 78.23, "blocked": 52},
                        {"date": "2025-01-13", "requests": 1123, "revenue": 59.45, "blocked": 38},
                        {"date": "2025-01-12", "requests": 1680, "revenue": 63.40, "blocked": 67},
                        {"date": "2025-01-11", "requests": 1920, "revenue": 72.80, "blocked": 78},
                        {"date": "2025-01-10", "requests": 1750, "revenue": 66.50, "blocked": 71},
                        {"date": "2025-01-09", "requests": 2100, "revenue": 79.80, "blocked": 89},
                    ],
                    "publisher_id": publisher_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as agg_error:
            logger.warning(f"Analytics aggregation failed, using mock data: {agg_error}")
            # Fallback to mock data
            usage_data = {
                "usage_data": [
                    {"date": "2025-01-15", "requests": 1234, "revenue": 67.89, "blocked": 45},
                    {"date": "2025-01-14", "requests": 1456, "revenue": 78.23, "blocked": 52},
                    {"date": "2025-01-13", "requests": 1123, "revenue": 59.45, "blocked": 38},
                    {"date": "2025-01-12", "requests": 1680, "revenue": 63.40, "blocked": 67},
                    {"date": "2025-01-11", "requests": 1920, "revenue": 72.80, "blocked": 78},
                    {"date": "2025-01-10", "requests": 1750, "revenue": 66.50, "blocked": 71},
                    {"date": "2025-01-09", "requests": 2100, "revenue": 79.80, "blocked": 89},
                ],
                "publisher_id": publisher_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return usage_data

    # ------------------------------------------------------------------
    # /analytics/revenue
    # ------------------------------------------------------------------
    def get_revenue(self, publisher_id: str) -> Dict[str, Any]:
        """Build the ``/analytics/revenue`` payload (mock demo data, unchanged)."""
        return {
            "total_revenue": 1247.50,
            "monthly_revenue": [
                {"month": "2025-01", "revenue": 1247.50, "requests": 15420},
                {"month": "2024-12", "revenue": 1156.23, "requests": 14234},
                {"month": "2024-11", "revenue": 1089.45, "requests": 13567},
            ],
            "revenue_by_content_type": [
                {"content_type": "article", "revenue": 446.70, "percentage": 35.8},
                {"content_type": "report", "revenue": 539.00, "percentage": 43.2},
                {"content_type": "document", "revenue": 261.80, "percentage": 21.0},
            ],
            "publisher_id": publisher_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # /analytics/trends
    # ------------------------------------------------------------------
    def get_trends(self, publisher_id: str) -> Dict[str, Any]:
        """Build the ``/analytics/trends`` payload (mock demo data, unchanged)."""
        return {
            "growth_rate": 12.5,
            "trending_categories": [
                {"category": "Technology & Computing", "growth": 23.4},
                {"category": "Medical Health", "growth": 18.7},
                {"category": "Business and Finance", "growth": 15.2},
            ],
            "partner_growth": [
                {"month": "2025-01", "new_partners": 3, "total_partners": 23},
                {"month": "2024-12", "new_partners": 2, "total_partners": 20},
                {"month": "2024-11", "new_partners": 4, "total_partners": 18},
            ],
            "publisher_id": publisher_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # /analytics/revenue/categories
    # ------------------------------------------------------------------
    def get_revenue_categories(self, publisher_id: str) -> Dict[str, Any]:
        """Build the ``/analytics/revenue/categories`` payload (mock demo data, unchanged)."""
        return {
            "categories": [
                {
                    "category_id": 126,
                    "category_name": "Technology & Computing",
                    "revenue": 423.50,
                    "requests": 5234,
                    "avg_price": 0.081,
                },
                {
                    "category_id": 111,
                    "category_name": "Medical Health",
                    "revenue": 389.25,
                    "requests": 4567,
                    "avg_price": 0.085,
                },
                {
                    "category_id": 52,
                    "category_name": "Business and Finance",
                    "revenue": 434.75,
                    "requests": 5619,
                    "avg_price": 0.077,
                },
            ],
            "publisher_id": publisher_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # /analytics/access-logs
    # ------------------------------------------------------------------
    def get_access_logs(self, publisher_id: str, query_params: Dict[str, str]) -> Dict[str, Any]:
        """Build the ``/analytics/access-logs`` payload (direct DynamoDB reads + fallback).

        Reads the access-logs DynamoDB table with the requested filters, falling back to
        mock rows when the read fails, then applies the same client-side filtering and
        limiting the inline handler performed. Returns the full response payload including
        the echoed filters.
        """
        partner_id = query_params.get("partner_id")
        time_period = query_params.get("time_period", "last_24_hours")
        limit = int(query_params.get("limit", "100"))
        endpoint = query_params.get("endpoint")

        # Try to get real access logs from DynamoDB
        try:
            dynamodb = boto3.resource("dynamodb")
            access_logs_table = dynamodb.Table(os.environ.get("ACCESS_LOG_TABLE_NAME", "iab-access-logs"))

            # Build query parameters
            scan_kwargs = {
                "Limit": min(limit, 1000)  # Cap at 1000 for performance
            }

            # Add filters if provided
            filter_expressions = []
            expression_attribute_values = {}

            if partner_id:
                filter_expressions.append("partner_id = :pid")
                expression_attribute_values[":pid"] = partner_id

            if endpoint:
                filter_expressions.append("endpoint = :ep")
                expression_attribute_values[":ep"] = endpoint

            # Add time filter based on time_period
            if time_period != "all":
                now = datetime.now(timezone.utc)

                if time_period == "last_hour":
                    cutoff = now - timedelta(hours=1)
                elif time_period == "last_24_hours":
                    cutoff = now - timedelta(days=1)
                elif time_period == "last_7_days":
                    cutoff = now - timedelta(days=7)
                elif time_period == "last_30_days":
                    cutoff = now - timedelta(days=30)
                else:
                    cutoff = now - timedelta(days=1)  # Default to 24 hours

                filter_expressions.append("#ts >= :cutoff")
                expression_attribute_values[":cutoff"] = cutoff.isoformat()
                scan_kwargs["ExpressionAttributeNames"] = {"#ts": "timestamp"}

            if filter_expressions:
                scan_kwargs["FilterExpression"] = " AND ".join(filter_expressions)
                scan_kwargs["ExpressionAttributeValues"] = expression_attribute_values

            # Scan the access logs table
            response = access_logs_table.scan(**scan_kwargs)
            log_items = response.get("Items", [])

            # Transform to expected format
            access_logs = []
            for item in log_items:
                access_logs.append(
                    {
                        "log_id": item.get("log_id"),
                        "partner_id": item.get("partner_id"),
                        "partner_name": item.get("partner_name", "Unknown Partner"),
                        "timestamp": item.get("timestamp"),
                        "endpoint": item.get("endpoint"),
                        "request_type": item.get("request_type", "POST"),
                        "response_code": int(item.get("response_code", 200)),
                        "ip_address": item.get("ip_address"),
                        "user_agent": item.get("user_agent"),
                        "content_accessed": item.get("content_accessed"),
                        "price_charged": float(item.get("price_charged", 0.0)) if item.get("price_charged") else None,
                        "processing_time_ms": int(item.get("processing_time_ms", 0))
                        if item.get("processing_time_ms")
                        else None,
                    }
                )

            # Sort by timestamp descending
            access_logs.sort(key=lambda x: x["timestamp"], reverse=True)

        except Exception as db_error:
            logger.warning(f"Failed to get real access logs, using mock data: {db_error}")
            # Fallback to mock data
            access_logs = [
                {
                    "log_id": "log_001",
                    "partner_id": "partner_001",
                    "partner_name": "AI Research Corp",
                    "timestamp": "2025-01-15T14:30:15Z",
                    "endpoint": "/query",
                    "request_type": "POST",
                    "response_code": 200,
                    "ip_address": "192.168.1.100",
                    "user_agent": "AIBot/1.0",
                    "content_accessed": "article_12345",
                    "price_charged": 0.05,
                    "processing_time_ms": 245,
                },
                {
                    "log_id": "log_002",
                    "partner_id": "partner_002",
                    "partner_name": "Data Analytics Inc",
                    "timestamp": "2025-01-15T14:29:45Z",
                    "endpoint": "/query",
                    "request_type": "POST",
                    "response_code": 200,
                    "ip_address": "10.0.1.50",
                    "user_agent": "DataBot/2.1",
                    "content_accessed": "report_67890",
                    "price_charged": 0.25,
                    "processing_time_ms": 180,
                },
                {
                    "log_id": "log_003",
                    "partner_id": "partner_001",
                    "partner_name": "AI Research Corp",
                    "timestamp": "2025-01-15T14:28:30Z",
                    "endpoint": "/bid",
                    "request_type": "POST",
                    "response_code": 200,
                    "ip_address": "192.168.1.100",
                    "user_agent": "AIBot/1.0",
                    "content_accessed": "research_paper_11111",
                    "price_charged": 0.50,
                    "processing_time_ms": 320,
                },
            ]

        # Apply client-side filtering if needed (for mock data)
        if partner_id and access_logs:
            access_logs = [log for log in access_logs if log["partner_id"] == partner_id]

        if endpoint and access_logs:
            access_logs = [log for log in access_logs if log["endpoint"] == endpoint]

        # Limit results
        access_logs = access_logs[:limit]

        return {
            "access_logs": access_logs,
            "total_count": len(access_logs),
            "filters": {"partner_id": partner_id, "time_period": time_period, "endpoint": endpoint, "limit": limit},
            "publisher_id": publisher_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
