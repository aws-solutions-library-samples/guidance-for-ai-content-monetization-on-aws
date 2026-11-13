"""Canonical discovery preparation remains additive to the live route surface."""

from pathlib import Path

import pytest
import yaml
from jsonschema import Draft4Validator


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi-spec.yaml").read_text())


def _validator(spec, name):
    return Draft4Validator({"$ref": f"#/components/schemas/{name}", "components": spec["components"]})


@pytest.mark.parametrize("alias,value", [("content_types", ["article"]), ("limit", 10)])
def test_standard_schema_rejects_retired_fields(spec, alias, value):
    request = {"query": "health", "content_type": ["article"], "max_results": 10, alias: value}
    assert not _validator(spec, "StandardQueryRequest").is_valid(request)


@pytest.mark.parametrize("threshold", [0, 0.7, 1])
@pytest.mark.parametrize("name", ["StandardQueryRequest", "CompDiscoveryFilters"])
def test_threshold_schema_accepts_bounds_with_text(spec, threshold, name):
    _validator(spec, name).validate({"query": "health", "similarity_threshold": threshold})


@pytest.mark.parametrize("threshold", [None, True, "0.7", -0.1, 1.1])
@pytest.mark.parametrize("name", ["StandardQueryRequest", "CompDiscoveryFilters"])
def test_threshold_schema_rejects_invalid_values(spec, threshold, name):
    assert not _validator(spec, name).is_valid({"query": "health", "similarity_threshold": threshold})


def test_comp_threshold_requires_query_and_other_extensions_remain_allowed(spec):
    validator = _validator(spec, "CompDiscoveryFilters")
    assert not validator.is_valid({"similarity_threshold": 0})
    validator.validate({"topics": ["health"]})
    validator.validate({"query": "health", "vendor_field": "preserved"})


def test_search_is_deleted_and_query_is_the_discovery_operation(spec):
    assert "/search" not in spec["paths"]
    assert "post" in spec["paths"]["/query"]
    assert "StandardSearchRequest" not in spec["components"]["schemas"]
    assert "StandardSearchResponse" not in spec["components"]["schemas"]


def test_renderer_revision_is_additive_for_legacy_management_records(spec):
    schemas = spec["components"]["schemas"]
    for name in ("LlmsTxtRun", "LlmsTxtDraft", "LlmsTxtPublishedManifest"):
        field = schemas[name]["properties"]["renderer_revision"]
        assert field["type"] == "string"
        assert field["nullable"] is True
        assert "renderer_revision" not in schemas[name].get("required", [])
