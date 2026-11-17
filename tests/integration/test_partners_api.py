"""Deployed partner lifecycle through the internal (Cognito) plane (spec: native-apikey-auth-migration, 10.2).

Exercises the seven ``/partners`` operations end to end against a deployed ``ContentMonetizationStack``:
create (direct identity, key value returned once) → get (keys + tier limits) → update tier (keys
re-bound) → suspend / reactivate → rotate (old key disabled) → retire (refused for the only active
key, allowed for the rotated-out one) → delete (soft-delete + keys gone). The provisioned key is
exercised against the partner plane to prove it is live and that rotation revokes it.

Gated on ``RUN_INTEGRATION=1``; skips (never errors) without a deployment, Cognito config or
``COGNITO_PASSWORD`` (the password is never hardcoded). Every partner it creates is deleted in
``finally`` so a failed run leaves no gateway keys behind.
"""

import os
import time

import boto3
import pytest
import requests

RUN = os.environ.get("RUN_INTEGRATION") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
PROFILE = os.environ.get("AWS_PROFILE")
STACK_NAME = os.environ.get("TEST_STACK_NAME", "ContentMonetizationStack")
COGNITO_STACK_NAME = os.environ.get("TEST_COGNITO_STACK_NAME", "CMCognitoStack")
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")
BROWSER_UA = "Mozilla/5.0 (integration-test) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def _session():
    return boto3.Session(profile_name=PROFILE, region_name=REGION) if PROFILE else boto3.Session(region_name=REGION)


def _stack_outputs(stack_name):
    try:
        stack = _session().client("cloudformation").describe_stacks(StackName=stack_name)["Stacks"][0]
    except Exception:  # noqa: BLE001 - no deployment -> skip
        return {}
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


@pytest.fixture(scope="module")
def outputs():
    return {**_stack_outputs(COGNITO_STACK_NAME), **_stack_outputs(STACK_NAME)}


@pytest.fixture(scope="module")
def api_url(outputs):
    url = os.environ.get("TEST_API_URL") or outputs.get("ApiGatewayUrl")
    if not url:
        pytest.skip("no API URL: set TEST_API_URL or deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def partner_api_url(outputs):
    url = os.environ.get("PARTNER_API_URL") or outputs.get("PartnerApiUrl")
    if not url:
        pytest.skip("no PartnerApiUrl output")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def id_token(outputs):
    if not COGNITO_PASSWORD:
        pytest.skip("set COGNITO_PASSWORD to run Cognito-authed tests (password is never hardcoded)")
    user_pool_id = os.environ.get("COGNITO_USER_POOL_ID") or outputs.get("UserPoolId")
    client_id = os.environ.get("COGNITO_CLIENT_ID") or outputs.get("UserPoolClientId")
    if not user_pool_id or not client_id:
        pytest.skip("no Cognito config: set COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID or deploy the stack")
    try:
        resp = _session().client("cognito-idp").admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": COGNITO_USERNAME, "PASSWORD": COGNITO_PASSWORD},
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Cognito auth failed for {COGNITO_USERNAME}: {exc}")
    token = resp.get("AuthenticationResult", {}).get("IdToken")
    if not token:
        pytest.skip(f"Cognito auth returned no IdToken (challenge: {resp.get('ChallengeName')})")
    return token


@pytest.fixture(scope="module")
def http(id_token):
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA, "Authorization": f"Bearer {id_token}", "Content-Type": "application/json"})
    yield session
    session.close()


def _partner_call(partner_api_url, key_value, timeout=20):
    """POST /query with a partner key; returns the status code (authorization is what we assert)."""
    return requests.post(
        f"{partner_api_url}/query",
        headers={"x-api-key": key_value, "Content-Type": "application/json", "User-Agent": BROWSER_UA},
        json={"query": "integration lifecycle"},
        timeout=timeout,
    ).status_code


def _wait_for(predicate, *, attempts=12, delay=5):
    """API Gateway key/plan propagation is eventually consistent; poll a few times."""
    last = None
    for _ in range(attempts):
        last = predicate()
        if last:
            return last
        time.sleep(delay)
    return last


class TestPartnerLifecycle:
    def test_create_get_update_rotate_retire_delete(self, http, api_url, partner_api_url):
        partner_id = None
        try:
            # create: direct identity → one key, value shown once
            r = http.post(f"{api_url}/partners", json={
                "partner_name": "IT Lifecycle Partner", "pricing_tier": "basic",
                "contact_email": "lifecycle@example.test", "allowed_endpoints": ["queryContent"],
            }, timeout=30)
            assert r.status_code == 201, r.text
            created = r.json()
            partner_id = created["partner"]["partner_id"]
            first_key, first_value = created["key"]["key_id"], created["key_value"]
            assert first_value and created["partner"]["active_key_count"] == 1

            # the fresh key is live on the partner plane (authorized: anything but 401/403)
            assert _wait_for(lambda: _partner_call(partner_api_url, first_value) not in (401, 403)), "new key never authorized"

            # get: detail with keys and tier limits, never a key value
            r = http.get(f"{api_url}/partners/{partner_id}", timeout=30)
            assert r.status_code == 200, r.text
            detail = r.json()
            assert [k["key_id"] for k in detail["keys"]] == [first_key]
            assert detail["tier_limits"]["quota_period"] in ("DAY", "WEEK", "MONTH") and detail["tier_limits"]["quota_limit"] > 0
            usage = detail["keys"][0]["usage"]
            assert usage is not None and usage["period_start"] <= usage["period_end"]
            assert usage["used"] + usage["remaining"] == detail["tier_limits"]["quota_limit"]
            assert "key_value" not in r.text
            basic_plan = detail["keys"][0]["usage_plan_id"]

            # update tier: key re-bound to the new tier's plan
            r = http.put(f"{api_url}/partners/{partner_id}", json={"pricing_tier": "standard"}, timeout=30)
            assert r.status_code == 200 and r.json()["pricing_tier"] == "standard", r.text
            detail = http.get(f"{api_url}/partners/{partner_id}", timeout=30).json()
            assert detail["keys"][0]["usage_plan_id"] != basic_plan

            # suspend: key disabled at the gateway → 403; reactivate restores it
            assert http.put(f"{api_url}/partners/{partner_id}", json={"status": "suspended"}, timeout=30).status_code == 200
            assert _wait_for(lambda: _partner_call(partner_api_url, first_value) == 403), "suspended key still authorized"
            assert http.put(f"{api_url}/partners/{partner_id}", json={"status": "active"}, timeout=30).status_code == 200
            assert _wait_for(lambda: _partner_call(partner_api_url, first_value) not in (401, 403)), "reactivated key not restored"

            # retire the only active key → 409 conflict
            r = http.delete(f"{api_url}/partners/{partner_id}/keys/{first_key}", timeout=30)
            assert r.status_code == 409 and r.json()["error_code"] == "conflict", r.text

            # rotate: new key live, old key revoked
            r = http.post(f"{api_url}/partners/{partner_id}/keys", timeout=30)
            assert r.status_code == 201, r.text
            rotated = r.json()
            second_key, second_value = rotated["key"]["key_id"], rotated["key_value"]
            assert second_key != first_key and second_value
            assert _wait_for(lambda: _partner_call(partner_api_url, second_value) not in (401, 403)), "rotated key never authorized"
            assert _wait_for(lambda: _partner_call(partner_api_url, first_value) == 403), "old key still authorized after rotation"
            detail = http.get(f"{api_url}/partners/{partner_id}", timeout=30).json()
            assert {k["key_id"]: k["status"] for k in detail["keys"]} == {first_key: "disabled", second_key: "active"}

            # retire the rotated-out key → 204 and gone
            assert http.delete(f"{api_url}/partners/{partner_id}/keys/{first_key}", timeout=30).status_code == 204
            detail = http.get(f"{api_url}/partners/{partner_id}", timeout=30).json()
            assert [k["key_id"] for k in detail["keys"]] == [second_key]

            # delete: soft-deleted, keys removed, remaining key revoked
            r = http.delete(f"{api_url}/partners/{partner_id}", timeout=30)
            assert r.status_code == 200 and r.json()["status"] == "deleted", r.text
            assert _wait_for(lambda: _partner_call(partner_api_url, second_value) == 403), "key still authorized after delete"
            assert http.delete(f"{api_url}/partners/{partner_id}", timeout=30).status_code == 404
            partner_id = None  # nothing left to clean up
        finally:
            if partner_id:
                http.delete(f"{api_url}/partners/{partner_id}", timeout=30)

    def test_validation_and_marketplace(self, http, api_url):
        r = http.post(f"{api_url}/partners", json={"partner_name": "x"}, timeout=30)
        assert r.status_code == 400 and r.json()["error_code"] == "validation_error", r.text
        partner_id = None
        try:
            r = http.post(f"{api_url}/partners", json={
                "partner_name": "IT Marketplace Partner", "pricing_tier": "basic",
                "identity_source": "marketplace", "marketplace_type": "aws",
            }, timeout=30)
            assert r.status_code == 201, r.text
            body = r.json()
            partner_id = body["partner"]["partner_id"]
            assert body["key"] is None and body["key_value"] is None
            r = http.post(f"{api_url}/partners/{partner_id}/keys", timeout=30)
            assert r.status_code == 409 and r.json()["error_code"] == "conflict", r.text
        finally:
            if partner_id:
                http.delete(f"{api_url}/partners/{partner_id}", timeout=30)

    def test_requires_cognito(self, api_url):
        r = requests.post(f"{api_url}/partners", json={"partner_name": "n", "pricing_tier": "basic"},
                          headers={"User-Agent": BROWSER_UA, "Content-Type": "application/json"}, timeout=30)
        assert r.status_code in (401, 403), r.text
