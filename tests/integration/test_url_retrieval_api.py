#!/usr/bin/env python3
"""Integration-tier coverage for URL-addressable content retrieval (#14).

Runs against the DEPLOYED partner API, the real ``cm-content`` table and its
``url_hash-index`` GSI, and the real ``cm-usage-events`` store. It covers the behaviour that a
mocked unit test cannot establish:

* the GSI round-trip and its eventual consistency,
* a real ETag / ``If-None-Match`` exchange over HTTP,
* that a ``304`` reaches the usage store as NOTHING — ``tests/content_management/
  test_get_content_by_url.py`` pins the control flow (the early return sits above the emit), but
  only a live read of ``cm-usage-events`` proves no billable row is written,
* canonical-URL normalization as the deployed code actually resolves it,
* the partner-key auth boundary at API Gateway, with the WAF in front.

Follows the conventions in ``tests/integration/_partner_helpers.py``: ``RUN_INTEGRATION=1``
gating, CloudFormation output discovery, a key resolved from the deployed API Gateway key (never
hardcoded), a browser User-Agent because the WebACL blocks the default python-requests UA, and
``poll_for_verdict`` so an edge that has not yet bound a freshly-created key does not read as a
product verdict.

How to run
----------
    RUN_INTEGRATION=1 AWS_PROFILE=genai AWS_REGION=us-west-2 \\
        pytest tests/integration/test_url_retrieval_api.py -v
"""

import hashlib
import time
import uuid
import warnings
from datetime import datetime, timezone

import pytest
import requests

from tests.integration._partner_helpers import (
    BROWSER_UA,
    MAIN_STACK,
    RUN,
    partner_key_value,
    session,
    stack_outputs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set RUN_INTEGRATION=1 to run against live AWS"),
]

TIMEOUT = 45
CONTENT_TABLE = "cm-content"
USAGE_EVENTS_TABLE = "cm-usage-events"

#: NFR-2: a content update must be visible through the url_hash lookup within this budget.
NFR2_CONSISTENCY_BUDGET_S = 5.0
NFR2_POLL_INTERVAL_S = 0.25


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
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
def api_key(outputs):
    """The seeded test partner key, resolved from its API Gateway key id (never stored)."""
    value = partner_key_value(outputs)
    if not value:
        pytest.skip("no partner key: set TEST_PARTNER_API_KEY_ID or deploy the test partner key")
    return value


@pytest.fixture(scope="module")
def http():
    s = requests.Session()
    s.headers.update({"User-Agent": BROWSER_UA})
    yield s
    s.close()


@pytest.fixture(scope="module")
def key_headers(api_key):
    return {"x-api-key": api_key}


@pytest.fixture(scope="module")
def content_table():
    return session().resource("dynamodb").Table(CONTENT_TABLE)


def _url_hash(canonical_url: str) -> str:
    """sha256 of the canonical URL — mirrors shared.utils.edge_address (REQ-1.1)."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def _unique_url(suffix: str = "") -> str:
    return f"https://it-probe.example.com/articles/{uuid.uuid4().hex[:12]}{suffix}"


@pytest.fixture
def fixture_content(content_table):
    """Factory for retrievable content rows; every row created is deleted on teardown.

    Content is written straight to DynamoDB rather than through an API: this feature has no
    authoring endpoint of its own (content arrives via the GEO pipeline). Field names match what
    ``handle_get_content_by_url`` reads — ``content_text`` for the body, ``updated_at`` as the
    ETag source, ``url_hash`` as the GSI key.

    The row is a CONFORMING content record, not the minimum this feature reads. ``cm-content`` is
    shared, and ``tests/content_management/test_existing_data_validation.py`` samples it with a
    ``scan(Limit=1)`` and asserts the record schema — so a fixture carrying only #14's fields
    fails an unrelated suite whenever the scan happens to land on it. Keep ``created_at`` (and
    anything else that test requires) populated here.
    """
    created = []

    def _make(
        canonical_url: str, *, title="IT url-retrieval probe", body=None, updated_at=None, status="published"
    ):
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "content_id": f"it_url_{uuid.uuid4().hex[:10]}",
            "version": 1,
            "canonical_url": canonical_url,
            "url_hash": _url_hash(canonical_url),
            "title": title,
            "content_text": body or f"# {title}\n\nProbe markdown body.",
            "author": "Integration Probe",
            "publication_date": "2026-01-01T00:00:00Z",
            "created_at": now,
            "updated_at": updated_at or now,
            "word_count": 6,
            "language": "en",
            "content_type": "article",
            # A URL read serves only published records, as discovery does.
            "status": status,
        }
        content_table.put_item(Item=item)
        created.append(item)
        return item

    yield _make

    # Teardown must not mask a test failure, but a SILENT teardown failure is worse: these rows
    # live in the shared ``cm-content`` table, and leaked ones broke an unrelated suite once
    # already. Report what could not be deleted instead of swallowing it.
    leaked = []
    for item in created:
        try:
            content_table.delete_item(Key={"content_id": item["content_id"]})
        except Exception as exc:  # noqa: BLE001 - collected and reported, never raised
            leaked.append(f"{item['content_id']}: {exc}")
    if leaked:
        warnings.warn(
            "url-retrieval fixtures were left in the shared cm-content table and must be "
            "deleted by hand: " + "; ".join(leaked),
            stacklevel=1,
        )


def _get_by_url(http, base, headers, url, *, etag=None):
    request_headers = dict(headers)
    if etag is not None:
        request_headers["If-None-Match"] = etag
    return http.get(f"{base}/content", headers=request_headers, params={"url": url}, timeout=TIMEOUT)


def _usage_events_for(content_id: str) -> list:
    """Billable events recorded for one content_id.

    A ``scan`` with a filter rather than a partition ``query``: the raw store is keyed
    ``PK = PARTNER#<partner_id>#<YYYY-MM-DD>`` and the test does not know its own partner_id (the
    authorizer resolves it from the key server-side). Acceptable here because the fixture
    content_id is unique to this run, so the filter matches only events this test caused.

    PAGINATED deliberately. A filtered ``scan`` applies its filter AFTER reading a 1 MB page, so a
    single-page read can return zero matches while the matching item sits on a later page. With an
    append-only, TTL'd event store this becomes a near-certainty as the table grows, and the
    failure would look exactly like "no event was written" -- the symptom of GitLab #58 -- for an
    unrelated reason. Following ``LastEvaluatedKey`` keeps a pass meaningful.
    """
    table = session().resource("dynamodb").Table(USAGE_EVENTS_TABLE)
    items: list = []
    scan_kwargs = {
        "FilterExpression": "content_id = :cid",
        "ExpressionAttributeValues": {":cid": content_id},
    }
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return items
        scan_kwargs["ExclusiveStartKey"] = last_key


# --------------------------------------------------------------------------- #
# REQ-1.2 / REQ-2.1 — the retrieval round-trip
# --------------------------------------------------------------------------- #
def test_retrieval_returns_markdown_metadata_and_etag(http, partner_base, key_headers, fixture_content):
    """``GET /content?url=`` returns 200 with the markdown body, CoMP metadata, and an ETag.

    Exercises the ``url_hash-index`` GSI round-trip end to end: the fixture is written to
    DynamoDB and resolved by canonical URL through the deployed partner API.
    """
    url = _unique_url()
    item = fixture_content(url, title="IT retrieval probe")

    resp = _get_by_url(http, partner_base, key_headers, url)
    assert resp.status_code == 200, f"GET /content?url= -> {resp.status_code}: {resp.text[:300]}"

    etag = resp.headers.get("ETag")
    assert etag, "200 must carry an ETag header (REQ-2.1)"
    assert etag.startswith('"') and etag.endswith('"'), f"ETag must be quoted, got {etag}"

    body = resp.json()
    assert body["content_id"] == item["content_id"]
    assert body["canonical_url"] == url
    assert body["markdown"] == item["content_text"], "markdown body did not round-trip"

    metadata = body["metadata"]
    for field in ("title", "author", "pubdate", "update", "wordcount", "language", "sourcetype"):
        assert field in metadata, f"metadata missing required field {field} (REQ-1.2)"
    assert metadata["title"] == item["title"]
    # sourcetype is the CoMP 0/1/2 enumeration, not the raw content_type string.
    assert metadata["sourcetype"] in (0, 1, 2), (
        f"sourcetype must be a CoMP 0/1/2 code, got {metadata['sourcetype']!r}"
    )


# --------------------------------------------------------------------------- #
# REQ-2.2 / REQ-2.4 — conditional requests
# --------------------------------------------------------------------------- #
def test_conditional_request_matching_etag_is_304(http, partner_base, key_headers, fixture_content):
    """A matching ``If-None-Match`` returns 304 with an empty body; a stale one returns 200."""
    url = _unique_url()
    fixture_content(url, title="IT conditional probe")

    first = _get_by_url(http, partner_base, key_headers, url)
    assert first.status_code == 200, first.text[:300]
    etag = first.headers["ETag"]

    not_modified = _get_by_url(http, partner_base, key_headers, url, etag=etag)
    assert not_modified.status_code == 304, (
        f"matching If-None-Match must be 304, got {not_modified.status_code}: "
        f"{not_modified.text[:200]}"
    )
    assert not_modified.text == "", f"304 must carry an empty body, got {not_modified.text[:120]!r}"

    stale = _get_by_url(http, partner_base, key_headers, url, etag='"not-the-current-etag"')
    assert stale.status_code == 200, (
        f"mismatched If-None-Match must be 200, got {stale.status_code}: {stale.text[:200]}"
    )
    assert stale.json()["markdown"], "200 after a stale conditional must carry the body"


def test_etag_changes_when_content_is_updated(
    http, partner_base, key_headers, content_table, fixture_content
):
    """The ETag changes when the content's mutation field changes (REQ-2.4).

    ``_compute_etag`` derives from ``updated_at``, so a reprocess must produce a new ETag —
    otherwise a client holding the old one would be told 304 for content that has changed.
    """
    url = _unique_url()
    item = fixture_content(url, title="IT etag-invalidation probe")

    before = _get_by_url(http, partner_base, key_headers, url)
    assert before.status_code == 200, before.text[:300]
    etag_before = before.headers["ETag"]

    content_table.update_item(
        Key={"content_id": item["content_id"]},
        UpdateExpression="SET updated_at = :u, content_text = :c",
        ExpressionAttributeValues={
            ":u": datetime.now(timezone.utc).isoformat(),
            ":c": "# Updated\n\nThe body changed, so the ETag must change too.",
        },
    )

    after = _get_by_url(http, partner_base, key_headers, url)
    assert after.status_code == 200, after.text[:300]
    assert after.headers["ETag"] != etag_before, (
        "the ETag did not change after the content was updated — a client holding the old ETag "
        "would be served 304 for changed content (REQ-2.4)"
    )

    revalidate = _get_by_url(http, partner_base, key_headers, url, etag=etag_before)
    assert revalidate.status_code == 200, (
        f"the superseded ETag must no longer produce a 304, got {revalidate.status_code}"
    )


# --------------------------------------------------------------------------- #
# REQ-2.3 — the 304 billing exemption, proven against the usage store
# --------------------------------------------------------------------------- #
def test_200_is_billable_but_304_emits_no_usage_event(
    http, partner_base, key_headers, fixture_content
):
    """A 200 records exactly one billable usage event; a subsequent 304 records none.

    THIS IS THE ASSERTION THE UNIT TEST CANNOT MAKE. The unit test pins the CONTROL FLOW — that
    the 304 early return sits above ``emit_usage_event`` — by asserting a stubbed sink received
    nothing. Only reading the real ``cm-usage-events`` store proves no billable row is actually
    written, which is what REQ-2.3 promises a partner.

    The 200 baseline is asserted FIRST so a zero-delta on the 304 cannot pass vacuously: if the
    emit were broken outright, both counts would be zero and a 304-only test would look green.
    """
    url = _unique_url()
    item = fixture_content(url, title="IT billing-exemption probe")
    content_id = item["content_id"]

    first = _get_by_url(http, partner_base, key_headers, url)
    assert first.status_code == 200, first.text[:300]
    etag = first.headers["ETag"]

    # The sink writes off the hot path, so poll rather than racing it.
    deadline = time.time() + 10.0
    after_200 = []
    while time.time() < deadline:
        after_200 = _usage_events_for(content_id)
        if after_200:
            break
        time.sleep(0.5)

    assert after_200, (
        f"a 200 content retrieval recorded NO billable usage event for {content_id} within 10s — "
        "either the emit is broken or the sink is not wired, and the 304 assertion below would "
        "pass vacuously (REQ-2.3 depends on the 200 being billable)"
    )
    assert len(after_200) == 1, (
        f"a single 200 must record exactly one billable event, got {len(after_200)}"
    )
    baseline = len(after_200)

    not_modified = _get_by_url(http, partner_base, key_headers, url, etag=etag)
    assert not_modified.status_code == 304, (
        f"expected 304 to exercise the exemption, got {not_modified.status_code}"
    )

    # Wait at least as long as the 200's event took to land, so a late write is caught.
    time.sleep(3.0)
    after_304 = _usage_events_for(content_id)

    assert len(after_304) == baseline, (
        f"a 304 Not Modified recorded a billable usage event (count went {baseline} -> "
        f"{len(after_304)}). REQ-2.3 requires the 304 to be exempt; the exemption is the early "
        "return ABOVE emit_usage_event in handle_get_content_by_url and must not be moved below it."
    )


# --------------------------------------------------------------------------- #
# REQ-1.3 — error shapes
# --------------------------------------------------------------------------- #
def test_unknown_url_returns_404(http, partner_base, key_headers):
    """An unknown canonical URL returns 404 with a JSON error body carrying ``content_not_found``."""
    resp = _get_by_url(http, partner_base, key_headers, _unique_url("-never-ingested"))
    assert resp.status_code == 404, (
        f"an unknown URL must be 404, got {resp.status_code}: {resp.text[:200]}"
    )
    problem = resp.json()
    assert isinstance(problem, dict) and problem, f"404 body must be a JSON object: {resp.text[:200]}"
    assert problem.get("error_code") == "content_not_found", resp.text[:200]


def test_unpublished_url_returns_the_unknown_url_404(http, partner_base, key_headers, fixture_content):
    """A record that exists at the URL but is not published is not readable.

    It must get the same 404 body as a URL that was never ingested, so the response does not
    reveal that the record exists.
    """
    item = fixture_content(_unique_url("-unpublished"), status="processing")

    resp = _get_by_url(http, partner_base, key_headers, item["canonical_url"])
    unknown = _get_by_url(http, partner_base, key_headers, _unique_url("-never-ingested"))

    assert resp.status_code == 404, (
        f"an unpublished record must be 404, got {resp.status_code}: {resp.text[:200]}"
    )
    body, unknown_body = resp.json(), unknown.json()
    # The bodies differ only in timestamp and request_id.
    fields = ("error", "message", "error_code")
    assert tuple(map(body.get, fields)) == tuple(map(unknown_body.get, fields)), (
        f"unpublished and unknown URLs must share one 404: {resp.text[:200]} vs {unknown.text[:200]}"
    )


def _wait_until_indexed(content_table, item) -> None:
    """Wait until the ``url_hash`` GSI returns ``item``, so a 404 can't be explained by index lag."""
    started = time.time()
    while time.time() - started < NFR2_CONSISTENCY_BUDGET_S:
        found = content_table.query(
            IndexName="url_hash-index",
            KeyConditionExpression="url_hash = :h",
            ExpressionAttributeValues={":h": item["url_hash"]},
        ).get("Items", [])
        if any(row.get("content_id") == item["content_id"] for row in found):
            return
        time.sleep(NFR2_POLL_INTERVAL_S)
    pytest.fail(f"url_hash-index did not return {item['content_id']} within {NFR2_CONSISTENCY_BUDGET_S}s")


def test_unpublished_url_is_404_once_indexed_and_200_once_published(
    http, partner_base, key_headers, content_table, fixture_content
):
    """Positive control for the published-only rule.

    A fresh row can 404 only because the ``url_hash`` GSI hasn't caught up, which would let the
    unpublished check pass without exercising the filter. So this waits until the index returns
    the unpublished row, asserts the read still 404s, then publishes the row and polls until the
    same URL reads 200.
    """
    item = fixture_content(_unique_url("-unpublished-control"), status="processing")
    _wait_until_indexed(content_table, item)

    hidden = _get_by_url(http, partner_base, key_headers, item["canonical_url"])
    assert hidden.status_code == 404, (
        f"an indexed but unpublished record must be 404, got {hidden.status_code}: {hidden.text[:200]}"
    )

    content_table.update_item(
        Key={"content_id": item["content_id"]},
        UpdateExpression="SET #s = :p, updated_at = :u",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":p": "published", ":u": datetime.now(timezone.utc).isoformat()},
    )

    started = time.time()
    last = None
    while time.time() - started < NFR2_CONSISTENCY_BUDGET_S:
        last = _get_by_url(http, partner_base, key_headers, item["canonical_url"])
        if last.status_code == 200:
            break
        time.sleep(NFR2_POLL_INTERVAL_S)
    assert last is not None and last.status_code == 200, (
        f"the record did not become readable within {NFR2_CONSISTENCY_BUDGET_S}s of publishing: "
        f"{last.status_code if last is not None else None} {last.text[:200] if last is not None else ''}"
    )
    assert last.json()["content_id"] == item["content_id"]


def test_missing_url_parameter_does_not_500(http, partner_base, key_headers):
    """Omitting ``?url=`` is a client error or a list response — never a 500.

    The operation is dual-mode: without ``url`` it is the list surface. What must not happen is
    an unhandled failure, which is what this pins.
    """
    resp = http.get(f"{partner_base}/content", headers=key_headers, timeout=TIMEOUT)
    assert resp.status_code < 500, (
        f"GET /content with no url parameter must not 500, got {resp.status_code}: "
        f"{resp.text[:200]}"
    )


def test_moved_content_redirect_url_is_not_coverable(http, partner_base, key_headers):
    """The ``redirectUrl`` path has no authoring surface, so it cannot be fixtured.

    ``handle_get_content_by_url`` resolves it through ``_check_for_redirect(url_hash)``, but
    nothing CREATES a redirect mapping: no endpoint writes one and the content schema carries no
    redirect field this test could populate. The response SHAPE is unit-pinned by
    ``tests/content_management/test_get_content_by_url.py``; this records that the live path has
    no fixture route.

    WHEN A REDIRECT AUTHORING PATH EXISTS this skip becomes a real assertion: create a moved
    mapping, request the old URL, assert 404 + ``redirectUrl``.
    """
    pytest.skip(
        "no authoring surface creates a redirect mapping, so a moved-content fixture cannot be "
        "built from outside the service (REQ-1.3). Shape is unit-pinned in "
        "tests/content_management/test_get_content_by_url.py."
    )


# --------------------------------------------------------------------------- #
# REQ-1.5 — canonical URL normalization
# --------------------------------------------------------------------------- #
def test_normalization_resolves_citation_spellings_to_one_document(
    http, partner_base, key_headers, fixture_content
):
    """Trailing-slash and query-string spellings of one URL resolve to the SAME document.

    This is the property REQ-1.5 exists for: an agent citing a URL with a tracking query or a
    trailing slash must still reach the content. Normalization happens at both ends — the
    fixture's ``url_hash`` is derived from the canonical form, and the request URL is normalized
    before the GSI lookup — so a match proves the two agree.
    """
    canonical = _unique_url()
    item = fixture_content(canonical, title="IT normalization probe")

    # The query string is stripped by the normalizer. The trailing slash is NOT -- it is
    # load-bearing in the edge address (`/a/b/` -> `.../a/b/index.md`, `/a/b` -> `.../a/b.md`),
    # so collapsing it there would change the Shape 1 object key. REQ-1.5's citation tolerance is
    # therefore met on the READ path: `handle_get_content_by_url` retries the slash-toggled
    # spelling after the exact one misses. Both kinds of spelling must resolve here.
    for spelling in (
        canonical,
        f"{canonical}/",
        f"{canonical}?utm_source=ai",
        f"{canonical}/?utm_source=ai",
    ):
        resp = _get_by_url(http, partner_base, key_headers, spelling)
        assert resp.status_code == 200, (
            f"citation spelling {spelling!r} did not resolve: {resp.status_code} "
            f"{resp.text[:200]} (REQ-1.5)"
        )
        assert resp.json()["content_id"] == item["content_id"], (
            f"spelling {spelling!r} resolved to a DIFFERENT document (REQ-1.5)"
        )


def test_http_and_https_are_distinct_documents(http, partner_base, key_headers, fixture_content):
    """``http`` and ``https`` spellings are NOT interchangeable — they hash differently.

    Pinned at the unit level by ``tests/content_pipeline/test_edge_address.py``; asserted here as
    deployed behaviour so normalization is not quietly widened to collapse the scheme, which
    would let an ``http`` citation reach content published only over ``https``.
    """
    https_url = _unique_url()
    fixture_content(https_url, title="IT scheme probe")

    resp = _get_by_url(http, partner_base, key_headers, https_url.replace("https://", "http://", 1))
    assert resp.status_code == 404, (
        f"the http:// spelling of an https:// document must NOT resolve, got {resp.status_code} "
        "(REQ-1.5)"
    )


# --------------------------------------------------------------------------- #
# REQ-1.2 — the auth boundary
# --------------------------------------------------------------------------- #
def test_retrieval_requires_a_key(http, partner_base, fixture_content):
    """``GET /content?url=`` is key-gated — an unauthenticated call is rejected.

    API Gateway validates the native key before any handler runs, so the rejection is the
    gateway's (``403 invalid_api_key``) rather than the handler's.
    """
    url = _unique_url()
    fixture_content(url, title="IT auth-boundary probe")

    resp = http.get(f"{partner_base}/content", params={"url": url}, timeout=TIMEOUT)
    assert resp.status_code in (401, 403), (
        f"unauthenticated GET /content?url= must be 401/403, got {resp.status_code}: "
        f"{resp.text[:200]}"
    )
    assert "markdown" not in resp.text, "rejected request leaked the content body"


# --------------------------------------------------------------------------- #
# NFR-2 — consistency budget
# --------------------------------------------------------------------------- #
def test_update_converges_in_the_url_lookup_within_budget(
    http, partner_base, key_headers, content_table, fixture_content
):
    """A content update is visible through the ``url_hash`` lookup within the NFR-2 budget.

    The GSI is eventually consistent, so this can only be established against real DynamoDB:
    write, then poll the URL-addressable path until the new value appears.

    SCOPE NOTE — NFR-2 names both the ``content_id`` and ``url_hash`` lookups. The
    ``content_id`` read is a base-table read, immediately consistent by construction, so the
    convergence risk lives entirely on the GSI path asserted here.
    """
    url = _unique_url()
    item = fixture_content(url, title="IT consistency probe")

    first = _get_by_url(http, partner_base, key_headers, url)
    assert first.status_code == 200, first.text[:300]

    new_title = f"IT consistency converged {uuid.uuid4().hex[:6]}"
    content_table.update_item(
        Key={"content_id": item["content_id"]},
        UpdateExpression="SET title = :t, updated_at = :u",
        ExpressionAttributeValues={":t": new_title, ":u": datetime.now(timezone.utc).isoformat()},
    )

    started = time.time()
    converged_at = None
    last_seen = None
    while time.time() - started < NFR2_CONSISTENCY_BUDGET_S:
        resp = _get_by_url(http, partner_base, key_headers, url)
        if resp.status_code == 200:
            last_seen = resp.json()["metadata"]["title"]
            if last_seen == new_title:
                converged_at = time.time() - started
                break
        time.sleep(NFR2_POLL_INTERVAL_S)

    assert converged_at is not None, (
        f"the url_hash lookup did not reflect the update within the NFR-2 budget of "
        f"{NFR2_CONSISTENCY_BUDGET_S}s (last title seen: {last_seen!r}). Either the GSI is "
        "slower than the budget — measure it and revise NFR-2 — or the read path is caching."
    )
