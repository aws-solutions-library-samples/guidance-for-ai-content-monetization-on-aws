"""
Internal log handler for UI Backend Lambda

This handler provides internal logging endpoints for the UI Backend Lambda.
It replaces the external /log endpoint that was previously exposed to partners.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from services.internal_logging_service import InternalLoggingService

from ..auth.authentication_decorators import require_cognito_auth
from ..utils.response_helpers import create_error_response, create_success_response, create_validation_error_response

logger = logging.getLogger(__name__)


@require_cognito_auth
def handle_internal_log_partner_usage(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle internal logging of partner usage for billing tracking

    This endpoint is for internal use by the UI Backend to log partner usage
    for billing purposes. It's not exposed to external partners.

    Args:
        headers: HTTP headers
        request_data: Request body data
        event: API Gateway event
        **kwargs: Additional arguments including authenticated_user

    Returns:
        API response dictionary
    """
    logger.info("=== HANDLE_INTERNAL_LOG_PARTNER_USAGE START ===")

    try:
        user = kwargs["authenticated_user"]
        logging_service = InternalLoggingService()

        # Validate required fields
        required_fields = ["partner_id", "usage_data"]
        validation_errors = {}

        for field in required_fields:
            if field not in request_data:
                validation_errors[field] = f"Field '{field}' is required"

        if validation_errors:
            return create_validation_error_response(validation_errors)

        # Extract parameters
        partner_id = request_data["partner_id"]
        usage_data = request_data["usage_data"]
        timestamp = request_data.get("timestamp")

        # Parse timestamp if provided
        parsed_timestamp = None
        if timestamp:
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return create_error_response(400, "Invalid timestamp format. Use ISO 8601 format.")

        # Log partner usage
        result = logging_service.log_partner_usage(
            partner_id=partner_id, usage_data=usage_data, timestamp=parsed_timestamp
        )

        logger.info(f"=== HANDLE_INTERNAL_LOG_PARTNER_USAGE END === Logged usage for partner {partner_id}")

        return create_success_response(
            {
                "status": "logged",
                "log_id": result["log_id"],
                "timestamp": result["timestamp"],
                "logged_by": user["user_id"],
            }
        )

    except Exception as e:
        logger.error(f"=== HANDLE_INTERNAL_LOG_PARTNER_USAGE ERROR === {str(e)}")
        return create_error_response(500, "Failed to log partner usage")


@require_cognito_auth
def handle_internal_log_ui_test_usage(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle internal logging of UI test/preview usage

    This endpoint logs UI testing and preview usage for analytics purposes
    (not for billing).

    Args:
        headers: HTTP headers
        request_data: Request body data
        event: API Gateway event
        **kwargs: Additional arguments including authenticated_user

    Returns:
        API response dictionary
    """
    logger.info("=== HANDLE_INTERNAL_LOG_UI_TEST_USAGE START ===")

    try:
        user = kwargs["authenticated_user"]
        logging_service = InternalLoggingService()

        # Validate required fields
        required_fields = ["test_usage_data"]
        validation_errors = {}

        for field in required_fields:
            if field not in request_data:
                validation_errors[field] = f"Field '{field}' is required"

        if validation_errors:
            return create_validation_error_response(validation_errors)

        # Extract parameters
        test_usage_data = request_data["test_usage_data"]
        timestamp = request_data.get("timestamp")

        # Parse timestamp if provided
        parsed_timestamp = None
        if timestamp:
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return create_error_response(400, "Invalid timestamp format. Use ISO 8601 format.")

        # Log UI test usage
        result = logging_service.log_ui_test_usage(
            user_id=user["user_id"],
            publisher_id=user["publisher_id"],
            test_usage_data=test_usage_data,
            timestamp=parsed_timestamp,
        )

        logger.info(f"=== HANDLE_INTERNAL_LOG_UI_TEST_USAGE END === Logged UI test usage for user {user['user_id']}")

        return create_success_response(
            {
                "status": "logged",
                "log_id": result["log_id"],
                "timestamp": result["timestamp"],
                "note": result["note"],
                "logged_by": user["user_id"],
            }
        )

    except Exception as e:
        logger.error(f"=== HANDLE_INTERNAL_LOG_UI_TEST_USAGE ERROR === {str(e)}")
        return create_error_response(500, "Failed to log UI test usage")


@require_cognito_auth
def handle_get_usage_logs(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle retrieval of usage logs for analysis and billing

    This endpoint allows authorized users to retrieve usage logs for
    analysis, billing, and monitoring purposes.

    Args:
        headers: HTTP headers
        request_data: Request body data (filters)
        event: API Gateway event
        **kwargs: Additional arguments including authenticated_user

    Returns:
        API response dictionary with usage logs
    """
    logger.info("=== HANDLE_GET_USAGE_LOGS START ===")

    try:
        user = kwargs["authenticated_user"]
        logging_service = InternalLoggingService()

        # Extract filters from request data
        filters = request_data.get("filters", {})
        limit = min(request_data.get("limit", 100), 1000)  # Cap at 1000

        # Add publisher scoping if user is not admin
        if "admin" not in user.get("groups", []):
            # For non-admin users, only show logs related to their publisher
            filters["publisher_id"] = user["publisher_id"]

        # Retrieve usage logs
        logs = logging_service.get_usage_logs(filters=filters, limit=limit)

        logger.info(f"=== HANDLE_GET_USAGE_LOGS END === Retrieved {len(logs)} logs")

        return create_success_response(
            {"logs": logs, "count": len(logs), "filters_applied": filters, "requested_by": user["user_id"]}
        )

    except Exception as e:
        logger.error(f"=== HANDLE_GET_USAGE_LOGS ERROR === {str(e)}")
        return create_error_response(500, "Failed to retrieve usage logs")


@require_cognito_auth
def handle_log_api_access(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle logging of API access for monitoring and analytics

    This endpoint logs API access events for monitoring, analytics,
    and security purposes.

    Args:
        headers: HTTP headers
        request_data: Request body data
        event: API Gateway event
        **kwargs: Additional arguments including authenticated_user

    Returns:
        API response dictionary
    """
    logger.info("=== HANDLE_LOG_API_ACCESS START ===")

    try:
        user = kwargs["authenticated_user"]
        logging_service = InternalLoggingService()

        # Validate required fields
        required_fields = ["endpoint", "user_id", "auth_type", "metadata"]
        validation_errors = {}

        for field in required_fields:
            if field not in request_data:
                validation_errors[field] = f"Field '{field}' is required"

        if validation_errors:
            return create_validation_error_response(validation_errors)

        # Extract parameters
        endpoint = request_data["endpoint"]
        logged_user_id = request_data["user_id"]
        auth_type = request_data["auth_type"]
        metadata = request_data["metadata"]
        timestamp = request_data.get("timestamp")

        # Parse timestamp if provided
        parsed_timestamp = None
        if timestamp:
            try:
                parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                return create_error_response(400, "Invalid timestamp format. Use ISO 8601 format.")

        # Log API access
        result = logging_service.log_api_access(
            endpoint=endpoint,
            user_id=logged_user_id,
            auth_type=auth_type,
            metadata=metadata,
            timestamp=parsed_timestamp,
        )

        logger.info(f"=== HANDLE_LOG_API_ACCESS END === Logged API access for {auth_type} user {logged_user_id}")

        return create_success_response(
            {
                "status": "logged",
                "log_id": result["log_id"],
                "timestamp": result["timestamp"],
                "logged_by": user["user_id"],
            }
        )

    except Exception as e:
        logger.error(f"=== HANDLE_LOG_API_ACCESS ERROR === {str(e)}")
        return create_error_response(500, "Failed to log API access")
