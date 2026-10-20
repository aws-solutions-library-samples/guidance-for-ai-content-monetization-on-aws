#!/usr/bin/env python3
"""
Live CORS origin-echo integration test (TM-02, security-hardening-phase2, task 3.6).

Proves the F2 fix end-to-end against the DEPLOYED stack: a request carrying an
``Origin`` header that matches the configured allow-list gets that exact origin
echoed in ``Access-Control-Allow-Origin`` — never ``*``, never missing. This is the
runtime behaviour that the unit/stubbed tests (3.4a/3.5) cannot cover, because the
origin is resolved at invoke time from the ``/cm/ui/origin`` SSM parameter the UI
stack writes on deploy.

Expected lifecycle (as designed):
  * Run against a stack that has NOT yet deployed this branch → the deployed Lambda
    still emits ``*`` → this test FAILS (correct red signal that the fix isn't live).
  * After a fresh deploy of this branch (deploy-backend + deploy-ui) → the resolver
    is active and ``/cm/ui/origin`` is populated → the test GOES GREEN.

Config discovery mirrors tests/integration/test_api.py:
  * API base URL : env TEST_API_URL, else ContentMonetizationStack output ApiGatewayUrl.
  * Expected origin: env TEST_CORS_ORIGIN, else SSM /cm/ui/origin, else CMUIStack
    output UIUrl.
  * Region/profile : env AWS_REGION (default us-west-2) / AWS_PROFILE.

Gated: only runs when RUN_INTEGRATION=1 and marked @pytest.mark.integration. Skips
(never errors) when the stack / SSM parameter / API URL are unavailable, consistent
with the other live tests.

Run:
    RUN_INTEGRATION=1 AWS_PROFILE=genai AWS_REGION=us-west-2 \
        pytest tests/integration/test_cors_origin.py -v
"""

import os

import boto3
import pytest
import requests

RUN = os.environ.get("RUN_INTEGRATION") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE")
MAIN_STACK = "ContentMonetizationStack"
UI_STACK = "CMUIStack"
UI_ORIGIN_SSM_PARAMETER = "/cm/ui/origin"
TIMEOUT = 45

# WAF blocks the default python-requests UA; present as a browser (same as test_api.py).
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _session():
    if PROFILE:
        return boto3.Session(profile_name=PROFILE, region_name=REGION)
    return boto3.Session(region_name=REGION)


def _stack_outputs(stack_name):
    try:
        cfn = _session().client("cloudformation")
        stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
        return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    except Exception:  # noqa: BLE001 - absence => skip, not error
        return {}


@pytest.fixture(scope="module")
def api_base():
    url = os.environ.get("TEST_API_URL") or _stack_outputs(MAIN_STACK).get("ApiGatewayUrl")
    if not url:
        pytest.skip("no API URL: set TEST_API_URL or deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def expected_origin():
    """The configured portal origin the API should echo.

    Precedence MUST mirror the backend resolver (shared.utils.response_helpers):
    ALLOWED_ORIGINS env (first entry = the configured default) takes precedence over
    the /cm/ui/origin SSM parameter. We read the deployed value from the UI-backend
    Lambda's own environment so the test asserts exactly what the Lambda will echo;
    only when ALLOWED_ORIGINS is unset do we fall back to SSM, then the UIUrl output.
    """
    env_origin = os.environ.get("TEST_CORS_ORIGIN")
    if env_origin:
        return env_origin.strip()

    # 1. The deployed ALLOWED_ORIGINS env (highest precedence, same as the backend).
    #    Read it from a response Lambda's configuration. The first comma-separated
    #    entry is the configured default the resolver returns.
    try:
        main_outputs = _stack_outputs(MAIN_STACK)
        fn_name = main_outputs.get("UIBackendLambdaName")
        if fn_name:
            cfg = _session().client("lambda").get_function_configuration(FunctionName=fn_name)
            allowed = (cfg.get("Environment", {}).get("Variables", {}) or {}).get("ALLOWED_ORIGINS", "")
            entries = [o.strip() for o in allowed.split(",") if o.strip()]
            if entries:
                return entries[0]
    except Exception:  # noqa: BLE001 - fall through to SSM
        pass

    # 2. SSM /cm/ui/origin (the demo path, when ALLOWED_ORIGINS is unset).
    try:
        ssm = _session().client("ssm")
        value = ssm.get_parameter(Name=UI_ORIGIN_SSM_PARAMETER)["Parameter"]["Value"].strip()
        if value:
            return value
    except Exception:  # noqa: BLE001 - fall through to the stack output
        pass

    # 3. CMUIStack UIUrl output.
    ui_url = _stack_outputs(UI_STACK).get("UIUrl")
    if ui_url:
        return ui_url.strip()
    pytest.skip(
        "no portal origin: set TEST_CORS_ORIGIN, or deploy the stacks so ALLOWED_ORIGINS "
        "/ /cm/ui/origin (or the UIUrl output) is populated"
    )


@pytest.fixture(scope="module")
def http():
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    yield session
    session.close()


def test_matching_origin_is_echoed_not_wildcard(api_base, expected_origin, http):
    """A request whose Origin matches the configured allow-list gets that origin echoed."""
    resp = http.get(
        f"{api_base}/info",
        headers={"Origin": expected_origin},
        timeout=TIMEOUT,
    )
    # Header lookup is case-insensitive via requests' CaseInsensitiveDict.
    acao = resp.headers.get("Access-Control-Allow-Origin")
    assert acao is not None, (
        f"GET {api_base}/info returned no Access-Control-Allow-Origin for Origin={expected_origin!r} "
        f"(status {resp.status_code}). If the deployed Lambda predates the TM-02 fix this is the "
        f"expected red signal — redeploy this branch."
    )
    assert acao != "*", (
        f"GET {api_base}/info echoed a wildcard Access-Control-Allow-Origin: '*' — the TM-02 fix "
        f"is not deployed. Redeploy this branch (deploy-backend + deploy-ui)."
    )
    assert acao == expected_origin, (
        f"expected Access-Control-Allow-Origin={expected_origin!r}, got {acao!r}"
    )


def test_non_matching_origin_is_not_wildcard(api_base, expected_origin, http):
    """A non-allow-listed Origin never gets a wildcard; it gets the configured default or nothing."""
    resp = http.get(
        f"{api_base}/info",
        headers={"Origin": "https://not-an-allowed-origin.example"},
        timeout=TIMEOUT,
    )
    acao = resp.headers.get("Access-Control-Allow-Origin")
    # Either omitted (fail safe) or the configured default — but never "*".
    assert acao != "*", (
        f"GET {api_base}/info echoed '*' for a non-allow-listed Origin — the TM-02 fix is not "
        f"deployed or is misconfigured."
    )
    if acao is not None:
        assert acao != "https://not-an-allowed-origin.example", (
            "a non-allow-listed Origin was echoed back — the resolver must not reflect arbitrary origins"
        )
