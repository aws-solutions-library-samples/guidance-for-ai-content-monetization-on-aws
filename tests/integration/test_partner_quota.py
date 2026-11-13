"""
Integration-tier test for the partner usage-plan quota (spec: native-apikey-auth-migration, task
10.3a, Q11). Sandbox-only. Provisions a throwaway partner plus its key on the permanent
``cm-partner-itest-quota`` plan (3/DAY, no permanent key), drives it past the quota, and asserts
API Gateway returns 429 ``quota_exceeded`` with ``Retry-After``. The partner and key are deleted on
the way out.

Why a dedicated low-quota plan: the real tier plans allow thousands/day, so exhausting them in a
test is impractical; ``itest-quota`` exists solely to make the 429 path testable without touching a
real partner. The quota is enforced at the edge, so it fires regardless of the search backend state
(the handler may 503, but the usage plan still counts and then blocks the request).

Why the key is bound to an active partner: a bare usage-plan key resolves to no partner at all, so
every admitted call returns the handler's 403 ``partner_inactive``. Counting that as "admitted"
made the test green without ever proving a key resolved, so the key now belongs to an active
throwaway partner permitted to call ``queryContent`` and an admitted call is a real 200 (or 503
when the search data plane has nothing projectable).

Gated on ``RUN_INTEGRATION=1`` and ``@pytest.mark.integration``; skipped (never errored) without a
live deployment. Usage-plan-key association is eventually consistent, so the test polls for the
key to start being metered and counts the very response that settled.

How to run
----------
    RUN_INTEGRATION=1 AWS_PROFILE=genai AWS_REGION=us-west-2 \
        pytest tests/integration/test_partner_quota.py -v
"""

import time

import pytest
import requests

from tests.integration._partner_helpers import (
    BROWSER_UA,
    GATEWAY_REJECT,
    MAIN_STACK,
    POLL_DELAY_S,
    PROPAGATION_DEADLINE_S,
    RUN,
    SEARCH_UNAVAILABLE,
    UNBOUND_KEY_CODE,
    ThrowawayPartner,
    is_edge_propagation,
    json_body,
    poll_for_verdict,
    stack_outputs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

TIMEOUT = 45
QUERY_BODY = {"query": "quota probe", "content_type": ["article"], "max_results": 1}
ITEST_PLAN = "cm-partner-itest-quota"
# The partner-plane operationId for POST /query; allowed_endpoints is that closed set (Q5).
QUERY_OPERATION_ID = "queryContent"
# Settle with a slow cadence: every request against this plan is a request the quota under test may
# count, so trade poll frequency for the propagation window AWS documents.
SETTLE_DELAY_S = 20
# Calls allowed past the plan limit before giving up on the 429. AWS applies usage-plan quotas on a
# best-effort basis, so the first over-limit call is not guaranteed to be the one that throttles;
# the headroom lives here rather than in a bigger plan quota, which would weaken the same guarantee.
OVER_QUOTA_HEADROOM = 4
# Overrides the suite-wide ``--timeout=120``: one propagation window to settle, one more for edge
# nodes that still reject the key mid-measurement, the metered calls, and provisioning.
QUOTA_CASE_TIMEOUT_S = 2 * PROPAGATION_DEADLINE_S + 8 * TIMEOUT + 60


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
def http():
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA})
    yield s
    s.close()


# (status, error_code) of the two edge-propagation shapes (see ``is_edge_propagation``).
EDGE_SIGNATURES = {(403, GATEWAY_REJECT), (401, UNBOUND_KEY_CODE)}


def _metered(status, parsed):
    """``settled`` predicate for the quota plan: has this key started being metered?

    A 403 ``invalid_api_key`` means the edge has not associated the key with the plan yet, so the
    request is not counted against the quota either. Any *other* 403 came from the handler, which
    only runs once the edge admitted the request -- the key is live and being metered, so it
    settles and becomes the first counted observation. Polling past it would spend the 3/DAY quota
    this test is about to measure. A 5xx only settles as the admitted ``search_unavailable`` shape;
    anything else 5xx is an infrastructure blip. Every other status settles at once, so a wrong
    verdict reaches the assertions instead of being polled away.
    """
    if is_edge_propagation(status, parsed):
        return False
    if status == 403:
        return True
    if status >= 500:
        return parsed.get("error_code") == SEARCH_UNAVAILABLE
    return True


@pytest.mark.timeout(QUOTA_CASE_TIMEOUT_S)
def test_quota_exceeded_returns_429_with_retry_after(http, partner_base, outputs):
    throwaway = ThrowawayPartner(
        outputs, status="active", allowed_endpoints=[QUERY_OPERATION_ID], plan_name=ITEST_PLAN
    )
    with throwaway as key_value:
        limit = throwaway.quota_limit
        assert limit, f"{ITEST_PLAN} carries no quota; there is nothing to exhaust"
        url = f"{partner_base}/query"
        # The response that settles the poll is the first counted observation, so no admitted call
        # is spent proving the key is live: the probe no longer burns the quota it measures. The
        # rejections before it are not metered against this plan -- the gateway rejects them
        # precisely because it has not associated the key with the plan yet. `probe` keeps them
        # anyway: whether the failure was pre-propagation rejections, a stale `META` read or 5xx
        # blips decides which fix applies, and only these observations can tell them apart.
        probe = []
        response, body = poll_for_verdict(
            http, url, key_value, QUERY_BODY, _metered, delay=SETTLE_DELAY_S, history=probe
        )
        observations = [(response.status_code, body.get("error_code"))]
        # Up to `limit` admitted calls, then a throttled one within the headroom. Association is not
        # monotonic across edge nodes, so a node that has not bound the key yet can still answer
        # with an edge-propagation shape after admitted calls. That answer is not metered and is
        # not a verdict, so retry past it (kept in `probe`) until the window closes.
        edge_deadline = time.monotonic() + PROPAGATION_DEADLINE_S
        while len(observations) < limit + OVER_QUOTA_HEADROOM and observations[-1][0] != 429:
            response = http.post(url, json=QUERY_BODY, headers={"x-api-key": key_value}, timeout=TIMEOUT)
            body = json_body(response)
            observation = (response.status_code, body.get("error_code"))
            if is_edge_propagation(response.status_code, body) and time.monotonic() < edge_deadline:
                probe.append(observation)
                time.sleep(POLL_DELAY_S)
                continue
            observations.append(observation)
            if observation in EDGE_SIGNATURES:
                break

        statuses = [status for status, _ in observations]
        admitted = sum(1 for status, code in observations if status == 200 or code == SEARCH_UNAVAILABLE)
        if response.status_code == 429:
            assert body.get("error_code") == "quota_exceeded", response.text[:200]
            assert response.headers.get("Retry-After"), "429 must carry Retry-After"
        # KNOWN DEFERRAL: the edge can keep rejecting a freshly associated key for minutes, which
        # is an environment property, not a product verdict. xfail (non-strict) only when the run
        # ENDED on that signature -- the edge still had not bound the key after the propagation
        # window. A run that admitted calls and then exhausted the headroom without a 429 ends on a
        # 200/503 instead, which is the enforcement failing: the one property this test exists to
        # prove, so it fails hard.
        if 429 not in statuses and observations[-1] in EDGE_SIGNATURES:
            pytest.xfail(
                f"throwaway quota key was still rejected at the edge after {PROPAGATION_DEADLINE_S}s "
                f"of usage-plan key propagation: {observations} (probe: {probe})"
            )
        assert 429 in statuses, (
            f"quota never enforced across {len(statuses)} calls: {observations} (probe: {probe})"
        )
        assert admitted >= 1, f"no call was admitted before the quota fired: {observations} (probe: {probe})"
