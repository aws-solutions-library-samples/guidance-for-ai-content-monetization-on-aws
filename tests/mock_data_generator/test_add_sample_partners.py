"""Sample-partner seed (spec: native-apikey-auth-migration, task 7.1 / Q7).

Runs the seed against moto DynamoDB and API Gateway: META items on the partner-keyed shape, keys
provisioned through the same service primitive the lifecycle API uses, idempotent re-runs, and a
clean re-provision with ``--clear-existing``.
"""

import importlib.util
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-west-2"
TABLE = "cm-partners-seed-test"


def _load_seed():
    spec = importlib.util.spec_from_file_location(
        "seed_sample_partners", ROOT / "deployment/data/add-sample-partners.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        session = boto3.Session(region_name=REGION)
        table = session.resource("dynamodb").create_table(
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
        apigw = session.client("apigateway")
        plans = {
            tier: apigw.create_usage_plan(name=f"cm-partner-{tier}", quota={"limit": 10, "period": "DAY"})["id"]
            for tier in ("basic", "standard", "premium", "enterprise")
        }
        test_key = apigw.create_api_key(name="cm-test-partner", enabled=True)
        apigw.create_usage_plan_key(usagePlanId=plans["standard"], keyId=test_key["id"], keyType="API_KEY")
        from services import partners_service

        monkeypatch.setattr(partners_service, "_usage_plan_cache", {})
        monkeypatch.setattr(partners_service, "USAGE_PLAN_NAME_PREFIX", "cm-partner-")
        yield {"session": session, "table": table, "apigw": apigw, "plans": plans, "test_key_id": test_key["id"]}


def _seed(env, seed, **kwargs):
    return seed.seed_partners(
        seed.create_sample_partners(),
        table_name=TABLE,
        test_key_id=env["test_key_id"],
        region=REGION,
        session=env["session"],
        **kwargs,
    )


def _key_items(env):
    return [i for i in env["table"].scan()["Items"] if i["SK"].startswith("KEY#")]


class TestSeed:
    def test_first_run_writes_meta_and_provisions_one_key_per_partner(self, env):
        seed = _load_seed()
        rows = _seed(env, seed)
        items = env["table"].scan()["Items"]
        metas = [i for i in items if i["SK"] == "META"]
        assert len(metas) == len(rows) == 6
        assert len(_key_items(env)) == 6
        assert all("partner_key_hash" not in i and "api_quota" not in i for i in items)
        # Every non-test partner got a freshly provisioned key whose value is shown exactly once.
        for row in rows:
            if row["partner_id"] != seed.TEST_PARTNER_ID:
                assert row["key_value"] and not row["key_value"].startswith("(")
        assert not any("key_value" in i or "value" in i for i in _key_items(env))

    def test_test_partner_uses_the_stack_key(self, env):
        seed = _load_seed()
        rows = _seed(env, seed)
        row = next(r for r in rows if r["partner_id"] == seed.TEST_PARTNER_ID)
        assert row["key_id"] == env["test_key_id"]
        assert "get-api-key --include-value" in row["key_value"]
        item = env["table"].get_item(Key={"PK": f"PARTNER#{seed.TEST_PARTNER_ID}", "SK": f"KEY#{env['test_key_id']}"})[
            "Item"
        ]
        assert item["usage_plan_id"] == env["plans"]["standard"] and item["status"] == "active"
        assert len(env["apigw"].get_api_keys()["items"]) == 1 + 5  # stack key + five provisioned

    def test_keys_bound_to_tier_plans_and_suspended_partner_key_disabled(self, env):
        seed = _load_seed()
        _seed(env, seed)
        apigw = env["apigw"]
        for tier, plan_id in env["plans"].items():
            bound = {k["id"] for k in apigw.get_usage_plan_keys(usagePlanId=plan_id)["items"]}
            expected = {i["api_key_id"] for i in _key_items(env) if i["usage_plan_id"] == plan_id}
            assert bound == expected, tier
        suspended = env["table"].get_item(Key={"PK": "PARTNER#suspended_005", "SK": "META"})["Item"]
        assert suspended["status"] == "suspended"
        key = next(i for i in _key_items(env) if i["partner_id"] == "suspended_005")
        assert key["status"] == "disabled"
        assert apigw.get_api_key(apiKey=key["api_key_id"])["enabled"] is False

    def test_rerun_is_idempotent(self, env):
        seed = _load_seed()
        first = _seed(env, seed)
        second = _seed(env, seed)
        assert [r["key_id"] for r in first] == [r["key_id"] for r in second]
        assert len(env["apigw"].get_api_keys()["items"]) == 6
        assert all(r["key_value"].startswith("(") for r in second)  # values are never re-shown

    def test_clear_existing_reprovisions(self, env):
        seed = _load_seed()
        first = _seed(env, seed)
        second = _seed(env, seed, clear_existing=True)
        old = {r["key_id"] for r in first if r["partner_id"] != seed.TEST_PARTNER_ID}
        new = {r["key_id"] for r in second if r["partner_id"] != seed.TEST_PARTNER_ID}
        assert old.isdisjoint(new)
        live = {k["id"] for k in env["apigw"].get_api_keys()["items"]}
        assert live == new | {env["test_key_id"]}
        assert len(_key_items(env)) == 6


class TestRegionPinning:
    """``PartnersService`` builds its boto3 clients lazily with the ambient region; the CLI must
    pin ``AWS_DEFAULT_REGION`` from ``--region`` so the seed targets the right account even when the
    shell set no region (regression: seed hit the wrong region's tables with ResourceNotFound)."""

    def test_main_pins_default_region_from_flag_when_unset(self, monkeypatch):
        seed = _load_seed()
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.setattr(seed.sys, "argv", ["add-sample-partners.py", "--region", "ap-southeast-2"])
        # Fail fast right after the region is pinned so the test needs no live AWS.
        sentinel = RuntimeError("stop after region pin")

        def _boom(*_a, **_k):
            raise sentinel

        monkeypatch.setattr(seed.boto3, "Session", _boom)
        rc = seed.main()
        assert rc == 1  # the CLI boundary caught the sentinel
        assert seed.os.environ["AWS_DEFAULT_REGION"] == "ap-southeast-2"

    def test_main_does_not_override_an_explicit_default_region(self, monkeypatch):
        seed = _load_seed()
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        monkeypatch.setattr(seed.sys, "argv", ["add-sample-partners.py", "--region", "ap-southeast-2"])
        monkeypatch.setattr(seed.boto3, "Session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop")))
        seed.main()
        assert seed.os.environ["AWS_DEFAULT_REGION"] == "eu-west-1"  # setdefault leaves it untouched
