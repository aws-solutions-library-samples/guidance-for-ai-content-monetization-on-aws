"""Shared config-discovery and fixture helpers for the partner-plane integration tests
(task 10.3 / 10.3a / 12.1).

Mirrors the discovery pattern in ``tests/integration/test_connections_api.py``: env overrides win,
else ``ContentMonetizationStack`` CloudFormation outputs; the partner key VALUE is read from API
Gateway on demand and never stored. These are plain helpers (not fixtures) so both the plane and
quota modules can share them, along with the throwaway-partner provisioning and the propagation
polling both modules need.
"""

import os
import time
import uuid
from datetime import datetime, timezone

import boto3
import pytest
import requests

RUN = os.environ.get("RUN_INTEGRATION") == "1"
REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE")
MAIN_STACK = "ContentMonetizationStack"

# WAF blocks the default python-requests UA; present as a browser (see test_api.py / test_connections_api.py).
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# The gateway rejects a key it does not (yet) recognize with this customized body (task 6.3a). It
# is the signature of edge propagation, not of a Lambda verdict: a key is not live at the edge the
# moment ``create_usage_plan_key`` returns. AWS documents the window as "a few minutes"
# (https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-api-usage-plans.html),
# and the association is not monotonic across edge nodes, so a key seen as live by one request can
# still be rejected by the next. Tests therefore poll for the verdict they assert on and assert on
# that same response.
GATEWAY_REJECT = "invalid_api_key"
# In the same window the edge can also accept a key yet leave ``requestContext.identity.apiKeyId``
# out of the event, so partner resolution sees no key and the handler answers its generic
# ``401 authentication_required``. Same lag, different shape: it must not settle a poll either.
UNBOUND_KEY_CODE = "authentication_required"
# The handler's "search has nothing projectable" verdict. It is only reachable from inside
# ``handle_query_request``, which is decorated with ``require_partner_or_cognito_auth``, so a 503
# carrying it proves the request passed the key gate AND partner resolution -- the same reasoning
# ``test_valid_key_is_authorized`` relies on. It therefore has to settle a poll like a 200 does:
# polling past it would discard the one shape that proves an admitted request.
SEARCH_UNAVAILABLE = "search_unavailable"
PROPAGATION_DEADLINE_S = 240
POLL_DELAY_S = 5
HTTP_TIMEOUT_S = 45


def session():
    if PROFILE:
        return boto3.Session(profile_name=PROFILE, region_name=REGION)
    return boto3.Session(region_name=REGION)


def stack_outputs(stack_name=MAIN_STACK):
    stack = session().client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def partner_key_value(outputs):
    """Resolve the seeded test partner key value from its id output (never stored). None if absent."""
    value = os.environ.get("TEST_PARTNER_KEY") or os.environ.get("PARTNER_API_KEY")
    if value:
        return value
    key_id = os.environ.get("TEST_PARTNER_API_KEY_ID") or outputs.get("TestPartnerApiKeyId")
    if not key_id:
        return None
    try:
        return session().client("apigateway").get_api_key(apiKey=key_id, includeValue=True)["value"]
    except Exception:  # noqa: BLE001
        return None


def json_body(response):
    """Parsed JSON body, or ``{}`` when the body is not JSON (uncustomized gateway errors)."""
    try:
        return response.json()
    except ValueError:
        return {}


def poll_for_verdict(
    http,
    url,
    key_value,
    body,
    settled,
    *,
    deadline_s=PROPAGATION_DEADLINE_S,
    delay=POLL_DELAY_S,
    timeout=HTTP_TIMEOUT_S,
    history=None,
):
    """POST ``url`` with ``key_value`` until ``settled(status, parsed_body)``; return one observation.

    Returns ``(response, parsed_body)`` for the request that settled, so the caller asserts status
    and body against the *same* request. Polling with one request and asserting on another is the
    race this replaces: the key can propagate between the two, or stop being recognized again.
    When the deadline passes unsettled the last observation is returned anyway, so the caller's
    assertion reports what the edge actually said instead of skipping.

    ``history``, when a list is passed, collects ``(status, error_code)`` for every observation the
    poll made (``(None, <exception class>)`` for a transport failure). Without it the pre-settle
    responses are lost, and they are the evidence a caller needs to tell one failure cause from
    another.
    """
    deadline = time.monotonic() + deadline_s
    last = None
    while True:
        try:
            response = http.post(url, json=body, headers={"x-api-key": key_value}, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            # A transport blip (DNS, TLS, a reset at the edge) is as transient as the 5xx the
            # predicates already poll past; erroring the whole case on one is noise. It only
            # surfaces when the poll never managed to observe a response at all.
            if history is not None:
                history.append((None, type(exc).__name__))
            if time.monotonic() >= deadline:
                if last is None:
                    raise
                return last
            time.sleep(delay)
            continue
        parsed = json_body(response)
        last = (response, parsed)
        if history is not None:
            history.append((response.status_code, parsed.get("error_code")))
        if settled(response.status_code, parsed) or time.monotonic() >= deadline:
            return last
        time.sleep(delay)


def is_edge_propagation(status, parsed):
    """True for the shapes a freshly provisioned key answers before the edge has fully bound it."""
    if status == 403:
        return parsed.get("error_code") == GATEWAY_REJECT
    return status == 401 and parsed.get("error_code") == UNBOUND_KEY_CODE


def verdict_or_retry(expected_error_code, expected_error=None):
    """``settled`` predicate accepting only the expected ``403`` verdict from the handler.

    A ``403`` settles only on ``expected_error_code`` — and, when ``expected_error`` is given, only
    on that message too, because ``partner_inactive`` has five producers and only one of them is
    the property a caller asserts; the others (lookup failure, unmapped key, disabled credential,
    missing ``META``) are the binding/lag shapes, so they keep the poll going. Any other ``403``
    (the edge has not associated the key with the usage plan yet) is likewise not-yet-settled.

    A ``5xx`` settles only as ``search_unavailable``: that one comes from inside the authenticated
    handler, so it is an *admitted* request and the caller must see it. Every other ``5xx`` is an
    infrastructure blip. Every other status settles at once, so a wrong verdict reaches the
    caller's assertion instead of being polled away.
    """

    def settled(status, parsed):
        if is_edge_propagation(status, parsed):
            return False
        if status >= 500:
            return parsed.get("error_code") == SEARCH_UNAVAILABLE
        if status == 403:
            if parsed.get("error_code") != expected_error_code:
                return False
            return expected_error is None or parsed.get("error") == expected_error
        return True

    return settled


def usage_plan_by_name(apigw, name):
    """Return the usage plan named ``name``, skipping the test when it is not deployed."""
    for page in apigw.get_paginator("get_usage_plans").paginate():
        for plan in page.get("items", []):
            if plan.get("name") == name:
                return plan
    pytest.skip(f"{name} usage plan not found")


class ThrowawayPartner:
    """Context manager: provision a partner META + enabled key on ``plan_name``, delete on exit.

    Yields the key VALUE. ``quota_limit`` exposes the resolved plan's daily quota so a quota test
    does not have to look the plan up again.
    """

    def __init__(self, outputs, *, status, allowed_endpoints, plan_name="cm-partner-basic"):
        self.outputs = outputs
        self.status = status
        self.allowed_endpoints = allowed_endpoints
        self.plan_name = plan_name
        self.partner_id = f"itest-{uuid.uuid4().hex[:10]}"
        self.key_id = None
        self.quota_limit = None

    def __enter__(self):
        table_name = self.outputs.get("PartnerTableName", "cm-partners")
        self.table = session().resource("dynamodb").Table(table_name)
        self.apigw = session().client("apigateway")
        plan = usage_plan_by_name(self.apigw, self.plan_name)
        plan_id = plan["id"]
        self.quota_limit = int(plan.get("quota", {}).get("limit") or 0)
        # Python skips ``__exit__`` when ``__enter__`` raises, so a throttled ``put_item`` or a
        # missing IAM action after the key exists would leak an ENABLED key, its usage-plan
        # association and possibly the partner rows -- once per run. Roll back by hand; the
        # teardown is best-effort and None-safe on ``key_id``.
        try:
            key = self.apigw.create_api_key(name=f"{self.partner_id}-key", enabled=True)
            self.key_id = key["id"]
            self.apigw.create_usage_plan_key(usagePlanId=plan_id, keyId=self.key_id, keyType="API_KEY")
            now = datetime.now(timezone.utc).isoformat()
            self.table.put_item(
                Item={
                    "PK": f"PARTNER#{self.partner_id}",
                    "SK": "META",
                    "partner_id": self.partner_id,
                    "partner_name": "itest throwaway",
                    "pricing_tier": "basic",
                    "status": self.status,
                    "allowed_endpoints": self.allowed_endpoints,
                    "identity_source": "direct",
                    "created_at": now,
                    "updated_at": now,
                }
            )
            self.table.put_item(
                Item={
                    "PK": f"PARTNER#{self.partner_id}",
                    "SK": f"KEY#{self.key_id}",
                    "partner_id": self.partner_id,
                    "api_key_id": self.key_id,
                    "usage_plan_id": plan_id,
                    "status": "active",
                    "created_at": now,
                }
            )
        except BaseException:
            self.__exit__()
            raise
        return key["value"]

    def __exit__(self, *exc):
        if self.key_id:
            try:
                self.apigw.delete_api_key(apiKey=self.key_id)
            except Exception:  # noqa: BLE001
                pass
        for sk in ("META", f"KEY#{self.key_id}"):
            try:
                self.table.delete_item(Key={"PK": f"PARTNER#{self.partner_id}", "SK": sk})
            except Exception:  # noqa: BLE001
                pass
        return False
