"""Unit tests for where a single-document read gets its Markdown body.

The GEO pipeline stores a document's body only in S3, at the record's ``md_s3_key``; records
written directly carry ``content_text`` and no key. Every read (``GET /content?url=``,
``GET /content?content_id=`` and the internal ``GET /content/{content_id}``) must therefore serve
the stored object when the record names one. These tests pin that, and the properties the change
must not break:

- the ``ETag`` identifies the body actually served, including when the pipeline has replaced the
  object but not yet updated the record
- a ``304`` downloads no body and is never billed
- a partner read whose body can't be returned (missing, unreadable, too large) is a sanitized
  ``500``, never billed and never an empty ``200``
- the internal details view keeps the record's metadata when the body can't be read

The table and S3 are stubbed; the live round trip is the integration tier's job.
"""

import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services import content_service  # noqa: E402
from services.content_service import (  # noqa: E402
    handle_content_read,
    handle_get_content,
    handle_get_content_by_url,
)

USER = {"partner_id": "partner_test_001"}
COGNITO_USER = {"user_id": "user-1", "username": "publisher"}
CANONICAL = "https://news.example.com/articles/one"
MD_KEY = "content/news.example.com/articles/one.md"
STORED = "# Stored title\n\nThe body the pipeline rendered.\n"


class _TrackedBody(io.BytesIO):
    """A ``GetObject`` body stream that records whether it was downloaded."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.was_read = False

    def read(self, *args, **kwargs):
        self.was_read = True
        return super().read(*args, **kwargs)


def _stored_object(text: str = STORED, etag: str = '"etag-v1"', data: bytes = None):
    raw = text.encode("utf-8") if data is None else data
    return {"Body": _TrackedBody(raw), "ETag": etag, "ContentLength": len(raw)}


def _pipeline_record(**overrides):
    """A record shaped like the pipeline's ``ContentMetadata``: ``md_s3_key``, no ``content_text``."""
    record = {
        "content_id": "cnt_a1b2c3d4e5f6",
        "canonical_url": CANONICAL,
        "md_s3_key": MD_KEY,
        "status": "published",
        "title": "Stored title",
        "author": "Author",
        "publish_date": "2026-01-01T00:00:00Z",  # the pipeline's field name
        "updated_at": "2026-02-02T00:00:00Z",
        "word_count": 6,
        "language": "en",
        "content_type": "article",
    }
    record.update(overrides)
    return record


def _direct_record():
    record = _pipeline_record(content_text="# Direct\n\nWritten without the pipeline.")
    del record["md_s3_key"]
    record["publication_date"] = record.pop("publish_date")  # direct writes use this name
    return record


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": f"sensitive {code} detail"}}, "GetObject")


@pytest.fixture
def emit():
    with patch("services.content_service.emit_usage_event") as mocked:
        yield mocked


@pytest.fixture
def storage(monkeypatch):
    """S3 stub. Each call returns a fresh object; ``storage.opened`` lists every object handed out."""
    fake = MagicMock()
    fake.opened = []

    def get_object(**_):
        obj = fake.next_object() if callable(getattr(fake, "next_object", None)) else _stored_object()
        fake.opened.append(obj)
        return obj

    fake.next_object = None
    fake.get_object.side_effect = get_object
    monkeypatch.setattr(content_service, "s3", fake)
    return fake


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


def _read(mode, record, table, url_lookup, **extra):
    """Run one partner read in either mode against a stubbed record."""
    if mode == "url":
        url_lookup.return_value = [record]
        return handle_get_content_by_url(USER, {"url": CANONICAL, **extra})
    table.get_item.return_value = {"Item": record}
    return handle_content_read(USER, {"content_id": record["content_id"], **extra})


MODES = ["url", "content_id"]


# --------------------------------------------------------------------------- #
# The stored object is the body
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", MODES)
def test_pipeline_record_reads_back_with_the_stored_markdown(mode, table, url_lookup, storage, emit):
    response = _read(mode, _pipeline_record(), table, url_lookup)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["markdown"] == STORED
    storage.get_object.assert_called_once_with(Bucket=content_service.content_bucket, Key=MD_KEY)
    emit.assert_called_once()


@pytest.mark.parametrize("mode", MODES)
def test_md_s3_key_wins_over_content_text(mode, table, url_lookup, storage, emit):
    """The API serves the same bytes native edge delivery serves for the document."""
    response = _read(mode, _pipeline_record(content_text="stale copy"), table, url_lookup)

    assert json.loads(response["body"])["markdown"] == STORED


@pytest.mark.parametrize("mode", MODES)
def test_record_without_md_s3_key_keeps_serving_content_text(mode, table, url_lookup, storage, emit):
    response = _read(mode, _direct_record(), table, url_lookup)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["markdown"] == "# Direct\n\nWritten without the pipeline."
    storage.get_object.assert_not_called()


# --------------------------------------------------------------------------- #
# The ETag identifies the body actually served
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", MODES)
def test_etag_changes_when_the_stored_object_changes_before_the_record(mode, table, url_lookup, storage, emit):
    """The pipeline replaces the object before it updates ``updated_at``; the ETag must still move."""
    storage.next_object = lambda: _stored_object("# Old\n", etag='"etag-v1"')
    first = _read(mode, _pipeline_record(), table, url_lookup)
    storage.next_object = lambda: _stored_object("# New\n", etag='"etag-v2"')

    # Same record (same updated_at), new object: the old ETag must not earn a 304.
    revalidated = _read(mode, _pipeline_record(), table, url_lookup, **{"If-None-Match": first["headers"]["ETag"]})

    assert revalidated["statusCode"] == 200
    assert revalidated["headers"]["ETag"] != first["headers"]["ETag"]
    assert json.loads(revalidated["body"])["markdown"] == "# New\n"


@pytest.mark.parametrize("mode", MODES)
def test_etag_changes_when_the_record_changes(mode, table, url_lookup, storage, emit):
    first = _read(mode, _pipeline_record(), table, url_lookup)
    second = _read(mode, _pipeline_record(updated_at="2026-03-03T00:00:00Z"), table, url_lookup)

    assert first["headers"]["ETag"] != second["headers"]["ETag"]


# --------------------------------------------------------------------------- #
# A 304 stays free: no body download, no usage event
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", MODES)
def test_304_downloads_no_body_and_emits_nothing(mode, table, url_lookup, storage, emit):
    first = _read(mode, _pipeline_record(), table, url_lookup)
    emit.reset_mock()

    response = _read(mode, _pipeline_record(), table, url_lookup, **{"If-None-Match": first["headers"]["ETag"]})

    assert response["statusCode"] == 304
    conditional = storage.opened[-1]
    assert not conditional["Body"].was_read, "a 304 must answer from the object's headers alone"
    assert conditional["Body"].closed
    emit.assert_not_called()


@pytest.mark.parametrize("mode", MODES)
def test_304_for_a_record_without_md_s3_key_makes_no_storage_call(mode, table, url_lookup, storage, emit):
    first = _read(mode, _direct_record(), table, url_lookup)
    emit.reset_mock()

    response = _read(mode, _direct_record(), table, url_lookup, **{"If-None-Match": first["headers"]["ETag"]})

    assert response["statusCode"] == 304
    storage.get_object.assert_not_called()
    emit.assert_not_called()


# --------------------------------------------------------------------------- #
# The record's stored ETag answers a matching 304 with no S3 request
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", MODES)
def test_recorded_etag_answers_a_matching_304_with_no_storage_call(mode, table, url_lookup, storage, emit):
    record = _pipeline_record(md_etag='"etag-v1"')
    first = _read(mode, record, table, url_lookup)
    storage.get_object.reset_mock()
    emit.reset_mock()

    response = _read(mode, record, table, url_lookup, **{"If-None-Match": first["headers"]["ETag"]})

    assert response["statusCode"] == 304
    assert response["headers"]["ETag"] == first["headers"]["ETag"]
    storage.get_object.assert_not_called()
    emit.assert_not_called()


@pytest.mark.parametrize("mode", MODES)
def test_an_etag_from_the_s3_path_earns_the_fast_path_304(mode, table, url_lookup, storage, emit):
    """The two derivations agree, so recording the ETag changes no client's cache key.

    The ETag is issued by a record saved before ``md_etag`` existed (the S3 path); the same document,
    once its record carries ``md_etag``, must accept it on the fast path.
    """
    issued = _read(mode, _pipeline_record(), table, url_lookup)
    storage.get_object.reset_mock()

    response = _read(
        mode, _pipeline_record(md_etag='"etag-v1"'), table, url_lookup, **{"If-None-Match": issued["headers"]["ETag"]}
    )

    assert response["statusCode"] == 304
    assert response["headers"]["ETag"] == issued["headers"]["ETag"]
    storage.get_object.assert_not_called()


@pytest.mark.parametrize("mode", MODES)
def test_a_200_with_a_recorded_etag_still_makes_one_storage_call(mode, table, url_lookup, storage, emit):
    _read(mode, _pipeline_record(md_etag='"etag-v1"'), table, url_lookup)

    assert storage.get_object.call_count == 1
    emit.assert_called_once()


@pytest.mark.parametrize("mode", MODES)
def test_a_stale_conditional_request_falls_through_to_the_live_object(mode, table, url_lookup, storage, emit):
    """An If-None-Match that doesn't match the recorded ETag is decided on the object itself."""
    record = _pipeline_record(md_etag='"etag-v1"')

    response = _read(mode, record, table, url_lookup, **{"If-None-Match": '"some-older-etag"'})

    assert response["statusCode"] == 200
    assert storage.get_object.call_count == 1


@pytest.mark.parametrize("mode", MODES)
def test_a_record_with_md_etag_is_trusted_over_the_object(mode, table, url_lookup, storage, emit):
    """The accepted window: the record answers for the object. During a re-ingest (object replaced,
    record not yet saved) and after a direct S3 edit, a matching conditional read gets 304."""
    record = _pipeline_record(md_etag='"etag-v1"')
    first = _read(mode, record, table, url_lookup)
    storage.next_object = lambda: _stored_object("# Replaced\n", etag='"etag-v2"')
    storage.get_object.reset_mock()

    response = _read(mode, record, table, url_lookup, **{"If-None-Match": first["headers"]["ETag"]})

    assert response["statusCode"] == 304
    storage.get_object.assert_not_called()


@pytest.mark.parametrize("mode", MODES)
def test_md_etag_without_md_s3_key_is_ignored(mode, table, url_lookup, storage, emit):
    """A record with no stored object is identified by the record alone; a stray md_etag earns nothing."""
    record = _direct_record()
    record["md_etag"] = '"stray"'
    # The ETag that would match only if the stray md_etag were (wrongly) folded in.
    forged = content_service._compute_etag(record, {"ETag": '"stray"'})

    response = _read(mode, record, table, url_lookup, **{"If-None-Match": forged})

    assert response["statusCode"] == 200
    assert response["headers"]["ETag"].strip('"') != forged
    storage.get_object.assert_not_called()


# --------------------------------------------------------------------------- #
# A body that can't be returned is a sanitized 500, never billed, never an empty 200
# --------------------------------------------------------------------------- #
OPEN_FAILURES = {
    "missing-object": _client_error("NoSuchKey"),
    "access-denied": _client_error("AccessDenied"),
    "transport": RuntimeError("sensitive transport detail"),
}


def _assert_sanitized_unbilled_500(response, emit):
    assert response["statusCode"] == 500
    assert json.loads(response["body"])["message"] == "Failed to retrieve content"
    for leaked in (MD_KEY, content_service.content_bucket, "sensitive"):
        assert leaked not in response["body"]
    emit.assert_not_called()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("failure", list(OPEN_FAILURES), ids=list(OPEN_FAILURES))
def test_unopenable_object_is_a_sanitized_unbilled_500(mode, failure, table, url_lookup, storage, emit):
    storage.get_object.side_effect = OPEN_FAILURES[failure]

    _assert_sanitized_unbilled_500(_read(mode, _pipeline_record(), table, url_lookup), emit)


@pytest.mark.parametrize("mode", MODES)
def test_failed_download_is_a_sanitized_unbilled_500(mode, table, url_lookup, storage, emit):
    broken = _stored_object()
    broken["Body"].read = MagicMock(side_effect=RuntimeError("sensitive mid-stream failure"))
    storage.next_object = lambda: broken

    _assert_sanitized_unbilled_500(_read(mode, _pipeline_record(), table, url_lookup), emit)


@pytest.mark.parametrize("mode", MODES)
def test_non_utf8_object_is_an_unbilled_500(mode, table, url_lookup, storage, emit):
    storage.next_object = lambda: _stored_object(data=b"\xff\xfe not utf-8")

    _assert_sanitized_unbilled_500(_read(mode, _pipeline_record(), table, url_lookup), emit)


@pytest.mark.parametrize("mode", MODES)
def test_object_too_large_to_return_is_refused_before_download(mode, table, url_lookup, storage, emit):
    oversized = _stored_object()
    oversized["ContentLength"] = content_service._MAX_READ_RESPONSE_BYTES + 1
    storage.next_object = lambda: oversized

    _assert_sanitized_unbilled_500(_read(mode, _pipeline_record(), table, url_lookup), emit)
    assert not oversized["Body"].was_read


@pytest.mark.parametrize("mode", MODES)
def test_response_too_large_after_encoding_is_refused_before_billing(
    mode, table, url_lookup, storage, emit, monkeypatch
):
    """JSON escaping can push a body that fits on disk past the Lambda response limit."""
    body = "é" * 120  # 240 bytes stored; each escapes to six bytes in the JSON response
    monkeypatch.setattr(content_service, "_MAX_READ_RESPONSE_BYTES", 500)
    storage.next_object = lambda: _stored_object(body)

    _assert_sanitized_unbilled_500(_read(mode, _pipeline_record(), table, url_lookup), emit)


@pytest.mark.parametrize("mode", MODES)
def test_response_too_large_once_lambda_encodes_it_is_refused_before_billing(
    mode, table, url_lookup, storage, emit, monkeypatch
):
    """The runtime escapes the encoded body again, so the body alone can fit while the response doesn't."""
    body = '"quoted" \\ text\n' * 40
    storage.next_object = lambda: _stored_object(body)
    served = _read(mode, _pipeline_record(), table, url_lookup)
    body_bytes = len(served["body"].encode("utf-8"))
    response_bytes = len(json.dumps(served).encode("utf-8"))
    assert body_bytes < response_bytes, "precondition: the second escape inflates the response"

    monkeypatch.setattr(content_service, "_MAX_READ_RESPONSE_BYTES", (body_bytes + response_bytes) // 2)
    emit.reset_mock()

    _assert_sanitized_unbilled_500(_read(mode, _pipeline_record(), table, url_lookup), emit)


# --------------------------------------------------------------------------- #
# Metadata: the publication date under either field name
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", MODES)
def test_pubdate_comes_from_the_pipelines_publish_date(mode, table, url_lookup, storage, emit):
    response = _read(mode, _pipeline_record(), table, url_lookup)

    assert json.loads(response["body"])["metadata"]["pubdate"] == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize("mode", MODES)
def test_pubdate_still_reads_publication_date_on_direct_records(mode, table, url_lookup, storage, emit):
    response = _read(mode, _direct_record(), table, url_lookup)

    assert json.loads(response["body"])["metadata"]["pubdate"] == "2026-01-01T00:00:00Z"


@pytest.mark.parametrize("mode", MODES)
def test_publisher_and_modified_date_come_from_the_record(mode, table, url_lookup, storage, emit):
    record = _pipeline_record(publisher="Example News", modified_date="2026-01-03T09:00:00Z")
    metadata = json.loads(_read(mode, record, table, url_lookup)["body"])["metadata"]

    assert metadata["publisher"] == "Example News"
    assert metadata["modified_date"] == "2026-01-03T09:00:00Z"
    # update is when the record was last written, not the article's own update.
    assert metadata["update"] == "2026-02-02T00:00:00Z"


@pytest.mark.parametrize("mode", MODES)
def test_publisher_and_modified_date_are_empty_on_older_records(mode, table, url_lookup, storage, emit):
    metadata = json.loads(_read(mode, _pipeline_record(), table, url_lookup)["body"])["metadata"]

    assert (metadata["publisher"], metadata["modified_date"]) == ("", "")


# --------------------------------------------------------------------------- #
# The internal details read: the body when it can be read, the metadata always
# --------------------------------------------------------------------------- #
def test_internal_details_read_serves_the_stored_markdown(table, storage):
    table.get_item.return_value = {"Item": _pipeline_record()}

    response = handle_get_content(COGNITO_USER, "cnt_a1b2c3d4e5f6")

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    details = body.get("data", body)
    assert details["content_text"] == STORED
    storage.get_object.assert_called_once_with(Bucket=content_service.content_bucket, Key=MD_KEY)


@pytest.mark.parametrize("failure", list(OPEN_FAILURES), ids=list(OPEN_FAILURES))
def test_internal_details_read_keeps_the_metadata_when_the_body_is_unreadable(failure, table, storage):
    """The details view is how a publisher diagnoses a broken record, so it must still answer."""
    table.get_item.return_value = {"Item": _pipeline_record()}
    storage.get_object.side_effect = OPEN_FAILURES[failure]

    response = handle_get_content(COGNITO_USER, "cnt_a1b2c3d4e5f6")

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    details = body.get("data", body)
    assert details["content_text"] == ""
    assert details["status"] == "published"
    assert details["md_s3_key"] == MD_KEY
    assert "sensitive" not in response["body"]


def test_internal_details_read_reports_the_pipelines_publish_date(table, storage):
    table.get_item.return_value = {"Item": _pipeline_record()}

    body = json.loads(handle_get_content(COGNITO_USER, "cnt_a1b2c3d4e5f6")["body"])

    assert body.get("data", body)["publication_date"] == "2026-01-01T00:00:00Z"


def test_internal_details_read_reports_publisher_and_modified_date(table, storage):
    table.get_item.return_value = {"Item": _pipeline_record(publisher="Example News", modified_date="2026-01-03")}

    body = json.loads(handle_get_content(COGNITO_USER, "cnt_a1b2c3d4e5f6")["body"])

    details = body.get("data", body)
    assert (details["publisher"], details["modified_date"]) == ("Example News", "2026-01-03")


def test_internal_details_read_too_large_to_return_keeps_the_metadata(table, storage, monkeypatch):
    """A body too large for the Lambda response degrades like an unreadable one: details, no body."""
    table.get_item.return_value = {"Item": _pipeline_record()}
    # 12 KB stored, about 42 KB once encoded twice: it passes the size check on the stored object
    # and fails only on the response.
    storage.next_object = lambda: _stored_object("é" * 6_000)
    monkeypatch.setattr(content_service, "_MAX_READ_RESPONSE_BYTES", 20_000)

    response = handle_get_content(COGNITO_USER, "cnt_a1b2c3d4e5f6")

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    details = body.get("data", body)
    assert details["content_text"] == ""
    assert details["status"] == "published"
    assert details["md_s3_key"] == MD_KEY
