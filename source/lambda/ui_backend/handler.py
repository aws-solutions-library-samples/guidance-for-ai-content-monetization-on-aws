"""
UI Backend Lambda Handler

Handles internal dashboard operations with Cognito JWT authentication.
Endpoints: /analytics/*, /pricing/*, /bot-control/*, /content/*, /log (internal)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Route_Table-backed resolver for the thin dispatcher. Imported as ``routes`` (not
# ``ui_backend.routes``) to match the deployment zip-root layout where handler.py and the
# routes/ package both sit at the archive root. No sys.path manipulation is used to resolve
# this import (Requirement 15.2/15.3).
from routes import resolve_route

# Analytics domain migrated to its own route module (task 8.5). The dispatcher now routes
# ``/analytics/*`` to ``routes/analytics.py`` (delegating to
# ``services.analytics_service.AnalyticsService``, with the ``/analytics/partners`` alias served
# from ``services.partners_service.PartnersService``) instead of the (deleted) interim
# ``_route_analytics`` adapter, the inline ``handle_analytics_endpoints`` router, and the inline
# ``handle_analytics_*`` handlers. Imported as ``routes.analytics`` (zip-root layout), no sys.path
# manipulation (Requirement 15).
from routes.analytics import route as analytics_route

# Analytics-agent domain migrated to its own route module (task 11.4). The dispatcher now routes
# ``/analytics-agent/*`` to ``routes/analytics_agent.py`` (delegating to
# ``services.agent_runtime_client.AgentRuntimeClient``, the Service_Layer single home for the
# bedrock-agentcore ``invoke_agent_runtime`` call — Requirement 7.3) instead of the (deleted)
# interim ``_route_analytics_agent`` adapter, the inline ``handle_analytics_agent_endpoints``
# router, and the inline ``handle_analytics_agent_chat`` handler (which carried the inline
# ``invoke_agent_runtime`` call). Zero inline ``invoke_agent_runtime`` calls remain after this flip
# (Requirement 7.3). Imported as ``routes.analytics_agent`` (zip-root layout), no sys.path
# manipulation (Requirement 15).
from routes.analytics_agent import route as analytics_agent_route

# Bot-control domain migrated to its own route module (task 6.3). The dispatcher now routes
# ``/bot-control/*`` to ``routes/bot_control.py`` (delegating to
# ``services.bot_control_service.BotControlService``, now the single home for the bot-control
# domain's logic + ``iab-bot-rules-table`` DynamoDB access — the former bot-control API root
# module was extracted into the service and deleted, task 22) instead of the (deleted) inline
# ``handle_bot_control_endpoints`` router, its try/except ImportError fallback, and the inline mock
# bot-control handlers. Imported as ``routes.bot_control`` (zip-root layout), no sys.path
# manipulation (Requirement 15).
from routes.bot_control import route as bot_control_route

# Connections domain (integration-adapters #35, task 9.1). The dispatcher routes ``/connections*``
# to ``routes/connections.py``, the internal (Cognito) Connections API: adapter catalog via the
# integrations Registry, CRUD over cm-connections config (secrets written to Secrets Manager, only
# secret_ref persisted), test()/status() invocation, and the read-only mirror endpoint. Imported as
# ``routes.connections`` (zip-root layout), no sys.path manipulation (Requirement 15).
from routes.connections import route as connections_route

# Content domain migrated to its own route module (task 5.3). The dispatcher now routes
# ``/content*`` to ``routes/content.py`` (delegating to ``services.content_service.ContentService``,
# which for now still delegates to ``content_management.lambda_handler`` as an interim strangler-fig
# step per Requirement 4.5) instead of the (deleted) inline ``handle_content_endpoints`` router and
# its inline mock content handlers. Imported as ``routes.content`` (zip-root layout), no sys.path
# manipulation (Requirement 15).
from routes.content import route as content_route
from routes.llms_txt import route as llms_txt_route

# OpenAPI docs meta-route (#31): serves the contract to the in-app API reference page (full, or
# ?audience=partner via shared derive_partner_spec). Imported as ``routes.openapi`` (zip-root
# layout), no sys.path manipulation (Requirement 15).
from routes.openapi import route as openapi_route

# Partners domain migrated to its own route module (task 10.4). The dispatcher now routes both
# ``GET /partners`` and ``PUT /partners/{id}`` to ``routes/partners.py`` (delegating to
# ``services.partners_service.PartnersService``, the Service_Layer single home for the partners
# domain) instead of the (deleted) interim ``_route_partners_list`` / ``_route_partners_update``
# adapters and the inline ``handle_get_partners`` / ``handle_update_partner`` handlers. The route
# module's ``route(event, method, path)`` handles both endpoints, so both dispatch entries point
# at it. Imported as ``routes.partners`` (zip-root layout), no sys.path manipulation
# (Requirement 15).
from routes.partners import route as partners_route

# Pricing domain migrated to its own route module (task 9.4). The dispatcher now routes
# ``/pricing/*`` to ``routes/pricing.py`` (delegating to
# ``services.pricing_manager.PricingVersionManager``, the single home for the pricing domain,
# which owns the graceful-fallback / mock-seed feature flag) instead of the (deleted) interim
# ``_route_pricing`` adapter, the inline ``handle_pricing_endpoints`` router, and the inline
# ``handle_*_pricing_*`` handlers. Imported as ``routes.pricing`` (zip-root layout), no sys.path
# manipulation (Requirement 15).
from routes.pricing import route as pricing_route

# Processing domain migrated to its own route module (task 4.4). The dispatcher now routes
# ``/processing/status`` to ``routes/processing.py`` (delegating to
# ``services.processing_jobs_service.ProcessingJobsService``) instead of the (deleted) inline
# handler. Imported as ``routes.processing`` (zip-root layout), no sys.path manipulation
# (Requirement 15).
from routes.processing import route as processing_route

# Taxonomy domain migrated to its own route module (task 3.3). The dispatcher now routes
# ``/taxonomy/categories`` to ``routes/taxonomy.py`` instead of the (deleted) inline handler.
# Imported as ``routes.taxonomy`` (zip-root layout), no sys.path manipulation (Requirement 15).
from routes.taxonomy import route as taxonomy_route
from shared.auth import require_cognito_auth, require_partner_or_cognito_auth
from shared.utils.log_redaction import redact_event
from shared.utils.response_helpers import cors_headers, create_error_response, create_success_response
from shared.utils.response_validator import validate_response


def extract_cognito_user_from_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract Cognito user information from API Gateway event"""
    try:
        # Check if API Gateway Cognito authorizer populated the request context
        request_context = event.get("requestContext", {})
        authorizer = request_context.get("authorizer", {})

        if "claims" in authorizer:
            claims = authorizer["claims"]
            return {
                "user_id": claims.get("sub"),
                "email": claims.get("email"),
                "name": claims.get("name", claims.get("email")),
                "publisher_id": claims.get("custom:publisher_id", "default_publisher"),
                "type": "cognito_publisher",
            }

        # Fallback: try to decode JWT from Authorization header
        headers = event.get("headers", {})
        auth_header = headers.get("Authorization") or headers.get("authorization")

        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                import time

                import jwt

                # Decode without verification (API Gateway already verified)
                payload = jwt.decode(
                    token, options={"verify_signature": False}
                )  # nosemgrep: unverified-jwt-decode - Cognito token validated by API Gateway authorizer

                if payload.get("token_use") == "id" and payload.get("exp", 0) > time.time():
                    logger.info("Detected Cognito JWT token")
                    logger.info(f"JWT access: {payload.get('sub')} - {event.get('path')}")
                    return {
                        "user_id": payload.get("sub"),
                        "email": payload.get("email"),
                        "name": payload.get("name", payload.get("email")),
                        "publisher_id": payload.get("custom:publisher_id", "default_publisher"),
                        "type": "cognito_publisher",
                    }
            except Exception as e:
                logger.error(f"JWT decode error: {str(e)}")

        return None
    except Exception as e:
        logger.error(f"Error extracting Cognito user: {str(e)}")
        return None


def require_auth(func):
    """Simple authentication decorator that extracts user from API Gateway context"""

    def wrapper(*args, **kwargs):
        # Find the event parameter
        event = None
        if len(args) >= 3:
            event = args[2]  # Typically (headers, request_data, event)
        elif "event" in kwargs:
            event = kwargs["event"]

        if not event:
            return create_error_response(401, "Authentication required")

        user = extract_cognito_user_from_event(event)
        if not user:
            return create_error_response(401, "Invalid or missing authentication")

        # Add user to kwargs
        kwargs["authenticated_user"] = user
        return func(*args, **kwargs)

    return wrapper


# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handle_options_request() -> Dict[str, Any]:
    """
    Handle OPTIONS requests for CORS preflight
    """
    return {
        "statusCode": 200,
        "headers": {
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            **cors_headers(),
        },
        "body": "",
    }


def _parse_request_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON request body from the event.

    The dispatcher validates the body up front and rejects invalid JSON with a 400, so by
    the time a route adapter runs the body is either absent or valid. This helper mirrors
    the monolith's ``request_data = json.loads(body)`` parse and falls back to ``{}`` when
    the body is absent (or, defensively, if it somehow failed to parse).
    """
    body = event.get("body", "")
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Dispatch adapters (strangler-fig interim).
#
# Each adapter has the uniform ``route(event, method, path)`` signature that per-domain
# route modules will expose, and delegates to the existing inline handler exactly as the
# pre-refactor monolith did (same target function, same arguments) so behavior is unchanged
# (Requirement 16.4). As each domain is migrated, its ``_DISPATCH`` entry is flipped from
# the inline adapter below to the new ``routes/<domain>.py`` ``route`` entry.
#
# Adapters reference the inline handlers by their global name so that they resolve at call
# time — this keeps unit-test monkeypatching of ``ui_backend.handler.handle_*`` working.
# ---------------------------------------------------------------------------


def _route_log(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    headers = event.get("headers") or {}
    request_data = _parse_request_data(event)
    return handle_internal_log_request(headers, request_data, event)


# Maps the route name returned by ``resolve_route`` to its handler. Interim: every entry
# points at an inline-delegating adapter, so behavior is identical to the monolith. Each
# domain migration flips exactly one entry to ``routes/<domain>.py``.
_DISPATCH = {
    "analytics": analytics_route,
    "partners": partners_route,
    "pricing": pricing_route,
    "bot_control": bot_control_route,
    "content": content_route,
    "connections": connections_route,
    "llms_txt": llms_txt_route,
    "log": _route_log,
    "processing": processing_route,
    "taxonomy": taxonomy_route,
    "analytics_agent": analytics_agent_route,
    "openapi": openapi_route,
}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    UI Backend Lambda handler for internal dashboard operations.

    Thin dispatcher: it owns the cross-cutting concerns (CORS preflight short-circuit,
    JSON body validation, unmatched-route 404, top-level exception 500, and structured
    request logging) and delegates each matched route to its handler via the Route_Table.
    CORS headers are attached to every response by the ``create_*_response`` helpers and
    the OPTIONS preflight response.
    """
    request_id = getattr(context, "aws_request_id", "unknown")
    logger.info(f"=== UI_BACKEND_LAMBDA START === Request ID: {request_id}")
    logger.info(f"Event: {json.dumps(redact_event(event), default=str)}")

    try:
        # Extract request information
        http_method = event.get("httpMethod", "")
        path = event.get("path", "")

        # Structured request log: request id, method, path (Requirement 3.7)
        logger.info(f"UI_BACKEND_DISPATCH request_id={request_id} method={http_method} path={path}")

        # Cross-cutting: CORS preflight short-circuit — no route resolution or auth
        if http_method == "OPTIONS":
            return handle_options_request()

        # Cross-cutting: reject a malformed JSON body before any route runs
        body = event.get("body", "")
        if body:
            try:
                json.loads(body)
            except json.JSONDecodeError:
                return create_error_response(400, "Invalid JSON in request body")

        # Resolve to exactly one route via the ordered Route_Table
        route_name = resolve_route(path, http_method)
        if route_name is None:
            return create_error_response(404, "Endpoint not found")

        # Delegate to the resolved handler (interim: inline-delegating adapter)
        return _DISPATCH[route_name](event, http_method, path)

    except Exception as e:
        logger.error(f"=== UI_BACKEND_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}")
        return create_error_response(500, "Internal server error")


# Pricing Endpoints — migrated to ``routes/pricing.py`` -> ``services.pricing_manager``
# (task 9.4). The interim ``_route_pricing`` adapter, the inline ``handle_pricing_endpoints``
# router, and the inline pricing handlers (handle_get_pricing_rules,
# handle_get_pricing_rules_resolved, handle_create_pricing_rules, handle_update_pricing_rules,
# handle_get_pricing_history, handle_calculate_pricing, handle_fix_pricing_versions) were the
# pre-refactor inline implementation; they were deleted in the same change that flipped the
# ``/pricing/*`` dispatcher entry (Requirement 16.3, mirroring taxonomy 3.3, processing 4.4,
# content 5.3, bot_control 6.3, analytics 8.5). Pricing route equivalence + auth are now enforced
# by ``tests/ui_backend/test_pricing_route_characterization.py``.


# Analytics-Agent Endpoints — migrated to ``routes/analytics_agent.py`` ->
# ``services.agent_runtime_client.AgentRuntimeClient`` (task 11.4). The interim
# ``_route_analytics_agent`` adapter, the inline ``handle_analytics_agent_endpoints`` router, and the
# inline ``handle_analytics_agent_chat`` handler — including its inline bedrock-agentcore
# ``invoke_agent_runtime`` call — were the pre-refactor inline implementation; they were deleted in
# the same change that flipped the ``/analytics-agent/*`` dispatcher entry (Requirement 16.3,
# mirroring taxonomy 3.3, processing 4.4, content 5.3, bot_control 6.3, analytics 8.5, pricing 9.4,
# partners 10.4). Zero inline ``invoke_agent_runtime`` calls remain after this flip (Requirement
# 7.3). Analytics-agent route equivalence + auth are now enforced by
# ``tests/ui_backend/test_analytics_agent_characterization.py``.


# Partners Endpoints — migrated to ``routes/partners.py`` -> ``services.partners_service.PartnersService``
# (task 10.4). The interim ``_route_partners_list`` / ``_route_partners_update`` adapters and the
# inline ``handle_get_partners`` / ``handle_update_partner`` handlers (both the ``GET /partners``
# reader and the ``PUT /partners/{id}`` writer) were the pre-refactor inline implementation; they
# were deleted in the same change that flipped the ``partners_list`` / ``partners_update`` dispatcher
# entries (Requirement 16.3, mirroring taxonomy 3.3, processing 4.4, content 5.3, bot_control 6.3,
# analytics 8.5, pricing 9.4). The ``/analytics/partners`` alias is served from the same
# ``PartnersService`` reader via ``routes/analytics.py``. Partners route equivalence + auth are now
# enforced by ``tests/ui_backend/test_partners_route_characterization.py``.


# Content Management Endpoints — migrated to ``routes/content.py`` -> ``services.content_service``
# (task 5.3). The inline mock content handlers (handle_list_content, handle_create_content,
# handle_get_content, handle_update_content, handle_delete_content, handle_bulk_content_operations)
# and the ``handle_content_endpoints`` router were the pre-refactor inline implementation; they were
# deleted in the same change that flipped the ``/content*`` dispatcher entry (Requirement 16.3).
# Content route equivalence + auth are now enforced by
# ``tests/content_management/test_content_route_characterization.py``.


# Other Endpoints
@validate_response("/log", "POST")
@require_partner_or_cognito_auth
def handle_internal_log_request(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle internal log endpoint (for UI testing/monitoring)"""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        # Log the internal request
        log_entry = {
            "log_id": f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "logged_by": user.get("user_id"),
            "publisher_id": publisher_id,
            "log_type": "internal_ui_test",
            "data": request_data,
        }

        logger.info(f"Internal log entry: {json.dumps(log_entry)}")

        return create_success_response({"logged": True, "log_id": log_entry["log_id"]})

    except Exception as e:
        logger.error(f"Error in handle_internal_log_request: {str(e)}")
        return create_error_response(500, "Failed to log request")
