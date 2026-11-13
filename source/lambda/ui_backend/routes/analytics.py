"""
Analytics domain Route_Module (``/analytics/*``).

Thin per-domain route adapter for the ui_backend thin dispatcher (Requirements 4.1, 4.2,
4.3). It exposes the uniform ``route(event, method, path)`` entry and delegates the
domain's business logic and all data-store access to ``services.analytics_service`` — the
Service_Layer single home for analytics (Requirement 8.1). It performs no analytics domain
logic and no direct AWS/data-store access itself; it only applies Cognito auth (via the
unchanged ``@require_cognito_auth`` wrapper), calls the service, and formats the response
with the shared ``create_success_response`` / ``create_error_response`` helpers.

Behavior-preserving (Requirement 2): the ``route`` dispatch mirrors the monolith's
``handle_analytics_endpoints`` path/method table exactly, and each handler keeps the same
``@validate_response`` + ``@require_cognito_auth`` decorator stack and the same
success/error responses (status/body/CORS) as the pre-refactor inline handlers.

``/analytics/partners`` is a behavior-preserving alias of ``GET /partners`` and is served
from the partners single home ``services.partners_service`` (design route table:
``/analytics/partners`` -> ``services/partners_service.py``), matching the monolith's
``handle_analytics_partners = handle_get_partners`` alias — including its
``@validate_response("/partners", "GET")`` validation target and its
``"Failed to retrieve partner data"`` error message.

This module is created ahead of the dispatcher flip (task 8.5); the dispatcher continues to
route ``/analytics/*`` to the inline handler until that flip lands (Requirement 16.4). All
imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``sys.path`` manipulation (Requirements 5.1,
15.2); this module imports no other Route_Module (Requirement 4.4).
"""

import json
import logging
from typing import Any, Dict

from services.analytics_service import AnalyticsService
from services.partners_service import PartnersService
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.response_validator import validate_response

logger = logging.getLogger(__name__)

# Stateless service instances; instantiated once at module load so import failures surface
# at load time rather than per request (Requirement 5.4).
_service = AnalyticsService()
_partners_service = PartnersService()


def _parse_request_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON request body from the event.

    The dispatcher validates the body up front and rejects invalid JSON with a 400, so by
    the time this route runs the body is either absent or valid. Mirrors the monolith's
    ``request_data = json.loads(body)`` parse and falls back to ``{}`` when absent.
    """
    body = event.get("body", "")
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


@validate_response("/analytics/metrics", "GET")
@require_cognito_auth
def handle_analytics_metrics(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle analytics metrics endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        metrics_data = _service.get_metrics(publisher_id)
        return create_success_response(metrics_data)

    except Exception as e:
        logger.error(f"Error in handle_analytics_metrics: {str(e)}")
        return create_error_response(500, "Failed to retrieve analytics metrics")


@validate_response("/analytics/usage", "GET")
@require_cognito_auth
def handle_analytics_usage(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle analytics usage endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        # Extract query parameters for time range
        query_params = event.get("queryStringParameters") or {}

        usage_data = _service.get_usage(publisher_id, query_params)
        return create_success_response(usage_data)

    except Exception as e:
        logger.error(f"Error in handle_analytics_usage: {str(e)}")
        return create_error_response(500, "Failed to retrieve usage analytics")


@validate_response("/analytics/revenue", "GET")
@require_cognito_auth
def handle_analytics_revenue(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle analytics revenue endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        revenue_data = _service.get_revenue(publisher_id)
        return create_success_response(revenue_data)

    except Exception as e:
        logger.error(f"Error in handle_analytics_revenue: {str(e)}")
        return create_error_response(500, "Failed to retrieve revenue analytics")


@validate_response("/analytics/trends", "GET")
@require_cognito_auth
def handle_analytics_trends(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle analytics trends endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        trends_data = _service.get_trends(publisher_id)
        return create_success_response(trends_data)

    except Exception as e:
        logger.error(f"Error in handle_analytics_trends: {str(e)}")
        return create_error_response(500, "Failed to retrieve analytics trends")


@validate_response("/analytics/revenue/categories", "GET")
@require_cognito_auth
def handle_analytics_revenue_categories(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle analytics revenue categories endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        categories_data = _service.get_revenue_categories(publisher_id)
        return create_success_response(categories_data)

    except Exception as e:
        logger.error(f"Error in handle_analytics_revenue_categories: {str(e)}")
        return create_error_response(500, "Failed to retrieve revenue by categories")


@validate_response("/partners", "GET")
@require_cognito_auth
def handle_analytics_partners(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle ``/analytics/partners`` — alias of ``GET /partners`` (partners single home)."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        partners_data = _partners_service.get_partners(publisher_id)
        return create_success_response(partners_data)

    except Exception as e:
        logger.error(f"Error in handle_get_partners: {str(e)}")
        return create_error_response(500, "Failed to retrieve partner data")


@validate_response("/analytics/access-logs", "GET")
@require_cognito_auth
def handle_analytics_access_logs(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle analytics access logs endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        # Extract query parameters
        query_params = event.get("queryStringParameters") or {}

        response_data = _service.get_access_logs(publisher_id, query_params)
        return create_success_response(response_data)

    except Exception as e:
        logger.error(f"Error in handle_analytics_access_logs: {str(e)}")
        return create_error_response(500, "Failed to retrieve access logs")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Dispatch ``/analytics/*`` to the analytics service (single source of truth).

    Mirrors the monolith's ``handle_analytics_endpoints`` routing exactly so responses
    (status/body/CORS) are identical to the pre-refactor inline handler.
    """
    headers = event.get("headers") or {}
    request_data = _parse_request_data(event)

    if path == "/analytics/metrics" and method == "GET":
        return handle_analytics_metrics(headers, request_data, event)
    elif path == "/analytics/usage" and method == "GET":
        return handle_analytics_usage(headers, request_data, event)
    elif path == "/analytics/revenue" and method == "GET":
        return handle_analytics_revenue(headers, request_data, event)
    elif path == "/analytics/trends" and method == "GET":
        return handle_analytics_trends(headers, request_data, event)
    elif path == "/analytics/revenue/categories" and method == "GET":
        return handle_analytics_revenue_categories(headers, request_data, event)
    elif path == "/analytics/partners" and method == "GET":
        return handle_analytics_partners(headers, request_data, event)
    elif path == "/analytics/access-logs" and method == "GET":
        return handle_analytics_access_logs(headers, request_data, event)
    else:
        return create_error_response(404, "Analytics endpoint not found")
