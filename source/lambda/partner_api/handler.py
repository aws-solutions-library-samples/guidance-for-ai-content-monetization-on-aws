"""
Partner API Lambda Handler

Handles external partner/bot access with Partner Key authentication.
Endpoints: /info, /taxonomy, /llms.txt (public), /query (hybrid)
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Import analytics collector for hybrid endpoint logging
from services.analytics_collector import AnalyticsCollector

# CoMP (IAB Content Monetization Protocol) v1.0 handling — detection, license validation (owning all
# `cm-licenses` Licenses_Table access), and Package-response building — lives in the Service_Layer
# single home `services/comp_query_service.py` (Requirement 18.1/18.6). The handler's CoMP path is
# limited to detecting a CoMP request, delegating to the service, and formatting the response
# (Requirement 18.2). The import is absolute, rooted at the Canonical_Import_Root `source/lambda/`,
# with no `sys.path` manipulation (Requirement 18.3, 15.1/15.2/15.3).
from services.comp_query_service import (
    handle_comp_query_request,
    is_comp_request,
)
from services.llms_txt_service import LlmsTxtService

# OpenSearch hybrid and filter-only discovery live in the shared service layer.
from services.search_service import (
    DiscoveryRequest,
    SearchService,
    SearchUnavailableError,
    validate_similarity_threshold,
)
from shared.auth import optional_auth, require_partner_auth, require_partner_or_cognito_auth
from shared.utils import cors_headers, create_error_response, create_success_response, validate_response
from shared.utils.log_redaction import redact_event
from shared.utils.partner_api_url import partner_api_link

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)


# Search/query logic and its data-store access are owned by the SearchService single home; the
# handler holds only this delegate instance (no direct AWS/data-store client here).
search_service = SearchService()


# Initialize analytics collector for hybrid endpoint logging
def get_analytics_collector():
    """Get analytics collector with lazy initialization"""
    if not hasattr(get_analytics_collector, "_collector"):
        get_analytics_collector._collector = AnalyticsCollector(os.environ.get("ANALYTICS_TABLE_NAME"))
    return get_analytics_collector._collector


# Import TaxonomyManager for database-driven taxonomy
from services.taxonomy_manager import TaxonomyManager

# Initialize taxonomy manager (used by the /taxonomy endpoint)
taxonomy_manager = TaxonomyManager()


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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Partner API Lambda handler for external partner/bot access
    """
    request_id = getattr(context, "aws_request_id", "unknown")
    logger.info(f"=== PARTNER_API_LAMBDA START === Request ID: {request_id}")
    logger.info(f"Event: {json.dumps(redact_event(event), default=str)}")

    try:
        # Extract request information
        http_method = event.get("httpMethod", "")
        path = event.get("path", "")
        headers = event.get("headers") or {}
        body = event.get("body", "")
        query_params = event.get("queryStringParameters") or {}

        # Parse request body if present
        request_data = {}
        if body:
            try:
                request_data = json.loads(body)
            except json.JSONDecodeError:
                return create_error_response(400, "Invalid JSON in request body")

        # Handle OPTIONS requests for CORS
        if http_method == "OPTIONS":
            return handle_options_request()

        # Route to appropriate handler
        if path == "/info" and http_method == "GET":
            return handle_info_request(headers, query_params, event)
        elif path == "/taxonomy" and http_method == "GET":
            return handle_taxonomy_request(headers, query_params, event)
        elif path == "/llms.txt" and http_method == "GET":
            return handle_llms_txt_request(headers, query_params, event)
        elif path == "/query" and http_method == "POST":
            return handle_query_request(headers, request_data, event)
        else:
            return create_error_response(404, "Endpoint not found")

    except Exception as e:
        logger.error(f"=== PARTNER_API_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}")
        return create_error_response(500, "Internal server error")


@validate_response("/info", "GET")
@optional_auth
def handle_info_request(
    headers: Dict[str, str], query_params: Dict[str, str], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle /info endpoint - API discovery
    """
    logger.info("=== HANDLE_INFO_REQUEST START ===")

    try:
        # Authentication is optional for info endpoint
        user = kwargs.get("authenticated_user")
        auth_type = kwargs.get("auth_type", "public")

        api_info = {
            "api_version": "1.0",
            "publisher": {"name": "Demo Publisher", "id": "pub_demo_001", "contact": "ai-content-monetization@demopublisher.com"},
            "authentication": {
                "method": "api_key",
                "header": "x-api-key",
                "registration_url": "https://demopublisher.com/ai-content-monetization/register",
            },
            "pricing": {
                "currency": "USD",
                "per_query_rate": 0.02,
                "content_rates": [
                    {"content_type": "article", "rate": 0.05, "description": "News articles and blog posts"},
                    {"content_type": "report", "rate": 0.25, "description": "Research reports and analysis"},
                ],
                "subscriptions": [
                    {
                        "plan_id": "starter",
                        "name": "Starter Plan",
                        "cost": 99,
                        "queries": 1000,
                        "period": "monthly",
                        "description": "Perfect for small AI applications",
                    },
                    {
                        "plan_id": "pro",
                        "name": "Pro Plan",
                        "cost": 499,
                        "queries": 10000,
                        "period": "monthly",
                        "description": "High-volume professional access",
                    },
                ],
            },
            "content": {
                "iab_categories": [
                    {"id": 1, "name": "Automotive", "tier_1": "Automotive"},
                    {"id": 52, "name": "Business and Finance", "tier_1": "Business and Finance"},
                    {"id": 111, "name": "Medical Health", "tier_1": "Medical Health"},
                    {"id": 126, "name": "Technology & Computing", "tier_1": "Technology & Computing"},
                ],
                "formats": ["json", "html", "pdf"],
                "query_endpoint": partner_api_link("/query", event),
            },
            "terms": {"url": "https://demopublisher.com/ai-content-monetization/terms", "last_updated": "2025-01-01"},
        }

        # Enhanced info for authenticated users
        if user and auth_type == "partner_key":
            api_info["partner_info"] = {
                "partner_id": user.get("partner_id"),
                "subscription": user.get("subscription", "basic"),
                "allowed_endpoints": user.get("allowed_endpoints", []),
            }
        elif user and auth_type == "cognito":
            api_info["publisher_info"] = {"publisher_id": user.get("publisher_id"), "testing_mode": True}

        logger.info("=== HANDLE_INFO_REQUEST END === API info retrieved successfully")
        return create_success_response(api_info)

    except Exception as e:
        logger.error(f"=== HANDLE_INFO_REQUEST ERROR === {str(e)}")
        return create_error_response(500, "Failed to retrieve API information")


@validate_response("/taxonomy", "GET")
@optional_auth
def handle_taxonomy_request(
    headers: Dict[str, str], query_params: Dict[str, str], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle /taxonomy endpoint - IAB Content Taxonomy
    """
    try:
        user = kwargs.get("authenticated_user")
        auth_type = kwargs.get("auth_type", "public")

        # Extract query parameters. HTTP query-string values always arrive as strings, so the
        # numeric tier_level is parsed to int once here (guarded against non-numeric input);
        # parent_id is a string end-to-end (table + response + spec), so it is compared as-is.
        tier_level = query_params.get("tier_level")
        parent_id = query_params.get("parent_id")
        tier_level_int = int(tier_level) if tier_level and str(tier_level).isdigit() else None

        # Get taxonomy data from database
        taxonomy_data = taxonomy_manager.get_iab_taxonomy()
        all_categories = taxonomy_data.get("categories", [])

        # Filter taxonomy based on parameters
        filtered_taxonomy = []
        for category in all_categories:
            if tier_level_int is not None and int(category.get("tier_level", 0)) != tier_level_int:
                continue
            if parent_id and str(category.get("parent_id")) != str(parent_id):
                continue
            filtered_taxonomy.append(category)

        response_data = {
            "categories": filtered_taxonomy,
            "total_count": len(filtered_taxonomy),
            "metadata": {
                "tier_level": tier_level_int,
                "parent_id": parent_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

        # Enhanced data for authenticated users
        if user:
            response_data["metadata"]["authenticated"] = True
            response_data["metadata"]["auth_type"] = auth_type

        return create_success_response(response_data)

    except Exception as e:
        logger.error(f"Error in handle_taxonomy_request: {str(e)}")
        return create_error_response(500, "Failed to retrieve taxonomy")


@validate_response("/llms.txt", "GET")
@optional_auth
def handle_llms_txt_request(
    headers: Dict[str, str], query_params: Dict[str, str], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle /llms.txt endpoint - LLM discovery
    """
    try:
        llms_content = LlmsTxtService().get_public_manifest_text()

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "text/markdown; charset=utf-8",
                "Cache-Control": "public, max-age=60",
                # TM-02: public, uncredentialed discovery endpoint — explicit opt-in,
                # still config-driven (never a bare "*").
                **cors_headers(event, public=True),
            },
            "body": llms_content,
        }

    except Exception as e:
        logger.error(f"Error in handle_llms_txt_request: {str(e)}")
        return create_error_response(500, "Failed to generate LLMs.txt")


@validate_response("/query", "POST")
@require_partner_or_cognito_auth
def handle_query_request(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle /query endpoint - Content query (hybrid authentication)
    Supports both standard format and CoMP v1.0 AISystem format
    """
    try:
        user = kwargs["authenticated_user"]
        auth_type = kwargs["auth_type"]

        # Detect if this is a CoMP v1.0 request
        if is_comp_request(request_data):
            logger.info("=== CoMP v1.0 REQUEST DETECTED ===")

            api_base_url = _api_base_url(event)

            # Handle CoMP request
            try:
                comp_response, error_msg = handle_comp_query_request(
                    request_data,
                    user,
                    auth_type,
                    search_function=search_service.execute_discovery,
                    api_base_url=api_base_url,
                )
            except SearchUnavailableError as exc:
                return _search_unavailable_response("/query", exc)

            if error_msg:
                return create_error_response(400, error_msg)

            return create_success_response(comp_response)

        # Standard (non-CoMP) request handling below
        logger.info("=== STANDARD REQUEST ===")

        for retired, canonical in (("content_types", "content_type"), ("limit", "max_results")):
            if retired in request_data:
                return create_error_response(400, f"{retired} is not supported; use {canonical}")

        # Validate required fields
        query_value = request_data.get("query")
        if not isinstance(query_value, str) or not (query := query_value.strip()):
            return create_error_response(400, "Query parameter is required")

        # Extract optional parameters
        content_types = _string_list(request_data.get("content_type", []))
        iab_categories = _string_list(request_data.get("iab_categories", []))
        max_results = request_data.get("max_results", 10)
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 50:
            return create_error_response(400, "max_results must be an integer from 1 through 50")

        similarity_threshold = None
        if "similarity_threshold" in request_data:
            try:
                similarity_threshold = validate_similarity_threshold(request_data["similarity_threshold"], query)
            except ValueError as exc:
                return create_error_response(400, str(exc))

        started_at = time.monotonic()
        try:
            results = search_content(query, content_types, iab_categories, max_results, similarity_threshold)
        except SearchUnavailableError as exc:
            return _search_unavailable_response("/query", exc)
        processing_time_ms = int((time.monotonic() - started_at) * 1000)

        # Generate unique query ID
        query_id = f"q_{uuid.uuid4().hex[:8]}"

        # Calculate billing (simplified)
        total_cost = len(results) * 0.05  # $0.05 per result

        response = {
            "status": "success",
            "query_id": query_id,
            "results": results,
            "billing": {
                "total_cost": total_cost,
                "currency": "USD",
                "breakdown": [
                    {
                        "content_type": "article",
                        "count": len([r for r in results if r.get("content_type") == "article"]),
                        "unit_cost": 0.05,
                        "total_cost": len([r for r in results if r.get("content_type") == "article"]) * 0.05,
                    },
                    {
                        "content_type": "report",
                        "count": len([r for r in results if r.get("content_type") == "report"]),
                        "unit_cost": 0.25,
                        "total_cost": len([r for r in results if r.get("content_type") == "report"]) * 0.25,
                    },
                ],
            },
            "metadata": {
                "processing_time_ms": processing_time_ms,
                "total_results_available": len(results),
                "query_timestamp": datetime.now(timezone.utc).isoformat(),
                "auth_type": auth_type,
            },
        }

        # Enhanced analytics collection with hybrid authentication differentiation
        analytics_collector = get_analytics_collector()

        # Determine access type for billing vs testing differentiation
        if auth_type == "partner_key":
            access_type = "partner_billing"
            user_id = user.get("partner_id", "unknown_partner")
            logger.info(f"Partner query (billing): {user_id} - {query}")

            # Record partner access for billing purposes
            analytics_collector.record_search_query(
                query=query,
                results_count=len(results),
                requester_info={
                    "user_id": user_id,
                    "user_type": "partner",
                    "access_type": "billing",
                    "auth_method": "partner_key",
                    "partner_name": user.get("name", "Unknown Partner"),
                    "subscription_tier": user.get("subscription", "basic"),
                    "api_key_id": user.get("api_key_id"),
                    "operation_id": user.get("operation_id"),
                    "endpoint": "/query",
                },
            )

        elif auth_type in ["cognito", "jwt"]:
            access_type = "ui_testing"
            user_id = user.get("user_id", user.get("email", "unknown_ui_user"))
            logger.info(f"UI test query (internal): {user_id} - {query}")

            # Record UI access for testing/internal usage tracking
            analytics_collector.record_search_query(
                query=query,
                results_count=len(results),
                requester_info={
                    "user_id": user_id,
                    "user_type": "ui_user",
                    "access_type": "testing",
                    "auth_method": auth_type,
                    "publisher_id": user.get("publisher_id", "default_publisher"),
                    "email": user.get("email", "unknown@example.com"),
                    "endpoint": "/query",
                },
            )

        # Also record API access for general metrics
        analytics_collector.record_api_access(
            endpoint="/query",
            user_id=user_id,
            metadata={
                "auth_type": auth_type,
                "access_type": access_type,
                "query_length": len(query),
                "results_count": len(results),
                "total_cost": total_cost,
                "processing_time_ms": processing_time_ms,
            },
        )

        return create_success_response(response)

    except Exception as e:
        logger.error("Discovery failed endpoint=/query failure_class=%s", type(e).__name__)
        return create_error_response(500, "Failed to process query")


def _search_unavailable_response(endpoint: str, error: SearchUnavailableError) -> Dict[str, Any]:
    logger.warning(
        "Discovery unavailable endpoint=%s failure_class=%s",
        endpoint,
        type(error.__cause__ or error).__name__,
    )
    return create_error_response(
        503,
        "Content discovery is temporarily unavailable",
        error_code="search_unavailable",
        additional_headers={"Retry-After": "3"},
    )


def _api_base_url(event: Dict[str, Any]) -> str:
    """Return the externally routable API Gateway base URL for CoMP Packages."""
    request_context = event.get("requestContext") or {}
    headers = event.get("headers") or {}
    host = headers.get("Host") or headers.get("host") or request_context.get("domainName") or "api.publisher.com"
    stage = request_context.get("stage")
    suffix = f"/{stage}" if stage and stage != "$default" else ""
    return f"https://{host}{suffix}"


def search_content(
    query: str,
    content_types: List[str],
    iab_categories: List[str],
    max_results: int,
    similarity_threshold: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Compatibility adapter for standard `/query`, backed only by OpenSearch."""
    return search_service.execute_discovery(
        DiscoveryRequest(
            query_text=query,
            content_types=content_types,
            iab_category_ids=iab_categories,
            limit=max_results,
            similarity_threshold=similarity_threshold,
            include_inline_content=True,
        )
    )


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
