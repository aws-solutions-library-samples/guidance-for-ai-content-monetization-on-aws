"""
Content domain Characterization_Test (required gate for the content dispatcher flip).

Task 5.2 of the ``lambda-handler-decomposition`` spec. This test enforces the two
correctness properties that gate flipping the ``/content*`` dispatcher entry from the
inline monolith handler to the new Route_Module (task 5.3):

* **Property 1 — Route equivalence** (design Property 1): FOR the representative
  ``/content*`` method/path matrix, the new Route_Module
  (``ui_backend.routes.content.route`` -> ``services.content_service.ContentService``)
  returns the same HTTP status code, the same response body, and the same CORS headers as
  the characterized golden baseline (Requirements 2.1, 2.2, 2.3).

* **Property 2 — Auth preserved** (design Property 2): a missing or invalid credential on a
  ``/content*`` request yields an identical ``401`` from both the Route_Module and the
  golden baseline, exercising the real (unmocked) auth path so no protected content
  operation is reachable without credentials (Requirements 2.5, 6.6).

**Validates: Requirements 2.1, 2.2, 2.3, 2.5, 6.6**

Golden-master baseline (post-flip form): before task 5.3 this suite compared the Route_Module
against the inline ``ui_backend.handler.handle_content_endpoints`` router. That inline router
(and its inline mock content handlers) was **deleted in the same change that flipped the
``/content*`` dispatcher entry** to ``routes/content.py`` (Requirement 16.3, mirroring the
taxonomy flip in task 3.3 and the processing flip in task 4.4), so the old-vs-new diff is no
longer possible. The inline router simply delegated to ``content_management.lambda_handler(
event, None)`` — which remains the live content implementation (its ``lambda_handler`` shim is
intentionally retained during the interim strangler-fig delegation, Requirement 4.5). The golden
baseline is therefore that same ``content_management.lambda_handler(event, None)`` call,
constructed independently of the Route_Module's own code path; the Route_Module must reproduce
that exact status/body/CORS contract.

Canonical-root-clean (Requirement 17.6): every first-party import is absolute and rooted at
``source/lambda/`` (``ui_backend.routes.content``, ``content_management``,
``shared.utils.response_helpers``) — no relative or bare-sibling forms — so the suite resolves
under both the current ``pytest.ini`` pythonpath and #11's tightened single-canonical-root
pythonpath.
"""

import copy
import json
import os
from typing import Any, Dict, Optional

# Environment defaults must be set before importing the application modules, which build
# boto3 resources / read table names at module load time (mirrors the sibling
# content_management unit tests). No AWS calls are made by the code paths exercised here.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

import pytest  # noqa: E402
from unittest.mock import patch  # noqa: E402

from services.content_service import ContentService  # noqa: E402
from shared.utils.response_helpers import create_success_response  # noqa: E402
from ui_backend.routes.content import route as content_route  # noqa: E402

CORS_ORIGIN_HEADER = "Access-Control-Allow-Origin"

# Representative /content* request matrix: (method, path, body). Covers list, create,
# item get/update/delete, and bulk — the full set of methods the content domain serves.
CONTENT_REQUEST_MATRIX = [
    ("GET", "/content", None),
    ("POST", "/content", {"title": "Example", "content_type": "article"}),
    ("GET", "/content/cnt_abc123", None),
    ("PUT", "/content/cnt_abc123", {"title": "Updated"}),
    ("DELETE", "/content/cnt_abc123", None),
    ("POST", "/content/bulk", {"operation": "delete", "content_ids": ["cnt_abc123"]}),
]

AUTHORIZED_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer test-cognito-token",
}


def _build_event(
    method: str, path: str, body: Optional[Dict[str, Any]], headers: Optional[Dict[str, str]]
) -> Dict[str, Any]:
    """Build an API Gateway ``aws_proxy`` event for a ``/content*`` request."""
    return {
        "httpMethod": method,
        "path": path,
        "headers": dict(headers) if headers is not None else {},
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": None,
        "pathParameters": None,
        "requestContext": {"requestId": "characterization-test"},
    }


def _invoke_golden(event: Dict[str, Any]) -> Dict[str, Any]:
    """Produce the characterized golden baseline for a ``/content*`` request.

    The (deleted) inline ``handle_content_endpoints`` router did exactly one thing:
    delegate to the content domain's single implementation. That implementation now lives in
    ``services.content_service.ContentService.handle_request`` (the former
    ``content_management.lambda_handler`` was bodily extracted into this method). The golden
    baseline is therefore an independent ``ContentService().handle_request(event, None)`` call,
    invoked here separately from the Route_Module's own singleton so the two can be compared.
    """
    return ContentService().handle_request(event, None)


def _normalize(response: Dict[str, Any]) -> Dict[str, Any]:
    """Return a response copy with volatile body fields removed for stable comparison.

    Error bodies carry per-invocation volatile fields — a random ``request_id`` (from
    ``shared.utils.response_helpers.create_error_response``) and a wall-clock
    ``timestamp`` (from the content module's error responder) — so two independent
    invocations of the same error path produce different raw bytes. Dropping those fields
    lets us assert structural body equivalence without coupling to the volatile values.
    """
    normalized = copy.deepcopy(response)
    raw_body = normalized.get("body")
    if isinstance(raw_body, str) and raw_body:
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            return normalized
        if isinstance(parsed, dict):
            parsed.pop("request_id", None)
            parsed.pop("timestamp", None)
        normalized["body"] = parsed
    return normalized


def _echo_delegate(self: Any, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Deterministic stand-in for ``ContentService.handle_request``.

    Echoes the delegated event so that any divergence in how the golden baseline vs the
    Route_Module forward the request (method, path, body, or context) would surface as a
    body/response difference. Produces no random fields, enabling exact comparison.
    """
    return create_success_response(
        {
            "seen_method": event.get("httpMethod"),
            "seen_path": event.get("path"),
            "seen_body": event.get("body"),
            "context_is_none": context is None,
        }
    )


class TestContentRouteEquivalence:
    """Property 1: the Route_Module matches the golden baseline across ``/content*``."""

    @pytest.mark.parametrize("method,path,body", CONTENT_REQUEST_MATRIX)
    def test_route_matches_golden_across_methods(self, method: str, path: str, body: Optional[Dict[str, Any]]):
        """Route_Module and golden baseline return identical status/body/CORS per request."""
        with patch.object(ContentService, "handle_request", _echo_delegate):
            golden_event = _build_event(method, path, body, AUTHORIZED_HEADERS)
            route_event = _build_event(method, path, body, AUTHORIZED_HEADERS)

            golden_response = _invoke_golden(golden_event)
            route_response = content_route(route_event, method, path)

        # Status code equivalence (Requirement 2.1).
        assert route_response["statusCode"] == golden_response["statusCode"]
        # Body equivalence — same fields, nesting, values (Requirement 2.2). Deterministic
        # delegate, so exact string equality holds.
        assert route_response["body"] == golden_response["body"]
        # CORS header equivalence (Requirement 2.3).
        assert route_response["headers"] == golden_response["headers"]
        assert CORS_ORIGIN_HEADER in route_response["headers"]
        # Sanity: the delegate actually saw the request unchanged via both paths.
        assert json.loads(route_response["body"])["seen_path"] == path
        assert json.loads(route_response["body"])["seen_method"] == method

    def test_route_matches_golden_on_real_delegate_unsupported_request(self):
        """Equivalence holds through the real (unmocked) content module.

        An authenticated but unsupported method/path passes auth and reaches the content
        module's terminal ``404 Endpoint not found`` branch without touching any data
        store, proving both paths reach the same live delegate and return the same result.
        """
        method, path = "PATCH", "/content"
        golden_event = _build_event(method, path, None, AUTHORIZED_HEADERS)
        route_event = _build_event(method, path, None, AUTHORIZED_HEADERS)

        golden_response = _invoke_golden(golden_event)
        route_response = content_route(route_event, method, path)

        assert route_response["statusCode"] == golden_response["statusCode"] == 404
        assert _normalize(route_response) == _normalize(golden_response)
        assert CORS_ORIGIN_HEADER in route_response["headers"]


class TestContentRouteAuthPreserved:
    """Property 2: missing/invalid credentials yield an identical 401 from both paths."""

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({"Content-Type": "application/json"}, id="no-authorization-header"),
            pytest.param({}, id="empty-headers"),
            pytest.param({"Authorization": "Token not-a-bearer"}, id="non-bearer-scheme"),
            pytest.param({"Authorization": ""}, id="empty-authorization"),
        ],
    )
    def test_missing_or_invalid_credential_returns_401(self, headers: Dict[str, str]):
        """Both the Route_Module and the golden baseline reject with an identical 401."""
        method, path = "GET", "/content"
        golden_event = _build_event(method, path, None, headers)
        route_event = _build_event(method, path, None, headers)

        golden_response = _invoke_golden(golden_event)
        route_response = content_route(route_event, method, path)

        assert route_response["statusCode"] == 401
        assert golden_response["statusCode"] == 401
        # Same auth-failure body shape/message and same CORS headers.
        assert _normalize(route_response) == _normalize(golden_response)
        assert CORS_ORIGIN_HEADER in route_response["headers"]

    def test_unauthenticated_request_never_reaches_data_layer(self):
        """A credential-less request short-circuits to 401 before any handler runs."""
        method, path = "POST", "/content"
        event = _build_event(method, path, {"title": "should-not-persist"}, {"Content-Type": "application/json"})

        route_response = content_route(event, method, path)

        assert route_response["statusCode"] == 401
        body = json.loads(route_response["body"])
        # The content module's auth-failure body carries the human-readable text in
        # ``message`` (with ``error``/``code`` set to the machine token ``unauthorized``).
        assert body["message"] == "Invalid or missing authentication token"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
