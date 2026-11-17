"""Unit tests for the single-document read entry point and its ``content_id`` mode.

``handle_content_read`` selects a read by exactly one of ``url`` or ``content_id``. The
``content_id`` mode serves through the same ``_serve_read`` as the URL mode, so these tests pin
parity with it (body, ``ETag``, ``304`` billing exemption, one usage event per ``200``), the
published-only predicate, the indistinguishable ``404`` and the ``400 invalid_read_selector``
cases, which must not touch the table. The table is stubbed; the live read is the integration
tier's job.
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
from services.content_service import handle_content_read, handle_get_content_by_url  # noqa: E402

USER = {"partner_id": "partner_test_001"}
CANONICAL = "https://news.example.com/articles/one"


def _record(**overrides):
    record = {
        "content_id": "cnt_a1b2c3d4e5f6",
        "canonical_url": CANONICAL,
        "status": "published",
        "title": "Probe",
        "content_text": "# Probe\n\nBody.",
        "author": "Author",
        "publication_date": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-02T00:00:00Z",
        "word_count": 2,
        "language": "en",
        "content_type": "article",
    }
    record.update(overrides)
    return record


@pytest.fixture
def emit():
    with patch("services.content_service.emit_usage_event") as mocked:
        yield mocked


@pytest.fixture
def table(monkeypatch):
    fake = MagicMock()
    fake.get_item.return_value = {}
    monkeypatch.setattr(content_service, "content_table", fake)
    return fake


@pytest.fixture
def url_lookup():
    with patch("services.content_service._query_content_by_url_hash") as mocked:
        mocked.return_value = []
        yield mocked


# --------------------------------------------------------------------------- #
# Selector validation: exactly one of url / content_id, before any table call
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"url": CANONICAL, "content_id": "cnt_a1b2c3d4e5f6"},
        {"content_id": ""},
        {"content_id": "x" * 129},
        {"content_id": "../x"},
        {"content_id": "cnt 1"},
    ],
    ids=["neither", "both", "empty", "too-long", "path-traversal", "space"],
)
def test_invalid_selector_is_400_without_a_table_call(params, table, url_lookup, emit):
    response = handle_content_read(USER, params)

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error_code"] == "invalid_read_selector"
    table.get_item.assert_not_called()
    url_lookup.assert_not_called()
    emit.assert_not_called()


def test_url_selector_delegates_to_the_unchanged_url_read(table, url_lookup, emit):
    url_lookup.return_value = [_record()]

    response = handle_content_read(USER, {"url": CANONICAL})

    assert response["statusCode"] == 200
    url_lookup.assert_called_once()
    table.get_item.assert_not_called()


# --------------------------------------------------------------------------- #
# content_id mode: parity with the URL read
# --------------------------------------------------------------------------- #
def test_content_id_read_returns_the_same_body_and_etag_as_the_url_read(table, url_lookup, emit):
    record = _record()
    table.get_item.return_value = {"Item": record}
    url_lookup.return_value = [record]

    by_id = handle_content_read(USER, {"content_id": record["content_id"]})
    by_url = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert by_id["statusCode"] == by_url["statusCode"] == 200
    assert json.loads(by_id["body"]) == json.loads(by_url["body"])
    assert by_id["headers"]["ETag"] == by_url["headers"]["ETag"]
    assert by_id["headers"]["Cache-Control"] == "private, must-revalidate"
    table.get_item.assert_called_once_with(Key={"content_id": record["content_id"]})


def test_content_id_read_of_canonical_less_content_returns_200(table, emit):
    table.get_item.return_value = {"Item": _record(canonical_url=None)}

    response = handle_content_read(USER, {"content_id": "cnt_a1b2c3d4e5f6"})

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["content_id"] == "cnt_a1b2c3d4e5f6"


def test_content_id_200_emits_exactly_one_usage_event(table, emit):
    table.get_item.return_value = {"Item": _record()}

    handle_content_read(USER, {"content_id": "cnt_a1b2c3d4e5f6"})

    emit.assert_called_once()
    kwargs = emit.call_args.kwargs
    assert kwargs["partner_id"] == USER["partner_id"]
    assert kwargs["content_id"] == "cnt_a1b2c3d4e5f6"
    assert kwargs["content_type"] == content_service.USAGE_CONTENT_TYPE_URL_RETRIEVAL


def test_content_id_304_emits_no_usage_event(table, emit):
    table.get_item.return_value = {"Item": _record()}
    first = handle_content_read(USER, {"content_id": "cnt_a1b2c3d4e5f6"})
    emit.reset_mock()

    response = handle_content_read(USER, {"content_id": "cnt_a1b2c3d4e5f6", "if_none_match": first["headers"]["ETag"]})

    assert response["statusCode"] == 304
    assert response["body"] == ""
    emit.assert_not_called()


# --------------------------------------------------------------------------- #
# Published-only, with one indistinguishable 404
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "item",
    [None, _record(status="processing"), _record(status="failed"), _record(status="active"), _record(status=None)],
    ids=["absent", "processing", "failed", "active", "no-status"],
)
def test_unknown_and_unpublished_records_return_the_same_404(item, table, emit):
    table.get_item.return_value = {"Item": item} if item else {}

    response = handle_content_read(USER, {"content_id": "cnt_a1b2c3d4e5f6"})

    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    # The URL read's 404 shape, with one message for every not-readable case.
    assert body["error"] == "not_found"
    assert body["message"] == "Content not found for the specified content_id"
    assert body["error_code"] == "content_not_found"
    emit.assert_not_called()


def test_table_failure_is_a_sanitized_500(table, emit):
    table.get_item.side_effect = RuntimeError("sensitive-backend-detail")

    response = handle_content_read(USER, {"content_id": "cnt_a1b2c3d4e5f6"})

    assert response["statusCode"] == 500
    assert "sensitive-backend-detail" not in response["body"]
    emit.assert_not_called()
