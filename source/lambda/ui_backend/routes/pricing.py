"""
Pricing route module (thin adapter over ``services.pricing_manager``).

This is the per-domain Route_Module for the ``/pricing/*`` prefix. It exposes the
uniform ``route(event, method, path)`` entry the thin dispatcher calls and delegates
all business logic, data-store access, and mock/seed behavior to
:class:`services.pricing_manager.PricingVersionManager` (the single home for the
pricing domain). The graceful-fallback feature flag that governs mock/seed data lives
inside the service (Requirements 4.1-4.3, 5.1, 7.5, 7.6).

Endpoints handled (behavior-preserving, identical to the pre-refactor inline handlers):

- ``GET  /pricing/rules``           -> ``PricingVersionManager.get_pricing_rules_view`` (resolved)
- ``POST /pricing/rules``           -> ``PricingVersionManager.create_pricing_rule_version`` (+ activate)
- ``PUT  /pricing/rules``           -> ``PricingVersionManager.create_pricing_rule_version`` (+ activate)
- ``GET  /pricing/history``         -> ``PricingVersionManager.get_pricing_history_view``
- ``POST /pricing/calculate``       -> ``PricingVersionManager.calculate_pricing_estimate``

Imports are canonical-root-rooted at ``source/lambda/`` and declared at module top level
(no lazy imports, no ``sys.path`` manipulation) per Requirements 5.1, 15.1-15.3.
"""

import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Dict

from services.pricing_manager import PricingVersionManager
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.response_validator import validate_response

logger = logging.getLogger(__name__)


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


@validate_response("/pricing/rules", "GET")
@require_cognito_auth
def handle_get_pricing_rules(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle get pricing rules endpoint with graceful fallback to mock data (in service)."""
    try:
        user = kwargs["authenticated_user"]

        # Extract query parameters from event (not request_data for GET requests)
        query_params = event.get("queryStringParameters") or {}
        rule_set_id = query_params.get("rule_set_id", "default-pricing")
        effective_date = query_params.get("effective_date")  # Optional, defaults to today
        created_by = user.get("email", "demo@example.com")

        pricing_manager = PricingVersionManager()
        response_data = pricing_manager.get_pricing_rules_view(rule_set_id, effective_date, created_by)

        if response_data is not None:
            return create_success_response(response_data)

        # If fallback is disabled and no active rules exist, return 404
        return create_error_response(404, "No active pricing rules found")

    except Exception as e:
        logger.error(f"Error in handle_get_pricing_rules: {str(e)}")
        return create_error_response(500, "Failed to retrieve pricing rules")


@validate_response("/pricing/rules", "POST")
@require_cognito_auth
def handle_create_pricing_rules(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle create pricing rules endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        logger.info(f"Creating pricing rules for publisher: {publisher_id}")
        logger.info(f"Request data: {json.dumps(request_data, default=str)}")

        # Validate required fields
        if "pricing_rules" not in request_data:
            return create_error_response(400, "pricing_rules is required")

        if "effective_date" not in request_data:
            return create_error_response(400, "effective_date is required")

        if "change_reason" not in request_data:
            return create_error_response(400, "change_reason is required")

        pricing_manager = PricingVersionManager()

        # Create new pricing rule version
        result = pricing_manager.create_pricing_rule_version(
            rule_set_id=request_data.get("rule_set_id", "default-pricing"),
            pricing_rules=request_data["pricing_rules"],
            effective_date=request_data["effective_date"],
            change_reason=request_data["change_reason"],
            created_by=user.get("email", user.get("user_id")),
        )

        # Activate if requested
        if request_data.get("activate", False):
            result = pricing_manager.activate_pricing_rule_version(
                rule_set_id=result["rule_set_id"], version_timestamp=result["version_timestamp"]
            )

        logger.info(f"Successfully created pricing rules: {result.get('rule_set_id')}")

        response_data = {
            "status": "success",
            "pricing_rule": result,
            "message": "Pricing rules created successfully",
        }

        return create_success_response(response_data, status_code=201)

    except Exception as e:
        logger.error(f"Error in handle_create_pricing_rules: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return create_error_response(500, f"Failed to create pricing rules: {str(e)}")


@validate_response("/pricing/rules", "PUT")
@require_cognito_auth
def handle_update_pricing_rules(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle update pricing rules endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        logger.info(f"Updating pricing rules for publisher: {publisher_id}")
        logger.info(f"Request data: {json.dumps(request_data, default=str)}")

        # Validate required fields
        if "pricing_rules" not in request_data:
            return create_error_response(400, "pricing_rules is required")

        if "effective_date" not in request_data:
            return create_error_response(400, "effective_date is required")

        if "change_reason" not in request_data:
            return create_error_response(400, "change_reason is required")

        pricing_manager = PricingVersionManager()

        # Get current active version for audit trail
        rule_set_id = request_data.get("rule_set_id", "default-pricing")
        current_rules = pricing_manager.get_active_pricing_rules(rule_set_id)
        previous_version = current_rules["version_timestamp"] if current_rules else None

        # Update pricing rules (creates new version)
        result = pricing_manager.create_pricing_rule_version(
            rule_set_id=rule_set_id,
            pricing_rules=request_data["pricing_rules"],
            effective_date=request_data["effective_date"],
            change_reason=request_data["change_reason"],
            created_by=user.get("email", user.get("user_id")),
            previous_version=previous_version,
        )

        # Activate the new version (updates are typically activated immediately)
        result = pricing_manager.activate_pricing_rule_version(
            rule_set_id=result["rule_set_id"], version_timestamp=result["version_timestamp"]
        )

        logger.info(f"Successfully updated pricing rules: {result.get('rule_set_id')}")

        response_data = {
            "status": "success",
            "pricing_rule": result,
            "message": "Pricing rules updated successfully",
        }

        return create_success_response(response_data)

    except Exception as e:
        logger.error(f"Error in handle_update_pricing_rules: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return create_error_response(500, f"Failed to update pricing rules: {str(e)}")


@validate_response("/pricing/history", "GET")
@require_cognito_auth
def handle_get_pricing_history(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle get pricing history endpoint (real/mock decision owned by the service)."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        # Get query parameters from the event
        query_params = event.get("queryStringParameters") or {}
        rule_set_id = query_params.get("rule_set_id", "default-pricing")
        limit = query_params.get("limit", 10)

        pricing_manager = PricingVersionManager()
        pricing_history = pricing_manager.get_pricing_history_view(rule_set_id, limit, publisher_id)

        return create_success_response(pricing_history)

    except Exception as e:
        logger.error(f"Error in handle_get_pricing_history: {str(e)}")
        return create_error_response(500, "Failed to retrieve pricing history")


@validate_response("/pricing/calculate", "POST")
@require_cognito_auth
def handle_calculate_pricing(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle calculate pricing endpoint."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        content_items = request_data.get("content_items", [])
        calculation_result = PricingVersionManager.calculate_pricing_estimate(content_items, publisher_id)

        return create_success_response(calculation_result)

    except Exception as e:
        logger.error(f"Error in handle_calculate_pricing: {str(e)}")
        return create_error_response(500, "Failed to calculate pricing")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Dispatch ``/pricing/*`` to the pricing service (single source of truth).

    Mirrors the monolith's ``handle_pricing_endpoints`` routing exactly so responses
    (status/body/CORS) are identical to the pre-refactor inline handler.
    """
    headers = event.get("headers") or {}
    request_data = _parse_request_data(event)

    if path == "/pricing/rules" and method == "GET":
        return handle_get_pricing_rules(headers, request_data, event)
    elif path == "/pricing/rules" and method == "POST":
        return handle_create_pricing_rules(headers, request_data, event)
    elif path == "/pricing/rules" and method == "PUT":
        return handle_update_pricing_rules(headers, request_data, event)
    elif path == "/pricing/history" and method == "GET":
        return handle_get_pricing_history(headers, request_data, event)
    elif path == "/pricing/calculate" and method == "POST":
        return handle_calculate_pricing(headers, request_data, event)
    else:
        return create_error_response(404, "Pricing endpoint not found")
