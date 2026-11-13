"""Unit tests for ``GET /content`` on the internal (Cognito) plane.

The internal API sends every ``/content`` method to ``ui_backend``. ``GET /content`` there is the
publisher content list. With a ``url`` or ``content_id`` it is the partner plane's single-document
read instead, through the same read function, with two differences:

- it emits no usage event, because a publisher reading their own content is not a licensed access
- it carries the CORS headers and exposes ``ETag``, because the caller is a browser

The partner plane's read keeps billing exactly as before.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services import content_service  # noqa: E402
from services.content_service import ContentService, handle_content_read  # noqa: E402

PORTAL = "https://portal.example.com"
CANONICAL = "https://news.example.com/articles/one"
COGNITO_USER = {"user_id": "user-1", "username": "publisher"}
PARTNER_USER = {"partner_id": "partner_test_001"}


def _record(**overrides):
    record = {
        "content_id": "cnt_a1b2c3d4e5f6",
        "canonical_url": CANONICAL,
        "status": "published",
        "title": "Probe",
        "content_text": "# Probe\n\nBody.",
        "updated_at": "2026-02-02T00:00:00Z",
        "content_type": "article",
    }
    record.update(overrides)
    return record


def _event(query=None, headers=None):
    return {
        "httpMethod": "GET",
        "path": "/content",
        "headers": {"Authorization": "Bearer test-cognito-token", **(headers or {})},
        "body": None,
        "queryStringParameters": query,
        "pathParameters": None,
        "requestContext": {"requestId": "internal-content-read-test"},
    }


@pytest.fixture(autouse=True)
def portal_origin(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", PORTAL)


@pytest.fixture
def cognito():
    with patch("services.content_service.get_user_from_token", return_value=COGNITO_USER) as mocked:
        yield mocked


@pytest.fixture
def emit():
    with patch("services.content_service.emit_usage_event") as mocked:
        yield mocked


@pytest.fixture
def table(monkeypatch):
    fake = MagicMock()
    fake.get_item.return_value = {"Item": _record()}
    monkeypatch.setattr(content_service, "content_table", fake)
    return fake


@pytest.fixture
def url_lookup():
    with patch("services.content_service._query_content_by_url_hash", return_value=[_record()]) as mocked:
        yield mocked


def _handle(event):
    return ContentService().handle_request(event, None)


# --------------------------------------------------------------------------- #
# Without a selector: the publisher content list
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query", [None, {}, {"limit": "10"}, {"search": "probe"}])
def test_get_content_without_a_selector_is_the_content_list(query, cognito, emit):
    with patch("services.content_service.handle_list_content", return_value={"statusCode": 200}) as listed:
        response = _handle(_event(query))

    assert response == {"statusCode": 200}
    listed.assert_called_once_with(COGNITO_USER, query or {})
    emit.assert_not_called()


# --------------------------------------------------------------------------- #
# With a selector: the partner read, unbilled
# --------------------------------------------------------------------------- #
def test_cognito_url_read_serves_the_document_and_bills_nothing(cognito, emit, table, url_lookup):
    response = _handle(_event({"url": CANONICAL}))

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["content_id"] == "cnt_a1b2c3d4e5f6"
    assert response["headers"]["ETag"]
    emit.assert_not_called()


def test_cognito_content_id_read_serves_the_document_and_bills_nothing(cognito, emit, table):
    response = _handle(_event({"content_id": "cnt_a1b2c3d4e5f6"}))

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["content_id"] == "cnt_a1b2c3d4e5f6"
    emit.assert_not_called()


@pytest.mark.parametrize("query", [{"url": CANONICAL}, {"content_id": "cnt_a1b2c3d4e5f6"}])
def test_cognito_read_matches_the_partner_read(query, cognito, emit, table, url_lookup):
    internal = _handle(_event(query))
    partner = handle_content_read(PARTNER_USER, dict(query))

    assert json.loads(internal["body"]) == json.loads(partner["body"])
    assert internal["headers"]["ETag"] == partner["headers"]["ETag"]


def test_the_partner_read_is_still_billed(emit, table, url_lookup):
    response = handle_content_read(PARTNER_USER, {"url": CANONICAL})

    assert response["statusCode"] == 200
    emit.assert_called_once()
    assert emit.call_args.kwargs["partner_id"] == "partner_test_001"


@pytest.mark.parametrize("header", ["If-None-Match", "if-none-match"])
def test_if_none_match_header_gives_a_304(header, cognito, emit, table, url_lookup):
    etag = _handle(_event({"url": CANONICAL}))["headers"]["ETag"]

    response = _handle(_event({"url": CANONICAL}, {header: etag}))

    assert response["statusCode"] == 304
    assert response["body"] == ""
    emit.assert_not_called()


def test_both_selectors_is_invalid_read_selector(cognito, emit):
    response = _handle(_event({"url": CANONICAL, "content_id": "cnt_a1b2c3d4e5f6"}))

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error_code"] == "invalid_read_selector"
    emit.assert_not_called()


def test_unpublished_content_id_is_404(cognito, emit, table):
    table.get_item.return_value = {"Item": _record(status="processing")}

    response = _handle(_event({"content_id": "cnt_a1b2c3d4e5f6"}))

    assert response["statusCode"] == 404
    emit.assert_not_called()


def test_no_token_is_401_before_any_read(emit, table):
    with patch("services.content_service.get_user_from_token", return_value=None):
        response = _handle(_event({"url": CANONICAL}))

    assert response["statusCode"] == 401
    table.get_item.assert_not_called()
    emit.assert_not_called()


# --------------------------------------------------------------------------- #
# The browser can read the response and the validator
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "query,status",
    [
        ({"content_id": "cnt_a1b2c3d4e5f6"}, 200),
        ({"content_id": "cnt_unknown000000"}, 404),
        ({"url": CANONICAL, "content_id": "cnt_a1b2c3d4e5f6"}, 400),
    ],
)
def test_internal_read_carries_cors_and_exposes_the_etag(query, status, cognito, emit, table):
    if status == 404:
        table.get_item.return_value = {}

    response = _handle(_event(query))

    assert response["statusCode"] == status
    assert response["headers"]["Access-Control-Allow-Origin"] == PORTAL
    assert response["headers"]["Access-Control-Expose-Headers"] == "ETag"


def test_internal_304_carries_cors(cognito, emit, table):
    etag = _handle(_event({"content_id": "cnt_a1b2c3d4e5f6"}))["headers"]["ETag"]

    response = _handle(_event({"content_id": "cnt_a1b2c3d4e5f6"}, {"If-None-Match": etag}))

    assert response["statusCode"] == 304
    assert response["headers"]["Access-Control-Allow-Origin"] == PORTAL
    assert response["headers"]["ETag"] == etag


def test_the_partner_read_response_is_unchanged(emit, table):
    response = handle_content_read(PARTNER_USER, {"content_id": "cnt_a1b2c3d4e5f6"})

    assert "Access-Control-Expose-Headers" not in response["headers"]
    assert "Access-Control-Allow-Origin" not in response["headers"]
