"""
Unit tests for services/opensearch_client.py (wave0 Phase 6, Req 19-21).

The OpenSearch client is exercised entirely through stubs/mocks — no real AOSS
endpoint is contacted, and no boto3 credentials/SSM calls escape the test. Each
test resets the module-level ``_client`` / ``_index_ensured`` singletons so the
lazy-init and idempotency logic can be observed deterministically regardless of
suite order (the root autouse isolation fixture handles os.environ).

Covered:
- ``_resolve_endpoint`` — env-var branch and SSM branch.
- ``get_client`` — lazy construction + caching.
- ``ensure_index`` — create-when-absent, skip-when-present, once-per-container,
  and the "racing writer already created it" swallow vs. re-raise.
- ``index_document`` — ensures index then upserts keyed on content_id.
- ``hybrid_search`` — query shape (kNN + BM25 bool.should), optional filters,
  score normalization, and the empty-hits path.
"""

import importlib

import pytest

import services.opensearch_client as osc


@pytest.fixture(autouse=True)
def _reset_client_singletons():
    """Reset the module-level lazy singletons around every test."""
    osc._client = None
    osc._index_ensured = False
    yield
    osc._client = None
    osc._index_ensured = False


# --------------------------------------------------------------------------- #
# _resolve_endpoint
# --------------------------------------------------------------------------- #
def test_resolve_endpoint_prefers_env_var(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://from-env.us-east-1.aoss.amazonaws.com")
    # SSM must NOT be called when the env var is present.
    called = {"ssm": False}

    def _boom(*_a, **_k):
        called["ssm"] = True
        raise AssertionError("boto3.client should not be called when env var is set")

    monkeypatch.setattr(osc.boto3, "client", _boom)
    assert osc._resolve_endpoint() == "https://from-env.us-east-1.aoss.amazonaws.com"
    assert called["ssm"] is False


def test_resolve_endpoint_falls_back_to_ssm(monkeypatch):
    monkeypatch.delenv("OPENSEARCH_ENDPOINT", raising=False)

    class _FakeSSM:
        def get_parameter(self, Name):  # noqa: N803 — boto3 kwarg name
            assert Name == osc.ENDPOINT_SSM_PARAM
            return {"Parameter": {"Value": "https://from-ssm.us-east-1.aoss.amazonaws.com"}}

    monkeypatch.setattr(osc.boto3, "client", lambda service: _FakeSSM())
    assert osc._resolve_endpoint() == "https://from-ssm.us-east-1.aoss.amazonaws.com"


# --------------------------------------------------------------------------- #
# get_client
# --------------------------------------------------------------------------- #
def test_get_client_builds_and_caches(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://collection.us-east-1.aoss.amazonaws.com/")
    monkeypatch.setenv("REGION", "us-east-1")

    captured = {}

    class _FakeOpenSearch:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # Avoid real credential resolution / signer construction.
    monkeypatch.setattr(osc.boto3, "Session", lambda: type("S", (), {"get_credentials": lambda self: object()})())
    monkeypatch.setattr(osc, "AWSV4SignerAuth", lambda *a, **k: "signer")
    monkeypatch.setattr(osc, "OpenSearch", _FakeOpenSearch)

    c1 = osc.get_client()
    c2 = osc.get_client()

    assert c1 is c2  # cached
    # host derived from endpoint (scheme stripped, trailing slash removed)
    assert captured["hosts"] == [{"host": "collection.us-east-1.aoss.amazonaws.com", "port": 443}]
    assert captured["use_ssl"] is True


# --------------------------------------------------------------------------- #
# ensure_index
# --------------------------------------------------------------------------- #
def _install_fake_client(monkeypatch, *, exists, create_side_effect=None):
    """Install a fake OpenSearch client (with .indices) via get_client()."""
    from unittest.mock import MagicMock

    client = MagicMock()
    client.indices.exists.return_value = exists
    if create_side_effect is not None:
        client.indices.create.side_effect = create_side_effect
    monkeypatch.setattr(osc, "get_client", lambda: client)
    return client


def test_ensure_index_creates_when_absent(monkeypatch):
    client = _install_fake_client(monkeypatch, exists=False)
    osc.ensure_index()
    client.indices.create.assert_called_once_with(index=osc.INDEX_NAME, body=osc.INDEX_MAPPING)
    assert osc._index_ensured is True


def test_ensure_index_skips_when_present(monkeypatch):
    client = _install_fake_client(monkeypatch, exists=True)
    osc.ensure_index()
    client.indices.create.assert_not_called()
    client.indices.put_mapping.assert_called_once_with(index=osc.INDEX_NAME, body=osc.ADDITIVE_INDEX_MAPPING)
    assert osc._index_ensured is True


def test_ensure_index_runs_only_once_per_container(monkeypatch):
    client = _install_fake_client(monkeypatch, exists=False)
    osc.ensure_index()
    osc.ensure_index()  # second call short-circuits on _index_ensured
    client.indices.exists.assert_called_once()
    client.indices.create.assert_called_once()


def test_ensure_index_swallows_racing_already_exists(monkeypatch):
    client = _install_fake_client(
        monkeypatch, exists=False, create_side_effect=Exception("resource_already_exists_exception")
    )
    # Should NOT raise — a racing writer created it between exists() and create().
    osc.ensure_index()
    assert osc._index_ensured is True


def test_ensure_index_reraises_other_errors(monkeypatch):
    _install_fake_client(monkeypatch, exists=False, create_side_effect=Exception("mapping_parsing_exception"))
    with pytest.raises(Exception, match="mapping_parsing_exception"):
        osc.ensure_index()
    # Failed create must not mark the container as ensured.
    assert osc._index_ensured is False


# --------------------------------------------------------------------------- #
# index_document
# --------------------------------------------------------------------------- #
def test_index_document_ensures_index_then_upserts(monkeypatch):
    from unittest.mock import MagicMock

    client = MagicMock()
    client.index.return_value = {"result": "created", "_id": "cnt_1"}
    monkeypatch.setattr(osc, "get_client", lambda: client)

    ensure_calls = {"n": 0}
    monkeypatch.setattr(osc, "ensure_index", lambda: ensure_calls.__setitem__("n", ensure_calls["n"] + 1))

    doc = {"title": "hello", "content_vector": [0.1, 0.2]}
    result = osc.index_document("cnt_1", doc)

    assert ensure_calls["n"] == 1
    client.index.assert_called_once_with(index=osc.INDEX_NAME, id="cnt_1", body=doc)
    assert result["result"] == "created"


def test_delete_document_deletes_by_content_id(monkeypatch):
    from unittest.mock import MagicMock

    client = MagicMock()
    monkeypatch.setattr(osc, "get_client", lambda: client)

    assert osc.delete_document("cnt_1") is True
    client.delete.assert_called_once_with(index=osc.INDEX_NAME, id="cnt_1")


def test_delete_document_treats_not_found_as_done(monkeypatch):
    from unittest.mock import MagicMock

    from opensearchpy import NotFoundError

    client = MagicMock()
    client.delete.side_effect = NotFoundError(404, "not_found", {"result": "not_found"})
    monkeypatch.setattr(osc, "get_client", lambda: client)

    assert osc.delete_document("cnt_missing") is False


def test_delete_document_raises_other_errors(monkeypatch):
    from unittest.mock import MagicMock

    from opensearchpy import AuthorizationException

    client = MagicMock()
    client.delete.side_effect = AuthorizationException(403, "security_exception", {})
    monkeypatch.setattr(osc, "get_client", lambda: client)

    with pytest.raises(AuthorizationException):
        osc.delete_document("cnt_1")


# --------------------------------------------------------------------------- #
# hybrid_search
# --------------------------------------------------------------------------- #
def _search_client(monkeypatch, response):
    from unittest.mock import MagicMock

    client = MagicMock()
    client.search.return_value = response
    monkeypatch.setattr(osc, "get_client", lambda: client)
    monkeypatch.setattr(osc, "ensure_index", lambda: None)
    return client


def test_hybrid_search_builds_knn_and_bm25_query(monkeypatch):
    client = _search_client(
        monkeypatch,
        {
            "hits": {
                "max_score": 2.0,
                "hits": [
                    {"_score": 2.0, "_source": {"content_id": "a", "title": "A"}},
                    {"_score": 1.0, "_source": {"content_id": "b", "title": "B"}},
                ],
            }
        },
    )

    results = osc.hybrid_search("machine learning", [0.1, 0.2, 0.3], size=5)

    # Query shape: exactly the kNN + multi_match should-clauses.
    body = client.search.call_args.kwargs["body"]
    should = body["query"]["bool"]["should"]
    assert body["size"] == 5
    assert any("knn" in clause for clause in should)
    assert any("multi_match" in clause for clause in should)
    assert body["query"]["bool"]["minimum_should_match"] == 1
    assert "filter" not in body["query"]["bool"]  # no filters passed

    # Score normalization against max_score.
    assert results[0]["similarity_score"] == 1.0
    assert results[1]["similarity_score"] == 0.5
    assert results[0]["_score"] == 2.0


def test_hybrid_search_applies_filters(monkeypatch):
    client = _search_client(monkeypatch, {"hits": {"max_score": 1.0, "hits": []}})
    filters = [{"term": {"language": "en"}}]
    osc.hybrid_search("q", [0.0], size=3, filters=filters)
    body = client.search.call_args.kwargs["body"]
    assert body["query"]["bool"]["filter"] == filters
    assert client.search.call_args.kwargs["request_timeout"] == 24


def test_filter_search_is_deterministic_and_excludes_content_body(monkeypatch):
    client = _search_client(monkeypatch, {"hits": {"hits": []}})

    osc.filter_search(size=7, filters=[{"term": {"status": "published"}}])

    body = client.search.call_args.kwargs["body"]
    assert body["query"] == {
        "bool": {
            "must": [{"match_all": {}}],
            "filter": [{"term": {"status": "published"}}],
        }
    }
    assert body["sort"] == [
        {"publish_date": {"order": "desc", "missing": "_last"}},
        {"content_id": {"order": "asc"}},
    ]
    assert body["_source"]["excludes"] == ["content_vector", "body"]
    assert client.search.call_args.kwargs["request_timeout"] == 24


def test_retrieval_does_not_manage_index(monkeypatch):
    from unittest.mock import MagicMock

    _search_client(monkeypatch, {"hits": {"max_score": 1.0, "hits": []}})
    ensure_index = MagicMock()
    monkeypatch.setattr(osc, "ensure_index", ensure_index)

    osc.hybrid_search("q", [0.0], size=1)
    osc.filter_search(size=1, filters=[])

    ensure_index.assert_not_called()


def test_hybrid_search_handles_empty_hits(monkeypatch):
    _search_client(monkeypatch, {"hits": {"hits": []}})
    assert osc.hybrid_search("q", [0.0]) == []


def test_module_index_name_is_configurable():
    # INDEX_NAME resolves from OPENSEARCH_INDEX with a sane default.
    reloaded = importlib.reload(osc)
    assert isinstance(reloaded.INDEX_NAME, str) and reloaded.INDEX_NAME
    # Reset singletons the reload just re-created (belt & suspenders for isolation).
    reloaded._client = None
    reloaded._index_ensured = False


def test_index_mapping_persists_markdown_schema_version_as_keyword():
    mapping = osc.INDEX_MAPPING["mappings"]["properties"]
    assert mapping["markdown_schema_version"] == {"type": "keyword"}
    assert mapping["content_type"] == {"type": "keyword"}
    assert mapping["url_hash"] == {"type": "keyword"}
    assert mapping["edge_eligible"] == {"type": "boolean"}
    assert mapping["md_s3_key"] == {"type": "keyword", "index": False}
    assert mapping["canonical_url"] == {"type": "keyword", "index": False}
