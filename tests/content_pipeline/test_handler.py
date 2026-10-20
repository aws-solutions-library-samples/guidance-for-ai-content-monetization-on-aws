"""
Unit tests for content_pipeline/handler.py (wave0 Phase 6, Req 19-21).

The GEO pipeline is one Lambda dispatched on ``event["step"]``. These tests
drive each step with the AWS/Bedrock/OpenSearch collaborators mocked at the
module level (``handler.s3``, ``handler.index_document``,
``handler.generate_content_embedding``) and the extractor/enricher stubbed, so
no network/AWS call is made. The DynamoDB persist step uses moto.

Covered:
- ``lambda_handler`` dispatch table + unknown-step ValueError.
- ``step_extract`` — content_id from S3 metadata vs. derived, artifact writes.
- ``_derive_content_id`` — deterministic ``cnt_`` hash, ingest/ prefix stripping.
- ``step_classify_enrich`` — enrichment artifact + classification_failed flag.
- ``step_embed`` — success write and the embedding-failure RuntimeError.
- ``step_index`` — hybrid index document assembly.
- ``step_persist`` — DynamoDB put with completed vs. classification_failed status.

The content_pipeline conftest already puts the lambda root on sys.path and sets
CONTENT_TABLE_NAME; we import the handler as a package module.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

import content_pipeline.handler as h

FIXTURES = Path(__file__).resolve().parents[2] / "assets" / "sample_content"


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


@pytest.fixture
def stub_s3(monkeypatch):
    s3 = MagicMock()
    monkeypatch.setattr(h, "s3", s3)
    return s3


def _put_json_objects(stub_s3, mapping):
    """Configure stub_s3.get_object to return JSON bodies keyed by S3 key."""

    def _get_object(Bucket, Key):  # noqa: N803 — boto3 kwarg names
        if Key not in mapping:
            raise KeyError(Key)
        payload = mapping[Key]
        body = _FakeBody(json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload)
        obj = {"Body": body}
        return obj

    stub_s3.get_object.side_effect = _get_object


# --------------------------------------------------------------------------- #
# lambda_handler dispatch
# --------------------------------------------------------------------------- #
def test_lambda_handler_unknown_step_raises():
    with pytest.raises(ValueError, match="Unknown pipeline step"):
        h.lambda_handler({"step": "nope"}, None)


def test_lambda_handler_dispatches_to_step(monkeypatch):
    called = {}

    def _fake_extract(e):
        called["extract"] = e
        return {"ok": True}

    monkeypatch.setattr(h, "step_extract", _fake_extract)
    result = h.lambda_handler({"step": "extract", "content_id": "cnt_x"}, None)
    assert result == {"ok": True}
    assert called["extract"]["step"] == "extract"


# --------------------------------------------------------------------------- #
# _derive_content_id
# --------------------------------------------------------------------------- #
def test_derive_content_id_is_deterministic_and_strips_prefix():
    a = h._derive_content_id("ingest/article.html")
    b = h._derive_content_id("ingest/article.html")
    assert a == b
    assert a.startswith("cnt_")
    # ingest/ prefix and extension are stripped before hashing → same stem, same id
    assert h._derive_content_id("ingest/article.html") == h._derive_content_id("ingest/article.json")


# --------------------------------------------------------------------------- #
# step_extract
# --------------------------------------------------------------------------- #
def test_step_extract_uses_s3_metadata_content_id(stub_s3, monkeypatch):
    stub_s3.get_object.return_value = {
        "Body": _FakeBody(b"<html></html>"),
        "Metadata": {"content-id": "cnt_from_meta"},
    }
    monkeypatch.setattr(
        h,
        "extract",
        lambda raw, key: {
            "title": "A Title",
            "markdown": "# A Title",
            "text": "body text",
            "format": "html",
        },
    )

    result = h.step_extract({"bucket": "b", "key": "ingest/x.html"})

    assert result["content_id"] == "cnt_from_meta"
    assert result["md_key"] == "content/cnt_from_meta.md"
    assert result["extracted_key"] == "pipeline/cnt_from_meta/extracted.json"
    assert result["content_type"] == "document"  # html → document
    assert result["markdown_schema_version"] == "1.0"
    # md + extracted artifacts written
    assert stub_s3.put_object.call_count == 2
    markdown_write = next(
        call.kwargs for call in stub_s3.put_object.call_args_list if call.kwargs["Key"] == "content/cnt_from_meta.md"
    )
    assert markdown_write["ContentType"] == "text/markdown; charset=utf-8"
    assert markdown_write["Metadata"] == {
        "content-id": "cnt_from_meta",
        "license-url": "/licenses/lic_default_v1",
        "markdown-schema-version": "1.0",
    }
    assert markdown_write["IfNoneMatch"] == "*"
    assert markdown_write["CacheControl"] == "public, max-age=3600"
    extracted_write = next(
        call.kwargs
        for call in stub_s3.put_object.call_args_list
        if call.kwargs["Key"] == "pipeline/cnt_from_meta/extracted.json"
    )
    assert json.loads(extracted_write["Body"])["markdown_schema_version"] == "1.0"


def test_step_extract_derives_content_id_when_no_metadata(stub_s3, monkeypatch):
    stub_s3.get_object.return_value = {"Body": _FakeBody(b"{}"), "Metadata": {}}
    monkeypatch.setattr(
        h,
        "extract",
        lambda raw, key: {"title": "T", "markdown": "T", "text": "t", "format": "json"},
    )
    result = h.step_extract({"bucket": "b", "key": "ingest/doc.json"})
    assert result["content_id"] == h._derive_content_id("ingest/doc.json")
    assert result["content_type"] == "article"  # json → article


def test_step_extract_preserves_submitted_content_type(stub_s3, monkeypatch):
    stub_s3.get_object.return_value = {
        "Body": _FakeBody(b"{}"),
        "Metadata": {"content-id": "cnt_report"},
    }
    monkeypatch.setattr(
        h,
        "extract",
        lambda raw, key: {
            "title": "Report",
            "markdown": "# Report",
            "text": "body",
            "format": "json",
            "content_type": "report",
        },
    )

    result = h.step_extract({"bucket": "b", "key": "ingest/report.json"})

    assert result["content_type"] == "report"


def test_step_extract_uses_canonical_host_edge_key(stub_s3, monkeypatch):
    stub_s3.get_object.return_value = {
        "Body": _FakeBody(b"<html></html>"),
        "Metadata": {"content-id": "cnt_edge"},
    }
    monkeypatch.setattr(
        h,
        "extract",
        lambda raw, key: {
            "title": "Edge",
            "markdown": "# Edge",
            "text": "body",
            "format": "html",
            "canonical_url": "HTTPS://News.Example.com/a/b/?q=1",
        },
    )

    result = h.step_extract({"bucket": "b", "key": "ingest/edge.html"})

    assert result["md_key"] == "content/news.example.com/a/b/index.md"
    assert result["edge_eligible"] is True
    assert result["url_hash"]
    markdown_write = next(
        call.kwargs
        for call in stub_s3.put_object.call_args_list
        if call.kwargs["Key"] == result["md_key"]
    )
    assert markdown_write["Metadata"]["canonical-url-hash"] == result["url_hash"]
    extracted_write = next(
        call.kwargs
        for call in stub_s3.put_object.call_args_list
        if call.kwargs["Key"] == "pipeline/cnt_edge/extracted.json"
    )
    artifact = json.loads(extracted_write["Body"])
    assert artifact["canonical_url"] == "https://news.example.com/a/b/"
    assert artifact["edge_eligible"] is True


@pytest.mark.parametrize(
    ("filename", "content_id", "expected_key", "eligible"),
    [
        (
            "free_tier_ev_adoption.html",
            "cnt_html",
            "content/demopublisher.com/auto/ev-adoption-2026.md",
            True,
        ),
        (
            "standard_tier_diabetes_screening.md",
            "cnt_markdown",
            "content/cnt_markdown.md",
            False,
        ),
        (
            "premium_tier_semiconductor_report.json",
            "cnt_json",
            "content/demopublisher.com/reports/semiconductor-capex-2026.md",
            True,
        ),
    ],
)
def test_real_source_formats_publish_expected_edge_key(
    stub_s3,
    filename,
    content_id,
    expected_key,
    eligible,
):
    stub_s3.get_object.return_value = {
        "Body": _FakeBody((FIXTURES / filename).read_bytes()),
        "Metadata": {"content-id": content_id},
    }

    result = h.step_extract({"bucket": "b", "key": f"ingest/{filename}"})

    assert result["md_key"] == expected_key
    assert result["edge_eligible"] is eligible
    markdown_write = next(
        call.kwargs
        for call in stub_s3.put_object.call_args_list
        if call.kwargs["Key"] == expected_key
    )
    assert markdown_write["Body"].startswith(b"# ")
    assert markdown_write["Metadata"]["markdown-schema-version"] == "1.0"


def test_markdown_edge_key_collision_is_rejected(stub_s3):
    stub_s3.put_object.side_effect = ClientError(
        {"Error": {"Code": "PreconditionFailed"}},
        "PutObject",
    )
    stub_s3.head_object.return_value = {
        "ETag": '"old"',
        "Metadata": {
            "content-id": "cnt_other",
            "canonical-url-hash": "other-hash",
        },
    }

    with pytest.raises(ValueError, match="edge key collision"):
        h._write_markdown_object(
            bucket="b",
            key="content/example.com/a.md",
            body=b"# New",
            metadata={
                "content-id": "cnt_new",
                "canonical-url-hash": "new-hash",
                "markdown-schema-version": "1.0",
            },
            edge_eligible=True,
        )


def test_markdown_edge_key_same_identity_rewrites_conditionally(stub_s3):
    stub_s3.put_object.side_effect = [
        ClientError({"Error": {"Code": "412"}}, "PutObject"),
        {"ETag": '"new"'},
    ]
    stub_s3.head_object.return_value = {
        "ETag": '"old"',
        "Metadata": {
            "content-id": "cnt_same",
            "canonical-url-hash": "same-hash",
        },
    }

    h._write_markdown_object(
        bucket="b",
        key="content/example.com/a.md",
        body=b"# Updated",
        metadata={
            "content-id": "cnt_same",
            "canonical-url-hash": "same-hash",
            "markdown-schema-version": "1.0",
        },
        edge_eligible=True,
    )

    assert stub_s3.put_object.call_count == 2
    assert stub_s3.put_object.call_args_list[1].kwargs["IfMatch"] == '"old"'


def test_concurrent_rewrite_reloads_metadata_and_retries(stub_s3):
    stub_s3.put_object.side_effect = [
        ClientError({"Error": {"Code": "412"}}, "PutObject"),
        ClientError({"Error": {"Code": "412"}}, "PutObject"),
        ClientError({"Error": {"Code": "412"}}, "PutObject"),
        {"ETag": '"new"'},
    ]
    stub_s3.head_object.return_value = {
        "ETag": '"old"',
        "Metadata": {
            "content-id": "cnt_same",
            "canonical-url-hash": "same-hash",
        },
    }

    h._write_markdown_object(
        bucket="b",
        key="content/example.com/a.md",
        body=b"# Racing update",
        metadata={
            "content-id": "cnt_same",
            "canonical-url-hash": "same-hash",
            "markdown-schema-version": "1.0",
        },
        edge_eligible=True,
    )

    assert stub_s3.put_object.call_count == 4
    assert stub_s3.head_object.call_count == 2
    assert stub_s3.put_object.call_args_list[0].kwargs["IfNoneMatch"] == "*"
    assert stub_s3.put_object.call_args_list[1].kwargs["IfMatch"] == '"old"'
    assert stub_s3.put_object.call_args_list[2].kwargs["IfNoneMatch"] == "*"
    assert stub_s3.put_object.call_args_list[3].kwargs["IfMatch"] == '"old"'


def test_repeated_concurrent_rewrites_fail_without_unconditional_put(stub_s3):
    stub_s3.put_object.side_effect = [
        ClientError({"Error": {"Code": "412"}}, "PutObject")
        for _ in range(6)
    ]
    stub_s3.head_object.return_value = {
        "ETag": '"old"',
        "Metadata": {
            "content-id": "cnt_same",
            "canonical-url-hash": "same-hash",
        },
    }

    with pytest.raises(RuntimeError, match="changed repeatedly"):
        h._write_markdown_object(
            bucket="b",
            key="content/example.com/a.md",
            body=b"# Racing update",
            metadata={
                "content-id": "cnt_same",
                "canonical-url-hash": "same-hash",
                "markdown-schema-version": "1.0",
            },
            edge_eligible=True,
        )

    assert stub_s3.put_object.call_count == 6
    for index, call in enumerate(stub_s3.put_object.call_args_list):
        condition = "IfNoneMatch" if index % 2 == 0 else "IfMatch"
        assert condition in call.kwargs


# --------------------------------------------------------------------------- #
# step_classify_enrich
# --------------------------------------------------------------------------- #
def test_step_classify_enrich_writes_artifact_and_flag(stub_s3, monkeypatch):
    _put_json_objects(
        stub_s3,
        {"pipeline/cnt_1/extracted.json": {"title": "T", "text": "body", "author": "A", "cms_tags": []}},
    )
    monkeypatch.setattr(
        h,
        "classify_and_enrich",
        lambda **kw: {"iab_categories": [{"id": "597"}], "classification_failed": False, "topics": ["ai"]},
    )
    event = {"content_id": "cnt_1", "bucket": "b", "extracted_key": "pipeline/cnt_1/extracted.json"}
    result = h.step_classify_enrich(event)

    assert result["enrichment_key"] == "pipeline/cnt_1/enrichment.json"
    assert result["classification_failed"] is False
    stub_s3.put_object.assert_called_once()


def test_step_classify_enrich_passes_title_and_body_once(stub_s3, monkeypatch):
    _put_json_objects(
        stub_s3,
        {"e.json": {"title": "C# Guide", "text": "Body only", "author": "", "cms_tags": []}},
    )
    captured = {}
    monkeypatch.setattr(
        h,
        "classify_and_enrich",
        lambda **kwargs: captured.update(kwargs)
        or {"iab_categories": [], "classification_failed": False},
    )

    h.step_classify_enrich({"content_id": "c", "bucket": "b", "extracted_key": "e.json"})

    assert captured["title"] == "C# Guide"
    assert captured["content_text"] == "Body only"


def test_step_classify_enrich_propagates_failure_flag(stub_s3, monkeypatch):
    _put_json_objects(stub_s3, {"e.json": {"title": "T", "text": "b"}})
    monkeypatch.setattr(
        h, "classify_and_enrich", lambda **kw: {"iab_categories": [], "classification_failed": True}
    )
    result = h.step_classify_enrich({"content_id": "c", "bucket": "b", "extracted_key": "e.json"})
    assert result["classification_failed"] is True


# --------------------------------------------------------------------------- #
# step_embed
# --------------------------------------------------------------------------- #
def test_step_embed_writes_embedding_artifact(stub_s3, monkeypatch):
    _put_json_objects(stub_s3, {"e.json": {"title": "T", "text": "the body"}})
    captured = {}
    monkeypatch.setattr(
        h,
        "generate_content_embedding",
        lambda text, model: captured.update({"text": text})
        or {"success": True, "embedding": [0.1] * 1024, "model": model, "dimensions": 1024},
    )
    result = h.step_embed({"content_id": "cnt_1", "bucket": "b", "extracted_key": "e.json"})
    assert result["embedding_key"] == "embeddings/cnt_1.json"
    assert captured["text"] == "T. the body"
    stub_s3.put_object.assert_called_once()


def test_step_embed_raises_on_embedding_failure(stub_s3, monkeypatch):
    _put_json_objects(stub_s3, {"e.json": {"title": "T", "text": "b"}})
    monkeypatch.setattr(
        h, "generate_content_embedding", lambda text, model: {"success": False, "error": "bedrock down"}
    )
    with pytest.raises(RuntimeError, match="bedrock down"):
        h.step_embed({"content_id": "cnt_1", "bucket": "b", "extracted_key": "e.json"})


# --------------------------------------------------------------------------- #
# step_index
# --------------------------------------------------------------------------- #
def test_step_index_builds_hybrid_document(stub_s3, monkeypatch):
    _put_json_objects(
        stub_s3,
        {
            "ex.json": {"title": "Title", "text": "long body", "publish_date": "2025-01-01"},
            "en.json": {
                "iab_categories": [{"id": "597"}, {"id": "111"}],
                "entities": [{"name": "X", "type": "org", "relevance": 0.9}],
                "topics": ["ai"],
                "answer_capsule": "capsule",
                "language": "en",
            },
            "emb.json": {"embedding": [0.2] * 1024},
        },
    )
    captured = {}
    monkeypatch.setattr(h, "index_document", lambda cid, doc: captured.update({"cid": cid, "doc": doc}))

    event = {
        "content_id": "cnt_1",
        "bucket": "b",
        "content_type": "report",
        "extracted_key": "ex.json",
        "enrichment_key": "en.json",
        "embedding_key": "emb.json",
    }
    result = h.step_index(event)

    assert result is event
    assert captured["cid"] == "cnt_1"
    doc = captured["doc"]
    assert doc["content_id"] == "cnt_1"
    assert doc["content_type"] == "report"
    assert doc["iab_categories"] == ["597", "111"]  # flattened to id list
    assert doc["content_vector"] == [0.2] * 1024
    assert doc["answer_capsule"] == "capsule"
    assert doc["markdown_schema_version"] == "1.0"
    assert doc["edge_eligible"] is False


# --------------------------------------------------------------------------- #
# step_persist (moto DynamoDB)
# --------------------------------------------------------------------------- #
@mock_aws
def test_step_persist_writes_completed_record(stub_s3, monkeypatch):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=h.CONTENT_TABLE_NAME,
        KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    # Rebind handler's module-level dynamodb resource to the moto-backed one.
    monkeypatch.setattr(h, "dynamodb", dynamodb)

    _put_json_objects(
        stub_s3,
        {
            "ex.json": {
                "title": "Title",
                "text": "word one two three",
                "publish_date": "2025-01-01",
                "author": "Jane",
                "canonical_url": "https://example.com/article",
                "url_hash": "abc123",
                "edge_eligible": True,
            },
            "en.json": {
                "iab_categories": [{"id": "597", "name": "AI"}],
                "topics": ["ai"],
                "answer_capsule": "cap",
                "classification_failed": False,
                "quality_score": 0.8,
            },
        },
    )

    event = {
        "content_id": "cnt_persist",
        "bucket": "b",
        "extracted_key": "ex.json",
        "enrichment_key": "en.json",
        "content_type": "article",
        "md_key": "content/cnt_persist.md",
    }
    result = h.step_persist(event)

    assert result["persisted"] is True
    assert result["processing_status"] == "completed"
    stored = dynamodb.Table(h.CONTENT_TABLE_NAME).get_item(Key={"content_id": "cnt_persist"})["Item"]
    assert stored["title"] == "Title"
    assert stored["word_count"] == 4
    assert stored["processing_status"] == "completed"
    assert stored["markdown_schema_version"] == "1.0"
    assert stored["canonical_url"] == "https://example.com/article"
    assert stored["url_hash"] == "abc123"
    assert stored["edge_eligible"] is True


@mock_aws
def test_step_persist_marks_classification_failed(stub_s3, monkeypatch):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=h.CONTENT_TABLE_NAME,
        KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    monkeypatch.setattr(h, "dynamodb", dynamodb)
    _put_json_objects(
        stub_s3,
        {
            "ex.json": {"title": "T", "text": "body"},
            "en.json": {"iab_categories": [], "classification_failed": True},
        },
    )
    event = {"content_id": "cnt_fail", "bucket": "b", "extracted_key": "ex.json", "enrichment_key": "en.json"}
    result = h.step_persist(event)
    assert result["processing_status"] == "classification_failed"


@mock_aws
def test_reprocessing_upgrades_legacy_record_to_schema_1(stub_s3, monkeypatch):
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.create_table(
        TableName=h.CONTENT_TABLE_NAME,
        KeySchema=[{"AttributeName": "content_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "content_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.put_item(Item={"content_id": "cnt_legacy", "title": "Legacy", "content_type": "article"})
    monkeypatch.setattr(h, "dynamodb", dynamodb)
    _put_json_objects(
        stub_s3,
        {
            "ex.json": {
                "title": "Reprocessed",
                "text": "new body",
                "markdown_schema_version": "1.0",
            },
            "en.json": {"iab_categories": [], "classification_failed": False},
        },
    )

    h.step_persist(
        {
            "content_id": "cnt_legacy",
            "bucket": "b",
            "extracted_key": "ex.json",
            "enrichment_key": "en.json",
            "md_key": "content/cnt_legacy.md",
        }
    )

    upgraded = table.get_item(Key={"content_id": "cnt_legacy"})["Item"]
    assert upgraded["title"] == "Reprocessed"
    assert upgraded["markdown_schema_version"] == "1.0"
