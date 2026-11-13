"""Route tests for ``ui_backend/routes/comparison.py`` and its dispatcher wiring (spec
``scrape-vs-api-comparison``, design §3a / §6).

The service is stubbed; these tests cover auth, method + path dispatch, body parsing, the 500
fallback, and that both operations' responses validate against the contract (``_validation`` is
attached by ``validate_response`` outside production).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from shared.utils.response_helpers import create_success_response
from ui_backend.routes import comparison, resolve_route

COLLECTION = "/test/comparison-runs"
RUN_ID = "c" * 64 + "-1700000000"


def _event(method: str, path: str, body=None, authed: bool = True):
    event = {"httpMethod": method, "path": path, "headers": {}}
    if authed:
        event["requestContext"] = {"authorizer": {"claims": {"sub": "user-1", "cognito:username": "olivier"}}}
    if body is not None:
        event["body"] = json.dumps(body) if not isinstance(body, str) else body
    return event


def _body(response):
    return json.loads(response["body"])


@pytest.fixture
def service(monkeypatch):
    stub = MagicMock()
    monkeypatch.setattr(comparison, "comparison_service", stub)
    return stub


SUBMITTED = {
    "run_id": RUN_ID,
    "submitted_at": "2026-11-03T21:00:00+00:00",
    "ingest": "submitted",
    "allow_list_mode": "open",
    "article": {
        "website_url": "https://p.example.com/a",
        "final_url": "https://p.example.com/a",
        "canonical_url": "https://p.example.com/a",
        "canonical_injected": False,
        "markdown_key": "content/p.example.com/a.md",
        "licence_advertised": None,
    },
    "scrape": {
        "status": 200,
        "document_bytes": 10,
        "total_bytes": 10,
        "requests": 1,
        "external_refs": 0,
        "document_ms": 5,
        "total_ms": 6,
        "subresources_probed": 0,
        "subresources_skipped": 0,
    },
    "html": "<html/>",
}
READY = {
    "run_id": RUN_ID,
    "status": "ready",
    "elapsed_ms": 1200,
    "api": {
        "content_id": "cnt_abc",
        "resolve_ms": 3,
        "fetch_ms": 4,
        "bytes": 5,
        "markdown": "# a",
        "etag": "e1",
        "updated_at": "2026-11-03T21:00:01+00:00",
        "provenance": {"title": "a", "author": None, "pubdate": None, "canonical_url": "https://p.example.com/a"},
    },
}


class TestRouteTable:
    def test_both_operations_resolve_to_the_comparison_route(self):
        assert resolve_route(COLLECTION, "POST") == "comparison"
        assert resolve_route(f"{COLLECTION}/{RUN_ID}", "GET") == "comparison"

    def test_other_methods_do_not_resolve(self):
        assert resolve_route(COLLECTION, "DELETE") is None
        assert resolve_route(COLLECTION, "PUT") is None

    def test_dispatcher_maps_the_route_name(self):
        """``ui_backend/handler.py`` uses zip-root imports (``from routes ...``), so it is not
        importable from the canonical test root; assert the wiring on its source instead."""
        from pathlib import Path

        source = Path(comparison.__file__).resolve().parents[1] / "handler.py"
        text = source.read_text(encoding="utf-8")
        assert "from routes.comparison import route as comparison_route" in text
        assert '"comparison": comparison_route,' in text


class TestSubmitDispatch:
    def test_post_parses_body_and_passes_user(self, service):
        service.submit.return_value = create_success_response(SUBMITTED, 202)
        response = comparison.route(_event("POST", COLLECTION, {"url": "https://p.example.com/a"}), "POST", COLLECTION)
        assert response["statusCode"] == 202
        user, request_data, event = service.submit.call_args.args
        assert user["user_id"] == "user-1"
        assert request_data == {"url": "https://p.example.com/a"}
        assert event["path"] == COLLECTION
        assert _body(response)["_validation"]["valid"] is True, _body(response)["_validation"]

    def test_invalid_json_body_becomes_empty_request(self, service):
        service.submit.return_value = create_success_response(SUBMITTED, 202)
        comparison.route(_event("POST", COLLECTION, body="{not json"), "POST", COLLECTION)
        assert service.submit.call_args.args[1] == {}

    def test_unauthenticated_post_is_refused_before_the_service(self, service):
        response = comparison.route(_event("POST", COLLECTION, {"url": "x"}, authed=False), "POST", COLLECTION)
        assert response["statusCode"] in (401, 403)
        service.submit.assert_not_called()

    def test_service_exception_is_a_sanitized_500(self, service):
        service.submit.side_effect = RuntimeError("bucket name leaked")
        response = comparison.route(_event("POST", COLLECTION, {"url": "x"}), "POST", COLLECTION)
        assert response["statusCode"] == 500
        assert "bucket name leaked" not in response["body"]


class TestPollDispatch:
    def test_get_extracts_run_id(self, service):
        service.poll.return_value = create_success_response(READY)
        path = f"{COLLECTION}/{RUN_ID}"
        response = comparison.route(_event("GET", path), "GET", path)
        assert response["statusCode"] == 200
        assert service.poll.call_args.args[0] == RUN_ID
        assert _body(response)["_validation"]["valid"] is True, _body(response)["_validation"]

    def test_pending_shape_validates(self, service):
        service.poll.return_value = create_success_response({"run_id": RUN_ID, "status": "pending", "elapsed_ms": 10})
        path = f"{COLLECTION}/{RUN_ID}"
        assert _body(comparison.route(_event("GET", path), "GET", path))["_validation"]["valid"] is True

    def test_unauthenticated_get_is_refused(self, service):
        path = f"{COLLECTION}/{RUN_ID}"
        response = comparison.route(_event("GET", path, authed=False), "GET", path)
        assert response["statusCode"] in (401, 403)
        service.poll.assert_not_called()

    def test_get_on_the_collection_is_404(self, service):
        response = comparison.route(_event("GET", COLLECTION), "GET", COLLECTION)
        assert response["statusCode"] == 404
        service.poll.assert_not_called()

    def test_post_on_a_run_is_404(self, service):
        path = f"{COLLECTION}/{RUN_ID}"
        response = comparison.route(_event("POST", path, {}), "POST", path)
        assert response["statusCode"] == 404
        service.submit.assert_not_called()
