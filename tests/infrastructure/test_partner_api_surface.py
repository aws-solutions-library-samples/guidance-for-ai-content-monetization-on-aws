"""Partner API deploy surface (spec: native-apikey-auth-migration, task 6.6; Req 1, 2, 4, 10).

The partner plane is derived from the contract through the shared operation registry, so this
suite is fail-closed: the derived set must equal the expected set exactly, nothing internal may
appear, and the native API-key wiring must be present.
"""

import re
from pathlib import Path

import pytest
import yaml

from infrastructure.constructs.monetization_api import (
    PARTNER_GATEWAY_RESPONSES,
    PARTNER_OPENAPI_PATH,
    build_partner_definition,
    derive_partner_operations,
)
from shared.auth.partner_operations import DEFERRED_NO_HANDLER, KEY_GATED_OPERATIONS

_CONTRACT = Path(__file__).resolve().parents[2] / "openapi-spec.yaml"
_ARGS = dict(
    region="us-west-2",
    partner_api_arn="arn:aws:lambda:us-west-2:111122223333:function:partner",
    ui_backend_arn="arn:aws:lambda:us-west-2:111122223333:function:uibackend",
)

# Five operations while the three deferred ones have no handler; eight once they land.
EXPECTED_OPERATIONS = {
    ("POST", "/query"),
    ("POST", "/log"),
    ("GET", "/info"),
    ("GET", "/taxonomy"),
    ("GET", "/llms.txt"),
    # URL-addressable content retrieval (#14) — partner-key gated.
    ("GET", "/content"),
}


@pytest.fixture(scope="module")
def contract():
    return yaml.safe_load(_CONTRACT.read_text())


@pytest.fixture(scope="module")
def partner_def():
    return build_partner_definition(**_ARGS)


def _target(op):
    return op["x-amazon-apigateway-integration"]["uri"].split(":function:")[1].split("/")[0]


class TestDerivation:
    def test_derived_set_is_exactly_the_expected_set(self, contract):
        assert set(derive_partner_operations(contract)) == EXPECTED_OPERATIONS

    def test_deferred_are_excluded_but_still_in_contract(self, contract):
        for method, path in DEFERRED_NO_HANDLER:
            assert method.lower() in contract["paths"][path], f"{method} {path} left the contract; update the registry"
            assert (method, path) not in derive_partner_operations(contract)

    def test_every_key_gated_handler_is_deployed(self, contract):
        derived = set(derive_partner_operations(contract))
        assert set(KEY_GATED_OPERATIONS) - DEFERRED_NO_HANDLER <= derived

    def test_fail_closed_when_a_key_gated_operation_leaves_the_contract(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "openapi: 3.0.3\ninfo: {title: t, version: '1'}\npaths:\n  /info: {get: {x-audience: partner}}\n"
        )
        with pytest.raises(ValueError, match="key-gated partner operations missing"):
            build_partner_definition(**{**_ARGS, "contract_path": bad})


class TestDeployDefinition:
    def test_paths_are_derived_plus_openapi_and_nothing_else(self, partner_def):
        expected_paths = {path for _, path in EXPECTED_OPERATIONS} | {PARTNER_OPENAPI_PATH}
        assert set(partner_def["paths"]) == expected_paths
        assert "/{proxy+}" not in partner_def["paths"]

    def test_no_internal_operation_is_reachable(self, partner_def, contract):
        internal = {
            (m.upper(), p)
            for p, item in contract["paths"].items()
            for m, op in item.items()
            if isinstance(op, dict) and op.get("x-audience") == "internal"
        }
        deployed = {(m.upper(), p) for p, item in partner_def["paths"].items() for m in item if m != "options"}
        assert not (deployed & internal)
        assert not any(p.startswith("/licenses") or p.startswith("/partners") for p in partner_def["paths"])

    def test_key_gated_ops_require_native_api_key(self, partner_def):
        for method, path in KEY_GATED_OPERATIONS:
            if (method, path) in DEFERRED_NO_HANDLER:
                continue
            op = partner_def["paths"][path][method.lower()]
            assert op["security"] == [{"PartnerKeyAuth": []}]
            assert op["x-amazon-apigateway-api-key-required"] is True

    def test_public_ops_have_no_security(self, partner_def):
        for path in ("/info", "/taxonomy", "/llms.txt", PARTNER_OPENAPI_PATH):
            assert "security" not in partner_def["paths"][path]["get"]
            assert "x-amazon-apigateway-api-key-required" not in partner_def["paths"][path]["get"]

    def test_native_key_scheme_and_header_source(self, partner_def):
        assert partner_def["components"]["securitySchemes"] == {
            "PartnerKeyAuth": {"type": "apiKey", "name": "x-api-key", "in": "header"}
        }
        assert partner_def["x-amazon-apigateway-api-key-source"] == "HEADER"
        assert "x-amazon-apigateway-authorizer" not in str(partner_def)
        assert "X-Partner-Key" not in str(partner_def)

    def test_integration_targets(self, partner_def):
        for path in ("/info", "/query", "/taxonomy", "/llms.txt", "/content"):
            assert _target(next(op for m, op in partner_def["paths"][path].items() if m != "options")) == "partner"
        assert _target(partner_def["paths"]["/log"]["post"]) == "uibackend"
        assert _target(partner_def["paths"][PARTNER_OPENAPI_PATH]["get"]) == "uibackend"

    def test_taxonomy_cache_keys_carried_over(self, partner_def):
        integ = partner_def["paths"]["/taxonomy"]["get"]["x-amazon-apigateway-integration"]
        assert integ["cacheKeyParameters"] == [
            "method.request.querystring.tier_level",
            "method.request.querystring.parent_id",
            "method.request.querystring.search",
        ]

    def test_gateway_responses_carry_contract_error_codes(self, partner_def):
        responses = partner_def["x-amazon-apigateway-gateway-responses"]
        assert responses is PARTNER_GATEWAY_RESPONSES
        assert responses["INVALID_API_KEY"]["statusCode"] == 403
        assert '"invalid_api_key"' in responses["INVALID_API_KEY"]["responseTemplates"]["application/json"]
        for name, retry in (("QUOTA_EXCEEDED", "'3600'"), ("THROTTLED", "'1'")):
            assert responses[name]["statusCode"] == 429
            assert responses[name]["responseParameters"]["gatewayresponse.header.Retry-After"] == retry
        for name in responses:
            allow = responses[name]["responseParameters"]["gatewayresponse.header.Access-Control-Allow-Headers"]
            assert "X-Api-Key" in allow and "X-Partner-Key" not in allow

    def test_every_gateway_response_carries_a_registered_error_code(self, partner_def):
        """No partner-facing rejection ships without an ``error_code``, and each code is in the register."""
        with open(Path(__file__).resolve().parents[2] / "openapi-spec.yaml") as spec_file:
            registered = {c["error_code"] for c in yaml.safe_load(spec_file)["x-error-codes"]["codes"]}
        responses = partner_def["x-amazon-apigateway-gateway-responses"]
        for name in ("WAF_FILTERED", "ACCESS_DENIED", "MISSING_AUTHENTICATION_TOKEN", "DEFAULT_4XX", "DEFAULT_5XX"):
            assert name in responses, f"{name} has no gateway response"
        for name, response in responses.items():
            template = response["responseTemplates"]["application/json"]
            codes = re.findall(r'"error_code": "([a-z_]+)"', template)
            assert len(codes) == 1, f"{name} body must carry exactly one error_code: {template}"
            assert codes[0] in registered, f"{name} emits {codes[0]!r}, which x-error-codes does not register"
        # The defaults stand in for many response types, so they must not pin one status.
        assert "statusCode" not in responses["DEFAULT_4XX"] and "statusCode" not in responses["DEFAULT_5XX"]
        for name in ("WAF_FILTERED", "ACCESS_DENIED", "MISSING_AUTHENTICATION_TOKEN"):
            assert responses[name]["statusCode"] == 403, f"{name} must keep the gateway's default 403"

    def test_every_path_has_cors_preflight(self, partner_def):
        for path, item in partner_def["paths"].items():
            assert item["options"]["x-amazon-apigateway-integration"]["type"] == "mock", path
