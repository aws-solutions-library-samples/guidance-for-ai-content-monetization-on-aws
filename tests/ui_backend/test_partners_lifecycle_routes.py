"""Route-level tests for the partner lifecycle operations (spec: native-apikey-auth-migration, 5.x).

Drives ``ui_backend.routes.partners.route`` for the seven ``/partners`` operations against moto
DynamoDB + API Gateway, asserting status codes, contract error codes, and that the key value is
only ever present in create/rotate responses.
"""

import json
import os

import boto3
import pytest
from moto import mock_aws

from ui_backend.routes.partners import route as partners_route

REGION = "us-west-2"
TABLE = "cm-partners-lifecycle-tests"


def _event(method, path, body=None, *, authed=True):
    event = {
        "httpMethod": method,
        "path": path,
        "headers": {"Content-Type": "application/json"},
        "queryStringParameters": None,
        "body": json.dumps(body) if body is not None else None,
    }
    if authed:
        event["requestContext"] = {
            "authorizer": {"claims": {"sub": "u1", "email": "t@example.com", "custom:publisher_id": "pub_1", "cognito:username": "t"}}
        }
    return event


def _call(method, path, body=None, **kw):
    response = partners_route(_event(method, path, body, **kw), method, path)
    parsed = json.loads(response["body"]) if response.get("body") else None
    return response["statusCode"], parsed


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("PARTNER_TABLE_NAME", TABLE)
    with mock_aws():
        boto3.resource("dynamodb", region_name=REGION).create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "api_key_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "api-key-id-index",
                    "KeySchema": [{"AttributeName": "api_key_id", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "INCLUDE", "NonKeyAttributes": ["partner_id", "usage_plan_id", "status"]},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        apigw = boto3.client("apigateway", region_name=REGION)
        for tier in ("basic", "standard", "premium", "enterprise"):
            plan = apigw.create_usage_plan(name=f"cm-partner-{tier}", quota={"limit": 100, "period": "MONTH"})
            monkeypatch.setenv(f"USAGE_PLAN_ID_{tier.upper()}", plan["id"])
        yield apigw


class TestLifecycleRoutes:
    def test_full_lifecycle_through_the_route(self, env):
        status, created = _call("POST", "/partners", {"partner_name": "Route Co", "pricing_tier": "basic"})
        assert status == 201 and created["key_value"] and created["key"]["status"] == "active"
        pid, first_key = created["partner"]["partner_id"], created["key"]["key_id"]

        status, detail = _call("GET", f"/partners/{pid}")
        assert status == 200 and detail["partner_id"] == pid and detail["tier_limits"]["quota_limit"] == 100
        assert [k["key_id"] for k in detail["keys"]] == [first_key] and "key_value" not in json.dumps(detail)

        status, listed = _call("GET", "/partners")
        assert status == 200 and any(p["partner_id"] == pid for p in listed["partners"])

        status, updated = _call("PUT", f"/partners/{pid}", {"pricing_tier": "premium", "status": "suspended"})
        assert status == 200 and updated["pricing_tier"] == "premium" and updated["active_key_count"] == 0

        status, rotated = _call("POST", f"/partners/{pid}/keys")
        assert status == 201 and rotated["key_value"] and rotated["key"]["key_id"] != first_key
        assert rotated["key"]["status"] == "disabled"  # partner is suspended

        status, _ = _call("PUT", f"/partners/{pid}", {"status": "active"})
        assert status == 200
        status, detail = _call("GET", f"/partners/{pid}")
        statuses = {k["key_id"]: k["status"] for k in detail["keys"]}
        assert statuses[rotated["key"]["key_id"]] == "active" and statuses[first_key] == "disabled"

        status, body = _call("DELETE", f"/partners/{pid}/keys/{rotated['key']['key_id']}")
        assert status == 409 and body["error_code"] == "conflict"  # only active key
        status, body = _call("DELETE", f"/partners/{pid}/keys/{first_key}")
        assert status == 204 and body is None

        status, deleted = _call("DELETE", f"/partners/{pid}")
        assert status == 200 and deleted["status"] == "deleted" and deleted["active_key_count"] == 0
        assert env.get_api_keys()["items"] == []

        status, body = _call("POST", f"/partners/{pid}/keys")
        assert status == 409 and body["error_code"] == "conflict"  # deleted partner

    def test_error_mapping(self, env):
        status, body = _call("POST", "/partners", {"partner_name": "x"})
        assert status == 400 and body["error_code"] == "validation_error"
        status, body = _call("GET", "/partners/ptr_missing")
        assert status == 404
        status, body = _call("DELETE", "/partners/ptr_missing")
        assert status == 404
        status, body = _call("DELETE", "/partners/ptr_missing/keys/k1")
        assert status == 404
        status, _ = _call("PATCH", "/partners/ptr_x")
        assert status == 404  # unsupported method under the prefix
        status, _ = _call("GET", "/partners/ptr_x/keys")
        assert status == 404  # keys collection has no GET in the contract

    def test_every_operation_requires_cognito(self, env):
        for method, path in [
            ("POST", "/partners"),
            ("GET", "/partners/p"),
            ("PUT", "/partners/p"),
            ("DELETE", "/partners/p"),
            ("POST", "/partners/p/keys"),
            ("DELETE", "/partners/p/keys/k"),
        ]:
            status, _ = _call(method, path, {"partner_name": "n", "pricing_tier": "basic"}, authed=False)
            assert status == 401, (method, path)
        assert env.get_api_keys()["items"] == []  # nothing provisioned without auth
