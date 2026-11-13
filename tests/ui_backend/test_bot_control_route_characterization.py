"""
Characterization_Test — bot_control Route_Module route-equivalence (Task 6.2, REQUIRED GATE).

This suite compares the new bot-control Route_Module
(``ui_backend/routes/bot_control.py`` -> ``services/bot_control_service.py``) against the
golden-master baseline — the bot-control single source of truth
``BotControlService().handle_request(event, None)`` — for the same requests, enforcing the
design's correctness properties for the ``/bot-control/*`` domain:

Golden-master baseline (post-flip form): before task 6.3 this suite compared the Route_Module
against the inline ``ui_backend/handler.handle_bot_control_endpoints`` router. That inline
router (and its ImportError fallback + inline mock handlers) was **deleted in the same change
that flipped the dispatcher** to ``routes/bot_control.py`` (Requirement 16.3), so the
old-vs-new diff is no longer possible. The inline router's primary (non-ImportError) path
delegated straight to the bot-control single source of truth; that logic now lives directly
in ``services.bot_control_service.BotControlService.handle_request`` (the former bot-control
``lambda_handler`` was bodily extracted into this method, task 22). The golden baseline is
therefore an independent ``BotControlService().handle_request(event, None)`` call the
Route_Module must reproduce.

* **Property 1: Route equivalence** — for every ``/bot-control/*`` request, the Route_Module
  returns the same HTTP status code, the same response body (fields, nesting, values), and
  the same CORS headers as the inline monolith handler.
  **Validates: Requirements 2.1, 2.2, 2.3**
* **Property 2: Auth preserved** — the bot-control path is intentionally **NOT** wrapped by
  ``@require_cognito_auth`` in the monolith: the (deleted) inline router delegated
  straight to the bot-control single source of truth, which performs its own request validation
  (GET is served without a Cognito credential). "Auth preserved" is therefore asserted **as
  the behavior actually is in the monolith**: a request carrying a missing/invalid credential
  yields byte-identical responses from the Route_Module and the inline handler — the
  Route_Module neither adds nor removes the (absent) Cognito 401.
  **Validates: Requirements 2.5, 6.6**

Both the golden baseline and the Route_Module reach the single source of truth
``BotControlService().handle_request(event, None)``; these tests confirm that path is
byte-for-byte equivalent across every ``/bot-control/*`` method (GET/POST/PUT/DELETE) and
across the error paths (405 method-not-allowed, 404 unknown sub-path).

Canonical-root-clean (Requirement 17.6): every first-party import below is an absolute
import rooted at ``source/lambda/`` (``ui_backend.*``, ``services.bot_control_service``) — no
relative or bare-sibling forms, no ``sys.path`` manipulation. The suite resolves under both the
current ``pytest.ini`` pythonpath and the tightened single-canonical-root pythonpath.

This is a REQUIRED gate: it must complete with zero failures before Task 6.3 flips the
dispatcher entry to the Route_Module and deletes the inline bot-control code.
"""

import json
from unittest.mock import Mock, patch

import boto3
import pytest
from moto import mock_aws

# Single source of truth both the golden-master baseline and the Route_Module reach
# (absolute, canonical-root-rooted). ``handle_request`` is the golden-master baseline.
from services.bot_control_service import BotControlService

# New Route_Module under test.
from ui_backend.routes.bot_control import route as bot_control_route

BOT_RULES_TABLE_NAME = "iab-bot-rules-table"
AWS_REGION = "us-west-2"

# CORS header names the bot-control single source of truth attaches to every response.
_CORS_HEADER_NAMES = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
)


def _cors(response):
    """Return only the CORS header subset from a proxy response."""
    headers = response.get("headers") or {}
    return {name: headers.get(name) for name in _CORS_HEADER_NAMES}


def _inline(event):
    """Produce the golden-master baseline for a request.

    The (deleted) inline ``handle_bot_control_endpoints`` router's primary path delegated
    straight to the bot-control single source of truth, which now lives in
    ``BotControlService.handle_request``, so that call is its characterized output. This
    helper reproduces that golden baseline directly (independent of any handler.py routing
    that no longer exists), which the Route_Module must match.
    """
    return BotControlService().handle_request(event, None)


def _routed(event):
    """Invoke the new Route_Module with the uniform ``route(event, method, path)`` entry."""
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    return bot_control_route(event, method, path)


def assert_route_equivalent(event):
    """Assert Route_Module == inline handler on status, body, and CORS for one request.

    Returns the (identical) inline response for any further per-case assertions.
    """
    inline_response = _inline(event)
    routed_response = _routed(event)

    # Property 1: identical HTTP status code (Requirement 2.1).
    assert routed_response["statusCode"] == inline_response["statusCode"], (
        f"status mismatch for {event.get('httpMethod')} {event.get('path')}: "
        f"route={routed_response['statusCode']} inline={inline_response['statusCode']}"
    )

    # Property 1: identical response body — fields, nesting, values (Requirement 2.2).
    assert json.loads(routed_response["body"]) == json.loads(inline_response["body"]), (
        f"body mismatch for {event.get('httpMethod')} {event.get('path')}"
    )

    # Property 1: identical CORS headers (Requirement 2.3).
    assert _cors(routed_response) == _cors(inline_response), (
        f"CORS mismatch for {event.get('httpMethod')} {event.get('path')}"
    )
    # TM-02 (security-hardening-phase2): the CORS origin is the CONFIGURED allow-list
    # origin (from ALLOWED_ORIGINS / SSM), never a wildcard "*". The suite conftest
    # configures ALLOWED_ORIGINS so the resolver returns a concrete origin here.
    assert routed_response["headers"]["Access-Control-Allow-Origin"] != "*"
    assert routed_response["headers"]["Access-Control-Allow-Origin"].startswith("https://")

    return inline_response


def _make_event(method, path, *, headers=None, body=None, query=None, path_params=None):
    """Build an API Gateway proxy event for a ``/bot-control/*`` request."""
    return {
        "httpMethod": method,
        "path": path,
        "headers": headers if headers is not None else {"Content-Type": "application/json"},
        "queryStringParameters": query,
        "pathParameters": path_params,
        "body": body,
    }


def _seed_rules_table():
    """Create the moto-backed bot-rules table with a fixed (deterministic) rule."""
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dynamodb.create_table(
        TableName=BOT_RULES_TABLE_NAME,
        KeySchema=[{"AttributeName": "rule_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "rule_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.put_item(
        Item={
            "rule_id": "rule_1",
            "rule_name": "BlockGPTBot",
            "description": "Block GPT bot access",
            "action": "block",
            "priority": 10,
            "conditions": {"user_agent_patterns": ["gptbot"]},
            "status": "active",
            "created_at": "2024-01-15T10:00:00+00:00",
            "updated_at": "2024-01-15T10:00:00+00:00",
            "sync_status": "synced",
        }
    )
    return table


# Deterministic WAF metrics so the activity/stats read paths compare byte-for-byte.
_WAF_METRICS = {
    "blocked_requests": {"total": 100},
    "allowed_requests": {"total": 900},
    "rule_BlockGPTBot_blocked": {"total": 75},
}


class TestBotControlRouteEquivalenceReadPaths:
    """Property 1 — live, moto-backed equivalence for deterministic read/error paths."""

    @mock_aws
    def test_get_rules_equivalent(self):
        table = _seed_rules_table()
        event = _make_event("GET", "/bot-control/rules")
        with patch("services.bot_control_service.bot_rules_table", table):
            inline_response = assert_route_equivalent(event)
        assert inline_response["statusCode"] == 200
        body = json.loads(inline_response["body"])
        assert body["rules"][0]["rule_name"] == "BlockGPTBot"

    @mock_aws
    def test_get_rules_with_status_filter_equivalent(self):
        table = _seed_rules_table()
        event = _make_event("GET", "/bot-control/rules", query={"status": "active"})
        with patch("services.bot_control_service.bot_rules_table", table):
            assert_route_equivalent(event)

    def test_get_activity_equivalent(self):
        event = _make_event("GET", "/bot-control/activity")
        with patch("services.bot_control_service.waf_sync_controller") as mock_waf:
            mock_waf.get_waf_metrics.return_value = _WAF_METRICS
            inline_response = assert_route_equivalent(event)
        assert inline_response["statusCode"] == 200

    @mock_aws
    def test_get_stats_equivalent(self):
        table = _seed_rules_table()
        event = _make_event("GET", "/bot-control/stats")
        with patch("services.bot_control_service.bot_rules_table", table), patch(
            "services.bot_control_service.waf_sync_controller"
        ) as mock_waf:
            mock_waf.get_waf_metrics.return_value = _WAF_METRICS
            inline_response = assert_route_equivalent(event)
        assert inline_response["statusCode"] == 200


class TestBotControlRouteEquivalenceErrorPaths:
    """Property 1 — equivalence on the error paths the domain produces itself."""

    def test_method_not_allowed_equivalent(self):
        # PATCH on /bot-control/rules -> 405 Method not allowed (no AWS access reached).
        event = _make_event("PATCH", "/bot-control/rules", body="{}")
        inline_response = assert_route_equivalent(event)
        assert inline_response["statusCode"] == 405

    def test_unknown_subpath_equivalent(self):
        # Unknown sub-path under the domain prefix -> 404 Endpoint not found.
        event = _make_event("GET", "/bot-control/does-not-exist")
        inline_response = assert_route_equivalent(event)
        assert inline_response["statusCode"] == 404

    def test_rules_id_missing_path_param_equivalent(self):
        # PUT /bot-control/rules/<id> without a pathParameters rule_id -> 400.
        event = _make_event("PUT", "/bot-control/rules/rule_1", body=json.dumps({"status": "inactive"}))
        inline_response = assert_route_equivalent(event)
        assert inline_response["statusCode"] == 400


class TestBotControlRouteEquivalenceAllMethods:
    """Property 1 — delegation equivalence across every ``/bot-control/*`` method.

    Read/error paths above compare live responses. Mutating paths (POST/PUT/DELETE) generate
    non-deterministic fields (timestamp-based ``rule_id`` and ``created_at``/``updated_at``),
    so two independent live invocations would legitimately differ. Instead, this class patches
    the single source of truth ``BotControlService.handle_request`` with a canned response and
    asserts that BOTH the golden-master baseline and the Route_Module reach it identically —
    same ``(event, None)`` arguments — and return its response unchanged. This proves route
    equivalence deterministically for every method, including mutating ones.
    """

    @pytest.mark.parametrize(
        "event",
        [
            _make_event("GET", "/bot-control/rules"),
            _make_event("POST", "/bot-control/rules", body=json.dumps({"rule_name": "R"})),
            _make_event(
                "PUT",
                "/bot-control/rules/rule_1",
                body=json.dumps({"status": "inactive"}),
                path_params={"rule_id": "rule_1"},
            ),
            _make_event("DELETE", "/bot-control/rules/rule_1", path_params={"rule_id": "rule_1"}),
            _make_event("GET", "/bot-control/activity"),
            _make_event("GET", "/bot-control/stats"),
        ],
        ids=["get-rules", "post-rules", "put-rule", "delete-rule", "get-activity", "get-stats"],
    )
    def test_delegation_equivalent_for_method(self, event):
        canned = {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Content-Type,Authorization",
                "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            },
            "body": json.dumps({"ok": True}),
        }

        with patch.object(BotControlService, "handle_request", return_value=canned) as inline_stub:
            inline_response = _inline(event)
        assert inline_stub.call_count == 1
        assert inline_stub.call_args.args == (event, None)
        assert inline_response == canned

        with patch.object(BotControlService, "handle_request", return_value=canned) as route_stub:
            routed_response = _routed(event)
        assert route_stub.call_count == 1
        assert route_stub.call_args.args == (event, None)
        assert routed_response == canned

        # Same request -> identical response from both paths.
        assert routed_response == inline_response


class TestBotControlAuthPreserved:
    """Property 2 — auth behavior preserved exactly as the monolith actually behaves.

    The bot-control path is not Cognito-wrapped; the monolith serves it without a Cognito
    credential and lets the bot-control service do its own validation. So "auth preserved" means
    the Route_Module reproduces that exact behavior for a request with missing/invalid
    credentials — no added and no removed 401.
    """

    @mock_aws
    def test_missing_credential_equivalent(self):
        table = _seed_rules_table()
        # No Authorization / X-Publisher-Key header present.
        event = _make_event("GET", "/bot-control/rules", headers={"Content-Type": "application/json"})
        with patch("services.bot_control_service.bot_rules_table", table):
            inline_response = assert_route_equivalent(event)
        # Monolith behavior: bot-control GET is served (200), NOT a Cognito 401.
        assert inline_response["statusCode"] == 200

    @mock_aws
    def test_invalid_credential_equivalent(self):
        table = _seed_rules_table()
        event = _make_event(
            "GET",
            "/bot-control/rules",
            headers={"Content-Type": "application/json", "Authorization": "Bearer not-a-real-token"},
        )
        with patch("services.bot_control_service.bot_rules_table", table):
            inline_response = assert_route_equivalent(event)
        # Monolith behavior: an invalid Bearer token is not rejected on the bot-control path
        # (no Cognito wrapper); the request is served (200). The Route_Module matches it — the
        # equivalence itself is asserted inside assert_route_equivalent above.
        assert inline_response["statusCode"] == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
