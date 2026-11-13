"""Integration tests for the comparison run route (spec ``scrape-vs-api-comparison`` R13.3, task 4.2).

Live AWS: ``POST /test/comparison-runs`` with the deployed fixture article → the GEO pipeline
publishes it → ``GET /test/comparison-runs/{run_id}`` returns the Markdown body. Also the reuse and
re-ingest rules (R1.6, R1.7) and one guard refusal.

Configuration (mirrors ``test_connections_api.py``):
  * gated on ``RUN_INTEGRATION=1``; skipped otherwise.
  * API base: ``TEST_API_URL`` or output ``ApiGatewayUrl`` of ``ContentMonetizationStack``.
  * Fixture URL: ``COMPARISON_FIXTURE_URL`` or output ``ComparisonFixtureUrl`` of ``CMUIStack``.
  * Cognito: ``COGNITO_USER_POOL_ID`` / ``COGNITO_CLIENT_ID`` or the stack outputs, plus
    ``COGNITO_USERNAME`` / ``COGNITO_PASSWORD`` (never hardcoded; skipped when unset).
  * Poll limit: ``COMPARISON_POLL_LIMIT_S`` (default 240 s; the pipeline runs Bedrock calls).
"""

from __future__ import annotations

import os
import time

import boto3
import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RUN = os.environ.get("RUN_INTEGRATION") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

REGION = os.environ.get("AWS_REGION", "us-west-2")
PROFILE = os.environ.get("AWS_PROFILE")
MAIN_STACK = "ContentMonetizationStack"
UI_STACK = "CMUIStack"
TIMEOUT = 60
POLL_LIMIT_S = int(os.environ.get("COMPARISON_POLL_LIMIT_S", "240"))
FIXTURE_SENTENCE = "Protected lanes change who feels safe enough to ride."

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")


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
    return _stack_outputs(MAIN_STACK)


@pytest.fixture(scope="module")
def api_base(outputs):
    url = os.environ.get("TEST_API_URL") or outputs.get("ApiGatewayUrl")
    if not url:
        pytest.skip("no API URL: set TEST_API_URL or deploy ContentMonetizationStack")
    return url.rstrip("/")


@pytest.fixture(scope="module")
def fixture_url():
    url = os.environ.get("COMPARISON_FIXTURE_URL")
    if not url:
        try:
            url = _stack_outputs(UI_STACK).get("ComparisonFixtureUrl")
        except Exception as exc:  # noqa: BLE001 - no UI stack => skip, not error
            pytest.skip(f"no fixture URL: set COMPARISON_FIXTURE_URL or deploy CMUIStack ({exc})")
    if not url:
        pytest.skip("no fixture URL: set COMPARISON_FIXTURE_URL or deploy CMUIStack")
    return url


@pytest.fixture(scope="module")
def cognito_config(outputs):
    user_pool_id = os.environ.get("COGNITO_USER_POOL_ID") or outputs.get("UserPoolId")
    client_id = os.environ.get("COGNITO_CLIENT_ID") or outputs.get("UserPoolClientId")
    if not user_pool_id or not client_id:
        pytest.skip("no Cognito config: set COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID or deploy the stack")
    return user_pool_id, client_id


@pytest.fixture(scope="module")
def http():
    """A browser-UA session that retries TRANSPORT failures only.

    This endpoint is fronted by CloudFront + WAF and reached from a sandboxed client whose TLS stack
    (OpenSSL 3 + urllib3 2) intermittently raises ``[SSL] passed invalid argument`` on a fresh
    connection — a transport fault, not a server response. ``status_forcelist`` is empty and
    ``raise_on_status`` is False, so HTTP status codes are NEVER retried and a real 4xx/5xx still
    reaches the assertion unchanged; only connect/read/TLS failures (where the request did not get a
    response) are retried. ``allowed_methods=None`` lets a POST retry too, which is safe because a
    connection-level failure means the request never reached the server.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    retry = Retry(
        total=5,
        connect=5,
        read=3,
        status=0,
        status_forcelist=[],
        allowed_methods=None,
        backoff_factor=0.6,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    yield session
    session.close()


@pytest.fixture(scope="module")
def id_token(cognito_config):
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
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Cognito auth failed for {COGNITO_USERNAME}: {exc}")
    token = resp.get("AuthenticationResult", {}).get("IdToken")
    if not token:
        pytest.skip(f"Cognito auth returned no IdToken (challenge: {resp.get('ChallengeName')})")
    return token


@pytest.fixture(scope="module")
def jwt_headers(id_token):
    return {"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"}


def _submit(http, api_base, jwt_headers, url, reingest=False):
    return http.post(
        f"{api_base}/test/comparison-runs",
        headers=jwt_headers,
        json={"url": url, "reingest": reingest},
        timeout=TIMEOUT,
    )


def _poll_until_ready(http, api_base, jwt_headers, run_id, limit_s=POLL_LIMIT_S):
    deadline = time.time() + limit_s
    delay = 3
    last = None
    while time.time() < deadline:
        resp = http.get(f"{api_base}/test/comparison-runs/{run_id}", headers=jwt_headers, timeout=TIMEOUT)
        assert resp.status_code == 200, f"poll {resp.status_code}: {resp.text[:300]}"
        last = resp.json()
        if last["status"] == "ready":
            return last
        time.sleep(delay)
        delay = min(delay * 1.5, 15)
    pytest.fail(f"run {run_id} not ready within {limit_s}s; last status {last}")


@pytest.fixture(scope="module")
def first_run(http, api_base, jwt_headers, fixture_url):
    """One run against the fixture, shared by the tests below (the pipeline takes a while)."""
    resp = _submit(http, api_base, jwt_headers, fixture_url)
    assert resp.status_code == 202, f"submit {resp.status_code}: {resp.text[:500]}"
    submitted = resp.json()
    started = time.time()
    ready = _poll_until_ready(http, api_base, jwt_headers, submitted["run_id"])
    return {"submitted": submitted, "ready": ready, "ingest_seconds": round(time.time() - started, 1)}


def test_submit_measures_the_scrape_side_and_queues_the_page(first_run, fixture_url):
    submitted = first_run["submitted"]
    assert submitted["ingest"] in ("submitted", "reused")
    assert submitted["origin"] in ("owned", "external")
    assert submitted["allow_list_mode"] in ("restricted", "open")
    article = submitted["article"]
    assert article["website_url"] == fixture_url
    assert article["canonical_url"].startswith("https://")
    assert article["markdown_key"].endswith(".md")
    if submitted["ingest"] == "submitted":
        # R15.1: a run's own ingest lands in the partition, never at the article's edge key.
        assert article["markdown_key"].startswith("comparison/"), article["markdown_key"]
    scrape = submitted["scrape"]
    assert scrape["status"] == 200
    assert scrape["document_bytes"] > 1000
    assert scrape["requests"] >= 2, "the fixture has same-origin subresources to count"
    assert scrape["total_bytes"] >= scrape["document_bytes"]
    assert FIXTURE_SENTENCE in submitted["html"]
    assert "Authorization" not in submitted["html"]


def test_poll_returns_the_published_markdown_body(first_run):
    """R10.3 / R13.3: the body is the fixture's article, not a SPA shell or an error page."""
    api = first_run["ready"]["api"]
    assert FIXTURE_SENTENCE in api["markdown"], api["markdown"][:500]
    assert api["bytes"] == len(api["markdown"].encode("utf-8"))
    assert api["bytes"] > 500
    assert api["content_id"].startswith("cnt_")
    assert api["resolve_ms"] >= 0 and api["fetch_ms"] >= 0
    assert api["provenance"]["canonical_url"] == first_run["submitted"]["article"]["canonical_url"]
    print(f"\n[comparison] ingest-to-published took {first_run['ingest_seconds']}s (feeds tasks.md 4.3)")


def test_the_run_lands_in_the_partition_not_the_catalogue(first_run, outputs):
    """R15.1 / R15.9: the run's record is ``status: comparison`` with an expiry, keyed outside content/."""
    if first_run["submitted"]["ingest"] != "submitted":
        pytest.skip("first run reused an owned, published record; nothing was ingested")
    table = _session().resource("dynamodb").Table(outputs.get("ContentTableName", "cm-content"))
    record = table.get_item(Key={"content_id": first_run["ready"]["api"]["content_id"]}, ConsistentRead=True)["Item"]
    assert record["status"] == "comparison"
    assert record["source"] == "comparison"
    assert record["md_s3_key"].startswith("comparison/")
    assert record.get("expires_at"), "comparison records carry expires_at for the expiry sweep"


def test_the_partition_record_is_not_served_by_the_content_read(first_run, http, api_base, jwt_headers):
    """R15.1.2: the read path serves only ``published``, so a comparison run never makes the scraped
    page readable through ``GET /content?url=`` (the internal Cognito read shares the partner read's
    published-only filter)."""
    if first_run["submitted"]["ingest"] != "submitted":
        pytest.skip("first run reused an owned, published record")
    canonical = first_run["submitted"]["article"]["canonical_url"]
    resp = http.get(f"{api_base}/content", params={"url": canonical}, headers=jwt_headers, timeout=TIMEOUT)
    if resp.status_code == 200:
        assert resp.json().get("content_id") != first_run["ready"]["api"]["content_id"], (
            "the comparison record must never be the one served"
        )
    else:
        assert resp.status_code == 404, resp.text[:300]


def _listed_content_ids(http, api_base, jwt_headers, params):
    """Every ``content_id`` the dashboard list returns for ``params``, following ``next_page_token``."""
    ids, token = [], None
    for _ in range(50):
        query = {**params, "limit": 100, **({"page_token": token} if token else {})}
        resp = http.get(f"{api_base}/content", params=query, headers=jwt_headers, timeout=TIMEOUT)
        assert resp.status_code == 200, f"list {params} {resp.status_code}: {resp.text[:300]}"
        body = resp.json()
        ids += [item["content_id"] for item in body.get("items", [])]
        token = body.get("pagination", {}).get("next_page_token")
        if not token:
            return ids
    pytest.fail(f"list {params} did not end within 50 pages")


def test_the_content_library_lists_the_run_only_when_asked(first_run, http, api_base, jwt_headers):
    """R15.1.5: the dashboard list (Cognito ``GET /content``, no ``status``) leaves comparison records
    out, because they are short-lived and not catalogue content; ``status=comparison`` lists them."""
    if first_run["submitted"]["ingest"] != "submitted":
        pytest.skip("first run reused an owned, published record")
    content_id = first_run["ready"]["api"]["content_id"]
    assert content_id not in _listed_content_ids(http, api_base, jwt_headers, {})
    assert content_id in _listed_content_ids(http, api_base, jwt_headers, {"status": "comparison"})


def test_an_external_run_never_reuses(first_run, http, api_base, jwt_headers, fixture_url):
    """R1.7: only an owned, published article is reused; an external page is ingested again."""
    if first_run["submitted"]["origin"] != "external":
        pytest.skip("the fixture host is a publisher host in this deployment")
    resp = _submit(http, api_base, jwt_headers, fixture_url)
    assert resp.status_code == 202, resp.text[:300]
    body = resp.json()
    assert body["ingest"] == "submitted" and body["origin"] == "external"
    assert not body["run_id"].endswith("-0")


def test_reingest_is_pending_until_the_partition_record_is_republished(
    first_run, http, api_base, jwt_headers, fixture_url
):
    """R1.6: a re-ingest goes to the partition (never a 409, R15), and the poll stays pending until
    the pipeline writes a newer ``updated_at`` on the run's own partition record."""
    resp = _submit(http, api_base, jwt_headers, fixture_url, reingest=True)
    assert resp.status_code == 202, resp.text[:300]
    body = resp.json()
    assert body["ingest"] == "submitted"
    assert not body["run_id"].endswith("-0")
    first_poll = http.get(
        f"{api_base}/test/comparison-runs/{body['run_id']}", headers=jwt_headers, timeout=TIMEOUT
    ).json()
    assert first_poll["status"] == "pending", "the previous publish must not be mistaken for this run's"
    ready = _poll_until_ready(http, api_base, jwt_headers, body["run_id"])
    assert FIXTURE_SENTENCE in ready["api"]["markdown"]
    assert ready["api"]["updated_at"] > first_run["ready"]["api"]["updated_at"]


def test_private_host_is_refused(http, api_base, jwt_headers):
    """R7.2: a URL that resolves to a non-public address is refused before any connection."""
    resp = _submit(http, api_base, jwt_headers, "https://localhost.localdomain/")
    assert resp.status_code == 400, resp.text[:300]
    assert resp.json()["error_code"] in ("invalid_url", "url_not_allowed", "url_unresolvable")


def test_malformed_run_id_is_404(http, api_base, jwt_headers):
    resp = http.get(f"{api_base}/test/comparison-runs/not-a-run", headers=jwt_headers, timeout=TIMEOUT)
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "run_not_found"


def test_unauthenticated_calls_are_rejected(http, api_base):
    resp = http.post(f"{api_base}/test/comparison-runs", json={"url": "https://example.com/"}, timeout=TIMEOUT)
    assert resp.status_code in (401, 403)
