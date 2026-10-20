"""
OpenSearch Serverless indexing/search client for the GEO pipeline.

The hybrid index (knn_vector + BM25 text fields) is created idempotently by
this client on first write — OpenSearch Serverless has no CloudFormation index
resource, so index bootstrap belongs to the writer, not the CDK stack.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

logger = logging.getLogger()
logger.setLevel(logging.INFO)

INDEX_NAME = os.environ.get("OPENSEARCH_INDEX", "iab-content-hybrid")

# Hybrid mapping per design.md. NextGen AOSS builds the ANN engine internally:
# it REJECTS an explicit method/engine block, but the field must still declare
# a top-level space_type, and the index needs settings.index.knn=true for the
# field to be queryable via `knn` (verified against the live collection).
INDEX_MAPPING = {
    "settings": {"index": {"knn": True}},
    "mappings": {
        "properties": {
            "content_id": {"type": "keyword"},
            "title": {"type": "text"},
            "body": {"type": "text"},
            "content_type": {"type": "keyword"},
            "content_vector": {
                "type": "knn_vector",
                "dimension": 1024,
                "space_type": "cosinesimil",
            },
            "iab_categories": {"type": "keyword"},
            "iab_category_details": {
                "type": "nested",
                "properties": {
                    "id": {"type": "keyword"},
                    "name": {"type": "keyword"},
                    "tier_path": {"type": "keyword"},
                },
            },
            "entities": {
                "type": "nested",
                "properties": {
                    "name": {"type": "keyword"},
                    "type": {"type": "keyword"},
                    "relevance": {"type": "float"},
                },
            },
            "topics": {"type": "keyword"},
            "topic_terms": {"type": "keyword"},
            "answer_capsule": {"type": "text"},
            "language": {"type": "keyword"},
            "license_id": {"type": "keyword"},
            "status": {"type": "keyword"},
            "cms_tags": {"type": "keyword"},
            "author": {"type": "keyword"},
            "word_count": {"type": "integer"},
            "markdown_schema_version": {"type": "keyword"},
            "canonical_url": {"type": "keyword", "index": False},
            "canonical_host": {"type": "keyword"},
            "canonical_path": {"type": "keyword"},
            "canonical_path_ancestors": {"type": "keyword"},
            "url_hash": {"type": "keyword"},
            "edge_eligible": {"type": "boolean"},
            "md_s3_key": {"type": "keyword", "index": False},
            "publish_date": {"type": "date", "ignore_malformed": True},
            "indexed_at": {"type": "date", "ignore_malformed": True},
        }
    },
}
ADDITIVE_INDEX_MAPPING = {
    "properties": {
        "content_type": {"type": "keyword"},
        "iab_category_details": {
            "type": "nested",
            "properties": {
                "id": {"type": "keyword"},
                "name": {"type": "keyword"},
                "tier_path": {"type": "keyword"},
            },
        },
        "topic_terms": {"type": "keyword"},
        "status": {"type": "keyword"},
        "cms_tags": {"type": "keyword"},
        "author": {"type": "keyword"},
        "word_count": {"type": "integer"},
        "markdown_schema_version": {"type": "keyword"},
        "canonical_url": {"type": "keyword", "index": False},
        "canonical_host": {"type": "keyword"},
        "canonical_path": {"type": "keyword"},
        "canonical_path_ancestors": {"type": "keyword"},
        "url_hash": {"type": "keyword"},
        "edge_eligible": {"type": "boolean"},
        "md_s3_key": {"type": "keyword", "index": False},
        "indexed_at": {"type": "date", "ignore_malformed": True},
    }
}

_client: Optional[OpenSearch] = None
_index_ensured = False


ENDPOINT_SSM_PARAM = "/iab-llm/opensearch/endpoint"


def _resolve_endpoint() -> str:
    """OPENSEARCH_ENDPOINT env var (pipeline Lambda) or SSM (partner API —
    its stack deploys before the collection exists, so no env-var wiring)."""
    endpoint = os.environ.get("OPENSEARCH_ENDPOINT")
    if endpoint:
        return endpoint
    ssm = boto3.client("ssm")
    return ssm.get_parameter(Name=ENDPOINT_SSM_PARAM)["Parameter"]["Value"]


def get_client() -> OpenSearch:
    global _client
    if _client is None:
        endpoint = _resolve_endpoint()  # e.g. https://xxxx.us-east-1.aoss.amazonaws.com
        host = endpoint.replace("https://", "").rstrip("/")
        region = os.environ.get("REGION", os.environ.get("AWS_REGION", "us-east-1"))
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, "aoss")
        _client = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=10,
            timeout=60,  # NextGen wake latency can be 10-30s after idle
        )
    return _client


def ensure_index() -> None:
    """Create the hybrid index if absent (idempotent, once per container)."""
    global _index_ensured
    if _index_ensured:
        return
    client = get_client()
    if not client.indices.exists(index=INDEX_NAME):
        try:
            client.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
            logger.info(f"Created OpenSearch index {INDEX_NAME}")
        except Exception as e:
            # Racing writer may have created it between exists() and create()
            if "resource_already_exists" not in str(e):
                raise
    else:
        client.indices.put_mapping(index=INDEX_NAME, body=ADDITIVE_INDEX_MAPPING)
    _index_ensured = True


def index_document(content_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotent upsert keyed on content_id (re-processing overwrites)."""
    ensure_index()
    return get_client().index(index=INDEX_NAME, id=content_id, body=doc)


def hybrid_search(
    query_text: str,
    query_vector: List[float],
    size: int = 10,
    filters: Optional[List[Dict[str, Any]]] = None,
    *,
    include_body: bool = False,
    timeout_seconds: int = 24,
) -> List[Dict[str, Any]]:
    """
    Hybrid query: kNN over content_vector + BM25 over title/body/answer_capsule,
    combined in one bool.should so both signals contribute to _score.
    """
    query: Dict[str, Any] = {
        "size": size,
        "_source": {"excludes": ["content_vector"] + ([] if include_body else ["body"])},
        "query": {
            "bool": {
                "should": [
                    {"knn": {"content_vector": {"vector": query_vector, "k": size}}},
                    {
                        "multi_match": {
                            "query": query_text,
                            "fields": ["title^2", "body", "answer_capsule^1.5", "topics"],
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        },
    }
    if filters:
        query["query"]["bool"]["filter"] = filters

    response = get_client().search(index=INDEX_NAME, body=query, request_timeout=timeout_seconds)
    return _search_results(response)


def filter_search(
    *,
    size: int,
    filters: List[Dict[str, Any]],
    include_body: bool = False,
    timeout_seconds: int = 24,
) -> List[Dict[str, Any]]:
    """Run deterministic filter-only discovery without a Titan embedding call."""
    query: Dict[str, Any] = {
        "size": size,
        "_source": {"excludes": ["content_vector"] + ([] if include_body else ["body"])},
        "sort": [
            {"publish_date": {"order": "desc", "missing": "_last"}},
            {"content_id": {"order": "asc"}},
        ],
        "query": {"bool": {"must": [{"match_all": {}}], "filter": filters}},
    }
    response = get_client().search(index=INDEX_NAME, body=query, request_timeout=timeout_seconds)
    return _search_results(response, filter_only=True)


def _search_results(response: Dict[str, Any], *, filter_only: bool = False) -> List[Dict[str, Any]]:
    hits = response.get("hits", {}).get("hits", [])
    max_score = response.get("hits", {}).get("max_score") or 1.0
    results = []
    for hit in hits:
        item = hit.get("_source", {})
        item["_score"] = hit.get("_score", 0.0)
        item["similarity_score"] = (
            1.0 if filter_only else round(min(hit.get("_score", 0.0) / max_score, 1.0), 4)
        )
        results.append(item)
    return results
