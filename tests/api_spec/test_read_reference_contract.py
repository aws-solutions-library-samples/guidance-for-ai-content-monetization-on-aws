"""Contract tests for the ``/query`` read references and the single-document ``GET /content`` read.

The contract (``openapi-spec.yaml``) is the source of truth: every standard result requires
``content_id`` and ``url``, CoMP Text and Retrieval carry typed publisher extensions, ``GET
/content`` declares the ``content_id`` selector with the same pattern the handler enforces, and
the handler's ``invalid_read_selector`` is a registered partner-plane error code.
"""

import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("CONTENT_BUCKET_NAME", "test-content-bucket")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi-spec.yaml").read_text())
SCHEMAS = SPEC["components"]["schemas"]


def test_every_standard_result_requires_identity_and_a_read_reference():
    items = SCHEMAS["StandardQueryResponse"]["properties"]["results"]["items"]
    assert items == {"$ref": "#/components/schemas/StandardQueryResult"}
    assert set(SCHEMAS["StandardQueryResult"]["required"]) >= {"content_id", "url"}


def test_comp_text_and_retrieval_extensions_are_typed():
    assert SCHEMAS["CompTextAsset"]["properties"]["ext"] == {"$ref": "#/components/schemas/CompTextExt"}
    assert set(SCHEMAS["CompTextExt"]["required"]) == {"content_id", "url"}
    assert SCHEMAS["CompRetrieval"]["properties"]["ext"] == {"$ref": "#/components/schemas/CompRetrievalExt"}
    assert "read_by_id" in SCHEMAS["CompRetrievalExt"]["required"]
    assert "/query" not in SCHEMAS["CompRetrieval"]["properties"]["endpoint"]["description"]


def test_get_content_declares_the_content_id_selector_with_the_handler_pattern():
    from services.content_service import _CONTENT_ID_PATTERN

    operation = SPEC["paths"]["/content"]["get"]
    params = {param["name"]: param for param in operation["parameters"]}
    assert params["content_id"]["in"] == "query"
    assert params["content_id"]["schema"]["pattern"] == _CONTENT_ID_PATTERN.pattern
    read_shapes = operation["responses"]["200"]["content"]["application/json"]["schema"]["oneOf"]
    assert {"$ref": "#/components/schemas/ContentReadResponse"} in read_shapes


def test_invalid_read_selector_is_a_registered_handler_error_code():
    codes = {entry["error_code"]: entry for entry in SPEC["x-error-codes"]["codes"]}
    assert codes["invalid_read_selector"]["status"] == "400"
    assert codes["invalid_read_selector"]["source"] == "handler"


def test_content_not_found_is_a_registered_handler_error_code():
    codes = {entry["error_code"]: entry for entry in SPEC["x-error-codes"]["codes"]}
    assert codes["content_not_found"]["status"] == "404"
    assert codes["content_not_found"]["source"] == "handler"


def test_content_read_response_matches_the_handler_body_keys():
    from unittest.mock import patch

    from services.content_service import _serve_read

    item = {"content_id": "cnt_1", "content_text": "# A", "updated_at": "2026-01-01T00:00:00Z"}
    with patch("services.content_service.emit_usage_event"):
        body = __import__("json").loads(_serve_read(item, {}, {"partner_id": "p"})["body"])
    schema = SCHEMAS["ContentReadResponse"]
    assert set(schema["required"]) <= set(body)
    assert set(body) <= set(schema["properties"])
