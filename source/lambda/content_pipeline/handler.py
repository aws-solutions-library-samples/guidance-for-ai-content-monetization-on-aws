"""
GEO enrichment pipeline — Step Functions task entry points.

One Lambda, dispatched on event["step"]:
  extract        → read ingest object, extract text/markdown, write artifacts
  classify_enrich → one Bedrock call (IAB 3.1 + GEO fields)
  embed          → Titan v2 via the existing bedrock_embeddings module
  index          → OpenSearch Serverless hybrid index write
  persist        → DynamoDB put (single-key content record; overwrite on re-run)

State payload discipline: Step Functions caps payloads at 256 KB, so states
pass S3 pointers (content_id, bucket, artifact keys) — never document bodies.
Intermediate artifacts live under pipeline/{content_id}/ in the content bucket.
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError
from services.opensearch_client import index_document
from services.search_projection import build_search_projection
from shared.bedrock_embeddings import generate_content_embedding
from shared.utils.edge_address import edge_address_from_canonical_url, legacy_markdown_key
from shared.utils.models import MARKDOWN_SCHEMA_VERSION, ContentMetadata

from content_pipeline.extractor import extract
from content_pipeline.geo_enricher import classify_and_enrich

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

CONTENT_TABLE_NAME = os.environ["CONTENT_TABLE_NAME"]
LICENSE_ID_STUB = os.environ.get("DEFAULT_LICENSE_ID", "lic_default_v1")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    step = event.get("step")
    logger.info(f"Pipeline step={step} content_id={event.get('content_id', '?')}")

    if step == "extract":
        return step_extract(event)
    if step == "classify_enrich":
        return step_classify_enrich(event)
    if step == "embed":
        return step_embed(event)
    if step == "index":
        return step_index(event)
    if step == "persist":
        return step_persist(event)
    raise ValueError(f"Unknown pipeline step: {step}")


# ── Step 1: Extract ────────────────────────────────────────────────────────


def step_extract(event: Dict[str, Any]) -> Dict[str, Any]:
    bucket = event["bucket"]
    key = event["key"]

    obj = s3.get_object(Bucket=bucket, Key=key)
    raw = obj["Body"].read()

    # Deterministic content_id: same object re-processed → same record.
    # POST /content sets S3 metadata content-id; otherwise derive from the key.
    content_id = obj.get("Metadata", {}).get("content-id") or _derive_content_id(key)

    extracted = extract(raw, key)
    markdown_schema_version = extracted.setdefault("markdown_schema_version", MARKDOWN_SCHEMA_VERSION)
    submitted_content_type = extracted.get("content_type")
    content_type = (
        submitted_content_type.strip()
        if isinstance(submitted_content_type, str) and submitted_content_type.strip()
        else "article"
        if extracted["format"] == "json"
        else "document"
    )

    edge_address = edge_address_from_canonical_url(extracted.get("canonical_url"))
    extracted["canonical_url"] = edge_address.canonical_url
    extracted["url_hash"] = edge_address.url_hash
    extracted["edge_eligible"] = edge_address.eligible
    extracted["edge_ineligible_reason"] = edge_address.reason

    md_key = edge_address.s3_key or legacy_markdown_key(content_id)
    object_metadata = {
        "content-id": content_id,
        "markdown-schema-version": markdown_schema_version,
        "license-url": f"/licenses/{LICENSE_ID_STUB}",
    }
    if edge_address.url_hash:
        object_metadata["canonical-url-hash"] = edge_address.url_hash
    _write_markdown_object(
        bucket=bucket,
        key=md_key,
        body=extracted["markdown"].encode("utf-8"),
        metadata=object_metadata,
        edge_eligible=edge_address.eligible,
    )

    extracted_key = f"pipeline/{content_id}/extracted.json"
    s3.put_object(
        Bucket=bucket, Key=extracted_key, Body=json.dumps(extracted).encode("utf-8"), ContentType="application/json"
    )

    return {
        "content_id": content_id,
        "bucket": bucket,
        "source_key": key,
        "extracted_key": extracted_key,
        "md_key": md_key,
        "title": extracted["title"][:200],
        "content_type": content_type,
        "markdown_schema_version": markdown_schema_version,
        "url_hash": edge_address.url_hash,
        "edge_eligible": edge_address.eligible,
    }


def _derive_content_id(key: str) -> str:
    stem = key
    if stem.startswith("ingest/"):
        stem = stem[len("ingest/") :]
    stem = stem.rsplit(".", 1)[0]
    # SHA1 here derives a stable content id from the object key, not a security
    # digest; usedforsecurity=False tells scanners (bandit) it is non-cryptographic.
    return "cnt_" + hashlib.sha1(stem.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def _write_markdown_object(
    *,
    bucket: str,
    key: str,
    body: bytes,
    metadata: Dict[str, str],
    edge_eligible: bool,
) -> None:
    """Create or safely replace one identity-owned Markdown object."""
    request = {
        "Bucket": bucket,
        "Key": key,
        "Body": body,
        "ContentType": "text/markdown; charset=utf-8",
        "CacheControl": "public, max-age=3600",
        "Metadata": metadata,
    }
    for attempt in range(3):
        try:
            s3.put_object(**request, IfNoneMatch="*")
            return
        except ClientError as exc:
            if not _is_precondition_failure(exc):
                raise

        try:
            existing = s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                continue
            raise

        existing_metadata = existing.get("Metadata", {})
        same_content = existing_metadata.get("content-id") == metadata["content-id"]
        same_canonical = existing_metadata.get("canonical-url-hash") == metadata.get("canonical-url-hash")
        legacy_upgrade = (
            not edge_eligible
            and not existing_metadata.get("canonical-url-hash")
            and key == legacy_markdown_key(metadata["content-id"])
        )
        if not ((same_content and same_canonical) or legacy_upgrade):
            raise ValueError(f"Markdown edge key collision for {key}")

        try:
            s3.put_object(**request, IfMatch=existing["ETag"])
            return
        except ClientError as exc:
            if not _is_precondition_failure(exc):
                raise
            logger.info(
                "Concurrent Markdown rewrite detected key=%s attempt=%s",
                key,
                attempt + 1,
            )

    raise RuntimeError(f"Markdown object changed repeatedly during conditional write: {key}")


def _is_precondition_failure(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"}


# ── Step 2: Classify + GEO enrich (one Bedrock call) ──────────────────────


def step_classify_enrich(event: Dict[str, Any]) -> Dict[str, Any]:
    extracted = _read_json(event["bucket"], event["extracted_key"])

    result = classify_and_enrich(
        title=extracted["title"],
        content_text=extracted["text"],
        author=extracted.get("author", ""),
        publication_date=extracted.get("publish_date") or "",
        cms_tags=extracted.get("cms_tags", []),
    )

    enrichment_key = f"pipeline/{event['content_id']}/enrichment.json"
    s3.put_object(
        Bucket=event["bucket"],
        Key=enrichment_key,
        Body=json.dumps(result).encode("utf-8"),
        ContentType="application/json",
    )

    return {
        **event,
        "enrichment_key": enrichment_key,
        "classification_failed": bool(result.get("classification_failed")),
    }


# ── Step 3: Embed (existing bedrock_embeddings, as-is) ─────────────────────


def step_embed(event: Dict[str, Any]) -> Dict[str, Any]:
    extracted = _read_json(event["bucket"], event["extracted_key"])

    # Title + first ~512 tokens of body, per design
    text_for_embedding = f"{extracted['title']}. {extracted['text'][:2048]}"
    result = generate_content_embedding(text_for_embedding, "titan-embed-text-v2")
    if not result.get("success"):
        raise RuntimeError(f"Embedding generation failed: {result.get('error')}")

    embedding_key = f"embeddings/{event['content_id']}.json"
    s3.put_object(
        Bucket=event["bucket"],
        Key=embedding_key,
        Body=json.dumps(
            {
                "content_id": event["content_id"],
                "embedding": result["embedding"],
                "model": result["model"],
                "dimensions": result["dimensions"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ).encode("utf-8"),
        ContentType="application/json",
    )

    return {**event, "embedding_key": embedding_key}


# ── Step 4: Index (OpenSearch Serverless hybrid) ───────────────────────────


def step_index(event: Dict[str, Any]) -> Dict[str, Any]:
    bucket = event["bucket"]
    extracted = _read_json(bucket, event["extracted_key"])
    enrichment = _read_json(bucket, event["enrichment_key"])
    embedding = _read_json(bucket, event["embedding_key"])

    doc = build_search_projection(
        content_id=event["content_id"],
        title=extracted["title"],
        body=extracted["text"],
        content_vector=embedding["embedding"],
        content_type=event.get("content_type", "document"),
        status="published",
        iab_categories=enrichment.get("iab_categories", []),
        entities=enrichment.get("entities", []),
        topics=enrichment.get("topics", []),
        answer_capsule=enrichment.get("answer_capsule", ""),
        language=enrichment.get("language", "en"),
        license_id=LICENSE_ID_STUB,
        publish_date=extracted.get("publish_date"),
        indexed_at=datetime.now(timezone.utc).isoformat(),
        markdown_schema_version=extracted.get("markdown_schema_version", MARKDOWN_SCHEMA_VERSION),
        canonical_url=extracted.get("canonical_url"),
        url_hash=extracted.get("url_hash"),
        edge_eligible=bool(extracted.get("edge_eligible")),
        md_s3_key=event.get("md_key"),
        author=extracted.get("author", ""),
        word_count=len(extracted["text"].split()),
        cms_tags=extracted.get("cms_tags", []),
    )
    index_document(event["content_id"], doc)
    return event


# ── Step 5: Persist (DynamoDB double-write, LATEST pattern) ────────────────


def step_persist(event: Dict[str, Any]) -> Dict[str, Any]:
    bucket = event["bucket"]
    extracted = _read_json(bucket, event["extracted_key"])
    enrichment = _read_json(bucket, event["enrichment_key"])

    now = datetime.now(timezone.utc).isoformat()
    classification_failed = bool(enrichment.get("classification_failed"))

    record = ContentMetadata(
        content_id=event["content_id"],
        title=extracted["title"],
        content_type=event.get("content_type", "document"),
        iab_categories=enrichment.get("iab_categories", []),
        entities=enrichment.get("entities", []),
        topics=enrichment.get("topics", []),
        sentiment=enrichment.get("sentiment", {}),
        quality_score=float(enrichment.get("quality_score", 0.0)),
        answer_capsule=enrichment.get("answer_capsule", ""),
        faq_schema=enrichment.get("faq_schema", []),
        indexed_at=now,
        publish_date=extracted.get("publish_date"),
        word_count=len(extracted["text"].split()),
        language=enrichment.get("language", "en"),
        license_id=LICENSE_ID_STUB,
        license_url=f"/licenses/{LICENSE_ID_STUB}",
        canonical_url=extracted.get("canonical_url"),
        url_hash=extracted.get("url_hash"),
        edge_eligible=bool(extracted.get("edge_eligible")),
        edge_ineligible_reason=extracted.get("edge_ineligible_reason"),
        author=extracted.get("author", ""),
        summary=enrichment.get("answer_capsule", ""),
        md_s3_key=event.get("md_key"),
        markdown_schema_version=extracted.get("markdown_schema_version", MARKDOWN_SCHEMA_VERSION),
        source_s3_key=event.get("source_key"),
        embedding_s3_key=event.get("embedding_key"),
        cms_tags=extracted.get("cms_tags", []),
        status="published",
        processing_status="classification_failed" if classification_failed else "completed",
        created_at=now,
        updated_at=now,
    )

    table = dynamodb.Table(CONTENT_TABLE_NAME)

    # Single-key table (ADR-004): overwrite the record in place. Re-processing the
    # same content_id replaces it — idempotent, no version history.
    table.put_item(Item=record.to_item())

    return {**event, "persisted": True, "processing_status": record.processing_status}


def _read_json(bucket: str, key: str) -> Dict[str, Any]:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
