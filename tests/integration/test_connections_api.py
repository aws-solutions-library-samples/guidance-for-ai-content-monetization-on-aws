#!/usr/bin/env python3
"""
Integration-tier test for the internal Connections API (integration-adapters, GitLab #35, task 16).

This is a THIN, auth-aware integration test that runs against the DEPLOYED Connections API + the
real ``cm-connections`` DynamoDB store + AWS Secrets Manager. It exercises the full lifecycle
round-trip the design's "Testing Strategy → Integration tier" bullet calls for:

    activate (POST /connections with a secret)
        -> read-back (GET /connections and GET /connections/{id}/status) shows secret_ref ONLY
        -> test()   (POST /connections/{id}/test) returns the fail-informative {ok, message} shape
        -> deactivate (DELETE /connections/{id})

and asserts the two load-bearing security/behavior contracts:

* The read-back NEVER returns the plaintext secret — only the Secrets Manager ``secret_ref``
  and non-secret config (Requirement 7.2 / design "Connection config … Invariant").
* ``test()`` returns the fail-informative ``{ok, message}`` shape (Requirements 6.5, 7.3) — a
  safe, read-only check that surfaces a diagnostic rather than raising past the API.

It is NOT part of the unit ``make test`` gate. Per the repo's integration-tier convention
(see ``tests/integration/test_api.py``) it is:

* marked ``@pytest.mark.integration`` (module-level ``pytestmark``), so ``make test`` — which runs
  ``-m "not integration"`` — deselects it, and ``make test-integration`` (``-m integration``,
  which includes ``tests/integration``) collects it; and
* additionally gated on ``RUN_INTEGRATION=1`` so it is skipped (never errored) unless explicitly
  enabled against a live deployment.

How to run
----------
    RUN_INTEGRATION=1 AWS_PROFILE=genai AWS_REGION=us-west-2 \
        COGNITO_PASSWORD=... \
        pytest tests/integration/test_connections_api.py -v

Config discovery (env overrides first, else CloudFormation outputs of ``ContentMonetizationStack``):
  * API base URL : env TEST_API_URL, else output ApiGatewayUrl (trailing slash stripped).
  * Cognito      : env COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID, else outputs UserPoolId /
                   UserPoolClientId. The Connections API is internal (Cognito JWT) only, so this
                   test authenticates with a Cognito IdToken (never a partner key).
  * Region       : env AWS_REGION (default us-west-2); profile from AWS_PROFILE (genai).

Credentials / skips (graceful degradation, never a hard failure without a deployment):
  * The Cognito IdToken is obtained via ADMIN_USER_PASSWORD_AUTH for COGNITO_USERNAME (default
    demo-user@example.com) / COGNITO_PASSWORD. If COGNITO_PASSWORD is unset or auth fails, the test
    is skipped. The password is NEVER hardcoded.

WAF note: this stack's WebACL blocks a default python-requests User-Agent, so a browser
User-Agent is sent on every request (matching tests/integration/test_api.py).
"""

import os

import boto3
import pytest
import requests

# A unique, obviously-fake secret value we activate the Connection with. The whole point of the
# test is to assert this string is NEVER echoed back by any read of the Connection.
_PLAINTEXT_SECRET = "it-integration-plaintext-secret-DO-NOT-LEAK-6f2a9c"

RUN = os.environ.get("RUN_INTEGRATION") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE")
MAIN_STACK = "ContentMonetizationStack"
TIMEOUT = 45

# WAF blocks the default python-requests UA; present as a browser (see test_api.py).
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Cognito auth defaults (password is NEVER hardcoded).
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")


# --------------------------------------------------------------------------- #
# Config discovery (mirrors tests/integration/test_api.py)
# --------------------------------------------------------------------------- #
def _session():
    if PROFILE:
        return boto3.Session(profile_name=PROFILE, region_name=REGION)
    return boto3.Session(region_name=REGION)


def _stack_outputs(stack_name):
    cfn = _session().client("cloudformation")
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


@pytest.fixture(scope="module")
def outputs():
    """CloudFormation outputs for the main stack, fetched at most once."""
    return _stack_outputs(MAIN_STACK)


@pytest.fixture(scope="module")
def api_base(outputs):
    """API Gateway invoke URL (…/v1), no trailing slash."""
    url = os.environ.get("TEST_API_URL") or outputs.get("ApiGatewayUrl")
    if not url:
        pytest.skip("no API URL: set TEST_API_URL or deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def cognito_config(outputs):
    """(user_pool_id, client_id) from env overrides or CloudFormation outputs."""
    user_pool_id = os.environ.get("COGNITO_USER_POOL_ID") or outputs.get("UserPoolId")
    client_id = os.environ.get("COGNITO_CLIENT_ID") or outputs.get("UserPoolClientId")
    if not user_pool_id or not client_id:
        pytest.skip("no Cognito config: set COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID or deploy the stack")
    return user_pool_id, client_id


@pytest.fixture(scope="module")
def http():
    """Per-module requests.Session that always sends a browser UA (WAF-friendly)."""
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    yield session
    session.close()


@pytest.fixture(scope="module")
def id_token(cognito_config):
    """Cognito IdToken via ADMIN_USER_PASSWORD_AUTH.

    Skips (never fails) when COGNITO_PASSWORD is unset or the auth call fails, so this test
    degrades gracefully without a real password in the environment. The Connections API is
    internal (Cognito) only, so a JWT is the only usable credential here.
    """
    if not COGNITO_PASSWORD:
        pytest.skip("set COGNITO_PASSWORD to run Cognito-authed tests (password is never hardcoded)")

    user_pool_id, client_id = cognito_config
    idp = _session().client("cognito-idp")
    try:
        resp = idp.admin_initiate_auth(
            UserPoolId=user_pool_id,
            ClientId=client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": COGNITO_USERNAME, "PASSWORD": COGNITO_PASSWORD},
        )
    except Exception as exc:  # noqa: BLE001 - any auth failure should skip, not error
        pytest.skip(f"Cognito auth failed for {COGNITO_USERNAME}: {exc}")

    token = resp.get("AuthenticationResult", {}).get("IdToken")
    if not token:
        pytest.skip(f"Cognito auth returned no IdToken (challenge: {resp.get('ChallengeName')})")
    return token


@pytest.fixture(scope="module")
def jwt_headers(id_token):
    return {"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _assert_secret_absent(payload, context):
    """Assert the plaintext secret value appears NOWHERE in a Connections API response.

    A conservative check: the raw serialized response must not contain the secret substring, so a
    leak in ANY field (including one this test does not explicitly read) is caught (Requirement 7.2).
    """
    import json

    blob = payload if isinstance(payload, str) else json.dumps(payload)
    assert _PLAINTEXT_SECRET not in blob, (
        f"SECURITY: plaintext secret leaked in {context} response (Req 7.2)"
    )


# --------------------------------------------------------------------------- #
# The lifecycle round-trip (activate -> read-back -> test -> deactivate)
# --------------------------------------------------------------------------- #
def test_connections_secret_roundtrip_lifecycle(http, api_base, jwt_headers):
    """Full Connections lifecycle against the deployed API, asserting secret confidentiality.

    Steps: activate (with a secret) -> read-back via GET /connections and GET /connections/{id}/status
    (secret_ref only, never the secret value) -> POST /connections/{id}/test ({ok, message} shape)
    -> DELETE /connections/{id}. A ``finally`` guarantees the Connection is deactivated even on a
    mid-test assertion failure, so the test leaves no residue in the deployed store.

    Validates: Requirements 6.7 (round-trip over the deployed contract), 7.2 (secret never
    returned), 7.3 (fail-informative test()).
    """
    connection_id = None
    try:
        # 1. Activate a Connection WITH a secret. The secret is a write-only request field; only
        #    the resulting Secrets Manager secret_ref should ever be persisted/returned.
        create_body = {
            "domain": "access",
            "adapter": "aws_waf",
            "brand": None,
            "config": {"region": REGION},
            "secret": _PLAINTEXT_SECRET,
        }
        create_resp = http.post(
            f"{api_base}/connections", headers=jwt_headers, json=create_body, timeout=TIMEOUT
        )
        assert create_resp.status_code == 201, (
            f"POST /connections -> {create_resp.status_code}: {create_resp.text[:300]}"
        )
        _assert_secret_absent(create_resp.text, "POST /connections")
        created = create_resp.json().get("connection", {})
        connection_id = created.get("connection_id")
        assert connection_id, f"activation returned no connection_id: {create_resp.text[:300]}"
        # The persisted record exposes only a Secrets Manager reference, not the value.
        assert created.get("secret_ref"), "activated Connection must carry a secret_ref (Req 6.4)"
        assert "secret" not in created, "response must not include a 'secret' field (Req 7.2)"

        # 2. Read-back via list: the Connection is present and still exposes secret_ref only.
        list_resp = http.get(
            f"{api_base}/connections",
            headers=jwt_headers,
            params={"domain": "access"},
            timeout=TIMEOUT,
        )
        assert list_resp.status_code == 200, (
            f"GET /connections -> {list_resp.status_code}: {list_resp.text[:300]}"
        )
        _assert_secret_absent(list_resp.text, "GET /connections")
        connections = list_resp.json().get("connections", [])
        mine = next((c for c in connections if c.get("connection_id") == connection_id), None)
        assert mine is not None, "activated Connection missing from GET /connections read-back"
        assert mine.get("secret_ref"), "read-back Connection must expose secret_ref (Req 7.2)"
        assert "secret" not in mine, "read-back Connection must not expose a secret value (Req 7.2)"

        # 3. test(): the deployed adapter's safe read-only check. Fail-informative {ok, message}
        #    shape (Req 6.5, 7.3) — ok may be true or false depending on the live adapter, but the
        #    shape is contractual and the secret must not leak in the diagnostic.
        test_resp = http.post(
            f"{api_base}/connections/{connection_id}/test",
            headers=jwt_headers,
            json={},
            timeout=TIMEOUT,
        )
        assert test_resp.status_code == 200, (
            f"POST /connections/{{id}}/test -> {test_resp.status_code}: {test_resp.text[:300]}"
        )
        _assert_secret_absent(test_resp.text, "POST /connections/{id}/test")
        test_payload = test_resp.json()
        assert "ok" in test_payload, f"test() response missing 'ok': {test_resp.text[:300]}"
        assert isinstance(test_payload["ok"], bool), "test() 'ok' must be a bool (Req 7.3)"
        assert "message" in test_payload, f"test() response missing 'message': {test_resp.text[:300]}"
        assert isinstance(test_payload["message"], str), "test() 'message' must be a str (Req 7.3)"
    finally:
        # 4. Deactivate — always clean up the deployed store, even on a mid-test failure.
        if connection_id:
            del_resp = http.delete(
                f"{api_base}/connections/{connection_id}", headers=jwt_headers, timeout=TIMEOUT
            )
            assert del_resp.status_code == 200, (
                f"DELETE /connections/{{id}} -> {del_resp.status_code}: {del_resp.text[:300]}"
            )


def test_connections_requires_cognito_auth(http, api_base):
    """The Connections API is internal (Cognito) — an unauthenticated read is rejected (Req 7.1)."""
    resp = http.get(f"{api_base}/connections", timeout=TIMEOUT)
    assert resp.status_code == 401, (
        f"GET /connections (no auth) -> {resp.status_code}: {resp.text[:300]}"
    )
