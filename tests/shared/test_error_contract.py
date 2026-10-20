"""Shared error producers, OpenAPI, and runtime validation agree."""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from services.content_service import create_error_response as create_legacy_error_response
from shared.utils.response_helpers import create_error_response
from shared.utils.response_validator import APIResponseValidator


@pytest.fixture(scope="module")
def error_schema():
    spec = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi-spec.yaml").read_text())
    return spec["components"]["schemas"]["Error"]


@pytest.mark.parametrize("producer", [create_error_response, create_legacy_error_response])
def test_actual_error_producers_match_schema_and_validator(error_schema, producer):
    response = producer(500, "Internal error")
    body = json.loads(response["body"])
    assert error_schema["required"] == ["error"]
    assert error_schema["properties"]["message"]["type"] == "string"
    jsonschema.validate(body, error_schema)
    validator = APIResponseValidator()
    validator.validation_enabled = True
    result = validator.validate_response(response, "/query", "POST", 500)
    assert result["valid"], result["errors"]


@pytest.mark.parametrize(
    "body,valid",
    [
        ({"error": "Unavailable"}, True),
        ({"error": "unavailable", "message": "Try later"}, True),
        ({"message": "Missing error"}, False),
        ({"error": 42}, False),
        ({"error": "unavailable", "message": 42}, False),
    ],
)
def test_error_field_requirements_match_runtime(error_schema, body, valid):
    response = {"statusCode": 500, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}
    validator = APIResponseValidator()
    validator.validation_enabled = True
    assert validator.validate_response(response, "/query", "POST", 500)["valid"] is valid
    assert jsonschema.Draft4Validator(error_schema).is_valid(body) is valid
