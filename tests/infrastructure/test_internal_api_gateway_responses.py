"""The internal (Cognito) API's gateway responses carry CORS headers and a registered ``error_code``.

Regression guard for a bug found while testing #10 in a real browser: the rejections API Gateway
produces BEFORE the Lambda runs (missing/expired/invalid token, authorizer deny, default 4XX/5XX)
carried no ``Access-Control-Allow-Origin`` header on the internal API, so a browser could not read
the status and surfaced an opaque ``TypeError: Failed to fetch`` / Firefox "NetworkError when
attempting to fetch resource". The portal could not tell "session expired" from a network outage.

The partner API already had this (``PARTNER_GATEWAY_RESPONSES``); this pins the same contract on the
internal plane. Spec: ``scrape-vs-api-comparison`` design §5 ("Session expired" state) depends on it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from infrastructure.constructs.monetization_api import (
    INTERNAL_GATEWAY_RESPONSES,
    build_deploy_definition,
)

_ARGS = {
    "region": "us-west-2",
    "partner_api_arn": "arn:aws:lambda:us-west-2:111122223333:function:partner",
    "ui_backend_arn": "arn:aws:lambda:us-west-2:111122223333:function:ui",
    "cognito_user_pool_arn": "arn:aws:cognito-idp:us-west-2:111122223333:userpool/us-west-2_TEST",
}


@pytest.fixture(scope="module")
def internal_def():
    return build_deploy_definition(**_ARGS)


@pytest.fixture(scope="module")
def registered_codes():
    with open(Path(__file__).resolve().parents[2] / "openapi-spec.yaml") as spec_file:
        return {c["error_code"] for c in yaml.safe_load(spec_file)["x-error-codes"]["codes"]}


class TestInternalGatewayResponses:
    def test_internal_definition_declares_gateway_responses(self, internal_def):
        assert internal_def["x-amazon-apigateway-gateway-responses"] is INTERNAL_GATEWAY_RESPONSES

    @pytest.mark.parametrize("name", ["UNAUTHORIZED", "ACCESS_DENIED", "EXPIRED_TOKEN", "DEFAULT_4XX", "DEFAULT_5XX"])
    def test_every_gateway_rejection_carries_cors_headers(self, name):
        """The whole point: a browser must be able to READ a gateway rejection."""
        params = INTERNAL_GATEWAY_RESPONSES[name]["responseParameters"]
        assert params["gatewayresponse.header.Access-Control-Allow-Origin"] == "'*'"
        assert "Authorization" in params["gatewayresponse.header.Access-Control-Allow-Headers"]
        assert "POST" in params["gatewayresponse.header.Access-Control-Allow-Methods"]

    def test_unauthorized_keeps_401_so_the_portal_can_offer_sign_in(self):
        """The comparison page maps 401 -> SessionExpiredError -> "Sign in again" (design §5)."""
        assert INTERNAL_GATEWAY_RESPONSES["UNAUTHORIZED"]["statusCode"] == 401

    def test_every_body_carries_exactly_one_registered_error_code(self, registered_codes):
        for name, response in INTERNAL_GATEWAY_RESPONSES.items():
            template = response["responseTemplates"]["application/json"]
            codes = re.findall(r'"error_code": "([a-z_]+)"', template)
            assert len(codes) == 1, f"{name} body must carry exactly one error_code: {template}"
            assert codes[0] in registered_codes, f"{name} emits {codes[0]!r}, which x-error-codes does not register"

    def test_defaults_pin_no_status(self):
        """DEFAULT_4XX/5XX stand in for many response types, so they must keep the gateway's status."""
        assert "statusCode" not in INTERNAL_GATEWAY_RESPONSES["DEFAULT_4XX"]
        assert "statusCode" not in INTERNAL_GATEWAY_RESPONSES["DEFAULT_5XX"]

    def test_internal_definition_is_otherwise_unchanged(self, internal_def):
        """Adding gateway responses must not alter the routing surface."""
        assert "/{proxy+}" in internal_def["paths"]
        assert "/test/comparison-runs" not in internal_def["paths"], "proxy routes are not explicit resources"
        assert internal_def["components"]["securitySchemes"]["CognitoAuth"]["x-amazon-apigateway-authtype"] == (
            "cognito_user_pools"
        )
