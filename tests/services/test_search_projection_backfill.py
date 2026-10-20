"""Unit tests for the explicit OpenSearch projection backfill Lambda."""

import json
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from search_projection_backfill import handler

REGION = "us-west-2"
BUCKET = "search-projection-backfill-test"
TABLE = "cm-content"


def _table():
    resource = boto3.resource("dynamodb", region_name=REGION)
    return resource.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _seed_artifacts(s3, content_id="cnt_1"):
    markdown_key = f"content/{content_id}.md"
    embedding_key = f"embeddings/{content_id}.json"
    s3.put_object(Bucket=BUCKET, Key=markdown_key, Body=b"# Title\n\nBody")
    s3.put_object(
        Bucket=BUCKET,
        Key=embedding_key,
        Body=json.dumps({"embedding": [0.1, 0.2]}).encode(),
    )
    return markdown_key, embedding_key


def _record(content_id, markdown_key, embedding_key, **overrides):
    result = {
        "content_id": content_id,
        "status": "published",
        "title": "Title",
        "content_type": "article",
        "iab_categories": [{"id": "111", "name": "Medical Health", "tier_path": ["Medical Health"]}],
        "topics": ["Healthcare"],
        "answer_capsule": "Summary",
        "language": "en",
        "license_id": "lic_1",
        "publish_date": "2026-01-01T00:00:00Z",
        "indexed_at": "2026-01-02T00:00:00Z",
        "markdown_schema_version": "1.0",
        "canonical_url": "https://news.example/health/article",
        "url_hash": "hash",
        "edge_eligible": True,
        "md_s3_key": markdown_key,
        "embedding_s3_key": embedding_key,
        "author": "Author",
        "word_count": 3,
        "cms_tags": ["health"],
    }
    result.update(overrides)
    return result


@mock_aws
def test_backfill_rebuilds_published_projection(monkeypatch):
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    table = _table()
    table.wait_until_exists()
    markdown_key, embedding_key = _seed_artifacts(s3)
    table.put_item(Item=_record("cnt_1", markdown_key, embedding_key))
    indexed = MagicMock()

    monkeypatch.setattr(handler, "CONTENT_TABLE_NAME", TABLE)
    monkeypatch.setattr(handler, "CONTENT_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(handler, "index_document", indexed)

    result = handler.lambda_handler({}, None)

    assert result["updated"] == 1
    assert result["failures"] == []
    indexed.assert_called_once()
    content_id, document = indexed.call_args.args
    assert content_id == "cnt_1"
    assert document["content_vector"] == [0.1, 0.2]
    assert document["canonical_path_ancestors"] == ["/", "/health", "/health/article"]


@mock_aws
def test_backfill_reports_missing_status_as_ineligible(monkeypatch):
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    table = _table()
    table.wait_until_exists()
    markdown_key, embedding_key = _seed_artifacts(s3)
    table.put_item(Item=_record("legacy", markdown_key, embedding_key, status=""))
    indexed = MagicMock()

    monkeypatch.setattr(handler, "CONTENT_TABLE_NAME", TABLE)
    monkeypatch.setattr(handler, "CONTENT_BUCKET_NAME", BUCKET)
    monkeypatch.setattr(handler, "index_document", indexed)

    result = handler.lambda_handler({"dry_run": True}, None)

    assert result["updated"] == 0
    assert result["ineligible_content_ids"] == ["legacy"]
    indexed.assert_not_called()


@mock_aws
def test_backfill_fails_when_published_artifact_is_missing(monkeypatch):
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
    table = _table()
    table.wait_until_exists()
    table.put_item(Item=_record("broken", "content/missing.md", "embeddings/missing.json"))

    monkeypatch.setattr(handler, "CONTENT_TABLE_NAME", TABLE)
    monkeypatch.setattr(handler, "CONTENT_BUCKET_NAME", BUCKET)

    with pytest.raises(RuntimeError, match="failed for 1 published"):
        handler.lambda_handler({}, None)
