"""Explicit migration Lambda for rebuilding the OpenSearch content projection."""

import json
import logging
import os
from typing import Any, Dict, List

import boto3
from services.opensearch_client import index_document
from services.search_projection import build_search_projection

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CONTENT_TABLE_NAME = os.environ["CONTENT_TABLE_NAME"]
CONTENT_BUCKET_NAME = os.environ["CONTENT_BUCKET_NAME"]


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Backfill every explicitly published content record when invoked by Make."""
    dry_run = bool(event.get("dry_run", False))
    s3_client = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(CONTENT_TABLE_NAME)
    updated = 0
    ineligible: List[str] = []
    failures: List[Dict[str, str]] = []
    scan_kwargs: Dict[str, Any] = {"ConsistentRead": True}

    while True:
        response = table.scan(**scan_kwargs)
        for record in response.get("Items", []):
            content_id = str(record.get("content_id") or "")
            if record.get("status") != "published":
                if content_id:
                    ineligible.append(content_id)
                continue
            try:
                document = _projection_from_record(record, s3_client)
                if not dry_run:
                    index_document(content_id, document)
                updated += 1
            except Exception as exc:
                logger.exception("Projection backfill failed for content_id=%s", content_id)
                failures.append({"content_id": content_id or "unknown", "error": str(exc)[:500]})
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    result = {
        "dry_run": dry_run,
        "updated": updated,
        "ineligible_content_ids": ineligible,
        "failures": failures,
    }
    logger.info(
        "Search projection backfill complete dry_run=%s updated=%s ineligible=%s failures=%s",
        dry_run,
        updated,
        len(ineligible),
        len(failures),
    )
    if failures:
        raise RuntimeError(f"Search projection backfill failed for {len(failures)} published item(s)")
    return result


def _projection_from_record(record: Dict[str, Any], s3_client: Any) -> Dict[str, Any]:
    content_id = str(record["content_id"])
    markdown_key = _required_string(record, "md_s3_key", content_id)
    embedding_key = _required_string(record, "embedding_s3_key", content_id)
    markdown = _read_text(s3_client, markdown_key, content_id)
    embedding = _read_embedding(s3_client, embedding_key, content_id)
    return build_search_projection(
        content_id=content_id,
        title=record.get("title"),
        body=markdown,
        content_vector=embedding,
        content_type=record.get("content_type"),
        status=record.get("status"),
        iab_categories=record.get("iab_categories", []),
        entities=record.get("entities", []),
        topics=record.get("topics", []),
        answer_capsule=record.get("answer_capsule") or record.get("summary"),
        language=record.get("language"),
        license_id=record.get("license_id"),
        publish_date=record.get("publish_date"),
        indexed_at=record.get("indexed_at"),
        markdown_schema_version=record.get("markdown_schema_version"),
        canonical_url=record.get("canonical_url"),
        url_hash=record.get("url_hash"),
        edge_eligible=record.get("edge_eligible"),
        md_s3_key=markdown_key,
        author=record.get("author"),
        word_count=record.get("word_count"),
        cms_tags=record.get("cms_tags", []),
    )


def _required_string(record: Dict[str, Any], field: str, content_id: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Published content {content_id} has no {field}")
    return value


def _read_text(s3_client: Any, key: str, content_id: str) -> str:
    try:
        return s3_client.get_object(Bucket=CONTENT_BUCKET_NAME, Key=key)["Body"].read().decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Markdown artifact unavailable for {content_id}") from exc


def _read_embedding(s3_client: Any, key: str, content_id: str) -> List[float]:
    try:
        payload = json.loads(s3_client.get_object(Bucket=CONTENT_BUCKET_NAME, Key=key)["Body"].read())
    except Exception as exc:
        raise ValueError(f"Embedding artifact unavailable for {content_id}") from exc
    embedding = payload.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise ValueError(f"Embedding artifact is invalid for {content_id}")
    return embedding
