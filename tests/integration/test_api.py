#!/usr/bin/env python3
"""
Integration tests for the AI Content Monetization API (live deployment).

These are auth-aware, gated integration tests that run against the deployed API
Gateway. They authenticate for real (Cognito JWT + partner key) and assert
status codes for every route+method so future runs give real coverage.

These tests are NOT part of the unit `make test` gate. They only run against a
live deployment when explicitly enabled.

How to run
----------
    RUN_INTEGRATION=1 AWS_PROFILE=genai AWS_REGION=us-west-2 \
        COGNITO_PASSWORD=... IAB_PARTNER_KEY=... \
        pytest tests/integration/test_api.py -v

Config discovery (env overrides first, else CloudFormation outputs):
  * API base URL : env TEST_API_URL, else ContentMonetizationStack output
                   ApiGatewayUrl (trailing slash stripped).
  * Cognito      : env COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID, else
                   ContentMonetizationStack outputs UserPoolId / UserPoolClientId.
  * Region       : env AWS_REGION (default us-west-2); profile from AWS_PROFILE.

Credentials / skips:
  * Cognito-authed tests obtain an IdToken via cognito-idp ADMIN_USER_PASSWORD_AUTH
    for COGNITO_USERNAME (default demo-user@example.com) / COGNITO_PASSWORD. If
    COGNITO_PASSWORD is unset or the auth call fails, those tests are skipped.
    The password is NEVER hardcoded.
  * Partner-key-authed tests need IAB_PARTNER_KEY. If unset they are skipped;
    obtain a usable key by running `deployment/data/add-sample-partners.py`,
    which prints partner keys.

WAF note: this stack's WebACL blocks a default python-requests User-Agent, so a
browser User-Agent is sent on every request.

The manual smoke script is preserved: run `python tests/integration/test_api.py
<api_gateway_url> [mock|real]` to exercise the human-readable print smoke.
"""

import os
import sys

import boto3
import pytest
import requests

RUN = os.environ.get("RUN_INTEGRATION") == "1"
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS")]

REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE")
MAIN_STACK = "ContentMonetizationStack"
TIMEOUT = 45

# WAF blocks the default python-requests UA; present as a browser.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# Cognito auth defaults (password is NEVER hardcoded).
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")
PARTNER_KEY = os.environ.get("IAB_PARTNER_KEY")

# JWT-authed GET routes that must all return 200. Add a route here in one line.
JWT_GET_200_ROUTES = [
    "/analytics/metrics",
    "/analytics/usage",
    "/analytics/revenue",
    "/analytics/trends",
    "/analytics/revenue/categories",
    "/analytics/partners",
    "/analytics/access-logs",
    "/taxonomy/categories",
    "/processing/status",
    "/content",
    "/partners",
    "/pricing/rules",
    "/pricing/history",
]


# --------------------------------------------------------------------------- #
# Config discovery
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
    # Only reach out to CFN if we actually need an output (i.e. an env override
    # is missing). Fetch lazily but cache for the module.
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
    """Per-module requests.Session that always sends a browser UA."""
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    yield session
    session.close()


@pytest.fixture(scope="module")
def id_token(cognito_config):
    """Cognito IdToken via ADMIN_USER_PASSWORD_AUTH.

    Skips (never fails) when COGNITO_PASSWORD is unset or the auth call fails, so
    these tests degrade gracefully without a real password in the environment.
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


@pytest.fixture(scope="module")
def partner_headers():
    if not PARTNER_KEY:
        pytest.skip("set IAB_PARTNER_KEY to run partner-key tests; get one from deployment/data/add-sample-partners.py")
    return {"x-api-key": PARTNER_KEY, "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def partner_base(outputs):
    """Partner-plane invoke URL (…/v1), no trailing slash. Native keys live ONLY on this plane."""
    url = os.environ.get("PARTNER_API_URL") or outputs.get("PartnerApiUrl")
    if not url:
        pytest.skip("no PartnerApiUrl: deploy ContentMonetizationStack")
    return url.rstrip("/")


# --------------------------------------------------------------------------- #
# Public (no auth)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/info", "/taxonomy", "/llms.txt"])
def test_public_get_returns_200(http, api_base, path):
    resp = http.get(f"{api_base}{path}", timeout=TIMEOUT)
    assert resp.status_code == 200, f"GET {path} -> {resp.status_code}: {resp.text[:300]}"


# --------------------------------------------------------------------------- #
# Partner-key authed (x-api-key) — native keys resolve ONLY on the partner plane now (#32).
# --------------------------------------------------------------------------- #
def test_query_with_partner_key_authorized_on_partner_plane(http, partner_base, partner_headers):
    body = {"query": "artificial intelligence healthcare", "max_results": 3}
    resp = http.post(f"{partner_base}/query", headers=partner_headers, json=body, timeout=TIMEOUT)
    # The key is authorized and reaches the handler; a literal 200 depends on the search data plane
    # (#19) having content, else 503 search_unavailable. Either proves auth; a 403 would be a failure.
    assert resp.status_code != 403, f"partner key wrongly rejected: {resp.text[:300]}"
    if resp.status_code == 503:
        assert resp.json().get("error_code") == "search_unavailable"
    else:
        assert resp.status_code == 200, f"POST /query -> {resp.status_code}: {resp.text[:300]}"


def test_partner_key_not_accepted_on_internal_plane(http, api_base, partner_headers):
    # F8/#32: the internal plane is Cognito-only; a native key is not a valid credential there.
    body = {"query": "artificial intelligence healthcare", "max_results": 3}
    resp = http.post(f"{api_base}/query", headers=partner_headers, json=body, timeout=TIMEOUT)
    assert resp.status_code in (401, 403), f"internal /query must reject a partner key: {resp.status_code}"


def test_search_is_deleted_on_partner_plane(http, partner_base, partner_headers):
    # /search is deleted (no tombstone). The partner plane has no catch-all, so API Gateway answers
    # the missing resource with MISSING_AUTHENTICATION_TOKEN even when the key is valid. That
    # gateway response keeps its default 403 and carries the contract code `route_not_found`.
    body = {"query": "machine learning", "max_results": 3}
    resp = http.post(f"{partner_base}/search", headers=partner_headers, json=body, timeout=TIMEOUT)
    assert resp.status_code == 403, f"/search must not exist on the partner plane: {resp.status_code}"
    assert resp.json().get("error_code") == "route_not_found", resp.text[:200]


# --------------------------------------------------------------------------- #
# Cognito JWT authed (Authorization: Bearer <IdToken>)
# --------------------------------------------------------------------------- #
def test_search_is_deleted_on_internal_plane(http, api_base, jwt_headers):
    # On the internal plane /search falls through to the Cognito /{proxy+} catch-all: 401 without a
    # token, and the UI backend's unmatched-route 404 with one. Neither reaches discovery.
    body = {"query": "machine learning", "max_results": 3}
    anonymous = http.post(f"{api_base}/search", json=body, timeout=TIMEOUT)
    assert anonymous.status_code == 401, f"anonymous internal /search -> {anonymous.status_code}"
    authed = http.post(f"{api_base}/search", headers=jwt_headers, json=body, timeout=TIMEOUT)
    assert authed.status_code == 404, f"Cognito internal /search -> {authed.status_code}: {authed.text[:300]}"
    assert authed.json().get("error") == "Endpoint not found"


@pytest.mark.parametrize("path", JWT_GET_200_ROUTES)
def test_jwt_get_returns_200(http, api_base, jwt_headers, path):
    resp = http.get(f"{api_base}{path}", headers=jwt_headers, timeout=TIMEOUT)
    assert resp.status_code == 200, f"GET {path} -> {resp.status_code}: {resp.text[:300]}"


def test_pricing_calculate_returns_200(http, api_base, jwt_headers):
    body = {"content_type": "article", "iab_categories": [111]}
    resp = http.post(f"{api_base}/pricing/calculate", headers=jwt_headers, json=body, timeout=TIMEOUT)
    assert resp.status_code == 200, f"POST /pricing/calculate -> {resp.status_code}: {resp.text[:300]}"


def test_analytics_agent_chat_routes_and_auths(http, api_base, jwt_headers):
    """Env-dependent: assert routing+auth are OK (not 401/404). A 500 occurs when
    ANALYTICS_AGENT_ARN is unset (the Bedrock agent is not deployed), so we accept
    200 or 500."""
    body = {"message": "hi"}
    resp = http.post(f"{api_base}/analytics-agent/chat", headers=jwt_headers, json=body, timeout=TIMEOUT)
    assert resp.status_code in (200, 500), f"POST /analytics-agent/chat -> {resp.status_code}: {resp.text[:300]}"
    assert resp.status_code not in (401, 404), (
        f"POST /analytics-agent/chat routing/auth broken -> {resp.status_code}: {resp.text[:300]}"
    )


# --------------------------------------------------------------------------- #
# Auth negatives
# --------------------------------------------------------------------------- #
def test_content_without_auth_returns_401(http, api_base):
    resp = http.get(f"{api_base}/content", timeout=TIMEOUT)
    assert resp.status_code == 401, f"GET /content (no auth) -> {resp.status_code}: {resp.text[:300]}"


def test_query_without_key_returns_401(http, api_base):
    body = {"query": "artificial intelligence healthcare", "max_results": 3}
    resp = http.post(f"{api_base}/query", json=body, timeout=TIMEOUT)
    assert resp.status_code == 401, f"POST /query (no key) -> {resp.status_code}: {resp.text[:300]}"


# --------------------------------------------------------------------------- #
# Manual smoke entrypoint (NOT collected by pytest — non-test_ name)
# --------------------------------------------------------------------------- #
def run_manual_smoke(base_url: str, mock_mode: bool = None):
    """Human-readable smoke test of the AI Content Monetization API endpoints.

    Preserved for manual invocation via `python test_api.py <url> [mock|real]`.
    This is intentionally NOT named test_* so pytest does not collect it.
    """

    # Determine test mode from environment or parameter
    if mock_mode is None:
        demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"
        use_mock_data = demo_mode or os.getenv("USE_MOCK_DATA", "false").lower() == "true"
        mock_mode = use_mock_data

    mode_str = "MOCK DATA MODE" if mock_mode else "REAL DATA MODE"
    print(f"Testing Enhanced AI Content Monetization API at: {base_url}")
    print(f"Test Mode: {mode_str}")
    print("=" * 70)

    # Test 1: Info endpoint (no auth required)
    print("\n1. Testing /info endpoint (Discovery)")
    try:
        response = requests.get(f"{base_url}/info")
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Publisher: {data['publisher']['name']}")
            print(f"API Version: {data['api_version']}")
            print(f"Content Categories: {len(data['content']['categories'])}")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 2: Taxonomy endpoint
    print("\n2. Testing /taxonomy endpoint")
    try:
        response = requests.get(f"{base_url}/taxonomy")
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Taxonomy Version: {data['version']}")
            print(f"Total Categories: {data['total_count']}")
            print(f"Returned Categories: {data['filtered_count']}")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 3: Query endpoint (requires auth)
    print("\n3. Testing /query endpoint with authentication")
    headers = {"x-api-key": "pk_demo_test_key", "Content-Type": "application/json"}

    query_data = {
        "query": "artificial intelligence healthcare",
        "content_type": ["article"],
        "format": "json",
        "max_results": 5,
    }

    try:
        response = requests.post(f"{base_url}/query", headers=headers, json=query_data)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Query ID: {data['query_id']}")
            print(f"Results: {len(data['results'])}")
            print(f"Total Cost: ${data['billing']['total_cost']}")
            if data["results"]:
                print(f"Top Result: {data['results'][0]['title']}")
                print(f"Relevance Score: {data['results'][0]['relevance_score']}")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 4: Query endpoint without auth (should fail)
    print("\n4. Testing /query endpoint without authentication")
    try:
        response = requests.post(f"{base_url}/query", json=query_data)
        print(f"Status: {response.status_code}")
        if response.status_code == 401:
            print("Correctly rejected unauthorized request")
        else:
            print(f"Unexpected response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 5: LLMs.txt endpoint (IAB Framework Requirement)
    print("\n5. Testing /llms.txt endpoint (AI Agent-friendly content)")
    try:
        response = requests.get(f"{base_url}/llms.txt")
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            content = response.text
            print(f"Content Type: {response.headers.get('Content-Type', 'unknown')}")
            print(f"Content Length: {len(content)} characters")
            print(f"First 200 chars: {content[:200]}...")
            if "AI Content Monetization API" in content and "# " in content:
                print("IAB-compliant markdown format detected")
            else:
                print("May not be fully IAB-compliant")
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

    # Test 6: Content Management - List Content
    print("\n6. Testing /content endpoint without authentication")
    try:
        response = requests.get(f"{base_url}/content")
        print(f"Status: {response.status_code}")
        if response.status_code == 401:
            print("Correctly rejected unauthorized request")
        else:
            print(f"Unexpected response: {response.text}")
    except Exception as e:
        print(f"Error: {e}")

    print("\n" + "=" * 70)
    print("ENHANCED API SMOKE COMPLETE!")
    print("=" * 70)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_api.py <api_gateway_url> [mock|real]")
        print("Example: python test_api.py https://abc123.execute-api.us-west-2.amazonaws.com/v1")
        print("Example: python test_api.py https://abc123.execute-api.us-west-2.amazonaws.com/v1 mock")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")

    # Determine test mode
    mock_mode = None
    if len(sys.argv) > 2:
        mode_arg = sys.argv[2].lower()
        if mode_arg == "mock":
            mock_mode = True
        elif mode_arg == "real":
            mock_mode = False

    run_manual_smoke(base_url, mock_mode)
