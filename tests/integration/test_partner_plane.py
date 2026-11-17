"""
Integration-tier test for the native API-key partner plane (spec: native-apikey-auth-migration,
task 10.3). Runs against the DEPLOYED partner API + the real ``cm-partners`` table + API Gateway
usage plans/keys. It proves the wiring that unit tests cannot: the native key is enforced at the
edge, the partner plane exposes only its own operations, partner resolution gates on partner state
and the allow-list, and the internal plane now requires Cognito on ``/query`` (the F8 fix).

What is asserted
----------------
* No key on ``POST /query`` -> 403 ``invalid_api_key`` (API Gateway native enforcement).
* The seeded test key on ``POST /query`` is AUTHORIZED: the request passes the key gate and reaches
  the handler. A literal 200 depends on the OpenSearch data plane (#19) having projectable content,
  which the mock seed does not provide, so a ``503 search_unavailable`` with a valid key is accepted
  as proof of authorization (it is NOT an auth rejection). A 200 is validated when content exists.
* A suspended partner's disabled key -> 403 (rejected at the gateway).
* ``partner_inactive`` and ``endpoint_not_allowed`` on a provisioned throwaway partner (an active
  key whose partner is suspended, and an active partner whose allow-list excludes ``queryContent``),
  cleaned up by the ``ThrowawayPartner`` context manager. Both are polled to a single settled
  observation, because usage-plan key association reaches the API Gateway edge minutes after
  ``create_usage_plan_key`` returns. The inactive case also pins the verdict's message, since
  ``partner_inactive`` is raised for five different reasons and only one of them is the gate
  under test.
* Partner ``GET /openapi`` returns exactly the partner-plane operations, no ``/{proxy+}``, with the
  native ``x-api-key`` scheme and host-derived ``servers`` (Q10).
* F8: internal ``POST /query`` with no auth -> 401 (Cognito authorizer active).

Config discovery mirrors ``tests/integration/test_connections_api.py``: env overrides first, else
``ContentMonetizationStack`` CloudFormation outputs. The partner key VALUE is read from API Gateway
on demand (never stored). Gated on ``RUN_INTEGRATION=1`` and marked ``@pytest.mark.integration`` so
the unit ``make test`` gate deselects it and it is skipped (never errored) without a live deployment.

How to run
----------
    RUN_INTEGRATION=1 AWS_PROFILE=genai AWS_REGION=us-west-2 \
        pytest tests/integration/test_partner_plane.py -v
"""

import pytest
import requests
from boto3.dynamodb.conditions import Key

from tests.integration._partner_helpers import (
    BROWSER_UA,
    GATEWAY_REJECT,
    MAIN_STACK,
    PROPAGATION_DEADLINE_S,
    RUN,
    ThrowawayPartner,
    is_edge_propagation,
    partner_key_value,
    poll_for_verdict,
    session,
    stack_outputs,
    verdict_or_retry,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

TIMEOUT = 45
QUERY_BODY = {"query": "technology news", "content_type": ["article"], "max_results": 5}
# The one `partner_inactive` message that means "the partner's status is not active" -- the gate
# the inactive case exists to prove. Its four siblings all mean the key is not bound (yet).
INACTIVE_PARTNER_MESSAGE = "Partner is not active"
# Wall-clock budget for a provisioned case: the propagation deadline plus the last request's own
# timeout plus provisioning. Overrides the suite-wide ``--timeout=120``, which is shorter than the
# propagation window AWS documents.
PROVISIONED_CASE_TIMEOUT_S = PROPAGATION_DEADLINE_S + TIMEOUT + 60


@pytest.fixture(scope="module")
def outputs():
    return stack_outputs(MAIN_STACK)


@pytest.fixture(scope="module")
def partner_base(outputs):
    url = outputs.get("PartnerApiUrl") or outputs.get("MonetizationApiPartnerApiEndpoint20F2F20B")
    if not url:
        pytest.skip("no PartnerApiUrl: deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def internal_base(outputs):
    url = outputs.get("ApiGatewayUrl")
    if not url:
        pytest.skip("no ApiGatewayUrl: deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def http():
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA})
    yield s
    s.close()


@pytest.fixture(scope="module")
def test_key(outputs):
    value = partner_key_value(outputs)
    if not value:
        pytest.skip("no test partner key: TestPartnerApiKeyId output missing or unreadable")
    return value


# --------------------------------------------------------------------------- #
# Core partner-plane behavior against the seeded deployment
# --------------------------------------------------------------------------- #
def test_no_key_is_rejected_at_the_edge(http, partner_base):
    r = http.post(f"{partner_base}/query", json=QUERY_BODY, timeout=TIMEOUT)
    assert r.status_code == 403
    assert r.json().get("error_code") == "invalid_api_key"


def test_valid_key_is_authorized(http, partner_base, test_key):
    r = http.post(f"{partner_base}/query", json=QUERY_BODY, headers={"x-api-key": test_key}, timeout=TIMEOUT)
    # The key passed the gate and reached the handler. 200 when the search data plane has content;
    # 503 search_unavailable when it does not (mock seed is not projectable — #19, not an auth issue).
    assert r.status_code != 403, f"valid key wrongly rejected: {r.text[:200]}"
    assert r.status_code != 401
    if r.status_code == 503:
        assert r.json().get("error_code") == "search_unavailable"
    else:
        assert r.status_code == 200, f"unexpected status {r.status_code}: {r.text[:200]}"


def test_suspended_partner_key_is_rejected(http, partner_base, outputs):
    # suspended_005 is seeded with a DISABLED gateway key, so the edge rejects it before the Lambda.
    value = _key_value_for_seeded_partner(outputs, "suspended_005")
    if not value:
        pytest.skip("suspended_005 key not resolvable (disabled keys may not expose a value)")
    r = http.post(f"{partner_base}/query", json=QUERY_BODY, headers={"x-api-key": value}, timeout=TIMEOUT)
    assert r.status_code == 403


def test_partner_openapi_is_the_narrowed_plane(http, partner_base):
    r = http.get(f"{partner_base}/openapi", timeout=TIMEOUT)
    assert r.status_code == 200
    spec = r.json()
    # ``/content`` joined this inventory with URL-addressable retrieval (#14): the GET is
    # ``x-audience: partner``, so the derived partner document includes it. Only the GET —
    # ``POST /content`` is publisher ingest and stays on the Cognito plane, which the method
    # assertion below pins. If this list grows again, the question to ask is whether the new
    # operation is genuinely partner-facing, not whether to widen the expectation.
    assert sorted(spec.get("paths", {})) == [
        "/content",
        "/info",
        "/llms.txt",
        "/log",
        "/query",
        "/taxonomy",
    ]
    assert sorted(spec["paths"]["/content"]) == ["get"], (
        "the partner plane must expose only GET /content; POST /content is publisher ingest and "
        "belongs to the internal plane"
    )
    assert "/{proxy+}" not in spec.get("paths", {})
    schemes = spec.get("components", {}).get("securitySchemes", {})
    assert schemes.get("PartnerKeyAuth", {}).get("type") == "apiKey"
    assert schemes.get("PartnerKeyAuth", {}).get("name") == "x-api-key"
    assert spec.get("servers"), "partner spec should advertise host-derived servers"


def test_internal_query_requires_cognito(http, internal_base):
    # F8: the internal plane dropped the custom authorizer for Cognito; an unauthenticated call is 401.
    r = http.post(f"{internal_base}/query", json=QUERY_BODY, timeout=TIMEOUT)
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Provisioned-fixture cases: partner_inactive and endpoint_not_allowed
# These states are not in the seed, so provision a throwaway partner + enabled key, then clean up.
#
# Both cases poll for the verdict they assert on and assert on that one observation. The earlier
# shape -- probe until the key looks live, then POST again and assert on the second response --
# raced the edge: usage-plan key association propagates with a lag AWS documents as up to a few
# minutes and is not monotonic across edge nodes, so the asserting request could still be rejected
# with `invalid_api_key` (or the probe could end early on an uncustomized 403 that carries no
# error_code at all, which is how one run reported an error_code of None).
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(PROVISIONED_CASE_TIMEOUT_S)
def test_inactive_partner_is_rejected_by_the_handler(http, partner_base, outputs):
    with ThrowawayPartner(outputs, status="suspended", allowed_endpoints=[]) as key_value:
        # `partner_inactive` alone does not prove the status gate: resolve_partner_from_event
        # raises it for a lookup failure, an unmapped key, a disabled credential and a missing
        # `META` too -- all of which are the binding/propagation lag, not a suspended partner.
        # The message is what separates them, so settle (and assert) on that as well.
        response, body = poll_for_verdict(
            http,
            f"{partner_base}/query",
            key_value,
            QUERY_BODY,
            verdict_or_retry("partner_inactive", INACTIVE_PARTNER_MESSAGE),
        )
        # KNOWN DEFERRAL: the edge can still be rejecting the key when the deadline expires, which
        # is an environment property, not a product verdict. xfail (non-strict) only on that one
        # signature so it stays visible and self-heals; every other wrong verdict -- a 200 serving
        # a suspended partner, or the 503 an admitted request answers while the search plane has
        # nothing projectable -- falls through to the asserts below.
        if is_edge_propagation(response.status_code, body):
            pytest.xfail(
                f"throwaway suspended partner's key was still rejected at the edge after "
                f"{PROPAGATION_DEADLINE_S}s of usage-plan key propagation: {body}"
            )
        assert response.status_code == 403, f"expected 403, got {response.status_code}: {response.text[:200]}"
        assert body.get("error_code") == "partner_inactive", body
        assert body.get("error") == INACTIVE_PARTNER_MESSAGE, body


@pytest.mark.timeout(PROVISIONED_CASE_TIMEOUT_S)
def test_allow_list_miss_is_rejected_by_the_handler(http, partner_base, outputs):
    # Active partner whose allow-list permits only submitLog -> queryContent (/query) is forbidden.
    with ThrowawayPartner(outputs, status="active", allowed_endpoints=["submitLog"]) as key_value:
        # A 403 partner_inactive is also unsettled here: the partner META read is eventually
        # consistent, so a just-written active partner can briefly read as missing.
        response, body = poll_for_verdict(
            http,
            f"{partner_base}/query",
            key_value,
            QUERY_BODY,
            verdict_or_retry("endpoint_not_allowed"),
        )
        # KNOWN DEFERRAL: same edge-propagation signature as the inactive-partner case, and the
        # same narrow guard -- a 200 allow-list bypass, or the 503 an admitted request answers,
        # fails hard rather than xfailing. `endpoint_not_allowed` has a single producer, so the
        # code alone pins this verdict; no message check is needed.
        if is_edge_propagation(response.status_code, body):
            pytest.xfail(
                f"throwaway allow-list-miss partner's key was still rejected at the edge after "
                f"{PROPAGATION_DEADLINE_S}s of usage-plan key propagation: {body}"
            )
        assert response.status_code == 403, f"expected 403, got {response.status_code}: {response.text[:200]}"
        assert body.get("error_code") == "endpoint_not_allowed", body


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _key_value_for_seeded_partner(outputs, partner_id):
    """Resolve the gateway key value for a seeded partner by reading its KEY# item then API GW."""
    table_name = outputs.get("PartnerTableName", "cm-partners")
    ddb = session().resource("dynamodb").Table(table_name)
    items = ddb.query(
        KeyConditionExpression=Key("PK").eq(f"PARTNER#{partner_id}") & Key("SK").begins_with("KEY#")
    ).get("Items", [])
    if not items:
        return None
    apigw = session().client("apigateway")
    try:
        return apigw.get_api_key(apiKey=items[0]["api_key_id"], includeValue=True)["value"]
    except Exception:  # noqa: BLE001 - disabled key may not expose a value
        return None
