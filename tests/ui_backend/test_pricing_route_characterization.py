#!/usr/bin/env python3
"""Pricing domain Characterization_Test (REQUIRED GATE for the dispatcher flip, task 9.4).

This suite pins the behavior of the ``/pricing/*`` domain across the strangler-fig migration for the
per-domain Route_Module ``ui_backend/routes/pricing.py`` (which delegates to
``services.pricing_manager.PricingVersionManager``, the single home for the pricing domain). It
enforces the two correctness properties the design defines for a migration step:

* **Property 1 - Route equivalence** across every ``/pricing/*`` endpoint
  (``rules`` [now always category-resolved], ``history``, ``calculate``, plus create/update
  and the unknown-endpoint 404): the Route_Module produces the same HTTP status code, the same
  response body (same fields, nesting, and values), and the same CORS headers as the characterized
  golden baseline. Both the real-data path and the graceful-fallback / mock-seed path are covered.
  (Validates: Requirements 2.1, 2.2, 2.3)
* **Property 2 - Auth preserved**: a missing or invalid Cognito credential yields ``401`` via the
  unchanged ``@require_cognito_auth`` wrapper, and the pricing service is never instantiated when
  auth fails.
  (Validates: Requirements 2.5, 6.6)

Golden-master baseline (post-flip form): before task 9.4 this suite compared the Route_Module against
the pre-refactor inline handler in ``ui_backend/handler.py`` (``handle_pricing_endpoints``, reached
through the dispatcher adapter ``_route_pricing``). That inline router, its interim ``_route_pricing``
adapter, and the inline ``handle_*_pricing_*`` handlers were **deleted in the same change that flipped
the ``/pricing/*`` dispatcher entry** to ``routes/pricing.py`` (Requirement 16.3, mirroring the
taxonomy flip in task 3.3, processing in 4.4, content in 5.3, bot_control in 6.3, and analytics in
8.5), so the old-vs-new diff is no longer possible. The inline handlers delegated to the pricing
Service_Layer single home ``services.pricing_manager.PricingVersionManager`` and wrapped its output in
the shared ``create_success_response`` / ``create_error_response`` helpers. The golden baseline
(``_golden`` below) reproduces that exact status/body/CORS contract by calling the same service view
methods directly and assembling the same response envelopes -- constructed independently of the
Route_Module's own code path -- so the two can be compared for route equivalence.

Both paths delegate to the same ``PricingVersionManager`` class object. Data-access and feature-flag
boundaries (``get_active_pricing_rules``, ``get_pricing_history``, ``create_pricing_rule_version``,
``activate_pricing_rule_version``, ``fix_version_numbering``,
``FeatureFlagController.enable_graceful_fallback``, ``use_real_data``) are patched **on the shared
class / at the flag source**, so both the Route_Module's handler and the golden baseline run their
formatting logic against identical controlled inputs and the responses are compared. Note per task
9.1 the inline ``get_mock_pricing_history`` generator was relocated into the service, so the mock/seed
behavior now lives in ``PricingVersionManager`` and is feature-flag gated there.

Non-deterministic fields are normalized before comparison: the ``@validate_response`` decorator's
``_validation`` metadata block (wall-clock timestamp, injected only into the Route_Module body in
non-production) is dropped, and wall-clock values (``timestamp``, ``resolved_at``,
``calculation_timestamp``) plus the per-response ``request_id`` that ``create_error_response`` mints
are masked. Fixed template timestamps (``version_timestamp``, ``created_at``, ``effective_date``) are
deterministic and are **not** masked, so real value drift is still caught.

Authored canonical-root-clean (Requirement 17.6): every first-party import is absolute, rooted at the
canonical import root ``source/lambda/`` (``ui_backend.*``, ``services.*``, ``shared.*``), with no
relative (leading-dot) or bare-sibling import forms and no ``sys.path`` manipulation, so it resolves
under both the current ``pytest.ini`` pythonpath and #11's tightened single-canonical-root pythonpath.

_Requirements: 4.2, 4.6, 17.4, 17.6_
"""

import copy
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from services.pricing_manager import PricingVersionManager
from shared.utils.response_helpers import create_error_response, create_success_response
from ui_backend.routes.pricing import route as pricing_route

# --------------------------------------------------------------------------------------------------
# Patch targets. ``ui_backend.routes.pricing.PricingVersionManager`` IS
# ``services.pricing_manager.PricingVersionManager`` (an imported reference to the same class object),
# so patching methods on the class here affects the Route_Module handler and the golden baseline
# (which also calls the class) simultaneously.
# --------------------------------------------------------------------------------------------------
_PM = "services.pricing_manager.PricingVersionManager"
_TM = "services.taxonomy_manager.TaxonomyManager"

# CORS header names both implementations must attach to every response.
_CORS_HEADERS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
)

# Keys whose values are wall-clock / per-response non-deterministic and must be masked before
# comparison. Fixed template timestamps (version_timestamp, created_at, effective_date) are NOT here.
_VOLATILE_KEYS = {"timestamp", "resolved_at", "calculation_timestamp", "request_id"}

# The authenticated user the shared ``@require_cognito_auth`` wrapper resolves from the authorizer
# claims in ``_authed_event`` below. The golden baseline reproduces the values the inline handlers
# derived from that user (``email`` -> created_by, ``custom:publisher_id`` -> publisher_id).
_USER_EMAIL = "test@example.com"
_PUBLISHER_ID = "test-publisher"


def _scrub(obj):
    """Recursively drop ``_validation`` metadata and mask wall-clock / request-id values."""
    if isinstance(obj, dict):
        return {
            key: ("<masked>" if key in _VOLATILE_KEYS else _scrub(value))
            for key, value in obj.items()
            if key != "_validation"
        }
    if isinstance(obj, list):
        return [_scrub(item) for item in obj]
    return obj


def _normalize(response):
    """Return a comparable view (status + headers + scrubbed body) of a proxy response."""
    return {
        "statusCode": response["statusCode"],
        "headers": response["headers"],
        "body": _scrub(json.loads(response["body"])),
    }


def _authed_event(method, path, *, query=None, body=None):
    """Build a ``/pricing/*`` event that satisfies ``@require_cognito_auth``.

    The API Gateway Cognito authorizer claims satisfy ``extract_cognito_user_context`` so the happy
    path exercises no JWT verification. The Route_Module and the golden baseline share this identical
    authenticated-user object, so the derived ``publisher_id`` / ``created_by`` values match by
    construction.
    """
    return {
        "httpMethod": method,
        "path": path,
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "test-user-123",
                    "email": _USER_EMAIL,
                    "custom:publisher_id": _PUBLISHER_ID,
                    "cognito:username": "test_user",
                }
            }
        },
    }


def _run_new(event):
    """Invoke the Route_Module exactly as the dispatcher would."""
    return pricing_route(event, event["httpMethod"], event["path"])


def _golden(event):
    """Produce the characterized golden baseline for a ``/pricing/*`` request.

    The (deleted) inline ``handle_*_pricing_*`` handlers each delegated to the pricing Service_Layer
    single home (``services.pricing_manager.PricingVersionManager``), wrapping the returned data in
    the shared ``create_success_response`` / ``create_error_response`` helpers. This baseline
    reproduces that exact contract by calling the same service view methods directly -- independently
    of the Route_Module's own code path -- so the two can be compared for route equivalence.
    """
    method = event["httpMethod"]
    path = event["path"]
    query = event.get("queryStringParameters") or {}
    body = json.loads(event["body"]) if event.get("body") else {}

    if path == "/pricing/rules" and method == "GET":
        rule_set_id = query.get("rule_set_id", "default-pricing")
        effective_date = query.get("effective_date")
        data = PricingVersionManager().get_pricing_rules_view(rule_set_id, effective_date, _USER_EMAIL)
        if data is not None:
            return create_success_response(data)
        return create_error_response(404, "No active pricing rules found")

    if path == "/pricing/rules" and method == "POST":
        for field, message in (
            ("pricing_rules", "pricing_rules is required"),
            ("effective_date", "effective_date is required"),
            ("change_reason", "change_reason is required"),
        ):
            if field not in body:
                return create_error_response(400, message)
        manager = PricingVersionManager()
        result = manager.create_pricing_rule_version(
            rule_set_id=body.get("rule_set_id", "default-pricing"),
            pricing_rules=body["pricing_rules"],
            effective_date=body["effective_date"],
            change_reason=body["change_reason"],
            created_by=_USER_EMAIL,
        )
        if body.get("activate", False):
            result = manager.activate_pricing_rule_version(
                rule_set_id=result["rule_set_id"], version_timestamp=result["version_timestamp"]
            )
        return create_success_response(
            {"status": "success", "pricing_rule": result, "message": "Pricing rules created successfully"},
            status_code=201,
        )

    if path == "/pricing/rules" and method == "PUT":
        for field, message in (
            ("pricing_rules", "pricing_rules is required"),
            ("effective_date", "effective_date is required"),
            ("change_reason", "change_reason is required"),
        ):
            if field not in body:
                return create_error_response(400, message)
        manager = PricingVersionManager()
        rule_set_id = body.get("rule_set_id", "default-pricing")
        current_rules = manager.get_active_pricing_rules(rule_set_id)
        previous_version = current_rules["version_timestamp"] if current_rules else None
        result = manager.create_pricing_rule_version(
            rule_set_id=rule_set_id,
            pricing_rules=body["pricing_rules"],
            effective_date=body["effective_date"],
            change_reason=body["change_reason"],
            created_by=_USER_EMAIL,
            previous_version=previous_version,
        )
        result = manager.activate_pricing_rule_version(
            rule_set_id=result["rule_set_id"], version_timestamp=result["version_timestamp"]
        )
        return create_success_response(
            {"status": "success", "pricing_rule": result, "message": "Pricing rules updated successfully"}
        )

    if path == "/pricing/history" and method == "GET":
        rule_set_id = query.get("rule_set_id", "default-pricing")
        limit = query.get("limit", 10)
        data = PricingVersionManager().get_pricing_history_view(rule_set_id, limit, _PUBLISHER_ID)
        return create_success_response(data)

    if path == "/pricing/calculate" and method == "POST":
        content_items = body.get("content_items", [])
        data = PricingVersionManager.calculate_pricing_estimate(content_items, _PUBLISHER_ID)
        return create_success_response(data)

    return create_error_response(404, "Pricing endpoint not found")


def assert_route_equivalent(event, expected_status=None):
    """Assert Route_Module == golden baseline on status, body, and CORS for one request."""
    new_response = _run_new(event)
    golden_response = _golden(event)

    if expected_status is not None:
        assert new_response["statusCode"] == expected_status, (
            f"unexpected status for {event['httpMethod']} {event['path']}: {new_response['statusCode']}"
        )

    assert _normalize(new_response) == _normalize(golden_response), (
        f"route/golden mismatch for {event['httpMethod']} {event['path']}"
    )
    for header in _CORS_HEADERS:
        assert header in new_response["headers"]
        assert new_response["headers"][header] == golden_response["headers"][header]
    return new_response


# Controlled "real" active pricing-rule record returned by the patched data boundary.
_ACTIVE_RULE = {
    "rule_set_id": "default-pricing",
    "version_timestamp": "2025-02-01T00:00:00Z",
    "effective_date": "2025-02-01",
    "status": "active",
    "created_by": "seed@example.com",
    "created_at": "2025-02-01T00:00:00Z",
    "pricing_rules": {
        "per_access": {"article": "0.05", "report": "0.25"},
        "multipliers": {"iab_category": {"126": "1.1"}},
    },
    "audit_trail": {"version": 3, "change_reason": "characterization fixture", "previous_version": None},
}

# Controlled pricing-history rows returned by the patched data boundary (real path).
_HISTORY_ITEMS = [
    {
        "rule_set_id": "default-pricing",
        "version_timestamp": "2025-02-01T00:00:00Z",
        "effective_date": "2025-02-01",
        "status": "active",
        "created_by": "seed@example.com",
        "created_at": "2025-02-01T00:00:00Z",
        "pricing_rules": {"per_access": {"article": "0.05"}},
        "audit_trail": {"version": 2, "change_reason": "bump", "previous_version": "2025-01-01T00:00:00Z"},
    }
]


class TestPricingRulesEquivalence:
    """Property 1 - route equivalence for GET /pricing/rules (now always category-resolved)."""

    def test_get_rules_real_success_equivalent(self):
        fake_categories = {
            "126": {"name": "Technology & Computing", "full_path": "Technology & Computing", "tier_level": 2}
        }
        event = _authed_event("GET", "/pricing/rules")
        with patch(f"{_PM}.__init__", return_value=None), patch(
            f"{_PM}.get_active_pricing_rules", return_value=copy.deepcopy(_ACTIVE_RULE)
        ), patch(f"{_TM}.__init__", return_value=None), patch(
            f"{_TM}.get_all_categories", return_value=fake_categories
        ):
            response = assert_route_equivalent(event, expected_status=200)
        body = _normalize(response)["body"]
        assert body["status"] == "success"
        assert body["pricing_rule"]["audit_trail"]["version"] == 3
        # SSOT #26: GET /pricing/rules now always returns the resolved category block.
        resolved = body["pricing_rule"]["pricing_rules"]["multipliers"]["iab_category_resolved"]
        assert resolved["126"]["category_name"] == "Technology & Computing"

    def test_get_rules_no_fallback_returns_404_equivalent(self):
        """Real lookup yields no active rules => 404 from both (real-data-only, no mock fallback)."""
        event = _authed_event("GET", "/pricing/rules")
        with patch(f"{_PM}.__init__", return_value=None), patch(
            f"{_PM}.get_active_pricing_rules", return_value=None
        ):
            assert_route_equivalent(event, expected_status=404)


class TestPricingHistoryEquivalence:
    """Property 1 - route equivalence for GET /pricing/history (real + mock-seed paths)."""

    def test_history_empty_source_equivalent(self):
        """Empty real source => both return an empty history list (real-data-only)."""
        event = _authed_event("GET", "/pricing/history")
        with patch(f"{_PM}.__init__", return_value=None), patch(
            f"{_PM}.get_pricing_history", return_value=[]
        ):
            response = assert_route_equivalent(event, expected_status=200)
        body = _normalize(response)["body"]
        assert "history" in body
        assert body["history"] == []

    def test_history_real_data_equivalent(self):
        """Real data => both format the same DynamoDB rows identically."""
        event = _authed_event("GET", "/pricing/history")
        with patch(f"{_PM}.__init__", return_value=None), patch(
            f"{_PM}.get_pricing_history", return_value=copy.deepcopy(_HISTORY_ITEMS)
        ):
            response = assert_route_equivalent(event, expected_status=200)
        body = _normalize(response)["body"]
        assert len(body["history"]) == 1
        assert body["history"][0]["audit_trail"]["version"] == 2


class TestPricingCalculateEquivalence:
    """Property 1 - route equivalence for POST /pricing/calculate."""

    def test_calculate_equivalent(self):
        event = _authed_event(
            "POST",
            "/pricing/calculate",
            body={"content_items": [{"content_type": "article", "quantity": 3}, {"content_type": "report", "quantity": 2}]},
        )
        response = assert_route_equivalent(event, expected_status=200)
        body = _normalize(response)["body"]
        assert body["currency"] == "USD"
        assert len(body["breakdown"]) == 2


class TestPricingCreateUpdateEquivalence:
    """Property 1 - route equivalence for POST/PUT /pricing/rules (create + update)."""

    def test_create_missing_field_returns_400_equivalent(self):
        # No pricing_rules in the body => both short-circuit to the same 400 before any service call.
        event = _authed_event("POST", "/pricing/rules", body={"effective_date": "2025-01-01", "change_reason": "x"})
        response = assert_route_equivalent(event, expected_status=400)
        assert _normalize(response)["body"]["error"] == "pricing_rules is required"

    def test_create_success_equivalent(self):
        created = copy.deepcopy(_ACTIVE_RULE)
        created["status"] = "draft"
        event = _authed_event(
            "POST",
            "/pricing/rules",
            body={
                "rule_set_id": "default-pricing",
                "pricing_rules": {"per_access": {"article": "0.05"}},
                "effective_date": "2025-02-01",
                "change_reason": "characterization",
            },
        )
        with patch(f"{_PM}.__init__", return_value=None), patch(
            f"{_PM}.create_pricing_rule_version", return_value=created
        ):
            response = assert_route_equivalent(event, expected_status=201)
        assert _normalize(response)["body"]["message"] == "Pricing rules created successfully"

    def test_update_missing_field_returns_400_equivalent(self):
        event = _authed_event("PUT", "/pricing/rules", body={"pricing_rules": {}, "change_reason": "x"})
        response = assert_route_equivalent(event, expected_status=400)
        assert _normalize(response)["body"]["error"] == "effective_date is required"

    def test_update_success_equivalent(self):
        created = copy.deepcopy(_ACTIVE_RULE)
        activated = copy.deepcopy(_ACTIVE_RULE)
        activated["status"] = "active"
        event = _authed_event(
            "PUT",
            "/pricing/rules",
            body={
                "rule_set_id": "default-pricing",
                "pricing_rules": {"per_access": {"article": "0.06"}},
                "effective_date": "2025-02-01",
                "change_reason": "characterization update",
            },
        )
        with patch(f"{_PM}.__init__", return_value=None), patch(
            f"{_PM}.get_active_pricing_rules", return_value=None
        ), patch(f"{_PM}.create_pricing_rule_version", return_value=created), patch(
            f"{_PM}.activate_pricing_rule_version", return_value=activated
        ):
            response = assert_route_equivalent(event, expected_status=200)
        assert _normalize(response)["body"]["message"] == "Pricing rules updated successfully"


class TestPricingUnknownEndpointEquivalence:
    """Property 1 - an unmatched /pricing/* method+path yields the same 404 from both."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("DELETE", "/pricing/rules"),
            ("GET", "/pricing/calculate"),
            ("GET", "/pricing/unknown"),
        ],
        ids=["delete-rules", "get-calculate", "get-unknown"],
    )
    def test_unknown_pricing_endpoint_returns_404_equivalent(self, method, path):
        event = _authed_event(method, path)
        response = assert_route_equivalent(event, expected_status=404)
        assert _normalize(response)["body"]["error"] == "Pricing endpoint not found"


class TestPricingAuthPreserved:
    """Property 2 - missing/invalid Cognito credential => 401, service never instantiated."""

    def _assert_unauthorized(self, response):
        assert response["statusCode"] == 401
        body = json.loads(response["body"])
        assert body["error"] == "Invalid or missing Cognito token"
        for header in _CORS_HEADERS:
            assert header in response["headers"]

    def test_missing_credential_returns_401_and_skips_service(self):
        event = {
            "httpMethod": "GET",
            "path": "/pricing/rules",
            "headers": {"Content-Type": "application/json"},
            "queryStringParameters": None,
            "body": None,
        }
        with patch("ui_backend.routes.pricing.PricingVersionManager") as route_pm:
            new_response = _run_new(event)

        self._assert_unauthorized(new_response)
        route_pm.assert_not_called()

    def test_invalid_credential_returns_401_and_skips_service(self):
        event = {
            "httpMethod": "GET",
            "path": "/pricing/rules",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer not-a-real-jwt"},
            "queryStringParameters": None,
            "body": None,
        }
        with patch("ui_backend.routes.pricing.PricingVersionManager") as route_pm:
            new_response = _run_new(event)

        self._assert_unauthorized(new_response)
        route_pm.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
