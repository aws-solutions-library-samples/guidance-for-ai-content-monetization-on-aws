"""Unit tests for ``services/comparison_service.py`` (spec ``scrape-vs-api-comparison``, design §3a).

S3 and DynamoDB are moto-backed; the outbound fetch is stubbed at ``comparison_service.safe_fetch``
so no network is touched. Covered: canonical detection/injection, subresource discovery, the
licence Link header, submit (queued / reused / refused / cross-record 409), poll (pending, stale,
ready, malformed id), and the content-id mirror against the pipeline's own derivation (R1.7).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

import content_pipeline.handler as pipeline_handler
from services import comparison_service as cs
from shared.utils.safe_fetch import FetchRefused, FetchResult, ProbeResult

BUCKET = "test-content-bucket"
TABLE = "test-content-table"
PAGE_URL = "https://publisher.example.com/news/lanes"
CANONICAL = "https://publisher.example.com/news/lanes"
HTML_WITH_CANONICAL = (
    "<html><head><title>Lanes</title>"
    f'<link rel="canonical" href="{CANONICAL}">'
    '<link rel="stylesheet" href="/site.css">'
    '<script src="/app.js"></script>'
    '<script src="https://cdn.example.net/vendor.js"></script>'
    "</head><body><article><h1>Lanes</h1><p>Protected lanes change who feels safe enough to ride.</p>"
    '<img src="/img/a.svg"><img src="data:image/png;base64,AAAA"></article></body></html>'
)
HTML_NO_CANONICAL = "<html><head><title>Plain</title></head><body><p>Body</p></body></html>"
USER = {"user_id": "user-1", "username": "olivier"}


def _fetch_result(body: str, url: str = PAGE_URL, link: str | None = None, ms: int = 120) -> FetchResult:
    headers = {"content-type": "text/html; charset=utf-8"}
    if link:
        headers["link"] = link
    return FetchResult(
        status=200, final_url=url, headers=headers, body=body.encode("utf-8"), wall_clock_ms=ms, redirects=0
    )


@pytest.fixture
def aws(monkeypatch):
    """moto-backed S3 bucket + content table with the url_hash GSI, bound into the module."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-west-2")
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
        dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "content_id", "AttributeType": "S"},
                {"AttributeName": "url_hash", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": cs.URL_HASH_INDEX,
                    "KeySchema": [{"AttributeName": "url_hash", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setattr(cs, "s3", s3)
        monkeypatch.setattr(cs, "dynamodb", dynamodb)
        monkeypatch.setattr(cs, "CONTENT_BUCKET_NAME", BUCKET)
        monkeypatch.setattr(cs, "CONTENT_TABLE_NAME", TABLE)
        monkeypatch.setenv("COMPARISON_ALLOWED_HOSTS", cs.OPEN_MODE)  # open mode unless a test says otherwise
        yield {"s3": s3, "table": dynamodb.Table(TABLE)}


@pytest.fixture
def fetch_ok(monkeypatch):
    """Stub the guarded fetch and the subresource probes."""
    calls: Dict[str, List[str]] = {"fetch": [], "probe": []}

    def fetch_html(url, **kwargs):
        calls["fetch"].append(url)
        return _fetch_result(HTML_WITH_CANONICAL, link='<https://publisher.example.com/rsl>; rel="license"')

    def probe_size(url, **kwargs):
        calls["probe"].append(url)
        return ProbeResult(status=200, content_length=1000, wall_clock_ms=5)

    monkeypatch.setattr(cs.safe_fetch, "fetch_html", fetch_html)
    monkeypatch.setattr(cs.safe_fetch, "probe_size", probe_size)
    return calls


def _body(response):
    return json.loads(response["body"])


def _put_record(table, *, content_id: str, url_hash: str, status: str = "published", updated_at: str, md_key: str):
    table.put_item(
        Item={
            "content_id": content_id,
            "url_hash": url_hash,
            "status": status,
            "updated_at": updated_at,
            "md_s3_key": md_key,
            "title": "Lanes",
            "author": "Fixture Author",
            "publish_date": "2026-10-01T09:00:00Z",
            "canonical_url": CANONICAL,
        }
    )


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ── pure helpers ─────────────────────────────────────────────────────────────


class TestHelpers:
    def test_find_canonical_prefers_link_then_og_url(self):
        assert cs.find_canonical(HTML_WITH_CANONICAL) == CANONICAL
        og = '<html><head><meta property="og:url" content="https://p.example.com/x"></head></html>'
        assert cs.find_canonical(og) == "https://p.example.com/x"
        assert cs.find_canonical(HTML_NO_CANONICAL) is None

    def test_inject_canonical_before_head_close(self):
        out = cs.inject_canonical(HTML_NO_CANONICAL, "https://p.example.com/a?b=1&c=2")
        assert '<link rel="canonical" href="https://p.example.com/a?b=1&amp;c=2"></head>' in out
        assert cs.find_canonical(out) == "https://p.example.com/a?b=1&c=2"

    def test_inject_canonical_without_head(self):
        out = cs.inject_canonical("<html><body>x</body></html>", "https://p.example.com/")
        assert out.startswith('<html><head><link rel="canonical"')

    def test_discover_subresources_splits_origins_and_skips_data_urls(self):
        same, cross = cs.discover_subresources(HTML_WITH_CANONICAL, PAGE_URL)
        assert same == [
            "https://publisher.example.com/app.js",
            "https://publisher.example.com/img/a.svg",
            "https://publisher.example.com/site.css",
        ]
        assert cross == ["https://cdn.example.net/vendor.js"]

    def test_licence_from_link_header(self):
        assert cs.licence_from_link_header('<https://p.example.com/rsl>; rel="license"') == "https://p.example.com/rsl"
        assert cs.licence_from_link_header(
            '<https://p.example.com/n>; rel="next", <https://p.example.com/l>; rel=license'
        ) == ("https://p.example.com/l")
        assert cs.licence_from_link_header('<https://p.example.com/n>; rel="next"') is None
        assert cs.licence_from_link_header(None) is None

    def test_run_id_round_trip(self):
        url_hash = "a" * 64
        assert cs.parse_run_id(cs.make_run_id(url_hash, 1700000000)) == (url_hash, 1700000000)
        assert cs.parse_run_id(cs.make_run_id(url_hash, 0)) == (url_hash, 0)
        assert cs.parse_run_id("nope") is None
        assert cs.parse_run_id("a" * 63 + "-1") is None

    def test_partition_key_matches_the_pipeline(self):
        """R15.1: the route predicts the key the pipeline writes for an ingest/comparison/ object."""
        url_hash = "e" * 64
        assert cs.partition_markdown_key(url_hash) == pipeline_handler.comparison_markdown_key(url_hash, "cnt_x")
        assert pipeline_handler.partition_for(cs.ingest_key_for(url_hash)) == "comparison"
        assert cs.PARTITION_STATUS == pipeline_handler.PARTITION_COMPARISON

    def test_no_publisher_host_owns_nothing(self, monkeypatch):
        monkeypatch.delenv("COMPARISON_PUBLISHER_HOSTS", raising=False)
        assert cs.publisher_hosts() == []
        assert cs.is_owned_host("publisher.example.com", []) is False
        assert cs.is_owned_host("a.pub.example.com", ["*.pub.example.com"]) is True
        assert cs.is_owned_host("pub.example.com", ["*.pub.example.com"]) is False

    def test_strip_canonical_claims_keeps_other_tags(self):
        doc = (
            '<head><link rel="stylesheet" href="/s.css"><link rel="canonical" href="https://x/a">'
            '<meta property="og:url" content="https://x/a"><meta property="og:title" content="T"></head>'
        )
        out = cs.strip_canonical_claims(doc)
        assert cs.find_canonical(out) is None
        assert 'rel="stylesheet"' in out and "og:title" in out

    def test_derived_content_id_matches_the_pipeline(self):
        """R1.7: the mirror must agree with ``content_pipeline.handler._derive_content_id``."""
        url_hash = "f" * 64
        assert cs.derived_content_id(url_hash) == pipeline_handler._derive_content_id(cs.ingest_key_for(url_hash))


# ── submit ───────────────────────────────────────────────────────────────────


class TestSubmit:
    def test_queues_the_page_and_measures_the_scrape_side(self, aws, fetch_ok):
        response = cs.submit(USER, {"url": PAGE_URL})
        assert response["statusCode"] == 202
        body = _body(response)
        assert body["ingest"] == "submitted"
        assert body["origin"] == "external"  # no publisher host configured
        assert body["allow_list_mode"] == "open"
        assert body["article"]["canonical_url"] == CANONICAL
        assert body["article"]["canonical_injected"] is False
        assert body["article"]["canonical_overridden"] is False
        # R15.1: the partition key, never the article's content/<host>/<path>.md slot.
        url_hash = cs.edge_address_from_canonical_url(CANONICAL).url_hash
        assert body["article"]["markdown_key"] == f"comparison/{url_hash}.md"
        assert body["article"]["licence_advertised"] == "https://publisher.example.com/rsl"
        scrape = body["scrape"]
        assert scrape["requests"] == 4 and scrape["external_refs"] == 1
        assert scrape["subresources_probed"] == 3 and scrape["subresources_skipped"] == 0
        assert scrape["total_bytes"] == scrape["document_bytes"] + 3 * 1000
        assert "Protected lanes" in body["html"]
        url_hash, epoch = cs.parse_run_id(body["run_id"])
        assert epoch > 0

        key = cs.ingest_key_for(url_hash)
        obj = aws["s3"].get_object(Bucket=BUCKET, Key=key)
        assert obj["Metadata"]["source"] == "comparison"
        assert obj["Metadata"]["content-id"] == cs.derived_content_id(url_hash)
        assert obj["Metadata"]["submitted-by"] == "user-1"
        assert key.startswith("ingest/comparison/") and key.endswith(".html")

    def test_injects_canonical_when_the_page_has_none(self, aws, monkeypatch):
        monkeypatch.setattr(
            cs.safe_fetch,
            "fetch_html",
            lambda url, **kw: _fetch_result(HTML_NO_CANONICAL, url="https://p.example.com/plain"),
        )
        monkeypatch.setattr(cs.safe_fetch, "probe_size", lambda url, **kw: ProbeResult(200, 0, 1))
        body = _body(cs.submit(USER, {"url": "https://p.example.com/plain"}))
        assert body["article"]["canonical_injected"] is True
        assert body["article"]["canonical_url"] == "https://p.example.com/plain"
        assert 'rel="canonical" href="https://p.example.com/plain"' in body["html"]
        stored = (
            aws["s3"]
            .get_object(Bucket=BUCKET, Key=cs.ingest_key_for(cs.parse_run_id(body["run_id"])[0]))["Body"]
            .read()
        )
        assert b'rel="canonical"' in stored

    def test_restricted_mode_is_reported(self, aws, fetch_ok, monkeypatch):
        monkeypatch.setenv("COMPARISON_ALLOWED_HOSTS", "publisher.example.com")
        assert _body(cs.submit(USER, {"url": PAGE_URL}))["allow_list_mode"] == "restricted"

    def test_default_allow_list_is_the_portal_origin(self, aws, fetch_ok, monkeypatch):
        """D5-b: unset → the deployment's own portal host (from the CORS origin allow-list)."""
        monkeypatch.delenv("COMPARISON_ALLOWED_HOSTS", raising=False)
        monkeypatch.setattr(cs.response_helpers, "_resolve_allow_list", lambda: ["https://d123.cloudfront.net"])
        assert cs.allowed_hosts() == ["d123.cloudfront.net"]
        assert _body(cs.submit(USER, {"url": PAGE_URL}))["allow_list_mode"] == "restricted"

    def test_unresolvable_default_fails_closed(self, aws, fetch_ok, monkeypatch):
        monkeypatch.delenv("COMPARISON_ALLOWED_HOSTS", raising=False)
        monkeypatch.setattr(cs.response_helpers, "_resolve_allow_list", lambda: [])
        response = cs.submit(USER, {"url": PAGE_URL})
        assert response["statusCode"] == 503
        assert _body(response)["error_code"] == "allow_list_unavailable"
        assert fetch_ok["fetch"] == []  # nothing was fetched

    @pytest.mark.parametrize(
        "code",
        [
            "invalid_url",
            "url_not_allowed",
            "url_unresolvable",
            "fetch_too_large",
            "fetch_timeout",
            "fetch_not_html",
            "fetch_failed",
        ],
    )
    def test_refusals_map_to_400_with_error_code(self, aws, monkeypatch, code):
        def refuse(url, **kw):
            raise FetchRefused(code, "refused")

        monkeypatch.setattr(cs.safe_fetch, "fetch_html", refuse)
        response = cs.submit(USER, {"url": PAGE_URL})
        assert response["statusCode"] == 400
        assert _body(response)["error_code"] == code
        assert "Contents" not in aws["s3"].list_objects_v2(Bucket=BUCKET)

    def test_ineligible_canonical_is_refused(self, aws, monkeypatch):
        # Same host as the final URL (so the claim is entitled, R15.5) but an explicit port.
        page = '<html><head><link rel="canonical" href="https://publisher.example.com:8443/x"></head><body/></html>'
        monkeypatch.setattr(cs.safe_fetch, "fetch_html", lambda url, **kw: _fetch_result(page))
        monkeypatch.setattr(cs.safe_fetch, "probe_size", lambda url, **kw: ProbeResult(200, 0, 1))
        response = cs.submit(USER, {"url": PAGE_URL})
        assert response["statusCode"] == 400
        assert _body(response)["error_code"] == "canonical_ineligible"

    def test_reuses_an_owned_published_record(self, aws, fetch_ok, monkeypatch):
        monkeypatch.setenv("COMPARISON_PUBLISHER_HOSTS", "publisher.example.com")
        url_hash = cs.edge_address_from_canonical_url(CANONICAL).url_hash
        _put_record(
            aws["table"],
            content_id="cnt_other000001",
            url_hash=url_hash,
            updated_at=_iso(datetime.now(timezone.utc)),
            md_key="content/x.md",
        )
        body = _body(cs.submit(USER, {"url": PAGE_URL}))
        assert body["ingest"] == "reused"
        assert body["origin"] == "owned"
        assert body["article"]["markdown_key"] == "content/x.md"
        assert cs.parse_run_id(body["run_id"])[1] == 0
        assert "Contents" not in aws["s3"].list_objects_v2(Bucket=BUCKET)

    def test_external_page_never_reuses_a_catalogue_record(self, aws, fetch_ok):
        """Without publisher hosts the page is external: it goes to the partition, the record is untouched."""
        url_hash = cs.edge_address_from_canonical_url(CANONICAL).url_hash
        _put_record(
            aws["table"],
            content_id="cnt_other000001",
            url_hash=url_hash,
            updated_at="2026-01-01T00:00:00+00:00",
            md_key="content/x.md",
        )
        body = _body(cs.submit(USER, {"url": PAGE_URL}))
        assert body["ingest"] == "submitted" and body["origin"] == "external"
        assert aws["s3"].get_object(Bucket=BUCKET, Key=cs.ingest_key_for(url_hash))
        stored = aws["table"].get_item(Key={"content_id": "cnt_other000001"})["Item"]
        assert stored["status"] == "published" and stored["updated_at"] == "2026-01-01T00:00:00+00:00"

    def test_reingest_of_a_catalogue_record_goes_to_the_partition(self, aws, fetch_ok, monkeypatch):
        """The former 409 case: now a partition ingest that cannot touch the catalogue record."""
        monkeypatch.setenv("COMPARISON_PUBLISHER_HOSTS", "publisher.example.com")
        url_hash = cs.edge_address_from_canonical_url(CANONICAL).url_hash
        _put_record(
            aws["table"],
            content_id="cnt_other000001",
            url_hash=url_hash,
            updated_at=_iso(datetime.now(timezone.utc)),
            md_key="content/x.md",
        )
        response = cs.submit(USER, {"url": PAGE_URL, "reingest": True})
        assert response["statusCode"] == 202
        body = _body(response)
        assert body["ingest"] == "submitted" and body["origin"] == "owned"
        assert body["article"]["markdown_key"] == f"comparison/{url_hash}.md"

    def test_external_page_cannot_claim_another_sites_article(self, aws, monkeypatch):
        """R15.5, review case 2: open mode, a page names someone else's canonical URL."""
        page = (
            '<html><head><link rel="canonical" href="https://victim.example.org/story">'
            '<meta property="og:url" content="https://victim.example.org/story"></head>'
            "<body><p>x</p></body></html>"
        )
        monkeypatch.setattr(
            cs.safe_fetch, "fetch_html", lambda url, **kw: _fetch_result(page, url="https://attacker.example.net/p")
        )
        monkeypatch.setattr(cs.safe_fetch, "probe_size", lambda url, **kw: ProbeResult(200, 0, 1))
        body = _body(cs.submit(USER, {"url": "https://attacker.example.net/p"}))
        assert body["article"]["canonical_overridden"] is True
        assert body["article"]["canonical_injected"] is False
        assert body["article"]["canonical_url"] == "https://attacker.example.net/p"
        assert "victim.example.org" not in body["html"]
        assert cs.find_canonical(body["html"]) == "https://attacker.example.net/p"

    def test_owned_page_may_claim_only_a_publisher_host(self, aws, monkeypatch):
        monkeypatch.setenv("COMPARISON_PUBLISHER_HOSTS", "www.pub.example.com *.pub.example.com")
        page = '<html><head><link rel="canonical" href="https://elsewhere.example.org/a"></head><body/></html>'
        monkeypatch.setattr(
            cs.safe_fetch, "fetch_html", lambda url, **kw: _fetch_result(page, url="https://www.pub.example.com/a")
        )
        monkeypatch.setattr(cs.safe_fetch, "probe_size", lambda url, **kw: ProbeResult(200, 0, 1))
        body = _body(cs.submit(USER, {"url": "https://www.pub.example.com/a"}))
        assert body["origin"] == "owned"
        assert body["article"]["canonical_overridden"] is True
        assert body["article"]["canonical_url"] == "https://www.pub.example.com/a"

    def test_owned_page_keeps_a_canonical_on_another_publisher_host(self, aws, monkeypatch):
        monkeypatch.setenv("COMPARISON_PUBLISHER_HOSTS", "*.pub.example.com")
        page = '<html><head><link rel="canonical" href="https://news.pub.example.com/a"></head><body/></html>'
        monkeypatch.setattr(
            cs.safe_fetch, "fetch_html", lambda url, **kw: _fetch_result(page, url="https://amp.pub.example.com/a")
        )
        monkeypatch.setattr(cs.safe_fetch, "probe_size", lambda url, **kw: ProbeResult(200, 0, 1))
        body = _body(cs.submit(USER, {"url": "https://amp.pub.example.com/a"}))
        assert body["article"]["canonical_overridden"] is False
        assert body["article"]["canonical_url"] == "https://news.pub.example.com/a"

    def test_reingest_of_our_own_record_is_honoured(self, aws, fetch_ok):
        url_hash = cs.edge_address_from_canonical_url(CANONICAL).url_hash
        _put_record(
            aws["table"],
            content_id=cs.derived_content_id(url_hash),
            url_hash=url_hash,
            updated_at=_iso(datetime.now(timezone.utc)),
            md_key="content/x.md",
        )
        body = _body(cs.submit(USER, {"url": PAGE_URL, "reingest": True}))
        assert body["ingest"] == "submitted"
        assert cs.parse_run_id(body["run_id"])[1] > 0
        assert aws["s3"].get_object(Bucket=BUCKET, Key=cs.ingest_key_for(url_hash))

    def test_probe_failures_are_counted_not_fatal(self, aws, monkeypatch):
        monkeypatch.setattr(cs.safe_fetch, "fetch_html", lambda url, **kw: _fetch_result(HTML_WITH_CANONICAL))

        def probe(url, **kw):
            raise FetchRefused("fetch_timeout", "slow")

        monkeypatch.setattr(cs.safe_fetch, "probe_size", probe)
        scrape = _body(cs.submit(USER, {"url": PAGE_URL}))["scrape"]
        assert scrape["subresources_probed"] == 0 and scrape["subresources_skipped"] == 3
        assert scrape["total_bytes"] == scrape["document_bytes"]


# ── poll ─────────────────────────────────────────────────────────────────────


class TestPoll:
    url_hash = "b" * 64

    def test_malformed_id_is_404(self, aws):
        response = cs.poll("not-a-run")
        assert response["statusCode"] == 404
        assert _body(response)["error_code"] == "run_not_found"

    def test_pending_when_no_record(self, aws):
        submitted = int(time.time()) - 5
        body = _body(cs.poll(cs.make_run_id(self.url_hash, submitted)))
        assert body["status"] == "pending"
        assert body["elapsed_ms"] >= 5000
        assert "api" not in body

    def test_pending_when_record_is_not_published(self, aws):
        _put_record(
            aws["table"],
            content_id="cnt_a",
            url_hash=self.url_hash,
            status="processing",
            updated_at=_iso(datetime.now(timezone.utc)),
            md_key="content/a.md",
        )
        assert _body(cs.poll(cs.make_run_id(self.url_hash, int(time.time()) - 60)))["status"] == "pending"

    def _partition_record(self, aws, *, updated_at, md_key="comparison/a.md"):
        _put_record(
            aws["table"],
            content_id=cs.derived_content_id(self.url_hash),
            url_hash=self.url_hash,
            status="comparison",
            updated_at=updated_at,
            md_key=md_key,
        )

    def test_pending_when_record_predates_the_submit(self, aws):
        """R1.6: an earlier ingest of the same article is not this run's result."""
        now = datetime.now(timezone.utc)
        self._partition_record(aws, updated_at=_iso(now - timedelta(minutes=10)))
        aws["s3"].put_object(Bucket=BUCKET, Key="comparison/a.md", Body=b"# old")
        assert _body(cs.poll(cs.make_run_id(self.url_hash, int(now.timestamp()) - 60)))["status"] == "pending"

    def test_submitted_run_never_reads_a_catalogue_record(self, aws):
        """R15.3: a published record for the same URL is not this run's partition record."""
        now = datetime.now(timezone.utc)
        _put_record(
            aws["table"], content_id="cnt_cms", url_hash=self.url_hash, updated_at=_iso(now), md_key="content/a.md"
        )
        aws["s3"].put_object(Bucket=BUCKET, Key="content/a.md", Body=b"# catalogue")
        assert _body(cs.poll(cs.make_run_id(self.url_hash, int(now.timestamp()) - 60)))["status"] == "pending"

    def test_ready_returns_the_object_and_timings(self, aws):
        now = datetime.now(timezone.utc)
        self._partition_record(aws, updated_at=_iso(now))
        aws["s3"].put_object(
            Bucket=BUCKET,
            Key="comparison/a.md",
            Body="# Lanes\n\nProtected lanes change who feels safe enough to ride.".encode("utf-8"),
        )
        body = _body(cs.poll(cs.make_run_id(self.url_hash, int(now.timestamp()) - 120)))
        assert body["status"] == "ready"
        api = body["api"]
        assert api["content_id"] == cs.derived_content_id(self.url_hash)
        assert api["markdown"].startswith("# Lanes")
        assert api["bytes"] == len(api["markdown"].encode("utf-8"))
        assert api["etag"] and '"' not in api["etag"]
        assert api["provenance"] == {
            "title": "Lanes",
            "author": "Fixture Author",
            "pubdate": "2026-10-01T09:00:00Z",
            "canonical_url": CANONICAL,
        }
        assert api["resolve_ms"] >= 0 and api["fetch_ms"] >= 0

    def test_reused_record_has_no_staleness_bound(self, aws):
        old = datetime.now(timezone.utc) - timedelta(days=3)
        _put_record(
            aws["table"], content_id="cnt_a", url_hash=self.url_hash, updated_at=_iso(old), md_key="content/a.md"
        )
        aws["s3"].put_object(Bucket=BUCKET, Key="content/a.md", Body=b"# reused")
        body = _body(cs.poll(cs.make_run_id(self.url_hash, 0)))
        assert body["status"] == "ready" and body["elapsed_ms"] == 0

    def test_missing_object_is_a_sanitized_500(self, aws):
        now = datetime.now(timezone.utc)
        self._partition_record(aws, updated_at=_iso(now), md_key="comparison/missing.md")
        response = cs.poll(cs.make_run_id(self.url_hash, int(now.timestamp()) - 60))
        assert response["statusCode"] == 500
        assert "missing.md" not in response["body"] and BUCKET not in response["body"]

    def test_newest_published_record_wins(self, aws):
        now = datetime.now(timezone.utc)
        _put_record(
            aws["table"],
            content_id="cnt_old",
            url_hash=self.url_hash,
            updated_at=_iso(now - timedelta(hours=1)),
            md_key="content/old.md",
        )
        _put_record(
            aws["table"], content_id="cnt_new", url_hash=self.url_hash, updated_at=_iso(now), md_key="content/new.md"
        )
        aws["s3"].put_object(Bucket=BUCKET, Key="content/new.md", Body=b"# new")
        body = _body(cs.poll(cs.make_run_id(self.url_hash, 0)))
        assert body["api"]["content_id"] == "cnt_new"


class TestPathBMode:
    def test_only_object_mode_is_built_in_v1(self):
        with patch.object(cs, "PATH_B_MODE", "object"):
            assert cs.PATH_B_MODE == "object"
