"""Issue #38 owned configuration and synthetic traffic, without AWS calls."""

import ast
import importlib.util
import json
from pathlib import Path
import shlex
from unittest.mock import patch

from boto3.dynamodb.types import TypeDeserializer
from faker import Faker
import pytest

from mock_data_generator.config import GeneratorConfig
from mock_data_generator.generator import MockDataGenerator
from mock_data_generator.validation import SchemaValidator


ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AgentCoreGatewayConfig = load_module(
    "issue38_gateway_config", "source/agent/gateway_config.py"
).AgentCoreGatewayConfig


@pytest.fixture
def generator():
    with patch("boto3.Session"):
        yield MockDataGenerator(GeneratorConfig(access_log_count=8, date_range_months=1))


@pytest.fixture
def seed_module():
    return load_module("issue38_sample_partners", "deployment/data/add-sample-partners.py")


def assert_query_payload(payload):
    assert isinstance(payload["query"], str) and payload["query"].strip()
    assert isinstance(payload["content_type"], list)
    assert all(isinstance(value, str) for value in payload["content_type"])
    assert type(payload["max_results"]) is int
    assert 1 <= payload["max_results"] <= 50
    assert not {"limit", "content_types", "q", "offset", "type", "include_snippets"} & payload.keys()


@pytest.mark.parametrize("base", ["https://publisher.example/v1", "https://publisher.example/v1/"])
def test_gateway_search_tool_keeps_identity_with_canonical_contract(base):
    tools = AgentCoreGatewayConfig(iab_api_base=base).get_iab_api_gateway_tools_config()
    assert set(tools) == {"iab_search_content", "iab_get_content_details", "iab_get_analytics"}
    tool = tools["iab_search_content"]
    assert tool["endpoint"] == "https://publisher.example/v1/query"
    assert tool["method"] == "POST"
    assert tool["authentication"] == {
        "type": "bearer_token",
        "token_source": "user_context.publisher_key",
    }
    assert tool["parameters"] == {
        "query": {"type": "string", "required": True},
        "content_type": {"type": "array", "items": {"type": "string"}, "optional": True},
        "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
    }


def test_gateway_complete_config_carries_canonical_discovery():
    config = AgentCoreGatewayConfig(iab_api_base="https://publisher.example/v1")
    tool = config.create_complete_gateway_config()["iab_api_tools"]["iab_search_content"]
    assert tool["endpoint"].endswith("/query")
    assert tool["method"] == "POST"
    assert "limit" not in tool["parameters"]


def test_seed_permissions_are_key_gated_operation_ids(seed_module):
    from shared.auth.partner_operations import KEY_GATED_OPERATION_IDS

    partners = seed_module.create_sample_partners()
    assert [partner["partner_id"] for partner in partners][0] == seed_module.TEST_PARTNER_ID
    for partner in partners:
        assert set(partner["allowed_endpoints"]) <= KEY_GATED_OPERATION_IDS
        assert partner["status"] in ("active", "suspended")
        assert "api_quota" not in partner and "partner_key_hash" not in partner
    assert any(partner["status"] == "suspended" for partner in partners)


def test_seed_printed_example_is_post_with_canonical_json(seed_module, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["add-sample-partners.py", "--table-name", "unit-test-only"])
    monkeypatch.setattr("sys.argv", ["add-sample-partners.py", "--table-name", "unit-test-only", "--test-key-id", "k"])
    monkeypatch.setattr(seed_module, "create_sample_partners", lambda: [])
    monkeypatch.setattr(seed_module, "seed_partners", lambda *args, **kwargs: [])
    assert seed_module.main() == 0
    output = capsys.readouterr().out
    command = next(line.strip() for line in output.splitlines() if line.strip().startswith("curl "))
    args = shlex.split(command)
    assert args[args.index("-X") + 1] == "POST"
    assert "Content-Type: application/json" in args
    assert "https://PARTNER_API_URL/query" in args
    assert "x-api-key: API_KEY" in args and not any("X-Partner-Key" in a for a in args)
    assert_query_payload(json.loads(args[args.index("-d") + 1]))


@pytest.mark.parametrize(
    ("partner_type", "expected"),
    [
        ("publisher", ["queryContent", "submitLog", "validateLicense"]),
        ("ai_operator", ["queryContent", "submitLog"]),
        ("analytics_provider", ["queryContent"]),
    ],
)
def test_generated_partner_permissions(generator, partner_type, expected):
    attributes = generator._generate_partner_attributes(Faker(), partner_type)
    assert attributes["allowed_endpoints"] == expected


def test_discovery_parameter_examples_are_valid(generator):
    for _ in range(30):
        assert_query_payload(generator._generate_query_params("/api/v1/query"))


def test_generated_discovery_logs_use_post_body_and_no_cache(generator, monkeypatch):
    seen_endpoints = []

    def choose_query(endpoints):
        seen_endpoints.append(set(endpoints))
        return "/api/v1/query"

    monkeypatch.setattr(generator, "_select_endpoint_by_usage_pattern", choose_query)
    monkeypatch.setattr(generator, "_generate_response_code", lambda _: 204)
    logs = generator.generate_access_logs(["partner_001"], ["art_001"])
    assert len(logs) == 8
    assert all(
        endpoints == {"/api/v1/query", "/api/v1/content", "/api/v1/analytics", "/api/v1/bid"}
        for endpoints in seen_endpoints
    )
    deserialize = TypeDeserializer().deserialize
    for log in logs:
        assert log["endpoint"] == {"S": "/api/v1/query"}
        assert log["method"] == {"S": "POST"}
        assert log["response_code"] == {"N": "200"}
        assert log["query_params"] == {"M": {}}
        request_body = log["metadata"]["M"]["request_body"]
        assert request_body["M"]["max_results"]["N"].isdecimal()
        payload = deserialize(request_body)
        payload["max_results"] = int(payload["max_results"])
        assert_query_payload(payload)
        assert log["metadata"]["M"]["cache_hit"] == {"BOOL": False}
        validation = SchemaValidator().validate_record(log, "access_logs")
        assert validation.is_valid
        assert not validation.warnings


def test_generated_discovery_errors_remain_errors(generator, monkeypatch):
    monkeypatch.setattr(generator, "_select_endpoint_by_usage_pattern", lambda _: "/api/v1/query")
    monkeypatch.setattr(generator, "_generate_response_code", lambda _: 503)
    for log in generator.generate_access_logs(["partner_001"], []):
        assert log["response_code"] == {"N": "503"}
        assert log["billing_units"] == {"N": "0"}
        assert "error_details" in log


def test_combined_discovery_weight_and_request_sizes(generator):
    endpoints = dict.fromkeys(["/api/v1/query", "/api/v1/content", "/api/v1/analytics", "/api/v1/bid"])
    with patch("mock_data_generator.generator.random.choices", return_value=["/api/v1/query"]) as choose:
        assert generator._select_endpoint_by_usage_pattern(endpoints) == "/api/v1/query"
    choose.assert_called_once_with(list(endpoints), weights=[55, 30, 10, 5])
    with patch("mock_data_generator.generator.random.uniform", return_value=1):
        assert generator._generate_request_response_sizes("/api/v1/query", 200) == (800, 2000)


def test_synthetic_metric_labels_match_traffic_and_preserve_total(generator):
    for record in generator._generate_api_request_metrics("2026-09-10", Faker()):
        counts = record["metadata"]["M"]["endpoint_breakdown"]["M"]
        assert set(counts) == {"/api/v1/query", "/api/v1/content", "/api/v1/analytics", "/api/v1/bid"}
        total = int(record["value"]["N"])
        assert int(counts["/api/v1/query"]["N"]) == int(total * 0.55)
        assert sum(int(count["N"]) for count in counts.values()) == total


def test_endpoint_dictionary_literals_have_no_duplicate_keys():
    tree = ast.parse((ROOT / "source/mock_data_generator/generator.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value.startswith("/")
            ]
            assert len(keys) == len(set(keys)), f"Duplicate endpoint key at line {node.lineno}"
