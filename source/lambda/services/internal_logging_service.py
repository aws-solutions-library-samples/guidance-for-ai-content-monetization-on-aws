"""
Internal logging service for usage tracking and billing

This service handles internal logging of partner usage for billing purposes.
It's designed to be used by the UI Backend Lambda for internal tracking,
not exposed to external partners in a pre-pay model.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
cloudwatch = boto3.client("cloudwatch")


class InternalLoggingService:
    """
    Service for internal usage logging and billing tracking
    """

    def __init__(self):
        self.analytics_table_name = os.environ.get("ANALYTICS_TABLE_NAME", "iab-analytics")
        self.analytics_table = dynamodb.Table(self.analytics_table_name)

    def log_partner_usage(
        self, partner_id: str, usage_data: Dict[str, Any], timestamp: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Log partner usage for internal billing tracking

        Args:
            partner_id: Partner identifier
            usage_data: Usage data including content accessed, query details, etc.
            timestamp: Optional timestamp (defaults to current time)

        Returns:
            Log entry result with log_id
        """
        if not timestamp:
            timestamp = datetime.now(timezone.utc)

        log_id = f"internal_log_{uuid.uuid4().hex[:8]}"

        try:
            # Create internal log entry
            log_entry = {
                "log_id": log_id,
                "partner_id": partner_id,
                "log_type": "partner_usage",
                "timestamp": timestamp.isoformat(),
                "usage_data": usage_data,
                "billing_status": "pending",
                "created_at": timestamp.isoformat(),
            }

            # Store in analytics table for billing processing
            self.analytics_table.put_item(Item=log_entry)

            # Publish CloudWatch metrics for monitoring
            self._publish_usage_metrics(partner_id, usage_data)

            logger.info(f"Internal usage logged: {log_id} for partner {partner_id}")

            return {"status": "logged", "log_id": log_id, "timestamp": timestamp.isoformat()}

        except Exception as e:
            logger.error(f"Failed to log internal usage: {str(e)}")
            raise

    def log_ui_test_usage(
        self, user_id: str, publisher_id: str, test_usage_data: Dict[str, Any], timestamp: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Log UI testing/preview usage (not for billing)

        Args:
            user_id: UI user identifier
            publisher_id: Publisher identifier
            test_usage_data: Test usage data
            timestamp: Optional timestamp (defaults to current time)

        Returns:
            Log entry result with log_id
        """
        if not timestamp:
            timestamp = datetime.now(timezone.utc)

        log_id = f"ui_test_log_{uuid.uuid4().hex[:8]}"

        try:
            # Create UI test log entry
            log_entry = {
                "log_id": log_id,
                "user_id": user_id,
                "publisher_id": publisher_id,
                "log_type": "ui_test_usage",
                "timestamp": timestamp.isoformat(),
                "test_usage_data": test_usage_data,
                "billing_status": "not_billable",
                "created_at": timestamp.isoformat(),
            }

            # Store in analytics table for analytics (not billing)
            self.analytics_table.put_item(Item=log_entry)

            logger.info(f"UI test usage logged: {log_id} for user {user_id}")

            return {
                "status": "logged",
                "log_id": log_id,
                "timestamp": timestamp.isoformat(),
                "note": "UI test usage - not billable",
            }

        except Exception as e:
            logger.error(f"Failed to log UI test usage: {str(e)}")
            raise

    def log_api_access(
        self,
        endpoint: str,
        user_id: str,
        auth_type: str,
        metadata: Dict[str, Any],
        timestamp: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Log API access for analytics and monitoring

        Args:
            endpoint: API endpoint accessed
            user_id: User or partner identifier
            auth_type: Authentication type (partner_key, cognito, jwt)
            metadata: Additional metadata about the access
            timestamp: Optional timestamp (defaults to current time)

        Returns:
            Log entry result with log_id
        """
        if not timestamp:
            timestamp = datetime.now(timezone.utc)

        log_id = f"api_access_log_{uuid.uuid4().hex[:8]}"

        try:
            # Create API access log entry
            log_entry = {
                "log_id": log_id,
                "user_id": user_id,
                "log_type": "api_access",
                "endpoint": endpoint,
                "auth_type": auth_type,
                "timestamp": timestamp.isoformat(),
                "metadata": metadata,
                "billing_status": "billable" if auth_type == "partner_key" else "not_billable",
                "created_at": timestamp.isoformat(),
            }

            # Store in analytics table
            self.analytics_table.put_item(Item=log_entry)

            # Publish CloudWatch metrics
            self._publish_access_metrics(endpoint, auth_type)

            logger.info(f"API access logged: {log_id} for {auth_type} user {user_id}")

            return {"status": "logged", "log_id": log_id, "timestamp": timestamp.isoformat()}

        except Exception as e:
            logger.error(f"Failed to log API access: {str(e)}")
            raise

    def get_usage_logs(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        """
        Retrieve usage logs for analysis and billing

        Args:
            filters: Filters to apply (partner_id, date_range, log_type, etc.)
            limit: Maximum number of logs to return

        Returns:
            List of log entries
        """
        try:
            # Build scan parameters
            scan_params = {"Limit": limit}

            # Add filters if provided
            filter_expressions = []
            expression_values = {}

            if "partner_id" in filters:
                filter_expressions.append("partner_id = :partner_id")
                expression_values[":partner_id"] = filters["partner_id"]

            if "log_type" in filters:
                filter_expressions.append("log_type = :log_type")
                expression_values[":log_type"] = filters["log_type"]

            if "start_date" in filters:
                filter_expressions.append("#timestamp >= :start_date")
                expression_values[":start_date"] = filters["start_date"]
                scan_params["ExpressionAttributeNames"] = {"#timestamp": "timestamp"}

            if "end_date" in filters:
                filter_expressions.append("#timestamp <= :end_date")
                expression_values[":end_date"] = filters["end_date"]
                scan_params["ExpressionAttributeNames"] = {"#timestamp": "timestamp"}

            if filter_expressions:
                scan_params["FilterExpression"] = " AND ".join(filter_expressions)
                scan_params["ExpressionAttributeValues"] = expression_values

            # Perform scan
            response = self.analytics_table.scan(**scan_params)

            return response.get("Items", [])

        except Exception as e:
            logger.error(f"Failed to retrieve usage logs: {str(e)}")
            raise

    def _publish_usage_metrics(self, partner_id: str, usage_data: Dict[str, Any]):
        """
        Publish usage metrics to CloudWatch
        """
        try:
            metrics = []

            # Basic usage metric
            metrics.append(
                {
                    "MetricName": "PartnerUsage",
                    "Dimensions": [
                        {"Name": "PartnerId", "Value": partner_id},
                        {"Name": "Service", "Value": "AI-Content-Monetization"},
                    ],
                    "Value": 1,
                    "Unit": "Count",
                }
            )

            # Content access metrics if available
            if "content_ids" in usage_data:
                content_count = len(usage_data["content_ids"])
                metrics.append(
                    {
                        "MetricName": "ContentAccessed",
                        "Dimensions": [
                            {"Name": "PartnerId", "Value": partner_id},
                            {"Name": "Service", "Value": "AI-Content-Monetization"},
                        ],
                        "Value": content_count,
                        "Unit": "Count",
                    }
                )

            # Publish metrics
            cloudwatch.put_metric_data(Namespace="IAB/InternalLogging", MetricData=metrics)

        except Exception as e:
            logger.warning(f"Failed to publish usage metrics: {str(e)}")
            # Don't raise exception as this is not critical

    def _publish_access_metrics(self, endpoint: str, auth_type: str):
        """
        Publish API access metrics to CloudWatch
        """
        try:
            cloudwatch.put_metric_data(
                Namespace="IAB/InternalLogging",
                MetricData=[
                    {
                        "MetricName": "APIAccess",
                        "Dimensions": [
                            {"Name": "Endpoint", "Value": endpoint},
                            {"Name": "AuthType", "Value": auth_type},
                            {"Name": "Service", "Value": "AI-Content-Monetization"},
                        ],
                        "Value": 1,
                        "Unit": "Count",
                    }
                ],
            )

        except Exception as e:
            logger.warning(f"Failed to publish access metrics: {str(e)}")
            # Don't raise exception as this is not critical
