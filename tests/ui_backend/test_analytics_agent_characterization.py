#!/usr/bin/env python3
"""analytics_agent domain Characterization_Test (REQUIRED GATE for the dispatcher flip, task 11.4).

This suite pins the behavior of the ``/analytics-agent/*`` domain across the strangler-fig
migration for the per-domain Route_Module (``ui_backend/routes/analytics_agent.py`` ->
``services/agent_runtime_client.AgentRuntimeClient``). It enforces the two correctness
properties the design defines for a migration step:

* **Property 1 - Route equivalence** for ``POST /analytics-agent/chat``: the Route_Module
  produces the same HTTP status code, the same response body (same fields, nesting, and
  values), and the same CORS headers as the characterized golden baseline. Against a
  deterministically-mocked bedrock-agentcore ``invoke_agent_runtime`` the following request
  outcomes are covered:
    - the ARN-not-configured 500 (``"Analytics agent ARN not configured"``),
    - the message-required 400 (``"Message is required"``),
    - the success path (200, AgentCore response parsed and returned unchanged), and
    - the unmatched-path 404 (``"Analytics agent endpoint not found"``) within the prefix.
  (Validates: Requirements 2.1, 2.2, 2.3, 2.5)
* **Property 2 - Auth preserved**: a missing or invalid Cognito credential yields ``401`` via
  the unchanged ``@require_cognito_auth`` wrapper, with the auth-failure message and CORS
  headers on the 401 response, and the Service_Layer (``AgentRuntimeClient.invoke``) is never
  reached when auth fails.
  (Validates: Requirements 2.5, 6.6)

Golden-master baseline (post-flip form): before task 11.4 this suite compared the Route_Module
against the pre-refactor inline handler in ``ui_backend/handler.py``
(``handle_analytics_agent_endpoints`` -> ``handle_analytics_agent_chat``, reached through the
dispatcher adapter ``_route_analytics_agent``). That inline router, its interim
``_route_analytics_agent`` adapter, and the inline ``handle_analytics_agent_chat`` handler
(including its inline bedrock-agentcore ``invoke_agent_runtime`` call) were **deleted in the
same change that flipped the ``/analytics-agent/*`` dispatcher entry** to
``routes/analytics_agent.py`` (Requirement 16.3, mirroring the taxonomy flip in task 3.3,
processing in 4.4, content in 5.3, bot_control in 6.3, analytics in 8.5, pricing in 9.4, and
partners in 10.4), so the old-vs-new diff is no longer possible. The inline handler delegated
the bedrock-agentcore invoke and returned the parsed AgentCore output wrapped in
``create_success_response`` (with the same ``@validate_response`` / ``@require_cognito_auth``
guards). The golden baseline (``_golden`` below) reproduces that exact status/body/CORS
contract by assembling the same ``create_success_response`` / ``create_error_response``
envelopes directly -- constructed independently of the Route_Module's own code path -- so the
two can be compared for route equivalence. Because the mocked AgentCore output is a *complete*
agent response, the success contract reduces to ``create_success_response(_AGENT_OUTPUT)``.

The bedrock-agentcore ``invoke_agent_runtime`` call is mocked at the boto3-client level (the
Route_Module delegates to ``AgentRuntimeClient`` which builds a ``boto3.client("bedrock-agentcore")``),
so equivalence is asserted with an identical, deterministic AgentCore response and no real AWS
call is made.

Authored canonical-root-clean (Requirement 17.6): every first-party import is absolute, rooted
at the canonical import root ``source/lambda/`` (``ui_backend.*``, ``services.*``, ``shared.*``),
with no relative (leading-dot) or bare-sibling import forms and no ``sys.path`` manipulation, so
it resolves under both the current ``pytest.ini`` pythonpath and #11's tightened
single-canonical-root pythonpath.

This is a REQUIRED gate: it must complete with zero failures for task 11.4 (the dispatcher flip
to the Route_Module + deletion of the inline analytics-agent code).

_Requirements: 4.2, 4.6, 17.4, 17.6_
"""

import json
import os
from unittest.mock import Mock, patch

import pytest

import ui_backend.routes.analytics_agent as agent_route_module
from shared.utils.response_helpers import create_error_response, create_success_response

_PATH = "/analytics-agent/chat"
_METHOD = "POST"
_ARN = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/analytics-agent-test"

# CORS header names the Route_Module must attach to every response.
_CORS_HEADERS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
)

# Deterministic AgentCore output. It is a *complete* agent response — it already carries
# ``response``, ``page_context``, ``user_context`` and ``session_id`` — so the Route_Module does
# not inject its own ``processed_page_context`` (which would embed a non-deterministic
# ``datetime.now`` timestamp and break byte-for-byte comparison). This isolates route equivalence
# from wall-clock noise, and makes the golden success contract exactly
# ``create_success_response(_AGENT_OUTPUT)``.
_AGENT_OUTPUT = {
    "response": "Your revenue is up 12% week over week.",
    "intent_analysis": {"primary_intent": "revenue_query", "confidence": 0.95},
    "suggested_actions": ["view_revenue_breakdown"],
    "page_context": {"page_type": "dashboard", "url_path": "/dashboard"},
    "user_context": "test-publisher",
    "session_id": "session-abc",
    "model_used": "AgentCore Runtime",
}


def _make_fake_bedrock_client():
    """Build a Mock bedrock-agentcore client whose ``invoke_agent_runtime`` is deterministic.

    The returned response mimics the real AgentCore shape: a ``response`` StreamingBody whose
    ``read()`` yields the JSON-encoded ``_AGENT_OUTPUT`` bytes, plus a ``ResponseMetadata``
    block. The ``AgentRuntimeClient`` reads/decodes/parses it into ``{"output": _AGENT_OUTPUT}``.
    """
    body = Mock()
    body.read.return_value = json.dumps(_AGENT_OUTPUT).encode("utf-8")
    client = Mock()
    client.invoke_agent_runtime.return_value = {
        "response": body,
        "ResponseMetadata": {"HTTPStatusCode": 200},
    }
    return client


def _authenticated_event(body_dict=None):
    """Build a ``POST /analytics-agent/chat`` event that passes ``@require_cognito_auth``.

    The API Gateway Cognito authorizer claims satisfy ``extract_cognito_user_context``, so no
    JWT verification or AWS call is exercised on the happy path.
    """
    return {
        "httpMethod": _METHOD,
        "path": _PATH,
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        "queryStringParameters": None,
        "body": json.dumps(body_dict) if body_dict is not None else None,
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


def _run_new(event, path=_PATH, method=_METHOD):
    """Invoke the Route_Module exactly as the dispatcher would.

    Reset the module-level client's memoized boto3 client so a patched ``boto3.client`` takes
    effect on this invocation (the warm-container memoization would otherwise reuse a client
    created under a prior patch or none).
    """
    agent_route_module._agent_runtime_client._client = None
    return agent_route_module.route(event, method, path)


def _golden(event, path=_PATH, method=_METHOD):
    """Produce the characterized golden baseline for an ``/analytics-agent/*`` request.

    The (deleted) inline ``handle_analytics_agent_endpoints`` -> ``handle_analytics_agent_chat``
    validated the request, delegated the bedrock-agentcore invoke, and returned the parsed
    AgentCore output wrapped in ``create_success_response`` (unmatched paths within the prefix
    returned a ``create_error_response(404, ...)``). This baseline reproduces that exact
    status/body/CORS contract by assembling the same response envelopes directly — independently
    of the Route_Module's own code path — so the two can be compared for route equivalence.
    Guard order (message before ARN) mirrors the handler.
    """
    if path == _PATH and method == _METHOD:
        body = json.loads(event["body"]) if event.get("body") else {}
        message = body.get("message", "")
        if not message:
            return create_error_response(400, "Message is required")
        if not os.environ.get("ANALYTICS_AGENT_ARN"):
            return create_error_response(500, "Analytics agent ARN not configured")
        # _AGENT_OUTPUT is a complete agent response, so the handler returned it unchanged.
        return create_success_response(_AGENT_OUTPUT)

    return create_error_response(404, "Analytics agent endpoint not found")


def _normalize(response):
    """Return a comparable view of a proxy response.

    Strips two non-deterministic, non-contract fields so the real payload is compared:
    * ``_validation`` — test-environment metadata injected by ``@validate_response`` (carries a
      wall-clock timestamp); the Route_Module attaches it, the golden baseline does not.
    * ``request_id`` — a random ``req_<hex>`` id that ``create_error_response`` stamps on every
      error body; it differs per call by construction.
    """
    body = json.loads(response["body"])
    if isinstance(body, dict):
        body.pop("_validation", None)
        body.pop("request_id", None)
    return {"statusCode": response["statusCode"], "headers": response["headers"], "body": body}


def _assert_cors_present(response):
    for header in _CORS_HEADERS:
        assert header in response["headers"], f"missing CORS header {header}"


class TestAnalyticsAgentRouteEquivalence:
    """Property 1 - Route equivalence for POST /analytics-agent/chat."""

    def test_success_path_equivalent(self):
        """New route == golden baseline when AgentCore returns a parsed response (200)."""
        event = _authenticated_event({"message": "How is my revenue?", "session_id": "session-abc"})

        new_fake = _make_fake_bedrock_client()
        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": _ARN}), patch(
            "boto3.client", return_value=new_fake
        ):
            new_response = _run_new(event)
            golden_response = _golden(event)

        assert new_response["statusCode"] == 200
        assert _normalize(new_response) == _normalize(golden_response)

        # Body is the AgentCore response, returned unchanged (single source of truth).
        new_body = _normalize(new_response)["body"]
        assert new_body["response"] == _AGENT_OUTPUT["response"]
        assert new_body["intent_analysis"] == _AGENT_OUTPUT["intent_analysis"]
        assert new_body["session_id"] == "session-abc"
        _assert_cors_present(new_response)

        # The route reached the deterministically-mocked invoke_agent_runtime exactly once, with
        # the same ARN and the same AgentCore payload envelope the inline handler built.
        new_fake.invoke_agent_runtime.assert_called_once()
        call_kwargs = new_fake.invoke_agent_runtime.call_args.kwargs
        assert call_kwargs["agentRuntimeArn"] == _ARN
        payload = json.loads(call_kwargs["payload"])
        assert payload["tool_name"] == "analytics_chat"
        assert payload["client_type"] == "internal"
        assert payload["parameters"]["message"] == "How is my revenue?"

    def test_arn_not_configured_returns_500_equivalent(self):
        """Missing ANALYTICS_AGENT_ARN => 500, invoke never reached."""
        event = _authenticated_event({"message": "How is my revenue?"})

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANALYTICS_AGENT_ARN", None)
            with patch("boto3.client") as new_boto:
                new_response = _run_new(event)
            golden_response = _golden(event)

        assert new_response["statusCode"] == 500
        assert _normalize(new_response) == _normalize(golden_response)
        assert _normalize(new_response)["body"]["error"] == "Analytics agent ARN not configured"
        _assert_cors_present(new_response)
        # ARN guard short-circuits before any bedrock-agentcore client is built/invoked.
        new_boto.assert_not_called()

    def test_message_required_returns_400_equivalent(self):
        """Empty/absent message => 400, invoke never reached."""
        event = _authenticated_event({"session_id": "session-abc"})  # no "message"

        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": _ARN}), patch("boto3.client") as new_boto:
            new_response = _run_new(event)
            golden_response = _golden(event)

        assert new_response["statusCode"] == 400
        assert _normalize(new_response) == _normalize(golden_response)
        assert _normalize(new_response)["body"]["error"] == "Message is required"
        _assert_cors_present(new_response)
        new_boto.assert_not_called()

    def test_empty_message_string_returns_400_equivalent(self):
        """An explicit empty-string message is also rejected identically (400)."""
        event = _authenticated_event({"message": ""})

        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": _ARN}):
            new_response = _run_new(event)
            golden_response = _golden(event)

        assert new_response["statusCode"] == 400
        assert _normalize(new_response) == _normalize(golden_response)

    def test_unknown_subpath_returns_404_equivalent(self):
        """An unmatched method/path within the prefix => 404 (Requirement 4.6)."""
        event = _authenticated_event({"message": "hello"})

        new_response = _run_new(event, path="/analytics-agent/unknown", method="GET")
        golden_response = _golden(event, path="/analytics-agent/unknown", method="GET")

        assert new_response["statusCode"] == 404
        assert _normalize(new_response) == _normalize(golden_response)
        assert _normalize(new_response)["body"]["error"] == "Analytics agent endpoint not found"
        _assert_cors_present(new_response)


class TestAnalyticsAgentAuthPreserved:
    """Property 2 - Auth preserved: missing/invalid Cognito credential => 401."""

    def _assert_unauthorized(self, response):
        assert response["statusCode"] == 401
        body = json.loads(response["body"])
        assert body["error"] == "Invalid or missing Cognito token"
        _assert_cors_present(response)

    def test_missing_credential_returns_401_and_skips_service(self):
        """No authorizer claims and no bearer token => 401, service never invoked."""
        event = {
            "httpMethod": _METHOD,
            "path": _PATH,
            "headers": {"Content-Type": "application/json"},
            "queryStringParameters": None,
            "body": json.dumps({"message": "How is my revenue?"}),
        }

        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": _ARN}):
            with patch.object(agent_route_module._agent_runtime_client, "invoke") as route_invoke:
                new_response = _run_new(event)

        self._assert_unauthorized(new_response)
        # Auth short-circuits before the Service_Layer is reached.
        route_invoke.assert_not_called()

    def test_invalid_credential_returns_401_and_skips_service(self):
        """A malformed bearer token fails verification => 401, service never invoked."""
        event = {
            "httpMethod": _METHOD,
            "path": _PATH,
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer not-a-real-jwt"},
            "queryStringParameters": None,
            "body": json.dumps({"message": "How is my revenue?"}),
        }

        with patch.dict(os.environ, {"ANALYTICS_AGENT_ARN": _ARN}):
            with patch.object(agent_route_module._agent_runtime_client, "invoke") as route_invoke:
                new_response = _run_new(event)

        self._assert_unauthorized(new_response)
        route_invoke.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
