"""Shared config-discovery helpers for the partner-plane integration tests (task 10.3 / 10.3a).

Mirrors the discovery pattern in ``tests/integration/test_connections_api.py``: env overrides win,
else ``ContentMonetizationStack`` CloudFormation outputs; the partner key VALUE is read from API
Gateway on demand and never stored. These are plain helpers (not fixtures) so both the plane and
quota modules can share them.
"""

import os

import boto3

RUN = os.environ.get("RUN_INTEGRATION") == "1"
REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE")
MAIN_STACK = "ContentMonetizationStack"

# WAF blocks the default python-requests UA; present as a browser (see test_api.py / test_connections_api.py).
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


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
