"""Partner resolution from the native API key id (spec: native-apikey-auth-migration, task 3).

API Gateway validates the key and stamps ``requestContext.identity.apiKeyId``; the Lambda resolves
that id through ``cm-partners`` and authorizes the operation. These tests run against a moto table
with the real key schema and GSI so the access path exercised is the deployed one.
"""

import json
import os
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from shared.auth import authentication_functions as af
from shared.auth.authentication_decorators import optional_auth, require_partner_auth, require_partner_or_cognito_auth
from shared.auth.authentication_functions import PartnerResolutionError, resolve_partner_from_event
from shared.utils.models import Partner, PartnerCredential

TABLE = "cm-partners-test"


def _event(api_key_id="key-active", method="POST", resource="/query", claims=None):
    event = {
        "httpMethod": method,
        "resource": resource,
        "path": resource,
        "headers": {"Content-Type": "application/json"},
        "requestContext": {"identity": {"apiKeyId": api_key_id, "sourceIp": "203.0.113.5"}},
    }
    if claims is not None:
        event["requestContext"]["authorizer"] = {"claims": claims}
    return event


@pytest.fixture
def partner_table(monkeypatch):
    monkeypatch.setenv("PARTNER_TABLE_NAME", TABLE)
    monkeypatch.setenv("PARTNERS_TABLE_NAME", TABLE)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-west-2")
        table = ddb.create_table(
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
                    "Projection": {
                        "ProjectionType": "INCLUDE",
                        "NonKeyAttributes": ["partner_id", "usage_plan_id", "status"],
                    },
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        for item in (
            Partner(partner_id="ptr_active", partner_name="Active Partner", pricing_tier="premium").to_item(),
            PartnerCredential(partner_id="ptr_active", api_key_id="key-active", usage_plan_id="plan-premium").to_item(),
            PartnerCredential(
                partner_id="ptr_active", api_key_id="key-disabled", usage_plan_id="plan-premium", status="disabled"
            ).to_item(),
            Partner(partner_id="ptr_suspended", partner_name="Suspended", status="suspended").to_item(),
            PartnerCredential(
                partner_id="ptr_suspended", api_key_id="key-suspended", usage_plan_id="plan-basic"
            ).to_item(),
            Partner(partner_id="ptr_scoped", partner_name="Scoped", allowed_endpoints=["submitLog"]).to_item(),
            PartnerCredential(partner_id="ptr_scoped", api_key_id="key-scoped", usage_plan_id="plan-basic").to_item(),
            PartnerCredential(partner_id="ptr_orphan", api_key_id="key-orphan", usage_plan_id="plan-basic").to_item(),
        ):
            table.put_item(Item=item)
        with patch.object(af, "dynamodb", ddb), patch.object(af, "publish_auth_failure_metric"):
            yield table


class TestResolvePartnerFromEvent:
    def test_happy_path_returns_partner_context(self, partner_table):
        partner = resolve_partner_from_event(_event())
        assert partner["partner_id"] == "ptr_active"
        assert partner["api_key_id"] == "key-active"
        assert partner["usage_plan_id"] == "plan-premium"
        assert partner["name"] == "Active Partner"
        assert partner["pricing_tier"] == "premium"
        assert partner["operation_id"] == "queryContent"
        assert partner["allowed_endpoints"] == []

    def test_no_api_key_id_returns_none(self, partner_table):
        event = _event()
        event["requestContext"]["identity"].pop("apiKeyId")
        assert resolve_partner_from_event(event) is None
        assert resolve_partner_from_event({}) is None

    def test_unknown_key_id_is_partner_inactive(self, partner_table):
        with pytest.raises(PartnerResolutionError) as exc:
            resolve_partner_from_event(_event(api_key_id="key-unknown"))
        assert (exc.value.status_code, exc.value.error_code) == (403, "partner_inactive")

    def test_disabled_key_is_partner_inactive(self, partner_table):
        with pytest.raises(PartnerResolutionError) as exc:
            resolve_partner_from_event(_event(api_key_id="key-disabled"))
        assert exc.value.error_code == "partner_inactive"

    def test_suspended_partner_is_partner_inactive(self, partner_table):
        with pytest.raises(PartnerResolutionError) as exc:
            resolve_partner_from_event(_event(api_key_id="key-suspended"))
        assert exc.value.error_code == "partner_inactive"

    def test_credential_without_partner_record_is_partner_inactive(self, partner_table):
        with pytest.raises(PartnerResolutionError) as exc:
            resolve_partner_from_event(_event(api_key_id="key-orphan"))
        assert exc.value.error_code == "partner_inactive"

    def test_allow_list_miss_is_endpoint_not_allowed(self, partner_table):
        with pytest.raises(PartnerResolutionError) as exc:
            resolve_partner_from_event(_event(api_key_id="key-scoped", resource="/query"))
        assert (exc.value.status_code, exc.value.error_code) == (403, "endpoint_not_allowed")

    def test_allow_list_hit_passes(self, partner_table):
        partner = resolve_partner_from_event(_event(api_key_id="key-scoped", resource="/log"))
        assert partner["operation_id"] == "submitLog"

    def test_empty_allow_list_means_all_key_gated_operations(self, partner_table):
        for resource in ("/query", "/log", "/licenses/{license_id}/validate"):
            assert resolve_partner_from_event(_event(resource=resource))["partner_id"] == "ptr_active"

    def test_public_operation_is_never_blocked_by_allow_list(self, partner_table):
        partner = resolve_partner_from_event(_event(api_key_id="key-scoped", method="GET", resource="/info"))
        assert partner["operation_id"] == "getApiInfo"

    def test_lookup_failure_fails_closed(self, partner_table, monkeypatch):
        monkeypatch.setenv("PARTNER_TABLE_NAME", "does-not-exist")
        with pytest.raises(PartnerResolutionError) as exc:
            resolve_partner_from_event(_event())
        assert exc.value.error_code == "partner_inactive"

    def test_partner_status_is_read_strongly_consistent(self, partner_table, monkeypatch):
        """A partner suspended a moment ago must not be admitted on a stale ``META`` read."""
        reads = []

        class _RecordingTable:
            def __init__(self, table):
                self._table = table

            def __getattr__(self, name):
                return getattr(self._table, name)

            def get_item(self, **kwargs):
                reads.append(kwargs)
                return self._table.get_item(**kwargs)

        monkeypatch.setattr(af, "_partner_table", lambda: _RecordingTable(partner_table))
        resolve_partner_from_event(_event())
        assert reads, "resolution never read the partner META record"
        assert all(read.get("ConsistentRead") is True for read in reads)

    def test_resolution_never_touches_the_usage_counter(self, partner_table):
        resolve_partner_from_event(_event())
        meta = partner_table.get_item(Key={"PK": "PARTNER#ptr_active", "SK": "META"})["Item"]
        assert "api_usage" not in meta and "last_access" not in meta


class TestDecoratorsUseResolution:
    @staticmethod
    def _handler(headers, request_data, event, **kwargs):
        return {
            "statusCode": 200,
            "body": json.dumps({"user": kwargs["authenticated_user"], "auth_type": kwargs["auth_type"]}),
        }

    def test_require_partner_auth_resolves_from_event_not_headers(self, partner_table):
        wrapped = require_partner_auth(self._handler)
        result = wrapped({"X-Partner-Key": "ignored-legacy-value"}, {}, _event())
        body = json.loads(result["body"])
        assert result["statusCode"] == 200
        assert body["auth_type"] == "partner_key"
        assert body["user"]["partner_id"] == "ptr_active"

    def test_require_partner_auth_without_key_is_403_invalid_api_key(self, partner_table):
        event = _event()
        event["requestContext"]["identity"].pop("apiKeyId")
        result = require_partner_auth(self._handler)({"X-Partner-Key": "anything"}, {}, event)
        assert result["statusCode"] == 403
        assert json.loads(result["body"])["error_code"] == "invalid_api_key"

    def test_hybrid_decorator_denies_scoped_partner_with_error_code(self, partner_table):
        result = require_partner_or_cognito_auth(self._handler)({}, {}, _event(api_key_id="key-scoped"))
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert body["error_code"] == "endpoint_not_allowed"

    def test_hybrid_decorator_cognito_path_unchanged(self, partner_table):
        claims = {"sub": "user-1", "email": "pub@example.com"}
        event = _event(api_key_id=None, claims=claims)
        result = require_partner_or_cognito_auth(self._handler)({}, {}, event)
        body = json.loads(result["body"])
        assert body["auth_type"] == "cognito"
        assert body["user"]["user_id"] == "user-1"

    def test_optional_auth_identifies_partner_on_public_operation(self, partner_table):
        result = optional_auth(self._handler)({}, {}, _event(api_key_id="key-scoped", method="GET", resource="/info"))
        body = json.loads(result["body"])
        assert body["auth_type"] == "partner_key"
        assert body["user"]["partner_id"] == "ptr_scoped"

    def test_optional_auth_without_key_is_public(self, partner_table):
        event = _event(method="GET", resource="/info")
        event["requestContext"]["identity"].pop("apiKeyId")
        body = json.loads(optional_auth(self._handler)({}, {}, event)["body"])
        assert body["auth_type"] == "public" and body["user"] is None


def test_no_module_reads_the_legacy_partner_key_header():
    """The key value never reaches the Lambda; nothing may look for it."""
    root = os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda")
    offenders = []
    for dirpath, _, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            lowered = text.lower()
            if "x-partner-key" in lowered or "partner_key_hash" in lowered or "partner-id-index" in lowered:
                offenders.append(os.path.relpath(path, root))
    assert offenders == [], f"modules still referencing the legacy partner header or table shape: {offenders}"
