"""Unit tests for URL-addressable content retrieval (#14).

Pins the response SHAPES and the control flow of ``handle_get_content_by_url`` at the unit tier:
the 200 envelope, the ETag, the ``304`` early return and its billing exemption, the RFC 9457
``404 + redirectUrl``, the REQ-1.5 trailing-slash retry, and the published-only rule. The live GSI round-trip, the real
ETag exchange over HTTP, and the proof that no billable row reaches ``cm-usage-events`` are the
integration tier's job (``tests/integration/test_url_retrieval_api.py``) — this file deliberately
stubs the table so the control flow can be asserted without AWS.

The billing exemption is asserted here as CONTROL FLOW: the ``304`` must not call
``emit_usage_event`` at all. That is the guarantee the implementation comment claims (the early
return sits above the emit), and it is the half of REQ-2.3 a unit test can establish.
"""

import json
import os
import sys
from unittest.mock import patch

import pytest

os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.content_service import (  # noqa: E402
    _trailing_slash_variant,
    handle_get_content_by_url,
)

CANONICAL = "https://news.example.com/articles/one"
USER = {"partner_id": "partner_test_001"}


def _record(canonical_url=CANONICAL, content_id="cnt_url_001", status="published"):
    return {
        "content_id": content_id,
        "status": status,
        "canonical_url": canonical_url,
        "title": "Probe",
        "content_text": "# Probe\n\nBody.",
        "author": "Author",
        "publication_date": "2026-01-01T00:00:00Z",
        "updated_at": "2026-02-02T00:00:00Z",
        "word_count": 2,
        "language": "en",
        "content_type": "article",
    }


@pytest.fixture
def emit():
    with patch("services.content_service.emit_usage_event") as mocked:
        yield mocked


@pytest.fixture
def lookup():
    """Stub the GSI query, keyed by url_hash, so each test declares which hashes resolve."""
    with patch("services.content_service._query_content_by_url_hash") as mocked:
        mocked.return_value = []
        yield mocked


def _hash_of(url):
    from shared.utils.edge_address import edge_address_from_canonical_url

    return edge_address_from_canonical_url(url).url_hash


# --------------------------------------------------------------------------- #
# REQ-1.2 / REQ-2.1 — the 200 envelope
# --------------------------------------------------------------------------- #
def test_resolved_url_returns_200_with_markdown_and_quoted_etag(lookup, emit):
    lookup.return_value = [_record()]

    response = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert response["statusCode"] == 200
    etag = response["headers"]["ETag"]
    assert etag.startswith('"') and etag.endswith('"'), f"ETag must be quoted, got {etag}"
    body = json.loads(response["body"])
    assert body["markdown"] == "# Probe\n\nBody."
    assert body["canonical_url"] == CANONICAL
    assert body["metadata"]["sourcetype"] in (0, 1, 2)
    emit.assert_called_once()


def test_missing_url_parameter_is_400(lookup, emit):
    response = handle_get_content_by_url(USER, {})

    assert response["statusCode"] == 400
    lookup.assert_not_called()
    emit.assert_not_called()


# --------------------------------------------------------------------------- #
# REQ-2.2 / REQ-2.3 — the conditional request and its billing exemption
# --------------------------------------------------------------------------- #
def test_matching_if_none_match_is_304_and_emits_nothing(lookup, emit):
    """The 304 must not reach the emit at all — the exemption is the early return, not a flag.

    Asserting ``emit`` was never CALLED (rather than that the sink received nothing) is what pins
    the control flow: if the early return were moved below the emit, this fails even though the
    response body would still be empty.
    """
    lookup.return_value = [_record()]
    first = handle_get_content_by_url(USER, {"url": CANONICAL})
    etag = first["headers"]["ETag"].strip('"')
    emit.reset_mock()

    response = handle_get_content_by_url(USER, {"url": CANONICAL, "If-None-Match": f'"{etag}"'})

    assert response["statusCode"] == 304
    assert response["body"] == ""
    emit.assert_not_called()


def test_stale_if_none_match_is_200_and_is_billable(lookup, emit):
    lookup.return_value = [_record()]

    response = handle_get_content_by_url(
        USER, {"url": CANONICAL, "If-None-Match": '"not-the-current-etag"'}
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["markdown"]
    emit.assert_called_once()


# --------------------------------------------------------------------------- #
# REQ-1.3 — error shapes
# --------------------------------------------------------------------------- #
def test_unknown_url_is_404_and_emits_nothing(lookup, emit):
    lookup.return_value = []

    with patch("services.content_service._check_for_redirect", return_value=None):
        response = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["error_code"] == "content_not_found"
    # The existing body fields stay, so clients that read them are unaffected.
    assert body["error"] == "not_found"
    assert body["message"] == "Content not found for the specified URL"
    emit.assert_not_called()


@pytest.mark.parametrize("status", ["processing", "failed", "active", None])
def test_unpublished_record_is_404_like_an_unknown_url(status, lookup, emit):
    """URL mode serves only published content, like discovery and the ``content_id`` read.

    An unpublished record must get exactly the unknown-URL ``404``, so the response never reveals
    that a record exists at that URL. Nothing is billed.
    """
    record = _record(status=status)
    if status is None:
        del record["status"]

    with patch("services.content_service._check_for_redirect", return_value=None):
        lookup.return_value = [record]
        response = handle_get_content_by_url(USER, {"url": CANONICAL})
        lookup.return_value = []
        unknown = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert response["statusCode"] == 404
    body, unknown_body = json.loads(response["body"]), json.loads(unknown["body"])
    # The bodies differ only in timestamp and request_id.
    fields = ("error", "message", "error_code")
    assert tuple(map(body.get, fields)) == tuple(map(unknown_body.get, fields))
    assert body["message"] == "Content not found for the specified URL"
    emit.assert_not_called()


def test_published_record_is_served_when_an_unpublished_one_shares_the_url(lookup, emit):
    lookup.return_value = [
        _record(content_id="cnt_url_draft", status="processing"),
        _record(content_id="cnt_url_live"),
    ]

    response = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["content_id"] == "cnt_url_live"
    emit.assert_called_once()


def test_moved_content_is_404_problem_json_carrying_redirect_url(lookup, emit):
    lookup.return_value = []
    moved_to = "https://news.example.com/articles/one-renamed"

    with patch("services.content_service._check_for_redirect", return_value=moved_to):
        response = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert response["statusCode"] == 404
    assert response["headers"]["Content-Type"] == "application/problem+json"
    problem = json.loads(response["body"])
    assert problem["redirectUrl"] == moved_to
    assert problem["status"] == 404
    emit.assert_not_called()


# --------------------------------------------------------------------------- #
# REQ-1.5 — trailing-slash citation tolerance, on the read path
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "stored,cited",
    [
        (CANONICAL, f"{CANONICAL}/"),
        (f"{CANONICAL}/", CANONICAL),
    ],
)
def test_trailing_slash_spelling_resolves_to_the_stored_document(stored, cited, lookup, emit):
    """A citation whose trailing slash differs from the stored URL still resolves (REQ-1.5).

    The slash is NOT normalized away — it is load-bearing in the edge address, where ``/a/b/``
    maps to ``.../a/b/index.md`` and ``/a/b`` to ``.../a/b.md``. The tolerance therefore lives on
    the read path: the exact spelling is tried first, and only on a miss is the slash-toggled
    spelling tried.
    """
    stored_hash = _hash_of(stored)
    lookup.side_effect = lambda url_hash: [_record(canonical_url=stored)] if url_hash == stored_hash else []

    response = handle_get_content_by_url(USER, {"url": cited})

    assert response["statusCode"] == 200, response
    assert json.loads(response["body"])["canonical_url"] == stored
    assert lookup.call_count == 2, "the exact spelling must be tried before the slash-toggled one"


def test_exact_match_does_not_cost_a_second_query(lookup, emit):
    """The fallback runs only on a miss — a hit must not pay for an extra GSI query."""
    lookup.return_value = [_record()]

    handle_get_content_by_url(USER, {"url": CANONICAL})

    assert lookup.call_count == 1


def test_unknown_url_tries_both_spellings_before_404(lookup, emit):
    lookup.return_value = []

    with patch("services.content_service._check_for_redirect", return_value=None):
        response = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert response["statusCode"] == 404
    assert lookup.call_count == 2


def test_unpublished_exact_spelling_falls_back_to_the_published_toggled_one(lookup, emit):
    """An unpublished record at the cited spelling counts as a miss, so the toggled spelling is tried."""
    toggled = f"{CANONICAL}/"
    exact_hash, toggled_hash = _hash_of(CANONICAL), _hash_of(toggled)
    by_hash = {
        exact_hash: [_record(content_id="cnt_url_draft", status="processing")],
        toggled_hash: [_record(canonical_url=toggled, content_id="cnt_url_live")],
    }
    lookup.side_effect = lambda url_hash: by_hash.get(url_hash, [])

    response = handle_get_content_by_url(USER, {"url": CANONICAL})

    assert response["statusCode"] == 200, response
    assert json.loads(response["body"])["content_id"] == "cnt_url_live"
    assert [call.args[0] for call in lookup.call_args_list] == [exact_hash, toggled_hash]
    emit.assert_called_once()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://h.example.com/a", "https://h.example.com/a/"),
        ("https://h.example.com/a/", "https://h.example.com/a"),
        ("https://h.example.com/a/b/", "https://h.example.com/a/b"),
        # A root URL has no slash-free spelling: "https://h.example.com" normalizes straight back
        # to "https://h.example.com/", so toggling would re-query the same hash.
        ("https://h.example.com/", None),
        ("https://h.example.com", None),
        # The query is dropped, matching the normalizer, so the retry cannot smuggle one through.
        ("https://h.example.com/a?utm=x", "https://h.example.com/a/"),
    ],
)
def test_trailing_slash_variant_toggles_only_the_path(url, expected):
    assert _trailing_slash_variant(url) == expected
