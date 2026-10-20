"""Unit tests for the OpenSearch-only discovery service."""

import inspect
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from opensearchpy.exceptions import ConnectionTimeout, TransportError

import services.search_service as search_service
from shared import bedrock_embeddings
from services.search_service import DiscoveryRequest, SearchService, SearchUnavailableError, SectionPair


def _hit(**overrides):
    result = {
        "content_id": "cnt_1",
        "title": "Machine Learning in Healthcare",
        "content_type": "article",
        "status": "published",
        "similarity_score": 0.9,
        "iab_categories": ["111"],
        "iab_category_details": [
            {"id": "111", "name": "Medical Health", "tier_path": ["Medical Health", "Diseases"]}
        ],
        "topics": ["Machine Learning"],
        "cms_tags": ["healthcare"],
        "answer_capsule": "A concise answer.",
        "author": "Dr. Jane",
        "word_count": 42,
        "publish_date": "2026-01-15T00:00:00+00:00",
        "license_id": "lic_1",
        "md_s3_key": "content/news.example.com/health/ml.md",
    }
    result.update(overrides)
    return result


def test_text_discovery_uses_hybrid_search_with_denormalized_filters(monkeypatch):
    service = SearchService()
    monkeypatch.setattr(
        search_service,
        "generate_search_embedding",
        lambda *_args: {"success": True, "embedding": [0.1, 0.2]},
    )
    hybrid = MagicMock(return_value=[_hit()])
    monkeypatch.setattr(search_service, "hybrid_search", hybrid)

    results = service.execute_discovery(
        DiscoveryRequest(
            query_text="machine learning",
            content_types=["article"],
            iab_category_ids=["111"],
            topics=["MACHINE   LEARNING"],
            limit=5,
        )
    )

    assert results[0]["iab_categories"] == [
        {"id": "111", "name": "Medical Health", "tier_1": "Medical Health", "tier_2": "Diseases"}
    ]
    assert results[0]["url"] == "/content/news.example.com/health/ml.md"
    request_filters = hybrid.call_args.kwargs["filters"]
    assert {"term": {"status": "published"}} in request_filters
    assert {"terms": {"content_type": ["article"]}} in request_filters
    assert {"terms": {"iab_categories": ["111"]}} in request_filters
    assert {"terms": {"topic_terms": ["machine learning"]}} in request_filters


def test_filter_only_discovery_skips_embedding_and_uses_deterministic_search(monkeypatch):
    service = SearchService()
    embedding = MagicMock()
    filter_only = MagicMock(return_value=[_hit(similarity_score=1.0)])
    monkeypatch.setattr(search_service, "generate_search_embedding", embedding)
    monkeypatch.setattr(search_service, "filter_search", filter_only)

    results = service.execute_discovery(DiscoveryRequest(limit=10))

    assert results[0]["similarity_score"] == 1.0
    embedding.assert_not_called()
    assert filter_only.call_args.kwargs["filters"] == [{"term": {"status": "published"}}]


def test_section_pairs_are_or_grouped_without_host_path_cross_product():
    filters = SearchService()._build_filters(
        DiscoveryRequest(
            section_pairs=[
                SectionPair("first.example", "/health"),
                SectionPair("second.example", "/finance"),
            ]
        )
    )

    group = filters[-1]["bool"]
    assert group["minimum_should_match"] == 1
    assert group["should"][0]["bool"]["filter"] == [
        {"term": {"canonical_host": "first.example"}},
        {"term": {"canonical_path_ancestors": "/health"}},
    ]
    assert group["should"][1]["bool"]["filter"] == [
        {"term": {"canonical_host": "second.example"}},
        {"term": {"canonical_path_ancestors": "/finance"}},
    ]


def test_small_body_is_inline_only_for_standard_query(monkeypatch):
    service = SearchService()
    monkeypatch.setattr(
        search_service,
        "generate_search_embedding",
        lambda *_args: {"success": True, "embedding": [0.1]},
    )
    monkeypatch.setattr(search_service, "hybrid_search", lambda *_args, **_kwargs: [_hit(body="short body")])

    inline = service.execute_discovery(DiscoveryRequest(query_text="health", include_inline_content=True))[0]
    linked = service.execute_discovery(DiscoveryRequest(query_text="health", include_inline_content=False))[0]

    assert inline["content"] == "short body"
    assert "url" not in inline
    assert linked["url"] == "/content/news.example.com/health/ml.md"
    assert "content" not in linked


def test_similarity_threshold_filters_text_results(monkeypatch):
    service = SearchService()
    monkeypatch.setattr(
        search_service,
        "generate_search_embedding",
        lambda *_args: {"success": True, "embedding": [0.1]},
    )
    monkeypatch.setattr(
        search_service,
        "hybrid_search",
        lambda *_args, **_kwargs: [_hit(content_id="high", similarity_score=0.9), _hit(content_id="low", similarity_score=0.2)],
    )

    results = service.execute_discovery(DiscoveryRequest(query_text="health", similarity_threshold=0.5))

    assert [item["content_id"] for item in results] == ["high"]


@pytest.mark.parametrize("text", ["", "health"])
@pytest.mark.parametrize(
    "error",
    [
        TransportError(503, "unavailable"),
        TransportError(403, "forbidden"),
        TransportError(404, "index_not_found_exception"),
        ConnectionTimeout("TIMEOUT", "timeout"),
        EndpointConnectionError(endpoint_url="https://bedrock.invalid"),
        ClientError({"Error": {"Code": "ThrottlingException", "Message": "try later"}}, "InvokeModel"),
    ],
)
def test_backend_error_maps_to_search_unavailable(monkeypatch, text, error):
    service = SearchService()
    monkeypatch.setattr(
        search_service,
        "generate_search_embedding",
        lambda *_args: {"success": True, "embedding": [0.1]},
    )
    monkeypatch.setattr(search_service, "hybrid_search", MagicMock(side_effect=error))
    monkeypatch.setattr(search_service, "filter_search", MagicMock(side_effect=error))

    with pytest.raises(SearchUnavailableError, match="OpenSearch discovery failed"):
        service.execute_discovery(DiscoveryRequest(query_text=text))


@pytest.mark.parametrize("error", [KeyError("payload"), TypeError("payload"), RuntimeError("bug")])
@pytest.mark.parametrize("stage", ["embedding", "hybrid", "filter", "mapping"])
def test_programming_errors_are_not_retryable(monkeypatch, error, stage):
    monkeypatch.setattr(search_service, "generate_search_embedding", MagicMock(return_value={"success": True, "embedding": [0.1]}))
    monkeypatch.setattr(search_service, "hybrid_search", MagicMock(return_value=[_hit()]))
    monkeypatch.setattr(search_service, "filter_search", MagicMock(return_value=[_hit()]))
    service = SearchService()
    if stage == "mapping":
        monkeypatch.setattr(service, "_result_from_hit", MagicMock(side_effect=error))
    else:
        target = {"embedding": "generate_search_embedding", "hybrid": "hybrid_search", "filter": "filter_search"}[stage]
        monkeypatch.setattr(search_service, target, MagicMock(side_effect=error))
    with pytest.raises(type(error)) as raised:
        service.execute_discovery(DiscoveryRequest(query_text="" if stage == "filter" else "health"))
    assert raised.value is error


def test_real_embedding_helper_does_not_hide_programming_errors(monkeypatch):
    error = TypeError("bad embedding implementation")
    monkeypatch.setattr(bedrock_embeddings, "bedrock_runtime", MagicMock(invoke_model=MagicMock(side_effect=error)))
    with pytest.raises(TypeError) as raised:
        SearchService().execute_discovery(DiscoveryRequest(query_text="health"))
    assert raised.value is error


def test_request_time_discovery_contains_no_dynamodb_scan_path():
    source = inspect.getsource(search_service)

    assert "boto3" not in source
    assert ".scan(" not in source
    assert "filter_and_score_content" not in source
