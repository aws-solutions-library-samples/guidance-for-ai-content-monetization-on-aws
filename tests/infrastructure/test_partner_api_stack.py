"""Stack-level assertions for the partner API plane (spec: native-apikey-auth-migration, task 6.3/6.4).

Usage plans per pricing tier, the test partner key, the provisioning grant, the outputs, and the
absence of the retired custom authorizer and Secrets Manager test secret.
"""

import json

import pytest
from aws_cdk import App
from aws_cdk.assertions import Match, Template

from infrastructure.content_monetization_stack import ContentMonetizationStack

TIERS = ("basic", "standard", "premium", "enterprise")


@pytest.fixture(scope="module")
def template():
    return Template.from_stack(ContentMonetizationStack(App(), "TestStack"))


def _usage_plans(template):
    return {
        res["Properties"]["UsagePlanName"]: res["Properties"]
        for res in template.find_resources("AWS::ApiGateway::UsagePlan").values()
    }


class TestTwoApis:
    def test_two_rest_apis(self, template):
        names = {r["Properties"]["Name"] for r in template.find_resources("AWS::ApiGateway::RestApi").values()}
        assert names == {"AI Content Monetization API", "AI Content Monetization Partner API"}

    def test_partner_api_uses_header_key_source_and_no_authorizer(self, template):
        partner = next(
            r
            for r in template.find_resources("AWS::ApiGateway::RestApi").values()
            if r["Properties"]["Name"] == "AI Content Monetization Partner API"
        )
        body = partner["Properties"]["Body"]
        assert body["x-amazon-apigateway-api-key-source"] == "HEADER"
        assert "x-amazon-apigateway-authorizer" not in json.dumps(body)
        template.resource_count_is("AWS::ApiGateway::Authorizer", 0)

    def test_custom_authorizer_lambda_and_role_are_gone(self, template):
        fns = template.find_resources("AWS::Lambda::Function")
        assert not any("authorizer_handler" in r["Properties"].get("Handler", "") for r in fns.values())
        assert "ApiGatewayRole" not in json.dumps(template.to_json())

    def test_stages_carry_the_audience_variable(self, template):
        stages = {
            r["Properties"]["Variables"]["api_audience"]
            for r in template.find_resources("AWS::ApiGateway::Stage").values()
        }
        assert stages == {"internal", "partner"}


class TestUsagePlansAndKeys:
    def test_one_plan_per_tier_plus_itest(self, template):
        plans = _usage_plans(template)
        assert set(plans) == {f"cm-partner-{t}" for t in TIERS} | {"cm-partner-itest-quota"}

    def test_tier_plans_have_daily_quota_and_throttle_and_bind_the_partner_stage(self, template):
        plans = _usage_plans(template)
        expected = {"basic": 1_000, "standard": 10_000, "premium": 100_000, "enterprise": 1_000_000}
        for tier, limit in expected.items():
            plan = plans[f"cm-partner-{tier}"]
            assert plan["Quota"] == {"Limit": limit, "Period": "DAY"}
            assert plan["Throttle"]["RateLimit"] < plan["Throttle"]["BurstLimit"]
            assert len(plan["ApiStages"]) == 1

    def test_itest_plan_is_tiny_and_has_no_key(self, template):
        plans = _usage_plans(template)
        assert plans["cm-partner-itest-quota"]["Quota"] == {"Limit": 3, "Period": "DAY"}
        plan_keys = template.find_resources("AWS::ApiGateway::UsagePlanKey")
        itest_ids = [
            lid
            for lid, r in template.find_resources("AWS::ApiGateway::UsagePlan").items()
            if r["Properties"]["UsagePlanName"] == "cm-partner-itest-quota"
        ]
        assert not any(json.dumps(k["Properties"]["UsagePlanId"]).find(itest_ids[0]) >= 0 for k in plan_keys.values())

    def test_test_partner_key_bound_to_standard_only(self, template):
        template.resource_count_is("AWS::ApiGateway::ApiKey", 1)
        template.resource_count_is("AWS::ApiGateway::UsagePlanKey", 1)
        key = next(iter(template.find_resources("AWS::ApiGateway::UsagePlanKey").values()))
        standard_id = next(
            lid
            for lid, r in template.find_resources("AWS::ApiGateway::UsagePlan").items()
            if r["Properties"]["UsagePlanName"] == "cm-partner-standard"
        )
        assert standard_id in json.dumps(key["Properties"]["UsagePlanId"])

    def test_no_secrets_manager_test_key(self, template):
        dumped = json.dumps(template.to_json())
        assert "iab-llm-test-partner-key" not in dumped
        assert "TestPartnerApiKeySecret" not in dumped


class TestProvisioningWiring:
    def test_ui_backend_resolves_plans_by_name(self, template):
        # Plan ids cannot be exported into the Lambda (plan -> stage -> API -> Lambda cycle); the
        # name prefix is the CDK/service contract and the plan names must match it.
        ui = next(
            r
            for r in template.find_resources("AWS::Lambda::Function").values()
            if "UI Backend" in r["Properties"].get("Description", "")
        )
        env = ui["Properties"]["Environment"]["Variables"]
        assert env["USAGE_PLAN_NAME_PREFIX"] == "cm-partner-"
        assert not any(k.startswith("USAGE_PLAN_ID_") for k in env)
        for tier in TIERS:
            assert f"{env['USAGE_PLAN_NAME_PREFIX']}{tier}" in _usage_plans(template)

    def test_apigateway_grant_is_scoped_to_keys_and_plan_bindings(self, template):
        dumped = json.dumps(template.to_json())
        statements = [
            st
            for pol in template.find_resources("AWS::IAM::Policy").values()
            for st in pol["Properties"]["PolicyDocument"]["Statement"]
            if any(
                str(a).startswith("apigateway:")
                for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])
            )
        ]
        assert statements, "expected an apigateway grant for key provisioning"
        seen_tags_put = seen_usage_get = False
        for st in statements:
            actions = set(st["Action"]) if isinstance(st["Action"], list) else {st["Action"]}
            resources = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
            for res in resources:
                text = json.dumps(res)
                assert "/apikeys" in text or "/usageplans" in text or "/tags/" in text, res
                assert "::/restapis" not in text and text != '"*"'
                if "/tags/" in text:
                    # create_api_key(tags=...) is authorized as PUT on the tags resource, nothing else.
                    assert actions == {"apigateway:PUT"}, "tagging grant must be PUT-only"
                    seen_tags_put = True
                elif "/usageplans" in text and "/keys" not in text:
                    assert actions == {"apigateway:GET"}, "plans themselves are read-only to the Lambda"
                    if "/usage" in text:
                        seen_usage_get = True
        assert seen_tags_put, "key provisioning needs apigateway:PUT on /tags/* to tag keys by partner"
        assert seen_usage_get, "partner detail needs apigateway:GET on /usageplans/*/usage"
        assert "apigateway:*" not in dumped


class TestPartnerUrlFact:
    def test_partner_url_published_to_parameter_store_not_lambda_env(self, template):
        params = template.find_resources("AWS::SSM::Parameter")
        assert any(r["Properties"]["Name"] == "/cm/partner-api/url" for r in params.values())
        for fn in template.find_resources("AWS::Lambda::Function").values():
            env = fn["Properties"].get("Environment", {}).get("Variables", {})
            assert "PARTNER_API_URL" not in env  # would be a plan/API -> Lambda dependency cycle
        worker = next(
            r
            for r in template.find_resources("AWS::Lambda::Function").values()
            if "llms.txt" in r["Properties"].get("Description", "")
        )
        assert worker["Properties"]["Environment"]["Variables"]["PARTNER_API_URL_PARAMETER"] == "/cm/partner-api/url"


class TestOutputs:
    def test_partner_api_outputs_and_no_secret_value(self, template):
        outputs = template.to_json()["Outputs"]
        for name in ("PartnerApiUrl", "PartnerApiId", "TestPartnerApiKeyId"):
            assert name in outputs
        assert not any(n.startswith("TestPartnerApiKeySecret") for n in outputs)
        assert "APIKeyValue" not in json.dumps(outputs)
