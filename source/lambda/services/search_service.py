"""OpenSearch-backed content discovery for partner query and search routes."""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from botocore.exceptions import BotoCoreError, ClientError
from opensearchpy.exceptions import TransportError
from shared.bedrock_embeddings import generate_search_embedding

from services.opensearch_client import filter_search, hybrid_search
from services.search_projection import normalize_topic_term


class SearchUnavailableError(RuntimeError):
    """Embedding or OpenSearch could not satisfy a discovery request."""


def validate_similarity_threshold(value: object, query_text: str) -> float:
    """Validate a supplied cutoff; callers distinguish omission from explicit null."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("similarity_threshold must be a finite number from 0 through 1")
    if not math.isfinite(value):
        raise ValueError("similarity_threshold must be a finite number from 0 through 1")
    if not query_text.strip():
        raise ValueError("similarity_threshold requires explicit query text")
    return float(value)


@dataclass(frozen=True)
class SectionPair:
    """One exact canonical host/path-ancestor section filter."""

    canonical_host: str
    canonical_path_ancestor: str


@dataclass(frozen=True)
class DiscoveryRequest:
    """Validated search inputs shared by standard and CoMP request adapters."""

    query_text: str = ""
    content_types: List[str] = field(default_factory=list)
    iab_category_ids: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    publish_date_start: Optional[str] = None
    publish_date_end: Optional[str] = None
    section_pairs: List[SectionPair] = field(default_factory=list)
    content_ids: List[str] = field(default_factory=list)
    limit: int = 10
    similarity_threshold: Optional[float] = None
    include_inline_content: bool = False


class SearchService:
    """Single discovery service over the denormalized OpenSearch index."""

    def execute_discovery(self, request: DiscoveryRequest) -> List[Dict[str, Any]]:
        """Run hybrid or filter-only discovery and map results to API fields."""
        filters = self._build_filters(request)
        try:
            if request.query_text:
                embedding_result = generate_search_embedding(request.query_text, "titan-embed-text-v2")
                if not embedding_result["success"]:
                    raise SearchUnavailableError("Query embedding failed")
                hits = hybrid_search(
                    request.query_text,
                    embedding_result["embedding"],
                    size=request.limit,
                    filters=filters,
                    include_body=request.include_inline_content,
                    timeout_seconds=24,
                )
            else:
                hits = filter_search(
                    size=request.limit,
                    filters=filters,
                    include_body=request.include_inline_content,
                    timeout_seconds=24,
                )
        except SearchUnavailableError:
            raise
        except (TransportError, BotoCoreError, ClientError) as exc:
            raise SearchUnavailableError("OpenSearch discovery failed") from exc

        results = [self._result_from_hit(hit, request.include_inline_content) for hit in hits]
        if request.similarity_threshold is not None and request.query_text:
            results = [
                result
                for result in results
                if result.get("similarity_score", 0.0) >= request.similarity_threshold
            ]
        return results

    def _build_filters(self, request: DiscoveryRequest) -> List[Dict[str, Any]]:
        filters: List[Dict[str, Any]] = [{"term": {"status": "published"}}]
        if request.content_types:
            filters.append({"terms": {"content_type": sorted(set(request.content_types))}})
        if request.iab_category_ids:
            filters.append({"terms": {"iab_categories": sorted(set(request.iab_category_ids))}})
        topic_terms = sorted({normalize_topic_term(topic) for topic in request.topics if normalize_topic_term(topic)})
        if topic_terms:
            filters.append({"terms": {"topic_terms": topic_terms}})
        if request.content_ids:
            filters.append({"terms": {"content_id": sorted(set(request.content_ids))}})

        publish_range: Dict[str, str] = {}
        if request.publish_date_start:
            publish_range["gte"] = request.publish_date_start
        if request.publish_date_end:
            publish_range["lte"] = request.publish_date_end
        if publish_range:
            filters.append({"range": {"publish_date": publish_range}})

        if request.section_pairs:
            pair_filters = [
                {
                    "bool": {
                        "filter": [
                            {"term": {"canonical_host": pair.canonical_host}},
                            {"term": {"canonical_path_ancestors": pair.canonical_path_ancestor}},
                        ]
                    }
                }
                for pair in request.section_pairs
            ]
            filters.append({"bool": {"should": pair_filters, "minimum_should_match": 1}})
        return filters

    def _result_from_hit(self, hit: Dict[str, Any], include_inline_content: bool) -> Dict[str, Any]:
        categories = [_category_for_response(item) for item in hit.get("iab_category_details", [])]
        if not categories:
            categories = [
                {"id": str(category_id), "name": "", "tier_1": ""}
                for category_id in hit.get("iab_categories", [])
            ]
        result = {
            "content_id": hit.get("content_id"),
            "title": hit.get("title"),
            "content_type": hit.get("content_type", "article"),
            "format": "md",
            "markdown_schema_version": hit.get("markdown_schema_version", "legacy"),
            "similarity_score": hit.get("similarity_score", 0.0),
            "relevance_score": hit.get("similarity_score", 0.0),
            "answer_capsule": hit.get("answer_capsule", ""),
            "topics": hit.get("topics", []),
            "iab_categories": categories,
            "license_id": hit.get("license_id"),
            "author": hit.get("author"),
            "publication_date": hit.get("publish_date"),
            "publish_date": hit.get("publish_date"),
            "tags": hit.get("cms_tags", []),
            "word_count": hit.get("word_count", 0),
        }
        body = hit.get("body", "")
        if include_inline_content and body and len(body) < 1000:
            result["content"] = body
        else:
            result["url"] = _markdown_url(hit)
        return result


def _category_for_response(category: object) -> Dict[str, Any]:
    item = category if isinstance(category, dict) else {}
    tier_path = item.get("tier_path") if isinstance(item.get("tier_path"), list) else []
    result: Dict[str, Any] = {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or ""),
        "tier_1": str(tier_path[0]) if tier_path else str(item.get("name") or ""),
    }
    for index, field_name in enumerate(("tier_2", "tier_3", "tier_4"), start=1):
        if len(tier_path) > index:
            result[field_name] = str(tier_path[index])
    return result


def _markdown_url(content: Dict[str, Any]) -> str:
    key = content.get("md_s3_key")
    if isinstance(key, str) and key.startswith("content/"):
        return f"/{key}"
    return f"/content/{content.get('content_id')}"
