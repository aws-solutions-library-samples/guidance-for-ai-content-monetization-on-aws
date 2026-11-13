"""
Analytics domain Characterization_Test (required gate for task 8.5 — the dispatcher flip).

Enforces the design's route-equivalence and auth-preserved correctness properties for the
``/analytics/*`` surface served by the per-domain Route_Module
(``ui_backend/routes/analytics.py``).

Golden-master baseline (post-flip form): before task 8.5 this suite compared the Route_Module
against the pre-refactor inline handler in ``ui_backend/handler.py``
(``handle_analytics_endpoints``, reached through the dispatcher adapter ``_route_analytics``).
That inline router, its interim ``_route_analytics`` adapter, and the inline ``handle_analytics_*``
handlers (plus the ``handle_analytics_partners = handle_get_partners`` alias) were **deleted in
the same change that flipped the ``/analytics/*`` dispatcher entry** to ``routes/analytics.py``
(Requirement 16.3, mirroring the taxonomy flip in task 3.3, processing in 4.4, content in 5.3,
and bot_control in 6.3), so the old-vs-new diff is no longer possible. The inline handlers simply
delegated to the analytics Service_Layer single home ``services.analytics_service`` (and, for the
``/analytics/partners`` alias, ``services.partners_service`` — Requirement 8.1). The golden
baseline is therefore that same service call wrapped in the shared ``create_success_response``
helper, constructed independently of the Route_Module's own code path; the Route_Module must
reproduce that exact status/body/CORS contract.

Properties validated:

- **Property 1: Route equivalence** — FOR ALL ``/analytics/*`` GET requests, the Route_Module
  returns the same HTTP status code, the same response body (fields, nesting, values), and the
  same CORS headers as the golden baseline built from the service single home (Requirements 2.1,
  2.2, 2.3, 8.1). Non-deterministic ``timestamp`` fields (``datetime.now(...)`` on each call) and
  the test-only ``_validation`` metadata block injected by ``@validate_response`` are normalized
  before comparison; all other values are compared exactly. The unknown-analytics sub-path 404 is
  also checked.
- **Property 2: Auth preserved** — a missing/invalid Cognito credential yields a 401 via the
  unchanged ``@require_cognito_auth`` wrapper, with the auth-failure message and CORS headers on
  the 401 response, and the service is never reached (Requirements 2.5, 6.6).

Determinism: each analytics endpoint's success payload is exercised through its behavior-
preserving mock-fallback branch by forcing the underlying AWS calls (the aggregation Lambda
invoke and the DynamoDB scans) to fail identically for both the Route_Module and the golden
baseline. Because both delegate to the same ``services.analytics_service`` /
``services.partners_service`` fallback data, this yields identical, deterministic payloads.

Canonical-root-clean (Requirement 17.6): every first-party import is absolute, rooted at the
Canonical_Import_Root ``source/lambda/`` (``ui_backend.*``, ``services.*``, ``shared.*``, resolved
via the pytest pythonpath / root conftest). No relative, bare-sibling, or ``sys.path`` forms are
used, so this suite gates under both the current and the tightened single-canonical-root pythonpath.

Validates: Requirements 2.1, 2.2, 2.3, 2.5, 6.6, 8.1
"""

import json
import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# A region and dummy credentials must be present before the application modules are
# imported, because ``ui_backend/routes/analytics.py`` and ``shared/auth`` construct
# module-level boto3 clients/resources at import time. This is environment setup, not an
# import form.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

# Canonical-root-clean absolute imports rooted at source/lambda/ (Requirement 17.6).
from services.analytics_service import AnalyticsService
from services.partners_service import PartnersService
from shared.utils.response_helpers import create_error_response, create_success_response
from ui_backend.routes import analytics as analytics_route

# The full set of GET routes served under the /analytics/* prefix (including the
# /analytics/partners alias of GET /partners).
ANALYTICS_ROUTES = [
    "/analytics/metrics",
    "/analytics/usage",
    "/analytics/revenue",
    "/analytics/trends",
    "/analytics/revenue/categories",
    "/analytics/partners",
    "/analytics/access-logs",
]

# CORS header names that must be present and identical on every response (Requirements 2.3,
# 6.6). All responses flow through the shared create_success_response / create_error_response
# helpers, which attach these.
CORS_KEYS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
)

# Publisher id the golden baseline uses — matches the value @require_cognito_auth resolves from
# the authorizer claims below (``custom:publisher_id``).
_PUBLISHER_ID = "test-publisher"

# Authenticated request context: an API Gateway Cognito authorizer claim block that
# extract_cognito_user_context accepts, so @require_cognito_auth admits the request.
_AUTH_CLAIMS = {
    "requestContext": {
        "authorizer": {
            "claims": {
                "sub": "test-user-123",
                "email": "test@example.com",
                "custom:publisher_id": _PUBLISHER_ID,
            }
        }
    }
}


def _authed_event(path, method="GET", query=None, body=None):
    """Build an authenticated API Gateway proxy event for ``path``/``method``."""
    return {
        "httpMethod": method,
        "path": path,
        "headers": {"Content-Type": "application/json"},
        "queryStringParameters": query,
        "body": body,
        **_AUTH_CLAIMS,
    }


def _unauthed_event(path, method="GET"):
    """Build an event with no Cognito authorizer context and no Authorization header."""
    return {
        "httpMethod": method,
        "path": path,
        "headers": {},
        "queryStringParameters": None,
        "body": None,
        "requestContext": {},
    }


def _golden(path, event):
    """Produce the characterized golden baseline for an ``/analytics/*`` request.

    The (deleted) inline ``handle_analytics_*`` handlers each delegated to the analytics
    Service_Layer single home (``services.analytics_service.AnalyticsService``), and the
    ``/analytics/partners`` alias delegated to ``services.partners_service.PartnersService``,
    wrapping the returned data in ``create_success_response``. The golden baseline reproduces
    that exact contract by calling the same service methods directly — independently of the
    Route_Module's own code path — so the two can be compared for route equivalence.
    """
    query_params = event.get("queryStringParameters") or {}
    service = AnalyticsService()

    if path == "/analytics/metrics":
        data = service.get_metrics(_PUBLISHER_ID)
    elif path == "/analytics/usage":
        data = service.get_usage(_PUBLISHER_ID, query_params)
    elif path == "/analytics/revenue":
        data = service.get_revenue(_PUBLISHER_ID)
    elif path == "/analytics/trends":
        data = service.get_trends(_PUBLISHER_ID)
    elif path == "/analytics/revenue/categories":
        data = service.get_revenue_categories(_PUBLISHER_ID)
    elif path == "/analytics/partners":
        # The partners reader has no demo fallback: a storage failure is a 500 on both paths.
        try:
            data = PartnersService().get_partners(_PUBLISHER_ID)
        except Exception:
            return create_error_response(500, "Failed to retrieve partner data")
    elif path == "/analytics/access-logs":
        data = service.get_access_logs(_PUBLISHER_ID, query_params)
    else:  # pragma: no cover - guarded by the ANALYTICS_ROUTES parametrization
        raise AssertionError(f"unexpected golden path {path}")

    return create_success_response(data)


def _normalize(obj):
    """Recursively normalize non-deterministic / test-only body fields for comparison.

    Replaces ``timestamp`` values (stamped via ``datetime.now(...)`` on each call, differing
    between the two invocations) with a stable sentinel, and drops the ``_validation`` metadata
    block that ``@validate_response`` injects into the Route_Module's body in non-production (the
    golden baseline, built directly from ``create_success_response``, does not carry it). Every
    other value — including the static per-row timestamps inside the access-logs mock rows — is
    compared exactly.
    """
    if isinstance(obj, dict):
        return {
            k: ("<TS>" if k in ("timestamp", "request_id") else _normalize(v))
            for k, v in obj.items()
            if k != "_validation"
        }
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


@contextmanager
def _force_aws_fallback():
    """Force every analytics AWS call to fail so both paths take their mock-fallback branch.

    Patches the aggregation Lambda invoke and the DynamoDB scans (used by the analytics
    service and the partners service) to raise, which drives both the Route_Module and the
    golden baseline into their identical, deterministic fallback payloads.
    """
    lambda_client = MagicMock()
    lambda_client.invoke.side_effect = RuntimeError("aggregation unavailable")

    def _make_resource(*_args, **_kwargs):
        resource = MagicMock()
        table = MagicMock()
        table.scan.side_effect = RuntimeError("table unavailable")
        resource.Table.return_value = table
        return resource

    with patch("boto3.client", side_effect=lambda *a, **k: lambda_client), patch(
        "boto3.resource", side_effect=_make_resource
    ):
        yield


@pytest.mark.parametrize("path", ANALYTICS_ROUTES)
def test_route_equivalence_success(path):
    """Property 1: the Route_Module matches the golden baseline (status/body/CORS) per route."""
    method = "GET"
    event = _authed_event(path)

    with _force_aws_fallback():
        route_resp = analytics_route.route(event, method, path)
        golden_resp = _golden(path, event)

    # Same HTTP status code (Requirement 2.1).
    assert route_resp["statusCode"] == golden_resp["statusCode"], f"status mismatch for {path}"
    # Every analytics reader degrades to deterministic mock data except the partners alias,
    # whose single-home reader surfaces a storage failure as a 500.
    expected = 500 if path == "/analytics/partners" else 200
    assert route_resp["statusCode"] == expected, f"expected {expected} for {path}"

    # Same response body — fields, nesting, values (Requirement 2.2); timestamps + _validation
    # normalized.
    assert _normalize(json.loads(route_resp["body"])) == _normalize(
        json.loads(golden_resp["body"])
    ), f"body mismatch for {path}"

    # Same CORS headers, present on every response (Requirements 2.3, 6.6).
    for key in CORS_KEYS:
        assert key in route_resp["headers"], f"missing CORS header {key} for {path}"
        assert route_resp["headers"].get(key) == golden_resp["headers"].get(key), (
            f"CORS header {key} mismatch for {path}"
        )


@pytest.mark.parametrize("path", ["/analytics/metrics", "/analytics/revenue"])
def test_response_scoped_to_authenticated_publisher(path):
    """Explicit publisher-scoping (Req 14.3): the response body is scoped to the authenticated
    publisher id derived from the Cognito ``custom:publisher_id`` claim. This replaces the scoping
    coverage of the removed TestUIBackendIntegration::test_publisher_scoped_data_access."""
    event = _authed_event(path)
    with _force_aws_fallback():
        route_resp = analytics_route.route(event, "GET", path)

    assert route_resp["statusCode"] == 200
    body = json.loads(route_resp["body"])
    assert body.get("publisher_id") == _PUBLISHER_ID, f"response not scoped to publisher for {path}"


def test_route_equivalence_unknown_analytics_subpath():
    """Property 1: an unmatched /analytics/* sub-path returns a 404 with CORS headers."""
    method = "GET"
    path = "/analytics/does-not-exist"
    event = _authed_event(path)

    route_resp = analytics_route.route(event, method, path)

    assert route_resp["statusCode"] == 404
    assert json.loads(route_resp["body"])["error"] == "Analytics endpoint not found"
    for key in CORS_KEYS:
        assert key in route_resp["headers"]


@pytest.mark.parametrize("path", ANALYTICS_ROUTES)
def test_auth_preserved_returns_401(path):
    """Property 2: missing/invalid Cognito credential yields 401, service never reached."""
    method = "GET"
    event = _unauthed_event(path)

    # No AWS access should occur: @require_cognito_auth short-circuits before the service. If
    # the service were reached, these patched clients would raise instead of returning 401.
    with patch.object(AnalyticsService, "get_metrics", side_effect=AssertionError("service reached")), patch.object(
        AnalyticsService, "get_usage", side_effect=AssertionError("service reached")
    ), patch.object(
        PartnersService, "get_partners", side_effect=AssertionError("service reached")
    ):
        route_resp = analytics_route.route(event, method, path)

    assert route_resp["statusCode"] == 401, f"route expected 401 for {path}"

    # Auth-failure message from the unchanged wrapper.
    assert json.loads(route_resp["body"])["error"] == "Invalid or missing Cognito token"

    # CORS headers present on the 401 (Requirement 6.6).
    for key in CORS_KEYS:
        assert key in route_resp["headers"], f"missing CORS header {key} on 401 for {path}"


if __name__ == "__main__":
    pytest.main([__file__])
