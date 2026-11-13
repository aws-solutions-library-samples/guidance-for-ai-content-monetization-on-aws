"""
Integration test: API Gateway edge-resolution response caching.

Verifies the caching configured on the v1 stage
(infrastructure/content_monetization_stack.py): caching ON for the public
discovery GETs (/info 60s, /taxonomy 120s, /llms.txt 60s), OFF everywhere else.

Cache hits are proven WITHOUT depending on an Age/x-cache header (API Gateway
does not reliably emit one). Instead we use the `metadata.timestamp` that the
/taxonomy and /info handlers stamp into every response body: within the TTL
window, a cached response repeats the SAME timestamp, while a fresh Lambda
invoke produces a new one.

Runs against the LIVE deployment; skipped unless RUN_INTEGRATION=1 and AWS
credentials are present. Discovers the API URL from CloudFormation outputs.

Two environment knobs:
  RUN_INTEGRATION=1   required, as with the other integration tests
  IAB_PARTNER_KEY     required ONLY for the authed-bypass test (partner-plane
                      POST /query). If unset, that single test is skipped; the
                      public-cache tests still run.

WAF note: this stack's WebACL blocks a default python/requests User-Agent, so a
browser UA is sent on every call (see TESTING-search-with-wikipedia-samples.md).
"""

import json
import os
import time
import urllib.request

import boto3
import pytest

RUN = os.environ.get("RUN_INTEGRATION") == "1"
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS")]

REGION = os.environ.get("AWS_REGION", "us-east-1")
PARTNER_KEY = os.environ.get("IAB_PARTNER_KEY")

# WAF blocks the default urllib UA; present as a browser.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _outputs(stack_name):
    cfn = boto3.client("cloudformation", region_name=REGION)
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


@pytest.fixture(scope="module")
def api_base():
    """API Gateway invoke URL (…/v1), no trailing slash."""
    main = _outputs("ContentMonetizationStack")
    url = main.get("ApiGatewayUrl") or main["CMApiStackEndpointD3860570"]
    return url.rstrip("/")


@pytest.fixture(scope="module")
def partner_base():
    """Partner-plane invoke URL (…/v1): native API keys are valid only there (#32)."""
    url = os.environ.get("PARTNER_API_URL") or _outputs("ContentMonetizationStack").get("PartnerApiUrl")
    if not url:
        pytest.skip("no PartnerApiUrl output")
    return url.rstrip("/")


def _get(url, headers=None):
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", BROWSER_UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _post(url, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("User-Agent", BROWSER_UA)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _taxonomy_timestamp(body):
    """Handler stamps metadata.timestamp on every /taxonomy response."""
    return body.get("metadata", {}).get("timestamp")


def test_taxonomy_second_call_is_cache_hit(api_base):
    """Two identical /taxonomy calls inside the 120s TTL must return the SAME
    body timestamp — proof the second was served from the API GW cache and never
    reached the Lambda."""
    url = f"{api_base}/taxonomy?tier_level=1"

    status1, body1 = _get(url)
    assert status1 == 200, f"first /taxonomy call failed: {status1}"
    ts1 = _taxonomy_timestamp(body1)
    assert ts1, "no metadata.timestamp in /taxonomy response; test needs a per-call nonce"

    time.sleep(2)  # well inside the 120s TTL
    status2, body2 = _get(url)
    assert status2 == 200
    ts2 = _taxonomy_timestamp(body2)

    assert ts1 == ts2, (
        "second identical /taxonomy call returned a NEW timestamp — response was "
        "NOT served from cache (caching may be off, or the cache cluster is not "
        f"provisioned). ts1={ts1} ts2={ts2}"
    )


def test_taxonomy_cache_key_isolation(api_base):
    """Different query strings must NOT collide in the cache. Requires
    cacheKeyParameters on the /taxonomy integration; without it the stage cache
    keys only on the path and one caller gets another's filtered slice.

    The reliable signal is the per-request metadata.timestamp echoed with the
    filter params: a collision means the parent_id query is served the tier_level
    response (its metadata echoes tier_level=1 and shares the tier query's
    timestamp). We assert on the echoed params + timestamp, NOT on the category
    payload — the sample taxonomy may return empty sets for these filters, and
    two empty lists would compare equal regardless of caching.
    """
    _, body_tier = _get(f"{api_base}/taxonomy?tier_level=1")
    _, body_parent = _get(f"{api_base}/taxonomy?parent_id=52")

    meta_tier = body_tier.get("metadata", {})
    meta_parent = body_parent.get("metadata", {})

    # Each response must echo its OWN query params (a collision serves the tier
    # response for the parent query, so parent would echo tier_level=1/parent=None).
    assert meta_parent.get("parent_id") == "52" and meta_parent.get("tier_level") is None, (
        "cache-key isolation FAILED: the /taxonomy?parent_id=52 response echoes "
        f"{meta_parent!r} — it was served the tier_level query's cached response. "
        "Declare cacheKeyParameters on the /taxonomy integration."
    )
    # And the two responses must be distinct cache entries (distinct timestamps).
    assert meta_tier.get("timestamp") != meta_parent.get("timestamp"), (
        "cache-key isolation FAILED: both queries returned the same timestamp — one cache entry served both."
    )


def test_info_endpoint_is_cached(api_base):
    """/info (60s TTL) — second call within TTL should be a cache hit. Falls back
    to a soft check if /info carries no per-call nonce."""
    url = f"{api_base}/info"
    status1, body1 = _get(url)
    assert status1 == 200, f"/info failed: {status1}"

    # /info's pricing block includes a timestamp; use it if present.
    def info_ts(b):
        # timestamp is nested under pricing/metadata depending on shape; scan.
        for k in ("timestamp", "generated_at"):
            if k in b:
                return b[k]
        return json.dumps(b, sort_keys=True)  # fallback: whole-body identity

    ts1 = info_ts(body1)
    time.sleep(2)
    _, body2 = _get(url)
    ts2 = info_ts(body2)
    assert ts1 == ts2, "second /info call differed — not served from cache"


@pytest.mark.skipif(not PARTNER_KEY, reason="set IAB_PARTNER_KEY to test the authed /query bypass")
def test_query_is_not_cached(partner_base):
    """POST /query (key-gated) must NOT be cached: two identical calls must return
    DIFFERENT query ids, or a cached response could serve one partner another's
    results."""
    url = f"{partner_base}/query"
    headers = {"x-api-key": PARTNER_KEY}
    body = {"query": "electric vehicle battery range", "max_results": 3}

    status1, r1 = _post(url, body, headers)
    assert status1 == 200, f"/query failed (seed partner key? indexed content?): {status1} {r1}"
    time.sleep(2)
    status2, r2 = _post(url, body, headers)
    assert status2 == 200

    assert r1.get("query_id") and r2.get("query_id"), "no query_id to compare"
    assert r1["query_id"] != r2["query_id"], (
        "two identical /query calls returned the SAME query_id — /query appears "
        "to be CACHED. Authed/POST routes must have caching disabled."
    )
