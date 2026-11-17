"""Preparation contract through real routing, authentication and response decorators."""

import json
from unittest.mock import MagicMock

import pytest

from partner_api import handler
from services import comp_query_service, search_service
from shared.utils import response_validator


@pytest.fixture
def routed(monkeypatch):
    validator = response_validator.APIResponseValidator()
    validator.validation_enabled = True
    validator.validation_mode = "strict"
    monkeypatch.setattr(response_validator, "_validator", validator)
    monkeypatch.setattr(
        "shared.auth.authentication_decorators.extract_cognito_user_context",
        lambda *_args: {"user_id": "test"},
    )
    monkeypatch.setattr(comp_query_service, "validate_comp_license", lambda *_args: (True, None, {}))
    embedding = MagicMock(return_value={"success": True, "embedding": [0.1]})
    hybrid = MagicMock(return_value=[
        {"content_id": f"cnt_{index}", "title": str(index), "content_type": "article", "similarity_score": score}
        for index, score in enumerate([1.0, 0.7, 0.0])
    ])
    filtered = MagicMock(return_value=[])
    analytics = MagicMock()
    monkeypatch.setattr(search_service, "generate_search_embedding", embedding)
    monkeypatch.setattr(search_service, "hybrid_search", hybrid)
    monkeypatch.setattr(search_service, "filter_search", filtered)
    monkeypatch.setattr(handler, "get_analytics_collector", lambda: analytics)

    def invoke(body, path="/query"):
        return handler.lambda_handler(
            {
                "path": path,
                "httpMethod": "POST",
                "headers": {},
                "body": json.dumps(body),
                # API Gateway always sets the serving domain; read references are built from it.
                "requestContext": {"domainName": "internal.example", "stage": "v1"},
            },
            MagicMock(aws_request_id="test"),
        )

    return invoke, embedding, hybrid, filtered, analytics


def _body(comp, **fields):
    if not comp:
        return {"query": "health", **fields}
    return {"aisystem": {"name": "agent", "aisysuse": {
        "lid": "lic", "aiauth": 2, "scope": 0, "ext": {"discovery": fields},
    }}}


@pytest.mark.parametrize("comp", [False, True])
@pytest.mark.parametrize("threshold", [None, True, False, "0.7", -0.1, 1.1, float("nan"), float("inf"), -float("inf")])
def test_invalid_threshold_fails_before_backend_or_analytics(routed, comp, threshold):
    invoke, embedding, hybrid, filtered, analytics = routed
    response = invoke(_body(comp, query="health", similarity_threshold=threshold))
    assert response["statusCode"] == 400
    assert "similarity_threshold" in json.loads(response["body"])["error"]
    for mock in (embedding, hybrid, filtered, analytics.record_search_query, analytics.record_api_access):
        mock.assert_not_called()


@pytest.mark.parametrize("comp", [False, True])
@pytest.mark.parametrize("threshold,count", [(0, 3), (0.7, 2), (1, 1)])
def test_explicit_threshold_is_inclusive_and_preserves_hybrid(routed, comp, threshold, count):
    invoke, embedding, hybrid, filtered, analytics = routed
    response = invoke(_body(comp, query="health", similarity_threshold=threshold))
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    assert len(data["package"]["scope"]["text"] if comp else data["results"]) == count
    embedding.assert_called_once()
    hybrid.assert_called_once()
    filtered.assert_not_called()
    assert analytics.record_search_query.call_count == (0 if comp else 1)
    assert analytics.record_api_access.call_count == (0 if comp else 1)


@pytest.mark.parametrize("comp", [False, True])
def test_omitted_threshold_has_no_implicit_cutoff(routed, comp):
    invoke, embedding, hybrid, filtered, _analytics = routed
    response = invoke(_body(comp, query="health"))
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    assert len(data["package"]["scope"]["text"] if comp else data["results"]) == 3
    embedding.assert_called_once()
    hybrid.assert_called_once()
    filtered.assert_not_called()


@pytest.mark.parametrize("threshold", [0, 0.7, 1])
@pytest.mark.parametrize("license_id", ["lic", "PENDING_REVIEW"])
def test_filter_only_threshold_is_rejected_even_for_pending_license(routed, threshold, license_id):
    invoke, embedding, hybrid, filtered, _analytics = routed
    body = _body(True, similarity_threshold=threshold)
    body["aisystem"]["aisysuse"]["lid"] = license_id
    response = invoke(body)
    assert response["statusCode"] == 400
    assert "query" in json.loads(response["body"])["error"]
    for mock in (embedding, hybrid, filtered):
        mock.assert_not_called()


def test_filter_only_without_threshold_skips_embedding(routed):
    invoke, embedding, hybrid, filtered, _analytics = routed
    assert invoke(_body(True))["statusCode"] == 200
    embedding.assert_not_called()
    hybrid.assert_not_called()
    filtered.assert_called_once()


@pytest.mark.parametrize("comp", [False, True])
@pytest.mark.parametrize("limit", [None, True, 0, 51, "10", 1.5])
def test_invalid_limits_fail_without_backend_calls(routed, comp, limit):
    invoke, embedding, hybrid, filtered, _analytics = routed
    response = invoke(_body(comp, query="health", max_results=limit))
    assert response["statusCode"] == 400
    assert "max_results" in json.loads(response["body"])["error"]
    for mock in (embedding, hybrid, filtered):
        mock.assert_not_called()


@pytest.mark.parametrize("legacy,value", [("content_types", ["article"]), ("limit", 10)])
@pytest.mark.parametrize("with_canonical", [False, True])
def test_standard_legacy_fields_are_rejected_even_with_canonical(routed, legacy, value, with_canonical):
    invoke, embedding, hybrid, filtered, _analytics = routed
    body = _body(False, **{legacy: value})
    if with_canonical:
        body.update({"content_type": ["article"], "max_results": 10})
    response = invoke(body)
    assert response["statusCode"] == 400
    assert legacy in json.loads(response["body"])["error"]
    for mock in (embedding, hybrid, filtered):
        mock.assert_not_called()


@pytest.mark.parametrize("limit", [1, 10, 50])
def test_comp_represents_all_supported_results_once_in_order(routed, limit):
    invoke, _embedding, hybrid, _filtered, _analytics = routed
    hits = [{"content_id": f"cnt_{i}", "title": str(i), "content_type": "article"} for i in range(limit)]
    hybrid.return_value = [hits[0], {"content_id": "video", "content_type": "video"}, *hits]
    response = invoke(_body(True, query="health", max_results=limit))
    assert response["statusCode"] == 200
    data = json.loads(response["body"])
    assert [item["title"] for item in data["package"]["scope"]["text"]] == [str(i) for i in range(limit)]
    assert data["_meta"]["total_results"] == limit
    assert hybrid.call_args.kwargs["size"] == limit


def test_comp_unsupported_only_has_zero_represented_count(routed):
    invoke, _embedding, hybrid, _filtered, _analytics = routed
    hybrid.return_value = [{"content_id": "video", "content_type": "video"}]
    data = json.loads(invoke(_body(True, query="health"))["body"])
    assert data["package"]["scope"]["text"] == []
    assert data["_meta"]["total_results"] == 0
