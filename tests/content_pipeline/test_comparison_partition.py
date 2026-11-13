"""The pipeline's comparison partition (spec scrape-vs-api-comparison, R15.1).

A source object under ``ingest/comparison/`` (written only by the comparison run route) never becomes
a catalogue record: its Markdown goes to ``comparison/<url_hash>.md`` (outside ``content/``), it gets
no search document, and its record has ``status: comparison`` with ``source`` and ``expires_at``.
Every other ingest is unchanged. Regression guard from the R15 review: a comparison run must
not be able to take an article's ``content/<host>/<path>.md`` slot.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

import content_pipeline.handler as h

CANONICAL = "https://news.example.com/a/b/"


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest.fixture
def stub_s3(monkeypatch):
    s3 = MagicMock()
    monkeypatch.setattr(h, "s3", s3)
    return s3


@pytest.fixture
def fake_extract(monkeypatch):
    monkeypatch.setattr(
        h,
        "extract",
        lambda raw, key: {
            "title": "Edge",
            "markdown": "# Edge",
            "body_markdown": "Body.",
            "text": "body",
            "format": "html",
            "canonical_url": CANONICAL,
        },
    )


def _markdown_write(stub_s3, key):
    return next(call.kwargs for call in stub_s3.put_object.call_args_list if call.kwargs["Key"] == key)


def _serve_json(stub_s3, artifacts):
    stub_s3.get_object.side_effect = lambda Bucket, Key: {"Body": _FakeBody(json.dumps(artifacts[Key]).encode())}  # noqa: N803


# ── partition_for / key ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "partition"),
    [
        ("ingest/comparison/0f3c9a1b2c3d.html", "comparison"),
        ("ingest/article.html", "catalogue"),
        ("ingest/cnt_abc.json", "catalogue"),
        # Only the exact prefix switches; a look-alike path does not.
        ("ingest/comparisons/x.html", "catalogue"),
        ("ingest/x/comparison/y.html", "catalogue"),
    ],
)
def test_partition_for_uses_the_exact_prefix(key, partition):
    assert h.partition_for(key) == partition


def test_comparison_markdown_key_is_never_under_content():
    assert h.comparison_markdown_key("abc", "cnt_1") == "comparison/abc.md"
    assert h.comparison_markdown_key(None, "cnt_1") == "comparison/cnt_1.md"
    assert not h.comparison_markdown_key("abc", "cnt_1").startswith("content/")


# ── extract → render ─────────────────────────────────────────────────────────


def test_comparison_markdown_lands_in_the_partition_not_the_edge_key(stub_s3, fake_extract):
    """extract chooses the key, render writes it: the partition key must survive the hand-off."""
    stub_s3.get_object.return_value = {"Body": _FakeBody(b"<html></html>"), "Metadata": {"content-id": "cnt_cmp"}}

    result = h.step_extract({"bucket": "b", "key": "ingest/comparison/0f3c9a1b2c3d.html"})

    assert result["partition"] == "comparison"
    assert result["md_key"] == f"comparison/{result['url_hash']}.md"
    artifact = json.loads(_markdown_write(stub_s3, result["extracted_key"])["Body"])
    assert not any(call.kwargs["Key"].startswith("content/") for call in stub_s3.put_object.call_args_list)

    stub_s3.reset_mock()
    _serve_json(stub_s3, {result["extracted_key"]: artifact, "en.json": {"answer_capsule": "Cap."}})
    stub_s3.put_object.return_value = {"ETag": '"e"'}
    rendered = h.step_render({**result, "enrichment_key": "en.json"})

    # The partition travels on to index and persist; the only object written is the partition's.
    assert rendered["partition"] == "comparison"
    assert [call.kwargs["Key"] for call in stub_s3.put_object.call_args_list] == [result["md_key"]]
    assert _markdown_write(stub_s3, result["md_key"])["Metadata"]["content-id"] == "cnt_cmp"


def test_catalogue_extract_is_unchanged(stub_s3, fake_extract):
    stub_s3.get_object.return_value = {"Body": _FakeBody(b"<html></html>"), "Metadata": {"content-id": "cnt_cat"}}

    result = h.step_extract({"bucket": "b", "key": "ingest/edge.html"})

    assert result["partition"] == "catalogue"
    assert result["md_key"] == "content/news.example.com/a/b/index.md"


def test_comparison_and_catalogue_ingest_of_one_article_do_not_collide(stub_s3, fake_extract):
    """The CMS ingest of an article a comparison already ran takes its normal key (no collision)."""
    stub_s3.get_object.return_value = {"Body": _FakeBody(b"<html></html>"), "Metadata": {"content-id": "cnt_cmp"}}
    comparison = h.step_extract({"bucket": "b", "key": "ingest/comparison/0f3c9a1b2c3d.html"})
    stub_s3.get_object.return_value = {"Body": _FakeBody(b"<html></html>"), "Metadata": {"content-id": "cnt_cms"}}
    catalogue = h.step_extract({"bucket": "b", "key": "ingest/article.html"})

    assert comparison["url_hash"] == catalogue["url_hash"]
    assert comparison["md_key"] != catalogue["md_key"]


# ── index ────────────────────────────────────────────────────────────────────


def test_comparison_is_not_indexed(stub_s3, monkeypatch):
    index = MagicMock()
    monkeypatch.setattr(h, "index_document", index)
    event = {"content_id": "cnt_cmp", "bucket": "b", "partition": "comparison"}

    assert h.step_index(event) == event
    index.assert_not_called()
    stub_s3.get_object.assert_not_called()


# ── persist ──────────────────────────────────────────────────────────────────


def _persist(stub_s3, monkeypatch, partition):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=h.CONTENT_TABLE_NAME,
        KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    monkeypatch.setattr(h, "dynamodb", dynamodb)
    artifacts = {
        "ex.json": {"title": "T", "text": "a b", "canonical_url": CANONICAL, "url_hash": "abc", "edge_eligible": True},
        "en.json": {"classification_failed": False},
    }
    _serve_json(stub_s3, artifacts)
    event = {
        "content_id": "cnt_p",
        "bucket": "b",
        "extracted_key": "ex.json",
        "enrichment_key": "en.json",
        "md_key": "comparison/abc.md",
    }
    if partition is not None:
        event["partition"] = partition
    h.step_persist(event)
    return dynamodb.Table(h.CONTENT_TABLE_NAME).get_item(Key={"content_id": "cnt_p"})["Item"]


@mock_aws
def test_comparison_persist_sets_status_source_and_expiry(stub_s3, monkeypatch):
    stored = _persist(stub_s3, monkeypatch, "comparison")

    assert stored["status"] == "comparison"
    assert stored["source"] == "comparison"
    created = datetime.fromisoformat(stored["created_at"])
    expires = datetime.fromisoformat(stored["expires_at"])
    assert (expires - created).days == h.COMPARISON_RETENTION_DAYS


@pytest.mark.parametrize("partition", ["catalogue", None])
@mock_aws
def test_catalogue_persist_is_unchanged(stub_s3, monkeypatch, partition):
    stored = _persist(stub_s3, monkeypatch, partition)

    assert stored["status"] == "published"
    assert "source" not in stored
    assert "expires_at" not in stored
