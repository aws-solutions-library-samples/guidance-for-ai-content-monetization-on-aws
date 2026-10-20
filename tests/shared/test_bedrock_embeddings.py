"""
Unit tests for shared/bedrock_embeddings.py (wave0 Phase 6, Req 19-21).

The boto3 ``bedrock-runtime`` client is stubbed via the module-level
``bedrock_embeddings.bedrock_runtime`` singleton (constructed at import). No
real Bedrock InvokeModel call is made. Covered:

- Model config selection + unsupported-model rejection.
- ``generate_embedding`` Titan success, Cohere success, error/fallback path,
  and long-text truncation.
- ``generate_query_embedding`` — Cohere search_query branch and the Titan
  delegate-to-generate_embedding branch.
- ``calculate_similarity`` — identical, orthogonal, mismatched-length, zero.
- The lambda_handler action router (success + validation + unsupported).
- The module utility functions (``generate_content_embedding`` /
  ``generate_search_embedding``) and their dimension handling.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import shared.bedrock_embeddings as be


def _fake_invoke_response(payload: dict):
    """Build a fake Bedrock invoke_model response whose body.read() returns JSON."""
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode("utf-8")
    return {"body": body}


@pytest.fixture
def stub_runtime(monkeypatch):
    """Replace the module-level bedrock_runtime with a MagicMock."""
    client = MagicMock()
    monkeypatch.setattr(be, "bedrock_runtime", client)
    return client


# --------------------------------------------------------------------------- #
# Construction / config
# --------------------------------------------------------------------------- #
def test_service_selects_titan_v2_config_by_default():
    svc = be.BedrockEmbeddingsService()
    assert svc.model_id == "amazon.titan-embed-text-v2:0"
    assert svc.dimensions == 1024


def test_service_rejects_unsupported_model():
    with pytest.raises(ValueError, match="Unsupported model"):
        be.BedrockEmbeddingsService("no-such-model")


# --------------------------------------------------------------------------- #
# generate_embedding
# --------------------------------------------------------------------------- #
def test_generate_embedding_titan_success(stub_runtime):
    vector = [0.01] * 1024
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embedding": vector})

    svc = be.BedrockEmbeddingsService("titan-embed-text-v2")
    result = svc.generate_embedding("some content about AI")

    assert result["success"] is True
    assert result["dimensions"] == 1024
    assert result["embedding"] == vector
    assert result["model"] == "amazon.titan-embed-text-v2:0"
    # Titan request body carries inputText.
    sent = json.loads(stub_runtime.invoke_model.call_args.kwargs["body"])
    assert sent == {"inputText": "some content about AI"}


def test_generate_embedding_cohere_success(stub_runtime):
    vector = [0.5] * 1024
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embeddings": [vector]})

    svc = be.BedrockEmbeddingsService("cohere-embed-english-v3")
    result = svc.generate_embedding("hello")

    assert result["success"] is True
    assert result["dimensions"] == 1024
    sent = json.loads(stub_runtime.invoke_model.call_args.kwargs["body"])
    assert sent["texts"] == ["hello"]
    assert sent["input_type"] == "search_document"


def test_generate_embedding_error_returns_fallback(stub_runtime):
    stub_runtime.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "try later"}}, "InvokeModel"
    )

    svc = be.BedrockEmbeddingsService("titan-embed-text-v2")
    result = svc.generate_embedding("x")

    assert result["success"] is False
    assert result["embedding"] == []
    assert result["dimensions"] == 0
    assert "ThrottlingException" in result["error"]


def test_generate_embedding_truncates_long_text(stub_runtime):
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embedding": [0.0] * 1024})
    svc = be.BedrockEmbeddingsService("titan-embed-text-v2")

    long_text = "a" * (svc.max_tokens * 4 + 5000)
    svc.generate_embedding(long_text)

    sent = json.loads(stub_runtime.invoke_model.call_args.kwargs["body"])
    assert len(sent["inputText"]) == svc.max_tokens * 4  # truncated to the cap


# --------------------------------------------------------------------------- #
# generate_query_embedding
# --------------------------------------------------------------------------- #
def test_generate_query_embedding_cohere_uses_search_query(stub_runtime):
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embeddings": [[0.1] * 1024]})
    svc = be.BedrockEmbeddingsService("cohere-embed-english-v3")

    result = svc.generate_query_embedding("find me stuff")

    assert result["success"] is True
    sent = json.loads(stub_runtime.invoke_model.call_args.kwargs["body"])
    assert sent["input_type"] == "search_query"


def test_generate_query_embedding_titan_delegates(stub_runtime):
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embedding": [0.2] * 1024})
    svc = be.BedrockEmbeddingsService("titan-embed-text-v2")

    result = svc.generate_query_embedding("query text")

    assert result["success"] is True
    assert result["dimensions"] == 1024
    # Titan query path reuses generate_embedding → inputText body.
    sent = json.loads(stub_runtime.invoke_model.call_args.kwargs["body"])
    assert sent == {"inputText": "query text"}


def test_generate_query_embedding_cohere_error(stub_runtime):
    stub_runtime.invoke_model.side_effect = ClientError(
        {"Error": {"Code": "ServiceUnavailableException", "Message": "boom"}}, "InvokeModel"
    )
    svc = be.BedrockEmbeddingsService("cohere-embed-english-v3")
    result = svc.generate_query_embedding("q")
    assert result["success"] is False
    assert "boom" in result["error"]


@pytest.mark.parametrize("model", ["titan-embed-text-v2", "cohere-embed-english-v3"])
@pytest.mark.parametrize("method", ["generate_embedding", "generate_query_embedding"])
@pytest.mark.parametrize("error", [KeyError("bad body"), TypeError("bad result")])
def test_programming_errors_propagate(stub_runtime, model, method, error):
    stub_runtime.invoke_model.side_effect = error
    with pytest.raises(type(error)) as raised:
        getattr(be.BedrockEmbeddingsService(model), method)("query")
    assert raised.value is error


# --------------------------------------------------------------------------- #
# calculate_similarity
# --------------------------------------------------------------------------- #
def test_calculate_similarity_identical_vectors():
    svc = be.BedrockEmbeddingsService()
    v = [1.0, 2.0, 3.0]
    assert svc.calculate_similarity(v, v) == pytest.approx(1.0)


def test_calculate_similarity_orthogonal_vectors():
    svc = be.BedrockEmbeddingsService()
    assert svc.calculate_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_calculate_similarity_length_mismatch_returns_zero():
    svc = be.BedrockEmbeddingsService()
    assert svc.calculate_similarity([1.0, 2.0], [1.0]) == 0.0


def test_calculate_similarity_zero_vector_returns_zero():
    svc = be.BedrockEmbeddingsService()
    assert svc.calculate_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


# --------------------------------------------------------------------------- #
# lambda_handler router
# --------------------------------------------------------------------------- #
def test_lambda_handler_generate_embedding_success(stub_runtime):
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embedding": [0.0] * 1024})
    resp = be.lambda_handler({"action": "generate_embedding", "text": "hi"}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["success"] is True
    assert body["dimensions"] == 1024


def test_lambda_handler_generate_embedding_missing_text():
    resp = be.lambda_handler({"action": "generate_embedding", "text": ""}, None)
    assert resp["statusCode"] == 400
    assert "Text parameter is required" in json.loads(resp["body"])["error"]


def test_lambda_handler_calculate_similarity(stub_runtime):
    resp = be.lambda_handler(
        {"action": "calculate_similarity", "embedding1": [1.0, 0.0], "embedding2": [1.0, 0.0]}, None
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["similarity"] == pytest.approx(1.0)


def test_lambda_handler_get_model_info():
    resp = be.lambda_handler({"action": "get_model_info", "model": "titan-embed-text-v2"}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "titan-embed-text-v2" in body["available_models"]


def test_lambda_handler_unsupported_action():
    resp = be.lambda_handler({"action": "nope"}, None)
    assert resp["statusCode"] == 400
    assert "Unsupported action" in json.loads(resp["body"])["error"]


def test_lambda_handler_unsupported_model_returns_500():
    # BedrockEmbeddingsService(model_name) raises ValueError → caught → 500.
    resp = be.lambda_handler({"action": "generate_embedding", "model": "bad-model", "text": "x"}, None)
    assert resp["statusCode"] == 500


# --------------------------------------------------------------------------- #
# module utility functions
# --------------------------------------------------------------------------- #
def test_generate_content_embedding_helper(stub_runtime):
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embedding": [0.3] * 1024})
    result = be.generate_content_embedding("content body")
    assert result["success"] is True
    assert result["dimensions"] == 1024


def test_generate_search_embedding_helper(stub_runtime):
    stub_runtime.invoke_model.return_value = _fake_invoke_response({"embedding": [0.4] * 1024})
    result = be.generate_search_embedding("search query")
    assert result["success"] is True
    assert result["dimensions"] == 1024


def test_get_embeddings_service_returns_configured_instance():
    svc = be.get_embeddings_service("titan-embed-text-v1")
    assert svc.model_id == "amazon.titan-embed-text-v1"
    assert svc.dimensions == 1536
