"""
IAB Access Logger Lambda Function

This Lambda function handles:
1. Logging API access attempts for billing and analytics
2. Recording partner usage for quota management
3. Tracking content access patterns
4. Generating usage reports

Requirements covered:
- 2.4: Log access for billing and analytics purposes
- 4.3: Track usage for billing purposes
- 6.4: Display real-time metrics on content usage

MIGRATION NOTE: This file has been updated to use the new shared modules architecture.
Response helpers and utilities are now available in shared/ for reuse across Lambda functions.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import boto3

# Import shared utilities
from shared.utils.response_helpers import create_error_response, create_success_response

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
cloudwatch = boto3.client("cloudwatch")

# Environment variables
ACCESS_LOG_TABLE_NAME = os.environ.get("ACCESS_LOG_TABLE_NAME", "iab-access-logs")
PARTNERS_TABLE_NAME = os.environ.get("PARTNERS_TABLE_NAME", "cm-partners")

# TTL for log entries (90 days)
LOG_TTL_DAYS = 90


def generate_log_id() -> str:
    """Generate a unique log ID"""
    return f"log_{int(time.time())}_{str(uuid.uuid4())[:8]}"


def calculate_ttl() -> int:
    """Calculate TTL timestamp for log entry (90 days from now)"""
    ttl_date = datetime.utcnow() + timedelta(days=LOG_TTL_DAYS)
    return int(ttl_date.timestamp())


def publish_access_metrics(
    partner_id: str, request_type: str, response_code: int, price_charged: Optional[float] = None
) -> None:
    """
    Publish access metrics to CloudWatch

    Args:
        partner_id: Partner identifier
        request_type: Type of request
        response_code: HTTP response code
        price_charged: Amount charged for the request
    """
    try:
        metrics = []

        # Access attempt metric
        metrics.append(
            {
                "MetricName": "AccessAttempts",
                "Dimensions": [
                    {"Name": "Service", "Value": "AI-Content-Monetization"},
                    {"Name": "Partner", "Value": partner_id},
                    {"Name": "RequestType", "Value": request_type},
                ],
                "Value": 1,
                "Unit": "Count",
            }
        )

        # Success/failure metrics
        if response_code == 200:
            metrics.append(
                {
                    "MetricName": "SuccessfulAccess",
                    "Dimensions": [
                        {"Name": "Service", "Value": "AI-Content-Monetization"},
                        {"Name": "Partner", "Value": partner_id},
                    ],
                    "Value": 1,
                    "Unit": "Count",
                }
            )
        else:
            metrics.append(
                {
                    "MetricName": "FailedAccess",
                    "Dimensions": [
                        {"Name": "Service", "Value": "AI-Content-Monetization"},
                        {"Name": "Partner", "Value": partner_id},
                        {"Name": "ResponseCode", "Value": str(response_code)},
                    ],
                    "Value": 1,
                    "Unit": "Count",
                }
            )

            # Blocked access metric for 403 responses
            if response_code == 403:
                metrics.append(
                    {
                        "MetricName": "BlockedAccess",
                        "Dimensions": [
                            {"Name": "Service", "Value": "AI-Content-Monetization"},
                            {"Name": "Partner", "Value": partner_id},
                        ],
                        "Value": 1,
                        "Unit": "Count",
                    }
                )

        # Revenue metric
        if price_charged and price_charged > 0:
            metrics.append(
                {
                    "MetricName": "Revenue",
                    "Dimensions": [
                        {"Name": "Service", "Value": "AI-Content-Monetization"},
                        {"Name": "Partner", "Value": partner_id},
                    ],
                    "Value": price_charged,
                    "Unit": "None",
                }
            )

        # Publish metrics to CloudWatch
        cloudwatch.put_metric_data(Namespace="IAB/AccessLogger", MetricData=metrics)

        logger.debug(f"Published {len(metrics)} metrics to CloudWatch")

    except Exception as e:
        logger.warning(f"Failed to publish CloudWatch metrics: {str(e)}")
        # Don't raise exception as this is not critical for the main functionality


def log_access_attempt(
    partner_id: str,
    request_type: str,
    endpoint: str,
    response_code: int,
    content_id: Optional[str] = None,
    price_charged: Optional[float] = None,
    currency: str = "USD",
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    country_code: Optional[str] = None,
    additional_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Log an API access attempt

    Args:
        partner_id: Partner identifier
        request_type: Type of request (query, search, bid, etc.)
        endpoint: API endpoint accessed
        response_code: HTTP response code
        content_id: Content ID if specific content was accessed
        price_charged: Amount charged for the request
        currency: Currency for pricing
        user_agent: User agent string
        ip_address: Client IP address
        country_code: Country code from IP geolocation
        additional_metadata: Additional metadata to log

    Returns:
        Log ID of the created log entry
    """
    logger.info("=== LOG_ACCESS_ATTEMPT START ===")
    logger.info(f"Partner: {partner_id}, Request: {request_type}, Endpoint: {endpoint}, Response: {response_code}")

    try:
        access_log_table = dynamodb.Table(ACCESS_LOG_TABLE_NAME)

        log_id = generate_log_id()
        timestamp = datetime.utcnow().isoformat()
        ttl = calculate_ttl()

        # Build log entry
        log_entry = {
            "log_id": log_id,
            "timestamp": timestamp,
            "partner_id": partner_id,
            "request_type": request_type,
            "endpoint": endpoint,
            "response_code": response_code,
            "currency": currency,
            "ttl": ttl,
        }

        # Add optional fields
        if content_id:
            log_entry["content_id"] = content_id
        if price_charged is not None:
            log_entry["price_charged"] = price_charged
        if user_agent:
            log_entry["user_agent"] = user_agent
        if ip_address:
            log_entry["ip_address"] = ip_address
        if country_code:
            log_entry["country_code"] = country_code
        if additional_metadata:
            log_entry["metadata"] = additional_metadata

        # Store log entry
        access_log_table.put_item(Item=log_entry)

        logger.info(f"Logged access attempt: {log_id} for partner {partner_id}")

        # Publish CloudWatch metrics
        publish_access_metrics(partner_id, request_type, response_code, price_charged)

        logger.info(f"=== LOG_ACCESS_ATTEMPT END === Log ID: {log_id}")
        return log_id

    except Exception as e:
        logger.error(f"=== LOG_ACCESS_ATTEMPT ERROR === {str(e)}")
        raise


def get_partner_usage_stats(
    partner_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get usage statistics for a partner

    Args:
        partner_id: Partner identifier
        start_date: Start date for statistics (ISO format)
        end_date: End date for statistics (ISO format)

    Returns:
        Dictionary with usage statistics
    """
    logger.info("=== GET_PARTNER_USAGE_STATS START ===")
    logger.info(f"Partner: {partner_id}, Date range: {start_date} to {end_date}")

    try:
        access_log_table = dynamodb.Table(ACCESS_LOG_TABLE_NAME)

        # Default to last 30 days if no dates provided
        if not start_date:
            start_date = (datetime.utcnow() - timedelta(days=30)).isoformat()
        if not end_date:
            end_date = datetime.utcnow().isoformat()

        # Query logs for the partner
        response = access_log_table.query(
            IndexName="partner-timestamp-index",
            KeyConditionExpression="partner_id = :partner_id AND #ts BETWEEN :start_date AND :end_date",
            ExpressionAttributeNames={"#ts": "timestamp"},
            ExpressionAttributeValues={":partner_id": partner_id, ":start_date": start_date, ":end_date": end_date},
        )

        logs = response["Items"]

        # Calculate statistics
        stats = {
            "partner_id": partner_id,
            "period": {"start_date": start_date, "end_date": end_date},
            "total_requests": len(logs),
            "successful_requests": len([log for log in logs if log["response_code"] == 200]),
            "failed_requests": len([log for log in logs if log["response_code"] != 200]),
            "total_cost": sum([float(log.get("price_charged", 0)) for log in logs]),
            "currency": logs[0].get("currency", "USD") if logs else "USD",
            "request_types": {},
            "endpoints": {},
            "content_accessed": set(),
            "countries": set(),
        }

        # Analyze request patterns
        for log in logs:
            # Request types
            request_type = log.get("request_type", "unknown")
            stats["request_types"][request_type] = stats["request_types"].get(request_type, 0) + 1

            # Endpoints
            endpoint = log.get("endpoint", "unknown")
            stats["endpoints"][endpoint] = stats["endpoints"].get(endpoint, 0) + 1

            # Content accessed
            if log.get("content_id"):
                stats["content_accessed"].add(log["content_id"])

            # Countries
            if log.get("country_code"):
                stats["countries"].add(log["country_code"])

        # Convert sets to lists for JSON serialization
        stats["content_accessed"] = list(stats["content_accessed"])
        stats["countries"] = list(stats["countries"])
        stats["unique_content_count"] = len(stats["content_accessed"])
        stats["unique_countries_count"] = len(stats["countries"])

        logger.info(
            f"=== GET_PARTNER_USAGE_STATS END === Retrieved stats for {partner_id}: {stats['total_requests']} requests"
        )
        return stats

    except Exception as e:
        logger.error(f"=== GET_PARTNER_USAGE_STATS ERROR === {str(e)}")
        raise


def get_content_access_stats(
    content_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get access statistics for specific content

    Args:
        content_id: Content identifier
        start_date: Start date for statistics (ISO format)
        end_date: End date for statistics (ISO format)

    Returns:
        Dictionary with content access statistics
    """
    logger.info("=== GET_CONTENT_ACCESS_STATS START ===")
    logger.info(f"Content: {content_id}, Date range: {start_date} to {end_date}")

    try:
        access_log_table = dynamodb.Table(ACCESS_LOG_TABLE_NAME)

        # Default to last 30 days if no dates provided
        if not start_date:
            start_date = (datetime.utcnow() - timedelta(days=30)).isoformat()
        if not end_date:
            end_date = datetime.utcnow().isoformat()

        # Query logs for the content
        response = access_log_table.query(
            IndexName="content-timestamp-index",
            KeyConditionExpression="content_id = :content_id AND #ts BETWEEN :start_date AND :end_date",
            ExpressionAttributeNames={"#ts": "timestamp"},
            ExpressionAttributeValues={":content_id": content_id, ":start_date": start_date, ":end_date": end_date},
        )

        logs = response["Items"]

        # Calculate statistics
        stats = {
            "content_id": content_id,
            "period": {"start_date": start_date, "end_date": end_date},
            "total_accesses": len(logs),
            "successful_accesses": len([log for log in logs if log["response_code"] == 200]),
            "total_revenue": sum([float(log.get("price_charged", 0)) for log in logs]),
            "currency": logs[0].get("currency", "USD") if logs else "USD",
            "unique_partners": len(set([log["partner_id"] for log in logs])),
            "partners": list(set([log["partner_id"] for log in logs])),
            "countries": list(set([log.get("country_code") for log in logs if log.get("country_code")])),
            "request_types": {},
        }

        # Analyze request patterns
        for log in logs:
            request_type = log.get("request_type", "unknown")
            stats["request_types"][request_type] = stats["request_types"].get(request_type, 0) + 1

        logger.info(
            f"=== GET_CONTENT_ACCESS_STATS END === Retrieved stats for {content_id}: {stats['total_accesses']} accesses"
        )
        return stats

    except Exception as e:
        logger.error(f"=== GET_CONTENT_ACCESS_STATS ERROR === {str(e)}")
        raise


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for access logging operations

    Supported operations:
    - log_access: Log an API access attempt
    - get_partner_stats: Get usage statistics for a partner
    - get_content_stats: Get access statistics for content
    """
    request_id = getattr(context, "aws_request_id", "unknown")
    logger.info(f"=== ACCESS_LOGGER_LAMBDA START === Request ID: {request_id}")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    try:
        # Parse request
        operation = event.get("operation")

        if not operation:
            return create_error_response(
                400,
                "Missing operation parameter",
                details={"supported_operations": ["log_access", "get_partner_stats", "get_content_stats"]},
            )

        if operation == "log_access":
            return handle_log_access(event)
        elif operation == "get_partner_stats":
            return handle_get_partner_stats(event)
        elif operation == "get_content_stats":
            return handle_get_content_stats(event)
        else:
            return create_error_response(
                400,
                f"Unsupported operation: {operation}",
                details={"supported_operations": ["log_access", "get_partner_stats", "get_content_stats"]},
            )

    except Exception as e:
        logger.error(f"=== ACCESS_LOGGER_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}")
        return create_error_response(500, "Internal server error", str(e))


def handle_log_access(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle access logging request"""
    required_fields = ["partner_id", "request_type", "endpoint", "response_code"]

    for field in required_fields:
        if field not in event:
            return create_error_response(400, f"Missing required field: {field}")

    try:
        log_id = log_access_attempt(
            partner_id=event["partner_id"],
            request_type=event["request_type"],
            endpoint=event["endpoint"],
            response_code=event["response_code"],
            content_id=event.get("content_id"),
            price_charged=event.get("price_charged"),
            currency=event.get("currency", "USD"),
            user_agent=event.get("user_agent"),
            ip_address=event.get("ip_address"),
            country_code=event.get("country_code"),
            additional_metadata=event.get("metadata"),
        )

        return create_success_response({"log_id": log_id, "message": "Access logged successfully"})

    except Exception as e:
        return create_error_response(500, "Failed to log access", str(e))


def handle_get_partner_stats(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle partner statistics request"""
    partner_id = event.get("partner_id")

    if not partner_id:
        return create_error_response(400, "Missing partner_id parameter")

    try:
        stats = get_partner_usage_stats(
            partner_id=partner_id, start_date=event.get("start_date"), end_date=event.get("end_date")
        )

        return create_success_response(stats)

    except Exception as e:
        return create_error_response(500, "Failed to get partner statistics", str(e))


def handle_get_content_stats(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle content statistics request"""
    content_id = event.get("content_id")

    if not content_id:
        return create_error_response(400, "Missing content_id parameter")

    try:
        stats = get_content_access_stats(
            content_id=content_id, start_date=event.get("start_date"), end_date=event.get("end_date")
        )

        return create_success_response(stats)

    except Exception as e:
        return create_error_response(500, "Failed to get content statistics", str(e))
