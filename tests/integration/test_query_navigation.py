"""Deployed navigation acceptance: ``POST /query`` -> select a result -> read it.

For each authentication method (partner key on the partner plane, Cognito on the internal
plane) and for content with and without a canonical URL, the result's read reference is
followed with the SAME credential that made the query, and must return ``200`` for the same
``content_id``. Also pinned: a key whose partner lacks ``listContentOrGetByUrl`` is refused the
read (``403 endpoint_not_allowed``), an unpublished ``content_id`` is ``404``, an invalid
selector is ``400 invalid_read_selector``, and every read form returns the Markdown the pipeline
stored in S3.

Content: two published *articles* (CoMP Text assets are articles, reports or text), under stable
ids so reruns overwrite rather than accumulate. ``cnt_golden10_json`` is the schema 1.0 golden
JSON fixture the pipeline tier keeps in the sandbox, and has a canonical URL;
``cnt_itnav_nourl`` is a small JSON article with none. Either is ingested here, through the same
S3 ``ingest/`` path ``test_ingest_to_search.py`` uses, only when it is absent.

Runs against the LIVE deployment; skipped unless ``RUN_INTEGRATION=1``. The Cognito half also
needs ``COGNITO_USERNAME``/``COGNITO_PASSWORD`` (never hardcoded), like the lifecycle tier.
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from tests.integration._partner_helpers import (
    BROWSER_UA,
    MAIN_STACK,
    RUN,
    ThrowawayPartner,
    is_edge_propagation,
    json_body,
    partner_key_value,
    session,
    stack_outputs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
    # Pipeline ingestion (only when the fixtures are absent) and key propagation both take minutes.
    pytest.mark.timeout(900),
]

TIMEOUT = 45
LICENSE_ID = os.environ.get("IAB_TEST_LICENSE_ID", "lic_default_v1")
COGNITO_USERNAME = os.environ.get("COGNITO_USERNAME", "demo-user@example.com")
COGNITO_PASSWORD = os.environ.get("COGNITO_PASSWORD")

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "content_pipeline" / "fixtures" / "deterministic_markdown"
WITH_URL = "cnt_golden10_json"  # article, canonical https://example.com/articles/2026/item
WITHOUT_URL = "cnt_itnav_nourl"  # article, no canonical URL
_NO_URL_ARTICLE = {
    "title": "Query navigation probe without a canonical URL",
    "content_type": "article",
    "content_markdown": "# Query navigation probe\n\nAn article published without a canonical URL.",
    "author": "Integration Tier",
}
_FIXTURES = {
    WITH_URL: ("ingest/golden-markdown-1.0/cnt_golden10_json.json", lambda: (GOLDEN_DIR / "article.json").read_bytes()),
    WITHOUT_URL: ("ingest/query-navigation/cnt_itnav_nourl.json", lambda: json.dumps(_NO_URL_ARTICLE).encode()),
}
DISCOVERY_DEADLINE_S = 420
PROPAGATION_DEADLINE_S = 240
POLL_S = 10


# --------------------------------------------------------------------------- #
# Deployment and credentials
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def outputs():
    found = stack_outputs(MAIN_STACK)
    try:
        found = {**stack_outputs("CMCognitoStack"), **found}
    except Exception:  # noqa: BLE001 - Cognito outputs only matter for the Cognito half
        pass
    return found


@pytest.fixture(scope="module")
def partner_base(outputs):
    url = outputs.get("PartnerApiUrl")
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
def key_headers(outputs):
    value = partner_key_value(outputs)
    if not value:
        pytest.skip("no test partner key: deploy ContentMonetizationStack")
    return {"x-api-key": value}


@pytest.fixture(scope="module")
def cognito_headers(outputs):
    if not COGNITO_PASSWORD:
        pytest.skip("set COGNITO_USERNAME/COGNITO_PASSWORD to run the Cognito navigation (never hardcoded)")
    pool = os.environ.get("COGNITO_USER_POOL_ID") or outputs.get("UserPoolId")
    client = os.environ.get("COGNITO_CLIENT_ID") or outputs.get("UserPoolClientId")
    if not pool or not client:
        pytest.skip("no Cognito config: set COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID or deploy CMCognitoStack")
    resp = (
        session()
        .client("cognito-idp")
        .admin_initiate_auth(
            UserPoolId=pool,
            ClientId=client,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": COGNITO_USERNAME, "PASSWORD": COGNITO_PASSWORD},
        )
    )
    token = resp.get("AuthenticationResult", {}).get("IdToken")
    if not token:
        pytest.skip(f"Cognito auth returned no IdToken (challenge: {resp.get('ChallengeName')})")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def content_table(outputs):
    return session().resource("dynamodb").Table(outputs.get("ContentTableName", "cm-content"))


# --------------------------------------------------------------------------- #
# Content: two published, discoverable items, one per canonical-URL case
# --------------------------------------------------------------------------- #
def _comp_query(content_ids):
    return {
        "aisystem": {
            "name": "query-navigation-acceptance",
            "aisysuse": {
                "lid": LICENSE_ID,
                "aiauth": 2,
                "scope": 5,
                "ext": {"discovery": {"content_ids": list(content_ids)}},
            },
        }
    }


def _discovered_ids(http, base, headers):
    resp = http.post(f"{base}/query", json=_comp_query(_FIXTURES), headers=headers, timeout=TIMEOUT)
    if resp.status_code != 200:
        return set()
    return {asset["ext"]["content_id"] for asset in json_body(resp)["package"]["scope"].get("text", [])}


def _ingest_missing(outputs, missing):
    s3 = session().client("s3")
    for content_id in missing:
        key, body = _FIXTURES[content_id]
        s3.put_object(
            Bucket=outputs["ContentBucketName"],
            Key=key,
            Body=body(),
            ContentType="application/json",
            Metadata={"content-id": content_id},
        )


def _fixture_records(content_table):
    return {cid: content_table.get_item(Key={"content_id": cid}, ConsistentRead=True).get("Item") for cid in _FIXTURES}


def _unpublished(records):
    return sorted(cid for cid, item in records.items() if not item or item.get("status") != "published")


@pytest.fixture(scope="module")
def navigable(outputs, content_table, http, partner_base, key_headers):
    """Both fixtures are published and discoverable; ingest any that are absent, then wait.

    The wait checks the records as well as discovery. Discovery alone isn't enough: a deploy that
    clears ``cm-content`` leaves the fixtures' search documents behind, so ``/query`` can return
    both fixtures before the pipeline has written their records again.
    """
    records = _fixture_records(content_table)
    missing = _unpublished(records)
    if missing:
        _ingest_missing(outputs, missing)
    deadline = time.monotonic() + DISCOVERY_DEADLINE_S
    while True:
        records = _fixture_records(content_table)
        unpublished = _unpublished(records)
        found = _discovered_ids(http, partner_base, key_headers) if not unpublished else set()
        if not unpublished and found >= set(_FIXTURES):
            break
        if time.monotonic() >= deadline:
            pytest.fail(
                f"fixtures not ready within {DISCOVERY_DEADLINE_S}s: unpublished {unpublished}, "
                f"discoverable {sorted(found)}"
            )
        time.sleep(POLL_S)
    assert records[WITH_URL].get("canonical_url"), f"{WITH_URL} must have a canonical URL"
    assert not records[WITHOUT_URL].get("canonical_url"), f"{WITHOUT_URL} must have no canonical URL"
    return records


def _assets_by_id(package_response):
    return {asset["ext"]["content_id"]: asset for asset in package_response["package"]["scope"]["text"]}


# --------------------------------------------------------------------------- #
# query -> select -> read, partner key
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("content_id", [WITH_URL, WITHOUT_URL])
def test_partner_comp_result_reads_back_with_the_same_key(http, partner_base, key_headers, navigable, content_id):
    resp = http.post(f"{partner_base}/query", json=_comp_query([content_id]), headers=key_headers, timeout=TIMEOUT)
    assert resp.status_code == 200, resp.text[:300]
    data = resp.json()
    asset = _assets_by_id(data)[content_id]
    reference = asset["ext"]["url"]
    assert reference.startswith(f"{partner_base}/content?")
    expected_selector = "url" if content_id == WITH_URL else "content_id"
    assert set(parse_qs(urlsplit(reference).query)) == {expected_selector}
    assert data["package"]["retrieval"]["endpoint"] == f"{partner_base}/content"
    assert data["package"]["retrieval"]["auth"] == 1

    read = http.get(reference, headers=key_headers, timeout=TIMEOUT)
    assert read.status_code == 200, f"{reference} -> {read.status_code}: {read.text[:300]}"
    assert read.json()["content_id"] == content_id
    assert read.headers.get("ETag")


def test_partner_standard_result_carries_a_followable_url(http, partner_base, key_headers, navigable):
    title = navigable[WITHOUT_URL]["title"]
    resp = http.post(
        f"{partner_base}/query", json={"query": title, "max_results": 50}, headers=key_headers, timeout=TIMEOUT
    )
    assert resp.status_code == 200, resp.text[:300]
    results = resp.json()["results"]
    assert results and all(item.get("content_id") and item.get("url") for item in results)
    match = next((item for item in results if item["content_id"] == WITHOUT_URL), None)
    assert match, f"{WITHOUT_URL} not in the top 50 for its own title {title!r}"
    assert match["url"] == f"{partner_base}/content?content_id={WITHOUT_URL}"

    read = http.get(match["url"], headers=key_headers, timeout=TIMEOUT)
    assert read.status_code == 200, read.text[:300]
    assert read.json()["content_id"] == WITHOUT_URL


def test_content_id_read_returns_the_same_document_and_etag_as_the_url_read(http, partner_base, key_headers, navigable):
    canonical = navigable[WITH_URL]["canonical_url"]
    by_url = http.get(f"{partner_base}/content", params={"url": canonical}, headers=key_headers, timeout=TIMEOUT)
    by_id = http.get(f"{partner_base}/content", params={"content_id": WITH_URL}, headers=key_headers, timeout=TIMEOUT)

    assert by_url.status_code == by_id.status_code == 200
    assert by_url.json() == by_id.json()
    assert by_url.headers["ETag"] == by_id.headers["ETag"]

    not_modified = http.get(
        f"{partner_base}/content",
        params={"content_id": WITH_URL},
        headers={**key_headers, "If-None-Match": by_id.headers["ETag"]},
        timeout=TIMEOUT,
    )
    assert not_modified.status_code == 304


# --------------------------------------------------------------------------- #
# query -> select -> read, Cognito
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("content_id", [WITH_URL, WITHOUT_URL])
def test_cognito_comp_result_reads_back_with_the_same_token(
    http, internal_base, cognito_headers, navigable, content_id
):
    resp = http.post(f"{internal_base}/query", json=_comp_query([content_id]), headers=cognito_headers, timeout=TIMEOUT)
    assert resp.status_code == 200, resp.text[:300]
    data = resp.json()
    reference = _assets_by_id(data)[content_id]["ext"]["url"]
    assert reference == f"{internal_base}/content/{content_id}"
    assert data["package"]["retrieval"]["auth"] == 2

    read = http.get(reference, headers=cognito_headers, timeout=TIMEOUT)
    assert read.status_code == 200, f"{reference} -> {read.status_code}: {read.text[:300]}"
    assert read.json()["content_id"] == content_id


def test_cognito_standard_result_url_reads_back(http, internal_base, cognito_headers, navigable):
    title = navigable[WITH_URL]["title"]
    resp = http.post(
        f"{internal_base}/query", json={"query": title, "max_results": 50}, headers=cognito_headers, timeout=TIMEOUT
    )
    assert resp.status_code == 200, resp.text[:300]
    match = next((item for item in resp.json()["results"] if item["content_id"] == WITH_URL), None)
    assert match, f"{WITH_URL} not in the top 50 for its own title {title!r}"
    assert match["url"] == f"{internal_base}/content/{WITH_URL}"

    read = http.get(match["url"], headers=cognito_headers, timeout=TIMEOUT)
    assert read.status_code == 200, read.text[:300]


@pytest.mark.parametrize("selector", ["url", "content_id"])
def test_cognito_single_document_read_matches_the_partner_read(
    http, partner_base, internal_base, key_headers, cognito_headers, navigable, selector
):
    """On the internal plane, a Cognito ``GET /content?url=`` or ``?content_id=`` is the partner read.

    Same body, same ``ETag``, and a ``304`` on that ``ETag``. It used to be refused with
    ``403 invalid_api_key``, because the internal ``GET /content`` went to a partner-key-only handler.
    """
    record = navigable[WITH_URL]
    params = {"url": record["canonical_url"]} if selector == "url" else {"content_id": WITH_URL}
    partner = http.get(f"{partner_base}/content", params=params, headers=key_headers, timeout=TIMEOUT)
    internal = http.get(f"{internal_base}/content", params=params, headers=cognito_headers, timeout=TIMEOUT)

    assert partner.status_code == 200, partner.text[:300]
    assert internal.status_code == 200, f"Cognito GET /content?{selector}= -> {internal.status_code}: {internal.text[:300]}"
    assert internal.json() == partner.json()
    assert internal.headers["ETag"] == partner.headers["ETag"]

    revalidated = http.get(
        f"{internal_base}/content",
        params=params,
        headers={**cognito_headers, "If-None-Match": internal.headers["ETag"]},
        timeout=TIMEOUT,
    )
    assert revalidated.status_code == 304, revalidated.text[:300]


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
def test_unpublished_content_id_is_404(http, partner_base, key_headers, content_table):
    content_id = f"cnt_itnav{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    content_table.put_item(
        Item={
            "content_id": content_id,
            "status": "processing",
            "title": "query-navigation unpublished probe",
            "content_text": "not readable",
            "updated_at": now,
        }
    )
    try:
        unpublished = http.get(
            f"{partner_base}/content", params={"content_id": content_id}, headers=key_headers, timeout=TIMEOUT
        )
        unknown = http.get(
            f"{partner_base}/content", params={"content_id": f"{content_id}x"}, headers=key_headers, timeout=TIMEOUT
        )
    finally:
        content_table.delete_item(Key={"content_id": content_id})

    assert unpublished.status_code == unknown.status_code == 404
    assert unpublished.json()["message"] == unknown.json()["message"]
    assert unpublished.json().get("error_code") == unknown.json().get("error_code") == "content_not_found"


@pytest.mark.parametrize(
    "params", [{}, {"url": "https://example.com/a", "content_id": WITH_URL}, {"content_id": "../x"}]
)
def test_invalid_read_selector_is_400(http, partner_base, key_headers, params):
    resp = http.get(f"{partner_base}/content", params=params, headers=key_headers, timeout=TIMEOUT)
    assert resp.status_code == 400, resp.text[:300]
    assert resp.json()["error_code"] == "invalid_read_selector"


def test_read_is_refused_to_a_partner_without_the_read_operation(http, partner_base, outputs, navigable):
    with ThrowawayPartner(outputs, status="active", allowed_endpoints=["queryContent"]) as key_value:
        deadline = time.monotonic() + PROPAGATION_DEADLINE_S
        while True:
            resp = http.get(
                f"{partner_base}/content",
                params={"content_id": WITHOUT_URL},
                headers={"x-api-key": key_value},
                timeout=TIMEOUT,
            )
            parsed = json_body(resp)
            settled = not is_edge_propagation(resp.status_code, parsed) and resp.status_code < 500
            if settled or time.monotonic() >= deadline:
                break
            time.sleep(5)
    assert resp.status_code == 403, resp.text[:300]
    assert parsed.get("error_code") == "endpoint_not_allowed"


def _stored_markdown(outputs, record):
    """The Markdown object the pipeline wrote for ``record``, read straight from S3."""
    key = record.get("md_s3_key")
    assert key, f"{record.get('content_id')} has no md_s3_key, so it was not pipeline-ingested"
    stored = session().client("s3").get_object(Bucket=outputs["ContentBucketName"], Key=key)
    markdown = stored["Body"].read().decode("utf-8")
    # An empty stored body would let "the read equals the stored object" pass on the very
    # symptom these tests exist to catch.
    assert markdown.strip(), f"the stored Markdown for {record.get('content_id')} is empty"
    return markdown


@pytest.mark.parametrize("content_id", [WITH_URL, WITHOUT_URL])
def test_content_id_read_returns_the_stored_markdown(
    http, partner_base, key_headers, outputs, navigable, content_id
):
    """A pipeline-ingested document reads back with exactly the Markdown the pipeline stored.

    The pipeline keeps the body only in S3 (``md_s3_key``), so a read that served the record's
    ``content_text`` returned an empty ``markdown`` for every such document.
    """
    read = http.get(f"{partner_base}/content", params={"content_id": content_id}, headers=key_headers, timeout=TIMEOUT)
    assert read.status_code == 200, read.text[:300]
    assert read.json()["markdown"] == _stored_markdown(outputs, navigable[content_id])


def test_url_read_returns_the_stored_markdown(http, partner_base, key_headers, outputs, navigable):
    """Ingest -> ``GET /content?url=`` -> the stored Markdown, for the article with a canonical URL."""
    record = navigable[WITH_URL]
    read = http.get(
        f"{partner_base}/content", params={"url": record["canonical_url"]}, headers=key_headers, timeout=TIMEOUT
    )
    assert read.status_code == 200, read.text[:300]
    body = read.json()
    assert body["content_id"] == WITH_URL
    assert body["markdown"] == _stored_markdown(outputs, record)


def test_cognito_details_read_returns_the_stored_markdown(http, internal_base, cognito_headers, outputs, navigable):
    """The internal ``GET /content/{content_id}`` (the Cognito read reference) carries the body too."""
    read = http.get(f"{internal_base}/content/{WITHOUT_URL}", headers=cognito_headers, timeout=TIMEOUT)
    assert read.status_code == 200, read.text[:300]
    assert read.json()["content_text"] == _stored_markdown(outputs, navigable[WITHOUT_URL])
