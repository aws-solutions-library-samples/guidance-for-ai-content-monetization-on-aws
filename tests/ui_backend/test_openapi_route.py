"""
Tests for the /openapi meta-route (#31): serves the contract full, or partner-filtered via
?audience=partner, through the real ui_backend dispatcher.
"""

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "source" / "lambda"))
sys.path.insert(0, str(PROJECT_ROOT / "source" / "lambda" / "ui_backend"))

# Point the route at the committed SSOT for the test run.
os.environ["OPENAPI_SPEC_PATH"] = str(PROJECT_ROOT / "openapi-spec.yaml")

from ui_backend.handler import lambda_handler  # noqa: E402


class _Ctx:
    aws_request_id = "test-openapi"


def _get(path="/openapi", qs=None):
    return lambda_handler({"httpMethod": "GET", "path": path, "queryStringParameters": qs}, _Ctx())


class TestOpenApiRoute:
    def test_full_spec_served(self):
        resp = _get()
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["openapi"].startswith("3.")
        assert (
            len(body["paths"]) == 49
        )  # full contract (+6 /connections* #35, +5 managed llms.txt, +2 partner keys #15, -1 /search #38,
        # +2 /test/comparison-runs*)
        assert "x-tagGroups" in body

    def test_partner_view_filtered(self):
        resp = _get(qs={"audience": "partner"})
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        # only partner-facing ops; no internal paths
        assert "/query" in body["paths"]
        # The license COLLECTION read is internal-plane (#3 Req 4.1) — only the
        # single-document read and validate are partner-facing, so the bare /licenses
        # path is deliberately absent from the partner view.
        assert "/licenses" not in body["paths"]
        assert "/licenses/{license_id}" in body["paths"]
        assert "/analytics/metrics" not in body["paths"]
        assert "/pricing/rules" not in body["paths"]
        assert "/content" in body["paths"]  # the partner-facing URL read (#14)
        # only Partner API tag-group survives
        assert {g["name"] for g in body["x-tagGroups"]} == {"Partner API"}

    def test_partner_view_is_smaller_than_full(self):
        full = json.loads(_get()["body"])
        partner = json.loads(_get(qs={"audience": "partner"})["body"])
        assert len(partner["paths"]) < len(full["paths"])
        # partner schemas are a pruned subset (no internal-only leak)
        assert len(partner["components"]["schemas"]) <= len(full["components"]["schemas"])

    def test_unknown_audience_returns_full(self):
        resp = _get(qs={"audience": "bogus"})
        body = json.loads(resp["body"])
        assert (
            len(body["paths"]) == 49
        )  # anything other than 'partner' => full (+6 /connections* #35, +5 llms.txt, +2 partner keys #15, -1 /search #38,
        # +2 /test/comparison-runs*)

    def test_content_type_json(self):
        resp = _get()
        assert resp["headers"]["Content-Type"] == "application/json"


def _partner_plane_get(headers=None, qs=None):
    """A request as the partner API delivers it: stage variable set, Host of the generated API."""
    event = {
        "httpMethod": "GET",
        "path": "/openapi",
        "queryStringParameters": qs,
        "headers": headers if headers is not None else {"Host": "abc123.execute-api.us-west-2.amazonaws.com"},
        "stageVariables": {"api_audience": "partner"},
        "requestContext": {"stage": "v1"},
    }
    return lambda_handler(event, _Ctx())


class TestPartnerPlaneDocument:
    """GET /openapi served on the partner API (spec: native-apikey-auth-migration, task 6.5 / Q10)."""

    def test_only_routed_partner_operations(self):
        body = json.loads(_partner_plane_get()["body"])
        ops = {
            (m.upper(), p) for p, item in body["paths"].items() for m in item if m in ("get", "post", "put", "delete")
        }
        assert ops == {
            ("POST", "/query"),
            ("POST", "/log"),
            ("GET", "/info"),
            ("GET", "/taxonomy"),
            ("GET", "/llms.txt"),
            ("GET", "/content"),  # URL-addressable retrieval (#14)
        }
        # Retired, deferred, Cognito-only partner-product and internal operations are all
        # absent. /content is NOT in this list: its GET is the partner-facing URL read
        # (#14), asserted present above — only its POST (ingest) is internal, and a path
        # prefix check cannot express that, so the method-level assertion above covers it.
        for absent in ("/search", "/.well-known/comp.json", "/licenses", "/partners", "/analytics/metrics"):
            assert not any(p.startswith(absent) for p in body["paths"]), absent

    def test_matches_the_cdk_derivation(self):
        sys.path.insert(0, str(PROJECT_ROOT))
        from infrastructure.constructs.monetization_api import partner_operation_keys

        body = json.loads(_partner_plane_get()["body"])
        served = {
            (m.upper(), p) for p, item in body["paths"].items() for m in item if m in ("get", "post", "put", "delete")
        }
        assert served == set(partner_operation_keys()), "served document drifted from the deployed partner API"

    def test_native_key_scheme_and_no_legacy_header(self):
        resp = _partner_plane_get()
        body = json.loads(resp["body"])
        assert body["components"]["securitySchemes"]["PartnerKeyAuth"]["name"] == "x-api-key"
        assert set(body["components"]["securitySchemes"]) == {"PartnerKeyAuth"}
        assert "X-Partner-Key" not in resp["body"]
        assert "X-Partner-Key" not in resp["headers"]["Access-Control-Allow-Headers"]

    def test_servers_point_at_the_serving_host(self):
        body = json.loads(_partner_plane_get()["body"])
        assert body["servers"] == [
            {"url": "https://abc123.execute-api.us-west-2.amazonaws.com/v1", "description": "This deployment"}
        ]
        # Without a Host header the committed servers list is left alone rather than invented.
        body = json.loads(_partner_plane_get(headers={})["body"])
        assert body["servers"][0]["url"].startswith("https://api.publisher.com")

    def test_stage_variable_wins_over_query_parameter(self):
        # A partner-plane caller cannot widen the document by asking for another audience.
        body = json.loads(_partner_plane_get(qs={"audience": "internal"})["body"])
        assert "/analytics/metrics" not in body["paths"]
        assert "/licenses" not in body["paths"]

    def test_internal_stage_keeps_the_existing_views(self):
        event = {
            "httpMethod": "GET",
            "path": "/openapi",
            "queryStringParameters": {"audience": "partner"},
            "stageVariables": {"api_audience": "internal"},
        }
        body = json.loads(lambda_handler(event, _Ctx())["body"])
        assert "/licenses/{license_id}" in body["paths"]  # the partner product view (#31)
