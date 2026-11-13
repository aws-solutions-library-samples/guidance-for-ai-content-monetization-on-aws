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

# OpenSearch hybrid and filter-only discovery live in the shared service layer.
# The single usage-event emit() choke point (GitLab #3 task 9.2, Requirement 8.1). The CoMP
# branch, the standard discovery path, and the partner self-report at /log all route through
# this and nothing else — one event source, not several. SOURCE_OBSERVED,
# SOURCE_SELF_REPORTED and DEFAULT_CURRENCY are imported rather than spelled as literals so the
# source-marker vocabulary and the default currency have exactly one owner. No UsageEvent is
# constructed here and no telemetry sink is imported.
# URL-addressable content retrieval (#14 REQ-1.2): resolves a document by the canonical URL
# an agent cites, via the url_hash-index GSI.
from services.content_service import handle_content_read
from services.llms_txt_service import LlmsTxtService
from services.read_references import read_context, read_reference
from services.search_service import (
    DiscoveryRequest,
    SearchService,
    SearchUnavailableError,
    validate_similarity_threshold,
)
from services.usage_emitter import (
    DEFAULT_CURRENCY,
    SOURCE_OBSERVED,
    SOURCE_SELF_REPORTED,
    emit_usage_event,
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
        elif path == "/content" and http_method == "GET":
            return handle_content_request(headers, query_params, event)
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


# NOTE on the SEARCH markers below: main consolidated the separate search endpoint into
# `/query`, so there is one discovery surface rather than two. The `comp_search` / `search`
# markers are KEPT rather than deleted for two reasons: rows already written under those labels
# must stay interpretable (the raw store is immutable, so re-labelling history is not an option),
# and the vocabulary is what a future re-split would need. Nothing emits them today — the
# consolidated surface stamps `comp_query` / `query`.
# ── CoMP billable signal (GitLab #3, task 10.2 — Requirements 9.1, 9.3, 9.4, 9.5) ──
#
# The bug this fixes: the CoMP branch of `/query` built the Package and returned it immediately,
# *before* the `analytics_collector` calls the standard path makes, so a licensed CoMP access
# produced no billable-unit signal at all — the exact metering primitive Part B calls for.
#
# `content_type` names *what kind of access* this was (matching `url_retrieval` on the content path),
# which is what keeps metered surfaces distinguishable in the raw event store. main
# consolidated discovery onto a single endpoint, so a CoMP discovery access stamps
# `comp_query`; the `comp_search` marker is retained only so rows written before the
# consolidation stay interpretable (the raw store is immutable — history cannot be
# re-labelled).
USAGE_CONTENT_TYPE_COMP_QUERY = "comp_query"
USAGE_CONTENT_TYPE_COMP_SEARCH = "comp_search"

# ── Standard (non-CoMP) billable signal (GitLab #3, task 10.3 — Requirements 9.2, 8.1, 8.8) ──
#
# The standard (non-CoMP) discovery path routes its billable signal through the SAME choke
# point the CoMP branch above uses, so there is exactly one billable-event source
# (Requirement 8.1). The
# existing `analytics_collector.record_search_query()` / `record_api_access()` calls below stay
# exactly as they were — they are *operational* metrics, written to `cm-analytics`, and are NOT a
# billable counter. Requirement 8.8 forbids a second one, so nothing here removes, replaces, or
# duplicates them.
#
# As with the CoMP marker, `content_type` names *what kind of access* this was, which keeps the
# metered surfaces distinguishable in the raw event store: `url_retrieval`, `comp_query` and
# `query` are what the consolidated surfaces emit today; `comp_search` and `search` are
# retained for pre-consolidation history only.
USAGE_CONTENT_TYPE_STANDARD_QUERY = "query"
USAGE_CONTENT_TYPE_STANDARD_SEARCH = "search"

#: Retained marker for pre-consolidation history.
#:
#: The billable unit on the discovery surface is the **one result-set access**, not each
#: result (see ``_emit_standard_usage_event``). The consolidated ``/query`` mints a
#: per-access identifier for exactly that set — ``query_id`` (``q_<hex>``) — and returns it
#: to the caller, so the id in the event and the id in the caller's hand are the same
#: string. Nothing emits under this marker today.
USAGE_CONTENT_ID_SEARCH_ACCESS = "search_result_set"

#: The `_meta` flag `build_comp_package_response()` sets — and *only* it sets — on a Package built
#: after `validate_comp_license()` returned a license document. This is the structural discriminator
#: between a real Package and the `PENDING_REVIEW` Package (Requirement 9.4); see
#: `_comp_access_is_billable`.
COMP_META_LICENSE_VALIDATED = "license_validated"


def _comp_access_is_billable(comp_response: Optional[Dict[str, Any]]) -> bool:
    """True only for a CoMP response that represents a **licensed** content access.

    `handle_comp_query_request` returns ``(response, None)`` — i.e. *success* shaped — for two very
    different outcomes, so "no ``error_msg``" is NOT sufficient to bill (Requirement 9.4):

    1. a real Package, built after ``validate_comp_license()`` returned a license document; and
    2. the ``PENDING_REVIEW`` Package, which carries ``retrieval.auth = 0``, grants no content
       access, and exists only to point the caller at ``licenseurl`` to negotiate terms.

    Billing (2) would charge for being told to go get a license. The two are told apart by
    ``_meta.license_validated``, which ``build_comp_package_response()`` sets to ``True`` and the
    hand-built ``PENDING_REVIEW`` literal does not set at all.

    Deliberately a **positive** gate on the service's own report that it validated a licence, not a
    negative check for ``PENDING_REVIEW``:

    * it reads the *response* rather than re-deriving the service's branch condition from
      ``request_data``, so the handler cannot drift out of agreement with the service about which
      branch ran;
    * it fails closed. Any future non-validated success branch added to the service emits nothing
      until it explicitly declares a validated licence, rather than silently becoming billable.
      Matching on ``lid == "PENDING_REVIEW"`` would fail *open* that way.
    """
    if not isinstance(comp_response, dict):
        return False
    meta = comp_response.get("_meta")
    if not isinstance(meta, dict):
        return False
    return meta.get(COMP_META_LICENSE_VALIDATED) is True


def _comp_declared_function_code(request_data: Dict[str, Any]) -> Optional[int]:
    """The single CoMP function code to stamp as ``declared_use`` (Requirement 9.5).

    ``aisysuse.function`` is a **list** of codes while ``UsageEvent.declared_use`` is one optional
    int, and Requirement 9.1 fixes the event count at exactly one, so a request declaring several
    codes must still resolve to one value. This returns the **lowest** declared code.

    Why the lowest rather than the first: a request declaring ``[3, 2]`` and one declaring ``[2, 3]``
    are the same declaration, so the stamped value must not depend on list order. Among the canonical
    codes ``0=all`` and ``1=ai-all`` are the umbrella codes, so when an umbrella is declared
    alongside a specific use the lowest code records the widest right the caller claimed. Between
    sibling codes (``2``–``5``) there is no breadth ordering and the minimum is an arbitrary — but
    deterministic — tiebreak; the complete declared list stays visible in the CoMP request log line
    the service writes, so nothing is lost by the collapse.

    Total by construction: it never raises, so computing a telemetry argument can never turn a served
    CoMP response into a 500. Non-integer entries are dropped (``bool`` included — ``True`` is not
    function code 1, which is also how the emitter's own normalization reads it), and an absent,
    empty, or wholly unusable ``function`` yields ``None``: nothing was declared that can be recorded,
    and ``None`` records no false claim.
    """
    aisysuse = (request_data.get("aisystem") or {}).get("aisysuse") or {}
    functions = aisysuse.get("function")
    if not isinstance(functions, (list, tuple)):
        return None
    codes = [f for f in functions if isinstance(f, int) and not isinstance(f, bool)]
    return min(codes) if codes else None


def _emit_comp_usage_event(
    comp_response: Optional[Dict[str, Any]],
    request_data: Dict[str, Any],
    user: Dict[str, Any],
    event: Dict[str, Any],
    content_type: str,
) -> None:
    """Record the one billable usage event for a successful CoMP access (Requirement 9.1).

    Called only after the ``error_msg`` check, so a failed license validation — which returns
    ``(None, error_msg)`` and becomes a 400 — never reaches here (Requirement 9.3). That exemption is
    a **control-flow** guarantee at the call site, matching how the ``304`` exemption is enforced on
    the content path; it is not a condition inside the emitter, so it cannot be defeated by a later
    edit to ``services/usage_emitter.py``.

    **One event per CoMP access, not one per asset (Requirement 9.1).** A Package can advertise many
    text assets, but the billable unit is the Package: that is what the licence authorized and what
    was delivered. ``content_id`` is therefore the Package id. The assets are *metadata* — a text
    asset carries no ``content_id`` at all (see ``build_comp_text_asset``) — and the bytes are fetched
    later through the Package's ``retrieval.endpoint``, where each retrieval is metered at its own
    call site. Emitting per asset here would contradict "exactly one usage event" and would
    double-bill the same content once the agent dereferences it.

    **The validated ``license_id``.** The handler receives only ``(comp_response, error_msg)``, so the
    declared ``aisysuse.lid`` is read back from ``request_data`` — but only under the
    ``_comp_access_is_billable`` gate, which is what makes the declared value *the validated value*.
    ``validate_comp_license(license_id, functions)`` fetches the licence document by exactly that id
    and reports success only if that document exists, is not deleted, and permits every requested
    function; ``_meta.license_validated is True`` is set only on that path. Under the gate the two are
    the same string by construction, so nothing is gained by widening the service's return shape —
    and widening it would mean changing working, characterization-pinned code (and every caller,
    including ``handle_comp_search_request``) for a value already in hand. Reading the id off the
    already-parsed request also adds no request-time join back to ``cm-licenses``, which the
    read-path denormalization rule forbids.
    """
    if not _comp_access_is_billable(comp_response):
        # Failed validation (handled above) or the PENDING_REVIEW Package: no content was licensed,
        # so there is no billable unit (Requirements 9.3, 9.4).
        return

    package = comp_response.get("package") or {}
    aisysuse = (request_data.get("aisystem") or {}).get("aisysuse") or {}

    emit_usage_event(
        # A Cognito/JWT caller is the publisher testing its own surface and carries no partner_id;
        # the choke point's documented rule (no partner_id -> structured log, no event) is what
        # decides that, rather than a second billability branch here duplicating the judgement.
        partner_id=user.get("partner_id", "") if user else "",
        # The Package IS the billable unit — see the docstring above.
        content_id=package.get("id", ""),
        # ── denormalized at emit (Req 8.2) — values-at-time-of-access ──
        content_type=content_type,
        # The validated licence id; same string as the declared `lid` under the gate above.
        license_id=aisysuse.get("lid", ""),
        # Pricing is not resolved on this path: a CoMP Package is delivered under an already-agreed
        # licence (Path B), so the per-access charge is genuinely 0.0 today. No price is invented and
        # no pricing lookup is added here. Path C's x402 per-access charge (#25/#7) is what will
        # populate this, at the call site that knows the charge.
        price_charged=0.0,
        currency=DEFAULT_CURRENCY,
        declared_use=_comp_declared_function_code(request_data),
        request_id=(event.get("requestContext") or {}).get("requestId"),
        source=SOURCE_OBSERVED,  # server-observed, never self-reported
    )


def _emit_standard_usage_event(
    content_id: str,
    user: Dict[str, Any],
    event: Dict[str, Any],
    content_type: str,
) -> None:
    """Record the one billable usage event for a standard (non-CoMP) access (Requirement 9.2).

    The standard path's billable signal goes through the **same** choke point the CoMP branches use,
    which is the whole point of Requirement 9.2: one billable-event source, not two (Requirement
    8.1). The ``analytics_collector.record_search_query()`` / ``record_api_access()`` calls at the
    call sites are untouched **operational** metrics; Requirement 8.8 forbids a second billable
    counter, so this adds a route to the existing one rather than a parallel signal, and nothing is
    removed or duplicated.

    **Call it last, from the bottom of the standard path.** Every non-billable outcome on these two
    surfaces — the missing-``query`` 400, a search failure, any exception that becomes the handler's
    500 — exits *above* this call, so "an unsuccessful standard request emits nothing" is a
    **control-flow** guarantee at the call site, exactly as the ``304`` exemption is on the content
    path and the ``error_msg`` exemption is on the CoMP path. It is deliberately **not** a condition
    inside ``services/usage_emitter.py``, so it cannot be defeated by a later edit there; moving this
    call above those returns makes the pinning tests fail, which is the tripwire they exist to be.

    **One event per access, not one per result (Requirement 8.1's "exactly one" shape).** A standard
    response can carry up to 50 results, but what was served — and what the operational
    ``record_search_query`` counts once — is one result-set access. Emitting per result would both
    contradict that and double-bill the same content once the agent dereferences a result's ``url``
    through ``/content``, where each retrieval is metered at its own call site.

    **No ``license_id`` and ``price_charged=0.0``, both honest.** A standard request presents a
    partner key and declares no licence: there is no ``aisysuse.lid`` to stamp, and the per-result
    ``billing.total_cost`` in the ``/query`` response body is a *quoted* figure computed from a
    hard-coded rate, not a charge taken. Stamping either would require a read — ``cm-licenses`` for
    the licence, ``cm-pricing-rules`` for the price — which the read-path denormalization rule
    forbids on a hot path, and would record a value the access did not actually carry. ``0.0`` is the
    honest Path B figure (paid out-of-band); Path C's x402 per-access charge (#25/#7) is what will
    populate ``price_charged``, from the call site that knows the charge.

    ``declared_use`` is ``None``: no CoMP function code was declared, so no claim is recorded.
    """
    emit_usage_event(
        # A Cognito/JWT caller is the publisher testing its own surface and carries no partner_id;
        # the choke point's documented rule (no partner_id -> structured log, no event) is what
        # decides that, rather than a second billability branch here duplicating the judgement.
        partner_id=user.get("partner_id", "") if user else "",
        # The result-set access IS the billable unit — see the docstring above.
        content_id=content_id,
        # ── denormalized at emit (Req 8.2) — values-at-time-of-access ──
        content_type=content_type,
        license_id="",  # no licence was presented on this surface — see the docstring
        price_charged=0.0,  # Path B: paid out-of-band, genuinely 0.0 today
        currency=DEFAULT_CURRENCY,
        declared_use=None,  # not a CoMP request; no function code was declared
        request_id=(event.get("requestContext") or {}).get("requestId"),
        source=SOURCE_OBSERVED,  # server-observed, never self-reported
    )



@require_partner_auth
def handle_content_request(
    headers: Dict[str, str], query_params: Dict[str, str], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle ``GET /content?url=`` and ``GET /content?content_id=`` — single-document reads.

    The URL form is URL-addressable retrieval (#14 REQ-1.2); the ``content_id`` form is the read
    reference for discovery results without a canonical URL. Partner-authenticated.
    ``If-None-Match`` is lifted off the request headers and handed to the service layer as a query
    parameter, so the conditional-request decision (304 vs 200) and its billing exemption live in
    one place with the retrieval itself.
    """
    try:
        user = kwargs.get("authenticated_user", {})

        if_none_match = headers.get("If-None-Match") or headers.get("if-none-match")
        if if_none_match:
            query_params_with_etag = dict(query_params)
            query_params_with_etag["if_none_match"] = if_none_match
            return handle_content_read(user, query_params_with_etag)

        return handle_content_read(user, query_params)

    except Exception as e:
        logger.error(f"Error in handle_content_request: {str(e)}")
        return create_error_response(500, "Failed to retrieve content")


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
        # Where the caller reads what this query returns: the plane that admitted it, at a trusted
        # origin (never the viewer's Host header). Fails closed before any discovery work.
        read_ctx = read_context(event, auth_type)

        # Detect if this is a CoMP v1.0 request
        if is_comp_request(request_data):
            logger.info("=== CoMP v1.0 REQUEST DETECTED ===")

            api_base_url = read_ctx.base_url

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
                # Denied or malformed CoMP request -> 400, ABOVE the emit (Requirement 9.3).
                # The exemption is this early return, not a condition inside the emitter, so it
                # cannot be defeated by a later edit to services/usage_emitter.py.
                return create_error_response(400, error_msg)

            _emit_comp_usage_event(
                comp_response, request_data, user, event, USAGE_CONTENT_TYPE_COMP_QUERY
            )

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
        # Every result is readable with the credential that admitted this query; inline content,
        # when present, is additional data and never replaces the reference.
        for result in results:
            result["url"] = read_reference(read_ctx, result.get("content_id") or "", result.get("canonical_url"))

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

        # The standard path's billable signal, through the SAME choke point the CoMP branch
        # above uses (GitLab #3 task 10.3, Requirements 9.2 / 8.1). The two
        # analytics_collector calls above are untouched OPERATIONAL metrics — this is NOT a
        # second billable counter, which Requirement 8.8 forbids.
        #
        # Deliberately the LAST statement before the response: the missing-`query` 400, the
        # max_results 400, the SearchUnavailableError exit and every exception path sit above
        # it, so "an unsuccessful /query emits nothing" is a control-flow guarantee here rather
        # than a condition inside services/usage_emitter.py. Do not move this call above the
        # `query` validation. `response` is already assembled, so no emit work sits between the
        # data read and the response; _emit_standard_usage_event never raises and adds no read.
        _emit_standard_usage_event(query_id, user, event, USAGE_CONTENT_TYPE_STANDARD_QUERY)

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
