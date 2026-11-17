"""Tests for the internal-API generator of the MonetizationApi construct (GitLab #26, Task 8).

Two levels:
  1. build_deploy_definition unit tests — the generated OpenAPI 3.0 deploy def has the expected
     paths, integrations, authentication, cache keys; fail-closed on a missing partner path.
  2. Synth-equivalence — the generated deploy def routes equivalently to the previous hand-authored
     inline dict: same path set, same integration target (which Lambda) per path, same /taxonomy
     cache keys. Authentication on this plane is Cognito only since the native API key migration
     (the partner plane is covered by ``test_partner_api_surface.py``).
"""

import pytest

from infrastructure.constructs.monetization_api import (
    PARTNER_EXPLICIT,
    build_deploy_definition,
)

# Fake synth-time tokens (real synth injects CDK ARN tokens; shape is what matters here).
_ARGS = dict(
    region="us-west-2",
    partner_api_arn="arn:aws:lambda:us-west-2:111122223333:function:partner",
    ui_backend_arn="arn:aws:lambda:us-west-2:111122223333:function:uibackend",
    cognito_user_pool_arn="arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_ABC",
)


def _uri_target(integration: dict) -> str:
    """Return 'partner' or 'uibackend' (or 'mock') for an integration's Lambda target."""
    if integration.get("type") == "mock":
        return "mock"
    uri = integration["uri"]
    if ":function:partner/" in uri:
        return "partner"
    if ":function:uibackend/" in uri:
        return "uibackend"
    return "unknown"


@pytest.fixture(scope="module")
def deploy_def():
    return build_deploy_definition(**_ARGS)


class TestGeneratedDeployDefinition:
    def test_is_openapi_3(self, deploy_def):
        assert deploy_def["openapi"].startswith("3.0")

    def test_paths_are_partner_explicit_plus_proxy(self, deploy_def):
        assert set(deploy_def["paths"].keys()) == set(PARTNER_EXPLICIT) | {"/{proxy+}"}

    def test_cognito_is_the_only_scheme(self, deploy_def):
        # The custom partner authorizer is gone: the internal plane never sees partner keys.
        schemes = deploy_def["components"]["securitySchemes"]
        assert set(schemes) == {"CognitoAuth"}
        assert "X-Partner-Key" not in str(deploy_def)
        assert schemes["CognitoAuth"]["x-amazon-apigateway-authorizer"]["type"] == "cognito_user_pools"

    def test_partner_paths_integrate_to_partner_api(self, deploy_def):
        for path in PARTNER_EXPLICIT:
            for method, op in deploy_def["paths"][path].items():
                assert _uri_target(op["x-amazon-apigateway-integration"]) in ("partner", "mock"), (
                    f"{method} {path} should integrate to partner_api (or mock OPTIONS)"
                )

    def test_proxy_integrates_to_ui_backend_with_cognito(self, deploy_def):
        proxy = deploy_def["paths"]["/{proxy+}"]
        any_method = proxy["x-amazon-apigateway-any-method"]
        assert _uri_target(any_method["x-amazon-apigateway-integration"]) == "uibackend"
        schemes = {list(s.keys())[0] for s in any_method["security"]}
        assert schemes == {"CognitoAuth"}
        assert _uri_target(proxy["options"]["x-amazon-apigateway-integration"]) == "mock"
        allow_headers = proxy["options"]["x-amazon-apigateway-integration"]["responses"]["default"][
            "responseParameters"
        ]["method.response.header.Access-Control-Allow-Headers"]
        assert "If-Match" in allow_headers

    def test_taxonomy_cache_keys_present(self, deploy_def):
        integ = deploy_def["paths"]["/taxonomy"]["get"]["x-amazon-apigateway-integration"]
        assert integ["cacheKeyParameters"] == [
            "method.request.querystring.tier_level",
            "method.request.querystring.parent_id",
            "method.request.querystring.search",
        ]

    def test_query_cognito_secured(self, deploy_def):
        # The hybrid /query carries CognitoAuth on the internal plane so the UI can call it (F8).
        op = deploy_def["paths"]["/query"]["post"]
        schemes = {list(s.keys())[0] for s in op.get("security", [])}
        assert schemes == {"CognitoAuth"}, "/query should require CognitoAuth on the internal edge"

    def test_content_falls_through_to_ui_backend_on_the_internal_plane(self, deploy_def):
        # Every /content method on this plane belongs to ui_backend behind the proxy's Cognito
        # authorizer: the content list and the Cognito single-document reads. An explicit
        # resource integrated to partner_api would refuse Cognito callers (partner key only).
        assert "/content" not in deploy_def["paths"]
        assert "/content" not in PARTNER_EXPLICIT

    def test_search_is_deleted_from_the_internal_plane(self, deploy_def):
        # /search is gone (no tombstone): it falls through to the Cognito /{proxy+} catch-all.
        assert "/search" not in deploy_def["paths"]

    def test_public_discovery_unsecured(self, deploy_def):
        for path in ("/info", "/taxonomy", "/llms.txt"):
            for method, op in deploy_def["paths"][path].items():
                assert "security" not in op, f"{method} {path} should be public (no security)"

    def test_params_use_openapi_3_schema_shape(self, deploy_def):
        # No Swagger-2.0 top-level `type:` on parameters (the original import-failure cause).
        for path, item in deploy_def["paths"].items():
            for method, op in item.items():
                for p in op.get("parameters", []) or []:
                    assert "schema" in p and "type" not in p, (
                        f"{method} {path} param {p.get('name')} must use 3.0 schema: shape"
                    )

    def test_no_committed_arn_or_account_leak_in_contract(self):
        # The generator receives ARNs as args; the committed contract must not carry them.
        from pathlib import Path

        contract = (Path(__file__).resolve().parents[2] / "openapi-spec.yaml").read_text()
        assert "arn:aws:lambda" not in contract
        assert "x-amazon-apigateway-integration" not in contract

    def test_fail_closed_on_missing_partner_path(self, tmp_path):
        # A contract missing a PARTNER_EXPLICIT path must raise (can't advertise an undefined path).
        bad = tmp_path / "bad.yaml"
        bad.write_text("openapi: 3.0.3\ninfo: {title: t, version: '1'}\npaths:\n  /info: {get: {}}\n")
        with pytest.raises(ValueError, match="missing from openapi-spec.yaml"):
            build_deploy_definition(**{**_ARGS, "contract_path": bad})


class TestSynthEquivalence:
    """Generated deploy def routes equivalently to the previous inline Swagger-2.0 dict."""

    # The characterized routing behavior of the previous hand-authored inline dict.
    _EXPECTED_ROUTING = {
        "/info": {"target": "partner", "secured": False},
        "/query": {"target": "partner", "secured": True},
        "/taxonomy": {"target": "partner", "secured": False},
        "/llms.txt": {"target": "partner", "secured": False},
        "/{proxy+}": {"target": "uibackend", "secured": True},
    }

    def test_routing_matches_previous_inline_dict(self, deploy_def):
        for path, expected in self._EXPECTED_ROUTING.items():
            item = deploy_def["paths"][path]
            if path == "/{proxy+}":
                op = item["x-amazon-apigateway-any-method"]
            else:
                # the primary (non-OPTIONS) method
                method = next(m for m in item if m in ("get", "post", "put", "delete"))
                op = item[method]
            assert _uri_target(op["x-amazon-apigateway-integration"]) == expected["target"], (
                f"{path} integration target changed"
            )
            has_security = bool(op.get("security"))
            assert has_security == expected["secured"], f"{path} security posture changed"
