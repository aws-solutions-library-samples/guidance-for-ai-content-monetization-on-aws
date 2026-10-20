"""PartnersService: reads, metadata updates and key provisioning (spec: native-apikey-auth-migration, task 4.0).

Runs against moto DynamoDB (real key schema + GSI) and moto API Gateway (real usage plans and keys)
so the provisioning sequence exercised here is the one the lifecycle API and the seed script share.
"""

import os

import boto3
import pytest
from moto import mock_aws

from services.partners_service import (
    NoPartnerFieldsError,
    PartnerConflictError,
    PartnerNotFoundError,
    PartnersService,
    PartnerValidationError,
    usage_plan_id_for,
    validate_partner_fields,
)
from shared.utils.models import Partner, PartnerCredential

TABLE = "cm-partners-svc-test"
REGION = "us-west-2"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("PARTNER_TABLE_NAME", TABLE)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
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
        apigw = boto3.client("apigateway", region_name=REGION)
        plans = {}
        for tier in ("basic", "standard", "premium", "enterprise"):
            plan = apigw.create_usage_plan(name=f"partner-{tier}", quota={"limit": 1000, "period": "MONTH"})
            plans[tier] = plan["id"]
            monkeypatch.setenv(f"USAGE_PLAN_ID_{tier.upper()}", plan["id"])
        yield {"table": table, "apigw": apigw, "plans": plans, "service": PartnersService(apigateway_client=apigw)}


def _seed(table, partner_id="ptr_a", **overrides):
    partner = Partner(partner_id=partner_id, partner_name=f"Partner {partner_id}", pricing_tier="standard", **overrides)
    table.put_item(Item=partner.to_item())
    return partner


class TestValidation:
    def test_rejects_unknown_tier_status_and_endpoints(self):
        with pytest.raises(PartnerValidationError):
            validate_partner_fields({"pricing_tier": "platinum"})
        with pytest.raises(PartnerValidationError):
            validate_partner_fields({"status": "deleted"})  # only active/suspended via update
        with pytest.raises(PartnerValidationError, match="/search"):
            validate_partner_fields({"allowed_endpoints": ["queryContent", "/search"]})
        with pytest.raises(PartnerValidationError, match="getApiInfo"):
            validate_partner_fields({"allowed_endpoints": ["getApiInfo"]})  # public: never gated
        with pytest.raises(PartnerValidationError):
            validate_partner_fields({"partner_name": "  "})

    def test_accepts_key_gated_operation_ids_and_empty_list(self):
        validate_partner_fields({"allowed_endpoints": ["queryContent", "submitLog", "validateLicense"]})
        validate_partner_fields({"allowed_endpoints": [], "pricing_tier": "enterprise", "status": "suspended"})

    def test_usage_plan_lookup_rejects_unknown_tier_without_a_client_call(self):
        with pytest.raises(PartnerValidationError):
            usage_plan_id_for("platinum")


class TestUsagePlanLookup:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("USAGE_PLAN_ID_BASIC", "plan-override")
        assert usage_plan_id_for("basic") == "plan-override"

    def test_resolves_by_name_and_caches(self, env, monkeypatch):
        from services import partners_service

        monkeypatch.delenv("USAGE_PLAN_ID_STANDARD", raising=False)
        monkeypatch.setattr(partners_service, "_usage_plan_cache", {})
        monkeypatch.setattr(
            partners_service, "USAGE_PLAN_NAME_PREFIX", "partner-"
        )  # fixture names plans partner-<tier>
        apigw = env["apigw"]
        assert usage_plan_id_for("standard", apigw) == env["plans"]["standard"]
        apigw.delete_usage_plan(usagePlanId=env["plans"]["standard"])  # cached: no second lookup
        assert usage_plan_id_for("standard", apigw) == env["plans"]["standard"]

    def test_missing_plan_is_a_validation_error(self, env, monkeypatch):
        from services import partners_service

        monkeypatch.delenv("USAGE_PLAN_ID_ENTERPRISE", raising=False)
        monkeypatch.setattr(partners_service, "_usage_plan_cache", {})
        monkeypatch.setattr(partners_service, "USAGE_PLAN_NAME_PREFIX", "nope-")
        with pytest.raises(PartnerValidationError, match="enterprise"):
            usage_plan_id_for("enterprise", env["apigw"])


class TestReads:
    def test_get_partners_counts_active_keys_and_omits_retired_fields(self, env):
        table, svc = env["table"], env["service"]
        _seed(table, "ptr_a")
        _seed(table, "ptr_b", status="suspended")
        table.put_item(Item=PartnerCredential("ptr_a", "k1", "plan", status="active").to_item())
        table.put_item(Item=PartnerCredential("ptr_a", "k2", "plan", status="disabled").to_item())

        payload = svc.get_partners("pub")
        assert payload["total_partners"] == 2 and payload["active_partners"] == 1
        by_id = {p["partner_id"]: p for p in payload["partners"]}
        assert by_id["ptr_a"]["active_key_count"] == 1
        assert by_id["ptr_b"]["active_key_count"] == 0
        assert set(by_id["ptr_a"]) >= {"partner_name", "pricing_tier", "status", "identity_source", "allowed_endpoints"}
        for retired in ("name", "api_quota", "api_usage", "requests", "revenue", "last_active", "partner_key_hash"):
            assert retired not in by_id["ptr_a"]

    def test_get_partner_returns_keys_without_values(self, env):
        table, svc = env["table"], env["service"]
        _seed(table, "ptr_a")
        table.put_item(Item=PartnerCredential("ptr_a", "k1", "plan", created_at="2026-01-02T00:00:00Z").to_item())
        detail = svc.get_partner("ptr_a")
        assert detail["partner_id"] == "ptr_a"
        assert detail["keys"] == [
            {
                "key_id": "k1",
                "status": "active",
                "usage_plan_id": "plan",
                "created_at": "2026-01-02T00:00:00Z",
                "disabled_at": None,
                # The seeded plan id does not exist at the (moto) gateway: usage is null, not an error.
                "usage": None,
            }
        ]
        assert not any("value" in k for k in detail["keys"][0])
        # Limits come from the tier's real usage plan created by the fixture.
        assert detail["tier_limits"] == {"quota_limit": 1000, "quota_period": "MONTH", "rate_limit": 0.0, "burst_limit": 0}

    def test_get_partner_unknown_raises(self, env):
        with pytest.raises(PartnerNotFoundError):
            env["service"].get_partner("nope")


class TestUpdate:
    def test_updates_metadata_only_and_ignores_retired_fields(self, env):
        table, svc = env["table"], env["service"]
        _seed(table, "ptr_a")
        result = svc.update_partner(
            "ptr_a",
            {"pricing_tier": "premium", "api_quota": 99, "status": "suspended", "allowed_endpoints": ["submitLog"]},
        )
        assert result["pricing_tier"] == "premium" and result["status"] == "suspended"
        assert result["allowed_endpoints"] == ["submitLog"]
        stored = table.get_item(Key={"PK": "PARTNER#ptr_a", "SK": "META"})["Item"]
        assert "api_quota" not in stored and stored["updated_at"]

    def test_update_errors(self, env):
        table, svc = env["table"], env["service"]
        _seed(table, "ptr_a")
        _seed(table, "ptr_gone", status="deleted")
        with pytest.raises(PartnerNotFoundError):
            svc.update_partner("missing", {"status": "active"})
        with pytest.raises(PartnerNotFoundError):
            svc.update_partner("ptr_gone", {"status": "active"})  # deleted partners are gone to writers
        with pytest.raises(NoPartnerFieldsError):
            svc.update_partner("ptr_a", {"api_quota": 5})
        with pytest.raises(PartnerValidationError):
            svc.update_partner("ptr_a", {"status": "deleted"})


class TestProvisioning:
    def test_provision_key_creates_gateway_key_binds_plan_and_records_credential(self, env):
        table, svc, apigw, plans = env["table"], env["service"], env["apigw"], env["plans"]
        _seed(table, "ptr_a")

        key = svc.provision_key("ptr_a", "standard")

        assert key["status"] == "active" and key["usage_plan_id"] == plans["standard"]
        assert key["key_value"]  # returned once, to the caller only
        gateway_key = apigw.get_api_key(apiKey=key["key_id"], includeValue=True)
        assert gateway_key["enabled"] and gateway_key["value"] == key["key_value"]
        assert gateway_key["tags"]["partner_id"] == "ptr_a"
        bound = apigw.get_usage_plan_keys(usagePlanId=plans["standard"])["items"]
        assert [k["id"] for k in bound] == [key["key_id"]]
        item = table.get_item(Key={"PK": "PARTNER#ptr_a", "SK": f"KEY#{key['key_id']}"})["Item"]
        assert item["status"] == "active" and item["usage_plan_id"] == plans["standard"]
        assert not any("value" in attr for attr in item)  # never the secret

    def test_provision_key_rejects_unknown_tier_before_touching_gateway(self, env):
        with pytest.raises(PartnerValidationError):
            env["service"].provision_key("ptr_a", "platinum")
        assert env["apigw"].get_api_keys()["items"] == []

    def test_disable_and_enable_keys_round_trip(self, env):
        table, svc, apigw = env["table"], env["service"], env["apigw"]
        _seed(table, "ptr_a")
        k1 = svc.provision_key("ptr_a", "basic")["key_id"]
        k2 = svc.provision_key("ptr_a", "basic")["key_id"]

        disabled = svc.disable_keys("ptr_a", except_key_id=k2)
        assert disabled == [k1]
        assert apigw.get_api_key(apiKey=k1)["enabled"] is False
        assert apigw.get_api_key(apiKey=k2)["enabled"] is True
        k1_item = table.get_item(Key={"PK": "PARTNER#ptr_a", "SK": f"KEY#{k1}"})["Item"]
        assert k1_item["status"] == "disabled" and k1_item["disabled_at"]

        assert svc.disable_keys("ptr_a") == [k2]
        assert sorted(svc.enable_keys("ptr_a")) == sorted([k1, k2])
        assert apigw.get_api_key(apiKey=k1)["enabled"] is True
        assert "disabled_at" not in table.get_item(Key={"PK": "PARTNER#ptr_a", "SK": f"KEY#{k1}"})["Item"]

    def test_delete_keys_removes_plan_binding_gateway_key_and_item(self, env):
        table, svc, apigw, plans = env["table"], env["service"], env["apigw"], env["plans"]
        _seed(table, "ptr_a")
        k1 = svc.provision_key("ptr_a", "premium")["key_id"]
        k2 = svc.provision_key("ptr_a", "premium")["key_id"]

        assert svc.delete_keys("ptr_a", key_ids=[k1]) == [k1]
        assert [k["id"] for k in apigw.get_usage_plan_keys(usagePlanId=plans["premium"])["items"]] == [k2]
        with pytest.raises(apigw.exceptions.NotFoundException):
            apigw.get_api_key(apiKey=k1)
        assert "Item" not in table.get_item(Key={"PK": "PARTNER#ptr_a", "SK": f"KEY#{k1}"})

        # a key already gone from API Gateway is still cleaned from the table
        apigw.delete_api_key(apiKey=k2)
        assert svc.delete_keys("ptr_a") == [k2]
        assert svc.get_partner("ptr_a")["keys"] == []

    def test_list_gateway_keys_for_partner_finds_keys_by_tag(self, env):
        table, svc = env["table"], env["service"]
        _seed(table, "ptr_a")
        _seed(table, "ptr_b")
        mine = svc.provision_key("ptr_a", "basic")["key_id"]
        svc.provision_key("ptr_b", "basic")
        assert [k["id"] for k in svc.list_gateway_keys_for_partner("ptr_a")] == [mine]


def _key_items(table, partner_id):
    from boto3.dynamodb.conditions import Key as _K

    return table.query(KeyConditionExpression=_K("PK").eq(f"PARTNER#{partner_id}") & _K("SK").begins_with("KEY#"))["Items"]


class TestQuotaWindow:
    def test_windows_follow_the_plan_period(self):
        from datetime import datetime, timezone

        from services.partners_service import _quota_window

        at = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)  # a Wednesday
        assert _quota_window("DAY", at) == ("2026-10-07", "2026-10-07")
        assert _quota_window("WEEK", at) == ("2026-10-05", "2026-10-11")
        assert _quota_window("MONTH", at) == ("2026-10-01", "2026-10-31")
        assert _quota_window("MONTH", datetime(2026, 12, 15, tzinfo=timezone.utc)) == ("2026-12-01", "2026-12-31")


class TestLifecycle:
    """Tasks 4.1-4.4: create, enrich, tier/status side effects, delete, rotate, retire, reconcile."""

    def test_create_direct_partner_provisions_first_key_and_returns_value_once(self, env):
        table, svc, apigw = env["table"], env["service"], env["apigw"]
        result = svc.create_partner(
            {"partner_name": "Acme Crawler", "pricing_tier": "standard", "contact_email": "a@acme.test", "allowed_endpoints": ["queryContent"]}
        )
        partner, key = result["partner"], result["key"]
        assert partner["partner_id"].startswith("ptr_acme_crawler_") and partner["identity_source"] == "direct"
        assert partner["active_key_count"] == 1 and partner["status"] == "active"
        assert key["status"] == "active" and key["usage_plan_id"] == env["plans"]["standard"] and "key_value" not in key
        assert result["key_value"] and apigw.get_api_key(apiKey=key["key_id"], includeValue=True)["value"] == result["key_value"]
        assert apigw.get_api_key(apiKey=key["key_id"])["tags"]["partner_id"] == partner["partner_id"]
        stored = svc.get_partner(partner["partner_id"])
        assert [k["key_id"] for k in stored["keys"]] == [key["key_id"]] and "key_value" not in stored

    def test_create_marketplace_partner_has_no_key(self, env):
        svc = env["service"]
        result = svc.create_partner(
            {"partner_name": "Mkt", "pricing_tier": "basic", "identity_source": "marketplace", "marketplace_type": "aws", "marketplace_customer_id": "c1"}
        )
        assert result["key"] is None and result["key_value"] is None
        assert result["partner"]["identity_source"] == "marketplace" and result["partner"]["active_key_count"] == 0

    def test_create_validation(self, env):
        svc = env["service"]
        with pytest.raises(PartnerValidationError, match="missing required"):
            svc.create_partner({"partner_name": "x"})
        with pytest.raises(PartnerValidationError, match="identity_source"):
            svc.create_partner({"partner_name": "x", "pricing_tier": "basic", "identity_source": "saml"})
        with pytest.raises(PartnerValidationError, match="marketplace_type"):
            svc.create_partner({"partner_name": "x", "pricing_tier": "basic", "identity_source": "marketplace"})
        with pytest.raises(PartnerValidationError, match="pricing_tier"):
            svc.create_partner({"partner_name": "x", "pricing_tier": "platinum"})
        assert env["apigw"].get_api_keys()["items"] == []  # nothing provisioned on a rejected create

    def test_get_partner_reports_tier_limits_and_month_usage(self, env, monkeypatch):
        svc, apigw = env["service"], env["apigw"]
        created = svc.create_partner({"partner_name": "U", "pricing_tier": "premium"})
        pid, key_id = created["partner"]["partner_id"], created["key"]["key_id"]

        # moto does not implement GetUsage: the read degrades to usage=None without failing.
        detail = svc.get_partner(pid)
        assert detail["tier_limits"]["quota_limit"] == 1000 and detail["tier_limits"]["quota_period"] == "MONTH"
        assert detail["keys"][0]["usage"] is None

        # With a gateway that answers, per-day [used, remaining] pairs are summed for the month.
        real_get_usage = apigw.get_usage
        monkeypatch.setattr(
            apigw, "get_usage", lambda **kw: {"items": {kw["keyId"]: [[3, 997], [4, 993], []]}, "startDate": kw["startDate"], "endDate": kw["endDate"]}
        )
        usage = svc.get_partner(pid)["keys"][0]["usage"]
        assert usage["used"] == 7 and usage["remaining"] == 993
        assert usage["period_start"].endswith("-01") and usage["period_end"] > usage["period_start"]  # MONTH plan
        monkeypatch.setattr(apigw, "get_usage", real_get_usage)

        # A disabled key reports no usage; the read still succeeds.
        svc.disable_keys(pid)
        assert svc.get_partner(pid)["keys"][0]["usage"] is None

    def test_tier_change_rebinds_every_key(self, env):
        table, svc, apigw, plans = env["table"], env["service"], env["apigw"], env["plans"]
        pid = svc.create_partner({"partner_name": "T", "pricing_tier": "basic"})["partner"]["partner_id"]
        second = svc.provision_key(pid, "basic")
        svc.update_partner(pid, {"pricing_tier": "enterprise"})
        assert {k["usage_plan_id"] for k in _key_items(table, pid)} == {plans["enterprise"]}
        bound = {k["id"] for k in apigw.get_usage_plan_keys(usagePlanId=plans["enterprise"])["items"]}
        assert second["key_id"] in bound and len(bound) == 2
        assert apigw.get_usage_plan_keys(usagePlanId=plans["basic"])["items"] == []

    def test_suspend_disables_keys_and_reactivate_enables_them(self, env):
        table, svc, apigw = env["table"], env["service"], env["apigw"]
        pid = svc.create_partner({"partner_name": "S", "pricing_tier": "basic"})["partner"]["partner_id"]
        key_id = _key_items(table, pid)[0]["api_key_id"]
        suspended = svc.update_partner(pid, {"status": "suspended"})
        assert suspended["active_key_count"] == 0 and apigw.get_api_key(apiKey=key_id)["enabled"] is False
        reactivated = svc.update_partner(pid, {"status": "active"})
        assert reactivated["active_key_count"] == 1 and apigw.get_api_key(apiKey=key_id)["enabled"] is True

    def test_delete_partner_soft_deletes_meta_and_removes_keys(self, env):
        table, svc, apigw = env["table"], env["service"], env["apigw"]
        pid = svc.create_partner({"partner_name": "D", "pricing_tier": "basic"})["partner"]["partner_id"]
        key_id = _key_items(table, pid)[0]["api_key_id"]
        deleted = svc.delete_partner(pid)
        assert deleted["status"] == "deleted" and deleted["active_key_count"] == 0
        assert _key_items(table, pid) == [] and apigw.get_api_keys()["items"] == []
        with pytest.raises(PartnerNotFoundError):
            svc.delete_partner(pid)  # already deleted -> 404
        with pytest.raises(PartnerNotFoundError):
            svc.update_partner(pid, {"partner_name": "nope"})

    def test_rotate_key_disables_previous_and_returns_new_value(self, env):
        table, svc, apigw = env["table"], env["service"], env["apigw"]
        created = svc.create_partner({"partner_name": "R", "pricing_tier": "standard"})
        pid, old_id = created["partner"]["partner_id"], created["key"]["key_id"]
        rotated = svc.rotate_key(pid)
        new_id = rotated["key"]["key_id"]
        assert new_id != old_id and rotated["key_value"] and rotated["key"]["status"] == "active"
        assert apigw.get_api_key(apiKey=old_id)["enabled"] is False and apigw.get_api_key(apiKey=new_id)["enabled"] is True
        statuses = {k["api_key_id"]: k["status"] for k in _key_items(table, pid)}
        assert statuses == {old_id: "disabled", new_id: "active"} and rotated["partner"]["active_key_count"] == 1

    def test_rotate_key_on_suspended_partner_keeps_new_key_disabled(self, env):
        svc, apigw = env["service"], env["apigw"]
        pid = svc.create_partner({"partner_name": "RS", "pricing_tier": "basic"})["partner"]["partner_id"]
        svc.update_partner(pid, {"status": "suspended"})
        rotated = svc.rotate_key(pid)
        assert rotated["key"]["status"] == "disabled" and apigw.get_api_key(apiKey=rotated["key"]["key_id"])["enabled"] is False

    def test_reactivation_does_not_revive_rotated_keys(self, env):
        """suspend -> rotate -> reactivate: only the current key comes back (both orderings)."""
        table, svc, apigw = env["table"], env["service"], env["apigw"]
        # Order A: rotate while active, then suspend / reactivate.
        created = svc.create_partner({"partner_name": "A", "pricing_tier": "basic"})
        pid, old = created["partner"]["partner_id"], created["key"]["key_id"]
        new = svc.rotate_key(pid)["key"]["key_id"]
        svc.update_partner(pid, {"status": "suspended"})
        svc.update_partner(pid, {"status": "active"})
        assert {k["api_key_id"]: k["status"] for k in _key_items(table, pid)} == {old: "disabled", new: "active"}
        assert apigw.get_api_key(apiKey=old)["enabled"] is False
        # Order B: suspend first (old parked), rotate, then reactivate.
        created = svc.create_partner({"partner_name": "B", "pricing_tier": "basic"})
        pid, old = created["partner"]["partner_id"], created["key"]["key_id"]
        svc.update_partner(pid, {"status": "suspended"})
        new = svc.rotate_key(pid)["key"]["key_id"]
        svc.update_partner(pid, {"status": "active"})
        assert {k["api_key_id"]: k["status"] for k in _key_items(table, pid)} == {old: "disabled", new: "active"}
        assert apigw.get_api_key(apiKey=old)["enabled"] is False and apigw.get_api_key(apiKey=new)["enabled"] is True

    def test_rotate_key_conflicts(self, env):
        table, svc = env["table"], env["service"]
        mkt = svc.create_partner({"partner_name": "M", "pricing_tier": "basic", "identity_source": "marketplace", "marketplace_type": "aws"})
        with pytest.raises(PartnerConflictError, match="marketplace"):
            svc.rotate_key(mkt["partner"]["partner_id"])
        pid = svc.create_partner({"partner_name": "G", "pricing_tier": "basic"})["partner"]["partner_id"]
        svc.delete_partner(pid)
        with pytest.raises(PartnerConflictError, match="deleted"):
            svc.rotate_key(pid)
        with pytest.raises(PartnerNotFoundError):
            svc.rotate_key("ptr_missing")

    def test_retire_key_refuses_only_active_key_then_allows_after_rotate(self, env):
        table, svc, apigw = env["table"], env["service"], env["apigw"]
        created = svc.create_partner({"partner_name": "K", "pricing_tier": "basic"})
        pid, first = created["partner"]["partner_id"], created["key"]["key_id"]
        with pytest.raises(PartnerConflictError, match="only active key"):
            svc.retire_key(pid, first)
        svc.rotate_key(pid)  # first is now disabled; retiring it is allowed
        svc.retire_key(pid, first)
        assert first not in {k["api_key_id"] for k in _key_items(table, pid)}
        assert first not in {k["id"] for k in apigw.get_api_keys()["items"]}
        with pytest.raises(PartnerNotFoundError):
            svc.retire_key(pid, first)  # unknown key -> 404

    def test_reconcile_deletes_orphaned_gateway_keys_only(self, env):
        table, svc, apigw, plans = env["table"], env["service"], env["apigw"], env["plans"]
        created = svc.create_partner({"partner_name": "O", "pricing_tier": "basic"})
        pid, recorded = created["partner"]["partner_id"], created["key"]["key_id"]
        # Simulate a provision that died between create_api_key/plan binding and the KEY# write.
        orphan = apigw.create_api_key(name="orphan", enabled=True, tags={"partner_id": pid})["id"]
        apigw.create_usage_plan_key(usagePlanId=plans["basic"], keyId=orphan, keyType="API_KEY")
        other = apigw.create_api_key(name="other", enabled=True, tags={"partner_id": "someone_else"})["id"]
        result = svc.reconcile_keys(pid)
        assert result == {"orphaned_deleted": [orphan], "recorded": [recorded]}
        remaining = {k["id"] for k in apigw.get_api_keys()["items"]}
        assert remaining == {recorded, other}
