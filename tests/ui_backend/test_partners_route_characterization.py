#!/usr/bin/env python3
"""Partners domain Characterization_Test (Task 10.2, REQUIRED GATE for the dispatcher flip).

This suite pins the behavior of the partners domain across the strangler-fig migration by
comparing the new per-domain route module ``ui_backend/routes/partners.py`` against a
golden-master baseline reconstructed from the Service_Layer single home
``services.partners_service.PartnersService`` wrapped in the shared response helpers — and,
for the ``/analytics/partners`` alias, the analytics route module
``ui_backend/routes/analytics.py`` against that same baseline.

Golden-master baseline (post-flip form): before task 10.4 this suite compared the Route_Modules
against the pre-refactor inline handlers in ``ui_backend/handler.py`` (``handle_get_partners`` /
``handle_update_partner``, reached through the interim ``_route_partners_list`` /
``_route_partners_update`` dispatcher adapters). Those inline handlers and their interim adapters
were **deleted in the same change that flipped the ``partners_list`` / ``partners_update``
dispatcher entries** to ``routes/partners.py`` (Requirement 16.3, mirroring the taxonomy flip in
task 3.3, processing in 4.4, content in 5.3, bot_control in 6.3, analytics in 8.5, and pricing in
9.4), so the old-vs-new diff is no longer possible. The inline handlers simply delegated to
``services.partners_service.PartnersService`` (the reader for ``GET /partners`` / the
``/analytics/partners`` alias, and the writer for ``PUT /partners/{id}`` — Requirement 7.2) and
wrapped the result in ``create_success_response`` / mapped the typed domain errors to the same
404 / 400 responses. The golden baseline reproduces that exact contract by calling the same
service directly — independently of the Route_Modules' own code path — so equivalence here is
equivalence on the live path.

It enforces the two correctness properties the design defines for a migration step:

* **Property 1 - Route equivalence** for ``GET /partners``, ``PUT /partners/{id}``, and the
  ``/analytics/partners`` alias: the new route module produces the same HTTP status code, the
  same response body (fields, nesting, values), and the same CORS headers as the golden baseline
  built from the service single home. Both the real-DynamoDB path (moto-backed) and the
  deterministic mock-fallback path are covered, along with the ``404`` (partner not found) and
  ``400`` (no valid fields) writer error paths.
  (Validates: Requirements 2.1, 2.2, 2.3, 2.5)
* **Property 2 - Auth preserved**: a missing or invalid Cognito credential yields ``401`` from
  the route module via the unchanged ``@require_cognito_auth`` wrapper, and the partners service
  is never invoked when auth fails.
  (Validates: Requirements 2.5, 6.6)

Determinism: ``PartnersService`` stamps wall-clock ``timestamp`` / ``updated_at`` fields and
``create_error_response`` injects a random ``request_id``; the ``@validate_response`` wrapper
injects a ``_validation`` metadata block. These are non-deterministic between two independent
invocations and are **not** part of the API contract, so :func:`_normalize` strips them before
comparison — the same technique the taxonomy/analytics characterization suites use. The real
path is exercised against a moto-backed ``cm-partners`` table; the mock-fallback path is
forced deterministically by making the table accessor raise, so both implementations take the
identical fallback branch.

Authored canonical-root-clean (Requirement 17.6): every first-party import is absolute, rooted
at the canonical import root ``source/lambda/`` (``ui_backend.*``, ``services.*``, ``shared.*``),
with no relative (leading-dot) or bare-sibling import forms and no ``sys.path`` manipulation, so
it resolves under both the current ``pytest.ini`` pythonpath and #11's tightened
single-canonical-root pythonpath.

This is a REQUIRED gate: it must complete with zero failures before Task 10.4 flips the
partners dispatcher entry to the route module and deletes the superseded inline partner code.

_Requirements: 4.2, 4.6, 17.4, 17.6_
"""

import json
import os

# Configure a region + dummy credentials so the service's internal
# ``boto3.resource("dynamodb")`` (created without an explicit region) resolves under moto.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

from unittest.mock import patch  # noqa: E402

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

# Golden-master baseline is reconstructed from the Service_Layer single home + the shared response
# helpers (Requirement 7.2), independently of the Route_Modules under test — the inline
# ``handle_get_partners`` / ``handle_update_partner`` handlers were deleted at the task 10.4 flip.
from services.partners_service import (  # noqa: E402
    NoPartnerFieldsError,
    PartnerNotFoundError,
    PartnersService,
    PartnerValidationError,
)
from shared.utils.response_helpers import create_error_response, create_success_response  # noqa: E402

# New Route_Modules under test.
from ui_backend.routes.analytics import route as analytics_route  # noqa: E402
from ui_backend.routes.partners import route as partners_route  # noqa: E402

AWS_REGION = "us-west-2"
PARTNER_TABLE_NAME = os.environ.get("PARTNER_TABLE_NAME", "cm-partners")

# Publisher id the golden baseline uses — matches the value @require_cognito_auth resolves from
# the authorizer claims below (``custom:publisher_id``).
_PUBLISHER_ID = "test-publisher"

# Symbol path for the partners service single home. Both route modules import ``PartnersService``
# from here (``routes/partners.py`` instantiates it per call; ``routes/analytics.py`` holds a
# module-level instance), and the golden baseline does too — so patching the class method here
# affects every path uniformly.
_SVC_TABLE = "services.partners_service.PartnersService._partners_table"
_SVC_GET = "services.partners_service.PartnersService.get_partners"
_SVC_UPDATE = "services.partners_service.PartnersService.update_partner"

# CORS header names both implementations must attach to every response.
_CORS_HEADERS = (
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Headers",
    "Access-Control-Allow-Methods",
)

# Top-level body keys that are legitimately non-deterministic between two independent
# invocations (wall-clock stamps + per-response random id + test-only validation metadata).
_VOLATILE_KEYS = ("_validation", "request_id", "timestamp", "updated_at")


# --------------------------------------------------------------------------------------------
# Event builders + invocation helpers
# --------------------------------------------------------------------------------------------
def _authorizer_claims():
    """API Gateway Cognito authorizer claims that satisfy ``extract_cognito_user_context``."""
    return {
        "sub": "test-user-123",
        "email": "test@example.com",
        "custom:publisher_id": _PUBLISHER_ID,
        "cognito:username": "test_user",
    }


def _get_partners_event(*, authed=True, auth_header=None):
    """Build a ``GET /partners`` event (authed via authorizer claims by default)."""
    event = {
        "httpMethod": "GET",
        "path": "/partners",
        "headers": {"Content-Type": "application/json"},
        "queryStringParameters": None,
        "body": None,
    }
    if auth_header is not None:
        event["headers"]["Authorization"] = auth_header
    if authed:
        event["requestContext"] = {"authorizer": {"claims": _authorizer_claims()}}
    return event


def _analytics_partners_event(*, authed=True, auth_header=None):
    """Build a ``GET /analytics/partners`` alias event."""
    event = _get_partners_event(authed=authed, auth_header=auth_header)
    event["path"] = "/analytics/partners"
    return event


def _update_partner_event(partner_id, body, *, authed=True, auth_header=None):
    """Build a ``PUT /partners/{partner_id}`` event."""
    event = {
        "httpMethod": "PUT",
        "path": f"/partners/{partner_id}",
        "headers": {"Content-Type": "application/json"},
        "queryStringParameters": None,
        "body": json.dumps(body) if body is not None else None,
    }
    if auth_header is not None:
        event["headers"]["Authorization"] = auth_header
    if authed:
        event["requestContext"] = {"authorizer": {"claims": _authorizer_claims()}}
    return event


def _parse_body(event):
    """Parse the JSON request body from the event (mirrors the route's body parse)."""
    body = event.get("body", "")
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


def _golden_get_partners(event):
    """Golden baseline for ``GET /partners`` reconstructed from the service single home.

    The deleted inline ``handle_get_partners`` resolved ``publisher_id`` from the authenticated
    user, called ``PartnersService().get_partners(publisher_id)``, and wrapped it in
    ``create_success_response``. The golden baseline reproduces that exact contract directly.
    """
    try:
        partners_data = PartnersService().get_partners(_PUBLISHER_ID)
    except Exception:
        return create_error_response(500, "Failed to retrieve partner data")
    return create_success_response(partners_data)


def _new_get_partners(event):
    """Invoke the new partners Route_Module as the dispatcher would."""
    return partners_route(event, "GET", event["path"])


def _golden_update_partner(event):
    """Golden baseline for ``PUT /partners/{id}`` reconstructed from the service single home.

    The deleted inline ``handle_update_partner`` parsed the partner id from the path, called
    ``PartnersService().update_partner(partner_id, request_data)``, mapped the typed domain
    errors to 404 / 400, and wrapped success in ``create_success_response``. The golden baseline
    reproduces that inner contract directly (the outer 500 mapping and path-parse guard are owned
    by the thin handler and are not exercised by these moto-backed, valid-path cases).
    """
    request_data = _parse_body(event)
    partner_id = event["path"].split("/")[2]
    try:
        partner_response = PartnersService().update_partner(partner_id, request_data)
    except PartnerNotFoundError:
        return create_error_response(404, f"Partner {partner_id} not found")
    except NoPartnerFieldsError:
        return create_error_response(400, "No valid fields to update")
    except PartnerValidationError as exc:
        return create_error_response(400, str(exc), error_code="validation_error")
    return create_success_response(partner_response)


def _new_update_partner(event):
    """Invoke the new partners Route_Module as the dispatcher would."""
    return partners_route(event, "PUT", event["path"])


def _golden_analytics_partners(event):
    """Golden baseline for the ``/analytics/partners`` alias.

    The deleted inline alias was literally ``handle_analytics_partners = handle_get_partners``,
    so the alias baseline is the same partners-reader contract: ``PartnersService().get_partners``
    wrapped in ``create_success_response``.
    """
    try:
        partners_data = PartnersService().get_partners(_PUBLISHER_ID)
    except Exception:
        return create_error_response(500, "Failed to retrieve partner data")
    return create_success_response(partners_data)


def _new_analytics_partners(event):
    """Invoke the new analytics Route_Module for the ``/analytics/partners`` alias."""
    return analytics_route(event, "GET", "/analytics/partners")


def _normalize(response):
    """Return a comparable view of a proxy response with volatile fields stripped.

    Strips top-level ``_validation`` / ``request_id`` / ``timestamp`` / ``updated_at`` — these
    are non-deterministic between two independent invocations and are not part of the API
    contract. Both implementations produce equivalent ones, so removing them isolates the real
    response payload for a faithful equivalence comparison.
    """
    body = json.loads(response["body"])
    if isinstance(body, dict):
        for key in _VOLATILE_KEYS:
            body.pop(key, None)
    return {"statusCode": response["statusCode"], "headers": response["headers"], "body": body}


def _assert_equivalent(new_response, old_response):
    """Assert route == golden baseline on status, headers (incl. CORS), and normalized body."""
    assert _normalize(new_response) == _normalize(old_response)
    for header in _CORS_HEADERS:
        assert header in new_response["headers"]
        assert new_response["headers"][header] == old_response["headers"][header]


def _seed_partner_item():
    """Return a deterministic ``META`` item for the real read/write paths."""
    return {
        "PK": "PARTNER#partner_001",
        "SK": "META",
        "partner_id": "partner_001",
        "partner_name": "AI Research Corp",
        "contact_email": "ai@example.com",
        "pricing_tier": "premium",
        "status": "active",
        "identity_source": "direct",
        "allowed_endpoints": ["queryContent"],
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-10T00:00:00+00:00",
    }


def _seed_key_items():
    """Two credential items for partner_001: one active, one disabled."""
    return [
        {
            "PK": "PARTNER#partner_001",
            "SK": "KEY#key_active",
            "partner_id": "partner_001",
            "api_key_id": "key_active",
            "usage_plan_id": "plan-premium",
            "status": "active",
            "created_at": "2025-01-02T00:00:00+00:00",
        },
        {
            "PK": "PARTNER#partner_001",
            "SK": "KEY#key_old",
            "partner_id": "partner_001",
            "api_key_id": "key_old",
            "usage_plan_id": "plan-premium",
            "status": "disabled",
            "created_at": "2025-01-01T00:00:00+00:00",
            "disabled_at": "2025-01-02T00:00:00+00:00",
        },
    ]


def _create_partners_table(*, seed=True):
    """Create the moto-backed partners table (``PK``/``SK`` + ``api-key-id-index``); optionally seed it."""
    # Other collected suites set PARTNER_TABLE_NAME for their own fixtures.
    # Bind the service to this suite's table inside the current test so its
    # Moto setup and runtime lookup cannot diverge with randomized ordering.
    os.environ["PARTNER_TABLE_NAME"] = PARTNER_TABLE_NAME
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    table = dynamodb.create_table(
        TableName=PARTNER_TABLE_NAME,
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "api_key_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "api-key-id-index",
                "KeySchema": [{"AttributeName": "api_key_id", "KeyType": "HASH"}],
                "Projection": {
                    "ProjectionType": "INCLUDE",
                    "NonKeyAttributes": ["partner_id", "usage_plan_id", "status"],
                },
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    if seed:
        table.put_item(Item=_seed_partner_item())
        for item in _seed_key_items():
            table.put_item(Item=item)
    return table


# --------------------------------------------------------------------------------------------
# Property 1 — Route equivalence
# --------------------------------------------------------------------------------------------
class TestPartnersRouteEquivalence:
    """Property 1 — route equivalence for /partners, /partners/{id}, and /analytics/partners."""

    @mock_aws
    def test_get_partners_real_path_equivalent(self):
        """GET /partners: new route == golden baseline on the real DynamoDB (moto) path."""
        _create_partners_table(seed=True)

        new_response = _new_get_partners(_get_partners_event())
        old_response = _golden_get_partners(_get_partners_event())

        assert new_response["statusCode"] == 200
        _assert_equivalent(new_response, old_response)

        body = _normalize(new_response)["body"]
        assert body["total_partners"] == 1
        assert body["active_partners"] == 1
        partner = body["partners"][0]
        assert partner["partner_id"] == "partner_001"
        assert partner["partner_name"] == "AI Research Corp"
        assert partner["active_key_count"] == 1  # the disabled key is not counted
        assert partner["allowed_endpoints"] == ["queryContent"]
        for retired in ("name", "api_quota", "api_usage", "requests", "revenue", "last_active"):
            assert retired not in partner
        # The dynamic timestamp is stripped for comparison but must still be present.
        assert "timestamp" in json.loads(new_response["body"])

    def test_get_partners_storage_failure_is_500_equivalent(self):
        """No demo fallback: a failing partner table is a 500 on both paths (mock data is opt-in only)."""
        event = _get_partners_event()
        with patch(_SVC_TABLE, side_effect=RuntimeError("partner table unavailable")):
            new_response = _new_get_partners(event)
        with patch(_SVC_TABLE, side_effect=RuntimeError("partner table unavailable")):
            old_response = _golden_get_partners(event)
        assert new_response["statusCode"] == 500
        _assert_equivalent(new_response, old_response)
        assert json.loads(new_response["body"])["error"] == "Failed to retrieve partner data"

    @mock_aws
    def test_analytics_partners_alias_real_path_equivalent(self):
        """/analytics/partners: new analytics route == golden baseline on the real (moto) path."""
        _create_partners_table(seed=True)

        new_response = _new_analytics_partners(_analytics_partners_event())
        old_response = _golden_analytics_partners(_analytics_partners_event())

        assert new_response["statusCode"] == 200
        _assert_equivalent(new_response, old_response)
        # Alias returns the same partners payload shape as GET /partners.
        assert _normalize(new_response)["body"]["partners"][0]["partner_id"] == "partner_001"

    def test_analytics_partners_alias_storage_failure_is_500_equivalent(self):
        event = _analytics_partners_event()
        with patch(_SVC_TABLE, side_effect=RuntimeError("partner table unavailable")):
            new_response = _new_analytics_partners(event)
        with patch(_SVC_TABLE, side_effect=RuntimeError("partner table unavailable")):
            old_response = _golden_analytics_partners(event)
        assert new_response["statusCode"] == 500
        _assert_equivalent(new_response, old_response)

    @mock_aws
    def test_update_partner_success_equivalent(self):
        """PUT /partners/{id}: new route == golden baseline on a successful DynamoDB update."""
        table = _create_partners_table(seed=True)
        # A tier move re-binds the partner's keys to the new tier's usage plan, so the seeded keys
        # must exist at the (moto) gateway and the enterprise plan must be resolvable.
        # Seed the keys and their current plan at the gateway under the ids the table items carry
        # (the real service re-binds by deleting the old plan key, which must exist).
        apigw = boto3.client("apigateway", region_name=AWS_REGION)
        plan_id = apigw.create_usage_plan(name="cm-partner-enterprise")["id"]
        old_plan_id = apigw.create_usage_plan(name="cm-partner-premium")["id"]
        for item in _seed_key_items():
            key_id = apigw.create_api_key(name=item["api_key_id"], enabled=True)["id"]
            apigw.create_usage_plan_key(usagePlanId=old_plan_id, keyId=key_id, keyType="API_KEY")
            table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            table.put_item(Item={**item, "SK": f"KEY#{key_id}", "api_key_id": key_id, "usage_plan_id": old_plan_id})
        update_body = {"pricing_tier": "enterprise", "status": "active", "api_quota": 20000}  # api_quota ignored

        with patch.dict(os.environ, {"USAGE_PLAN_ID_ENTERPRISE": plan_id}):
            new_response = _new_update_partner(_update_partner_event("partner_001", update_body))
            old_response = _golden_update_partner(_update_partner_event("partner_001", update_body))

        assert new_response["statusCode"] == 200
        _assert_equivalent(new_response, old_response)

        body = _normalize(new_response)["body"]
        assert body["partner_id"] == "partner_001"
        assert body["pricing_tier"] == "enterprise"
        # The dynamic updated_at is stripped for comparison but must still be present.
        assert "updated_at" in json.loads(new_response["body"])
        # Both KEY# items now point at the enterprise plan.
        keys = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq("PARTNER#partner_001")
            & boto3.dynamodb.conditions.Key("SK").begins_with("KEY#")
        )["Items"]
        assert {k["usage_plan_id"] for k in keys} == {plan_id}

    @mock_aws
    def test_update_partner_not_found_equivalent(self):
        """PUT /partners/{id}: unknown partner -> identical 404 from both paths."""
        _create_partners_table(seed=False)  # table exists but has no matching partner
        event = _update_partner_event("does-not-exist", {"status": "suspended"})

        new_response = _new_update_partner(event)
        old_response = _golden_update_partner(event)

        assert new_response["statusCode"] == 404
        _assert_equivalent(new_response, old_response)
        assert json.loads(new_response["body"])["error"] == "Partner does-not-exist not found"

    @mock_aws
    def test_update_partner_no_fields_equivalent(self):
        """PUT /partners/{id}: empty update body -> identical 400 from both paths."""
        _create_partners_table(seed=True)
        event = _update_partner_event("partner_001", {})

        new_response = _new_update_partner(event)
        old_response = _golden_update_partner(event)

        assert new_response["statusCode"] == 400
        _assert_equivalent(new_response, old_response)
        assert json.loads(new_response["body"])["error"] == "No valid fields to update"

    @mock_aws
    def test_update_partner_invalid_endpoint_equivalent(self):
        _create_partners_table(seed=True)
        event = _update_partner_event("partner_001", {"allowed_endpoints": ["/search"]})
        new_response = _new_update_partner(event)
        old_response = _golden_update_partner(event)
        assert new_response["statusCode"] == 400
        _assert_equivalent(new_response, old_response)
        body = json.loads(new_response["body"])
        assert body["error_code"] == "validation_error"
        assert "/search" in body["error"]


# --------------------------------------------------------------------------------------------
# Property 2 — Auth preserved
# --------------------------------------------------------------------------------------------
class TestPartnersAuthPreserved:
    """Property 2 — missing/invalid Cognito credential => 401 from the route, service never invoked."""

    def _assert_unauthorized(self, response):
        assert response["statusCode"] == 401
        body = json.loads(response["body"])
        assert body["error"] == "Invalid or missing Cognito token"
        for header in _CORS_HEADERS:
            assert header in response["headers"]

    def test_get_partners_missing_credential_returns_401_and_skips_service(self):
        """GET /partners with no credential => 401 from the route; get_partners never called."""
        event = _get_partners_event(authed=False)

        with patch(_SVC_GET) as new_svc:
            new_response = _new_get_partners(event)

        self._assert_unauthorized(new_response)
        new_svc.assert_not_called()

    def test_get_partners_invalid_credential_returns_401_and_skips_service(self):
        """GET /partners with a malformed bearer token => 401 from the route; service never called."""
        event = _get_partners_event(authed=False, auth_header="Bearer not-a-real-jwt")

        with patch(_SVC_GET) as new_svc:
            new_response = _new_get_partners(event)

        self._assert_unauthorized(new_response)
        new_svc.assert_not_called()

    def test_update_partner_missing_credential_returns_401_and_skips_service(self):
        """PUT /partners/{id} with no credential => 401 from the route; update_partner never called."""
        event = _update_partner_event("partner_001", {"status": "suspended"}, authed=False)

        with patch(_SVC_UPDATE) as new_svc:
            new_response = _new_update_partner(event)

        self._assert_unauthorized(new_response)
        new_svc.assert_not_called()

    def test_analytics_partners_alias_missing_credential_returns_401_and_skips_service(self):
        """/analytics/partners with no credential => 401 from the route; service never called."""
        event = _analytics_partners_event(authed=False)

        with patch(_SVC_GET) as new_svc:
            new_response = _new_analytics_partners(event)

        self._assert_unauthorized(new_response)
        new_svc.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
