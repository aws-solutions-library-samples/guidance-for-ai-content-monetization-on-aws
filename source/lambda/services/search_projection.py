"""Denormalized OpenSearch projection shared by GEO indexing and backfill."""

import unicodedata
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from shared.utils.canonical_url import normalize_canonical_url
from shared.utils.models import MARKDOWN_SCHEMA_VERSION


def normalize_topic_term(value: object) -> str:
    """Return a stable keyword term for case-insensitive topic filtering."""
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def build_search_projection(
    *,
    content_id: object,
    title: object,
    body: object,
    content_vector: Iterable[float],
    content_type: object,
    status: object,
    iab_categories: object,
    entities: object,
    topics: object,
    answer_capsule: object,
    language: object,
    license_id: object,
    publish_date: object,
    indexed_at: object,
    markdown_schema_version: object,
    canonical_url: object,
    url_hash: object,
    edge_eligible: object,
    md_s3_key: object,
    author: object,
    word_count: object,
    cms_tags: object,
) -> Dict[str, Any]:
    """Build the complete denormalized discovery document for one content item."""
    normalized_url = normalize_canonical_url(canonical_url)
    canonical_host, canonical_path, path_ancestors = _canonical_address(normalized_url)
    category_details = _category_details(iab_categories)
    topic_labels = _strings(topics)

    return {
        "content_id": str(content_id),
        "title": str(title or "Untitled"),
        "body": str(body or "")[:30_000],
        "content_type": str(content_type or "document"),
        "status": str(status or ""),
        "content_vector": list(content_vector),
        "iab_categories": [item["id"] for item in category_details],
        "iab_category_details": category_details,
        "entities": list(entities) if isinstance(entities, list) else [],
        "topics": topic_labels,
        "topic_terms": _unique_terms(topic_labels),
        "answer_capsule": str(answer_capsule or ""),
        "language": str(language or "en"),
        "license_id": str(license_id or ""),
        "publish_date": str(publish_date or ""),
        "indexed_at": str(indexed_at or ""),
        "markdown_schema_version": str(markdown_schema_version or MARKDOWN_SCHEMA_VERSION),
        "canonical_url": normalized_url,
        "canonical_host": canonical_host,
        "canonical_path": canonical_path,
        "canonical_path_ancestors": path_ancestors,
        "url_hash": str(url_hash or ""),
        "edge_eligible": bool(edge_eligible),
        "md_s3_key": str(md_s3_key or ""),
        "author": str(author or ""),
        "word_count": _integer(word_count),
        "cms_tags": _strings(cms_tags),
    }


def _canonical_address(canonical_url: Optional[str]) -> tuple[str, str, List[str]]:
    if not canonical_url:
        return "", "", []
    parsed = urlsplit(canonical_url)
    path = parsed.path or "/"
    stripped = path.rstrip("/") or "/"
    segments = [segment for segment in stripped.split("/") if segment]
    ancestors = ["/"]
    current = ""
    for segment in segments:
        current += f"/{segment}"
        ancestors.append(current)
    return parsed.netloc, path, ancestors


def _category_details(value: object) -> List[Dict[str, Any]]:
    details: List[Dict[str, Any]] = []
    seen = set()
    for category in value if isinstance(value, list) else []:
        if isinstance(category, dict):
            category_id = str(category.get("id") or "").strip()
            name = str(category.get("name") or "").strip()
            tier_path = _strings(category.get("tier_path"))
        else:
            category_id = str(category or "").strip()
            name = ""
            tier_path = []
        if not category_id or category_id in seen:
            continue
        seen.add(category_id)
        details.append({"id": category_id, "name": name, "tier_path": tier_path})
    return details


def _strings(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique_terms(values: Iterable[str]) -> List[str]:
    terms: List[str] = []
    seen = set()
    for value in values:
        term = normalize_topic_term(value)
        if term and term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def _integer(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
