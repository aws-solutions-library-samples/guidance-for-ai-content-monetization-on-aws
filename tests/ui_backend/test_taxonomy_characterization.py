#!/usr/bin/env python3
"""Taxonomy domain Characterization_Test (required gate for the dispatcher flip, task 3.3).

This suite pins the behavior of the taxonomy domain across the strangler-fig migration for the
new per-domain route module ``ui_backend/routes/taxonomy.py``. It enforces the two correctness
properties the design defines for a migration step:

* **Property 1 - Route equivalence** for ``GET /taxonomy/categories``: the route module produces
  the same HTTP status code, the same response body (same fields, nesting, and values), and the
  same CORS headers as the pre-refactor inline handler it replaced. Both the real-taxonomy path
  and the hardcoded-fallback path are covered.
  (Validates: Requirements 2.1, 2.2, 2.3)
* **Property 2 - Auth preserved**: a missing or invalid Cognito credential yields ``401`` from the
  route module via the unchanged ``@require_cognito_auth`` wrapper, and the service is never
  invoked when auth fails.
  (Validates: Requirements 2.5, 6.6)

Golden-master baseline (post-flip form): before task 3.3 this suite compared the route module
against the inline ``ui_backend/handler.py:handle_taxonomy_categories`` handler. That inline
handler was **deleted in the same change that flipped the dispatcher** to ``routes/taxonomy.py``
(Requirement 16.3), so the old-vs-new diff is no longer possible. The inline handler's
characterized output is therefore pinned here as an explicit golden baseline, constructed via the
shared ``create_success_response`` helper (independent of the route module's own logic). The route
module must still reproduce that exact status/body/CORS contract.

Authored canonical-root-clean (Requirement 17.6): every first-party import is absolute, rooted at
the canonical import root ``source/lambda/`` (``ui_backend.*``, ``services.*``, ``shared.*``), with
no relative (leading-dot) or bare-sibling import forms and no ``sys.path`` manipulation, so it
resolves under both the current ``pytest.ini`` pythonpath and #11's tightened
single-canonical-root pythonpath.

_Requirements: 4.2, 4.6, 17.4, 17.6_
"""

import json
from unittest.mock import patch

import pytest

from shared.utils.response_helpers import create_success_response
from ui_backend.routes.taxonomy import route as taxonomy_route

# The route module binds TaxonomyManager at import time
# (``ui_backend.routes.taxonomy.TaxonomyManager``); patching this symbol swaps in the test double.
_ROUTE_TM = "ui_backend.routes.taxonomy.TaxonomyManager"

# Representative real-taxonomy dataset returned by the mocked TaxonomyManager. Keys are ints to
# exercise the ``str(category_id)`` coercion the route module performs.
_REAL_CATEGORIES = {
    1: {"name": "Automotive", "full_path": "Automotive", "tier_level": 1},
    52: {"name": "Business and Finance", "full_path": "Business and Finance", "tier_level": 1},
    126: {"name": "Technology & Computing", "full_path": "Technology & Computing", "tier_level": 2},
}

# Golden-master baseline payloads — the characterized output of the (deleted) inline handler.
# The real-data payload mirrors ``_REAL_CATEGORIES`` (formatted for UI dropdowns); the fallback
# payload is the hardcoded taxonomy set the handler returns when the real lookup fails.
_EXPECTED_REAL_DATA = {
    "categories": [
        {"value": "1", "label": "Automotive", "full_path": "Automotive", "tier_level": 1},
        {"value": "52", "label": "Business and Finance", "full_path": "Business and Finance", "tier_level": 1},
        {"value": "126", "label": "Technology & Computing", "full_path": "Technology & Computing", "tier_level": 2},
    ],
    "mappings": {"1": "Automotive", "52": "Business and Finance", "126": "Technology & Computing"},
    "total_count": 3,
}

_EXPECTED_FALLBACK_DATA = {
    "categories": [
        {"value": "1", "label": "Automotive", "full_path": "Automotive", "tier_level": 1},
        {"value": "52", "label": "Business and Finance", "full_path": "Business and Finance", "tier_level": 1},
        {"value": "111", "label": "Medical Health", "full_path": "Medical Health", "tier_level": 1},
        {"value": "126", "label": "Technology & Computing", "full_path": "Technology & Computing", "tier_level": 1},
        {"value": "150", "label": "Attractions", "full_path": "Attractions", "tier_level": 1},
    ],
    "mappings": {
        "1": "Automotive",
        "52": "Business and Finance",
        "111": "Medical Health",
        "126": "Technology & Computing",
        "150": "Attractions",
    },
    "total_count": 5,
}


class _FakeTaxonomyManager:
    """Minimal stand-in for ``services.taxonomy_manager.TaxonomyManager``."""

    def __init__(self, categories):
        self._categories = categories

    def get_all_categories(self):
        return self._categories


def _authenticated_event(query_params=None):
    """Build a ``GET /taxonomy/categories`` event that passes ``@require_cognito_auth``.

    The API Gateway Cognito authorizer claims satisfy ``extract_cognito_user_context``, so no JWT
    verification or AWS call is exercised on the happy path.
    """
    return {
        "httpMethod": "GET",
        "path": "/taxonomy/categories",
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        "queryStringParameters": query_params,
        "body": None,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "test-user-123",
                    "email": "test@example.com",
                    "custom:publisher_id": "test-publisher",
                    "cognito:username": "test_user",
                }
            }
        },
    }


def _run_new(event):
    """Invoke the new route module exactly as the dispatcher would."""
    return taxonomy_route(event, "GET", "/taxonomy/categories")


def _normalize(response):
    """Return a comparable view of a proxy response.

    Strips the non-deterministic ``_validation`` metadata block that the ``@validate_response``
    decorator injects into the body outside production (it carries a wall-clock timestamp). The
    ``_validation`` block is test-environment metadata, not part of the API contract, so removing
    it isolates the real response payload.
    """
    body = json.loads(response["body"])
    body.pop("_validation", None)
    return {"statusCode": response["statusCode"], "headers": response["headers"], "body": body}


def _expected_success_baseline(data):
    """Golden baseline for a taxonomy success response (the characterized inline-handler output).

    Built via the shared ``create_success_response`` helper — the same helper the route module
    uses — so the CORS headers, status code, and body serialization match the pre-refactor
    contract without depending on the route module's own code path.
    """
    return _normalize(create_success_response(data))


# CORS headers that the route module must attach to every response.
_CORS_HEADERS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
)


# Representative request variants. Taxonomy takes no request body and ignores query params, so all
# variants must yield identical responses (route equivalence FOR ALL requests the endpoint
# accepts).
_QUERY_VARIANTS = [
    pytest.param(None, id="no-query-params"),
    pytest.param({}, id="empty-query-params"),
    pytest.param({"format": "dropdown"}, id="with-query-params"),
]


class TestTaxonomyRouteEquivalence:
    """Property 1 - Route equivalence for GET /taxonomy/categories."""

    @pytest.mark.parametrize("query_params", _QUERY_VARIANTS)
    def test_real_taxonomy_path_equivalent(self, query_params):
        """New route == characterized baseline when the real taxonomy lookup succeeds."""
        event = _authenticated_event(query_params)
        fake = _FakeTaxonomyManager(_REAL_CATEGORIES)

        with patch(_ROUTE_TM, return_value=fake):
            new_response = _run_new(event)

        assert new_response["statusCode"] == 200
        assert _normalize(new_response) == _expected_success_baseline(_EXPECTED_REAL_DATA)
        # Body carries the formatted UI dropdown payload with real data.
        new_body = _normalize(new_response)["body"]
        assert new_body["total_count"] == len(_REAL_CATEGORIES)
        assert new_body["mappings"]["126"] == "Technology & Computing"
        for header in _CORS_HEADERS:
            assert header in new_response["headers"]

    @pytest.mark.parametrize("query_params", _QUERY_VARIANTS)
    def test_hardcoded_fallback_path_equivalent(self, query_params):
        """New route == characterized baseline when the real taxonomy lookup fails and it falls back."""
        event = _authenticated_event(query_params)

        with patch(_ROUTE_TM, side_effect=RuntimeError("taxonomy backend unavailable")):
            new_response = _run_new(event)

        assert new_response["statusCode"] == 200
        assert _normalize(new_response) == _expected_success_baseline(_EXPECTED_FALLBACK_DATA)
        # Falls back to the same hardcoded taxonomy set as the inline handler.
        new_body = _normalize(new_response)["body"]
        assert new_body["total_count"] == 5
        assert new_body["mappings"]["1"] == "Automotive"
        for header in _CORS_HEADERS:
            assert header in new_response["headers"]


class TestTaxonomyAuthPreserved:
    """Property 2 - Auth preserved: missing/invalid Cognito credential => 401."""

    def _assert_unauthorized(self, response):
        assert response["statusCode"] == 401
        body = json.loads(response["body"])
        assert body["error"] == "Invalid or missing Cognito token"
        for header in _CORS_HEADERS:
            assert header in response["headers"]

    def test_missing_credential_returns_401_and_skips_service(self):
        """No authorizer claims and no bearer token => 401, service never invoked."""
        event = {
            "httpMethod": "GET",
            "path": "/taxonomy/categories",
            "headers": {"Content-Type": "application/json"},
            "queryStringParameters": None,
            "body": None,
        }

        with patch(_ROUTE_TM) as route_tm:
            new_response = _run_new(event)

        self._assert_unauthorized(new_response)
        # Auth short-circuits before the service is reached.
        route_tm.assert_not_called()

    def test_invalid_credential_returns_401_and_skips_service(self):
        """A malformed bearer token fails JWT verification => 401, service never invoked."""
        event = {
            "httpMethod": "GET",
            "path": "/taxonomy/categories",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer not-a-real-jwt"},
            "queryStringParameters": None,
            "body": None,
        }

        with patch(_ROUTE_TM) as route_tm:
            new_response = _run_new(event)

        self._assert_unauthorized(new_response)
        route_tm.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
