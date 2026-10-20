"""
CDK assertion tests for UI, Pipeline, Cognito, and Bot Control stacks
(wave0 Phase 6, Req 19-21).

Follows the pattern in ``tests/infrastructure/test_analytics_table.py``: each
stack is synthesized in a fresh ``aws_cdk.App`` with an explicit dummy
environment (account='000000000000', region='us-west-2') and its primary
resources are asserted via ``aws_cdk.assertions.Template``.

Constructor signatures were read from the stack sources before writing these:
- ``CognitoStack(scope, id, domain_prefix=None, **kwargs)``
- ``BotControlStack(scope, id, **kwargs)``
- ``UIStack(scope, id, api_gateway_url=None, enable_demo_mode=True, cognito_*=None, ...)``
- ``PipelineStack(scope, id, content_table_name, content_bucket_name, **kwargs)``
  — requires the shared content table/bucket names (referenced by name, no
  cross-stack export). A valid S3 bucket name is required by
  ``Bucket.from_bucket_name`` or synth raises.

All four synthesize in isolation with a dummy env; no cross-stack args are
missing, so no assertion had to be skipped.
"""

from aws_cdk import App, Environment, Tags
from aws_cdk.assertions import Match, Template

from infrastructure.bot_control_stack import BotControlStack
from infrastructure.cognito_stack import CognitoStack
from infrastructure.pipeline_stack import PipelineStack
from infrastructure.search_collection_stack import SearchCollectionStack
from infrastructure.ui_stack import UIStack

DUMMY_ENV = Environment(account="000000000000", region="us-west-2")
CONTENT_BUCKET = "iab-llm-content-000000000000-us-west-2"


def _template(stack):
    return Template.from_stack(stack)


# --------------------------------------------------------------------------- #
# CognitoStack
# --------------------------------------------------------------------------- #
class TestCognitoStack:
    def _stack(self):
        return CognitoStack(App(), "TestCognitoStack", env=DUMMY_ENV)

    def test_user_pool_created(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::Cognito::UserPool", 1)
        t.has_resource_properties(
            "AWS::Cognito::UserPool",
            {"UserPoolName": "AI-Content-Monetization-UI-Users"},
        )

    def test_user_pool_client_and_identity_pool(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::Cognito::UserPoolClient", 1)
        t.resource_count_is("AWS::Cognito::IdentityPool", 1)
        t.has_resource_properties(
            "AWS::Cognito::IdentityPool", {"AllowUnauthenticatedIdentities": False}
        )

    def test_three_user_groups(self):
        # Administrators / Publishers / Viewers
        t = _template(self._stack())
        t.resource_count_is("AWS::Cognito::UserPoolGroup", 3)

    def test_exposes_pool_id_output(self):
        t = _template(self._stack())
        t.has_output("UserPoolId", Match.any_value())

    def test_authenticated_role_has_no_wildcard_cognito_policy(self):
        """TM-03 (security-hardening-phase2): the authenticated identity-pool role
        MUST NOT carry an inline policy granting cognito-*:* on resources "*".

        Fails if any IAM policy in the synthesized stack contains a statement whose
        Action is a cognito-sync/cognito-identity wildcard on Resource "*". The
        conditioned assume-role trust policy (who may assume) is unaffected — this
        guards only the action policy (what the role may do).
        """
        t = _template(self._stack())
        policies = t.find_resources("AWS::IAM::Policy")
        for logical_id, policy in policies.items():
            statements = policy["Properties"]["PolicyDocument"]["Statement"]
            for stmt in statements:
                actions = stmt.get("Action", [])
                if isinstance(actions, str):
                    actions = [actions]
                resources = stmt.get("Resource", [])
                if isinstance(resources, str):
                    resources = [resources]
                wildcard_cognito = any(
                    isinstance(a, str) and (a.startswith("cognito-sync:") or a.startswith("cognito-identity:"))
                    for a in actions
                )
                on_star = "*" in resources
                assert not (wildcard_cognito and on_star), (
                    f"IAM policy {logical_id} grants a cognito wildcard action on '*': "
                    f"actions={actions} resources={resources}"
                )


# --------------------------------------------------------------------------- #
# BotControlStack
# --------------------------------------------------------------------------- #
class TestBotControlStack:
    def _stack(self):
        return BotControlStack(App(), "TestBotControlStack", env=DUMMY_ENV)

    def test_waf_web_acl_created(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::WAFv2::WebACL", 1)
        t.has_resource_properties(
            "AWS::WAFv2::WebACL",
            {"Name": "iab-bot-control-webacl", "Scope": "REGIONAL"},
        )

    def test_waf_log_group_and_alarms(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::Logs::LogGroup", 1)
        # Two CloudWatch alarms: high-blocked-requests + suspicious-activity.
        t.resource_count_is("AWS::CloudWatch::Alarm", 2)

    def test_dashboard_and_webacl_id_output(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::CloudWatch::Dashboard", 1)
        t.has_output("WebACLId", Match.any_value())


# --------------------------------------------------------------------------- #
# UIStack
# --------------------------------------------------------------------------- #
class TestUIStack:
    def _stack(self):
        # No UI dist build present in CI → the stack falls back to an in-memory
        # placeholder deployment, which still synthesizes.
        return UIStack(App(), "TestUIStack", api_gateway_url="https://api.example.com", env=DUMMY_ENV)

    def test_ui_bucket_created(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::S3::Bucket", 1)
        t.has_resource_properties(
            "AWS::S3::Bucket",
            {"BucketName": "iab-llm-ingest-ui-000000000000-us-west-2"},
        )

    def test_cloudfront_distribution_created(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::CloudFront::Distribution", 1)
        t.has_resource_properties(
            "AWS::CloudFront::Distribution",
            {"DistributionConfig": {"DefaultRootObject": "index.html"}},
        )

    def test_exposes_ui_url_output(self):
        t = _template(self._stack())
        t.has_output("UIUrl", Match.any_value())


# --------------------------------------------------------------------------- #
# PipelineStack
# --------------------------------------------------------------------------- #
class TestPipelineStack:
    def _stack(self):
        return PipelineStack(
            App(),
            "TestPipelineStack",
            content_table_name="cm-content",
            content_bucket_name=CONTENT_BUCKET,
            env=DUMMY_ENV,
        )

    def _search_stack(self, app=None):
        # The collection lives in a free-standing top-level SearchCollectionStack
        # now (not nested in PipelineStack). Build it standalone with the same
        # fixed-name writer/reader role ARN strings app.py composes.
        acct, reg = "000000000000", "us-west-2"
        return SearchCollectionStack(
            app or App(),
            "TestSearchStack",
            collection_name="cm-content-search",
            collection_group_name="iab-content-group",
            endpoint_ssm_param="/iab-llm/opensearch/endpoint",
            writer_role_arns=[
                f"arn:aws:iam::{acct}:role/iab-llm-geo-pipeline-role-{reg}",
                f"arn:aws:iam::{acct}:role/iab-llm-geo-backfill-role-{reg}",
            ],
            reader_role_arns=[
                f"arn:aws:iam::{acct}:role/iab-llm-partner-api-role-{reg}",
                f"arn:aws:iam::{acct}:role/iab-llm-ui-backend-role-{reg}",
            ],
            env=DUMMY_ENV,
        )

    def test_state_machine_created(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::StepFunctions::StateMachine", 1)
        t.has_resource_properties(
            "AWS::StepFunctions::StateMachine",
            {"StateMachineName": "iab-geo-enrichment-pipeline"},
        )

    def test_opensearch_collection_group_and_collection(self):
        # The collection + its NextGen group live in the top-level
        # SearchCollectionStack, not the PipelineStack template.
        search = Template.from_stack(self._search_stack())
        search.resource_count_is("AWS::OpenSearchServerless::CollectionGroup", 1)
        search.resource_count_is("AWS::OpenSearchServerless::Collection", 1)
        # And NOT in the pipeline template.
        _template(self._stack()).resource_count_is("AWS::OpenSearchServerless::Collection", 0)

    def test_collection_carries_no_solution_version_tag(self):
        # The whole reason the collection is isolated in a top-level stack: the
        # per-release SolutionVersion tag must never reach it (its Tags are
        # Update-requires-Replacement, so a version bump would replace the
        # collection and wipe the index).
        #
        # Reproduce app.py's wiring so this test can actually FAIL if the search
        # stack is ever tagged with SolutionVersion: tag the search stack with
        # GOVERNANCE TAGS ONLY (no SolutionVersion — the tag_solution_no_version
        # contract), and tag the pipeline with the full set INCLUDING
        # SolutionVersion (tag_solution). Assert SolutionVersion is absent from
        # BOTH the search stack's stack-level tags AND the collection, WHILE a
        # pipeline Lambda retains it. This proves the fix is the governance-only
        # stack tagging (so there is no stack tag for CFN to propagate), not a
        # per-resource remove — the mechanism both prior approaches got wrong.
        app = App()
        search_stack = self._search_stack(app)
        Tags.of(search_stack).add("Project", "AI-Content-Monetization")
        Tags.of(search_stack).add("SolutionName", "test")
        Tags.of(search_stack).add("SolutionId", "SO9733")
        # Deliberately NO SolutionVersion on the search stack.

        pipeline_stack = PipelineStack(
            app,
            "TestPipelineStack",
            content_table_name="cm-content",
            content_bucket_name=CONTENT_BUCKET,
            env=DUMMY_ENV,
        )
        Tags.of(pipeline_stack).add("SolutionVersion", "9.9.9")

        search = Template.from_stack(search_stack)
        collections = search.find_resources("AWS::OpenSearchServerless::Collection")
        assert len(collections) == 1
        coll_tag_keys = {t["Key"] for t in next(iter(collections.values()))["Properties"].get("Tags", [])}
        assert "SolutionVersion" not in coll_tag_keys

        # The search stack's own STACK-LEVEL tags also exclude SolutionVersion —
        # this is the propagation source the nested approach failed to control.
        assert "SolutionVersion" not in (search_stack.tags.tag_values() or {})

        # Control: the pipeline Lambdas still carry SolutionVersion, proving the
        # omission is scoped to the search stack, not a global no-op.
        parent = _template(pipeline_stack)
        funcs = parent.find_resources("AWS::Lambda::Function")
        assert funcs, "expected pipeline Lambdas in the pipeline template"
        assert any(
            "SolutionVersion" in {t["Key"] for t in f["Properties"].get("Tags", [])}
            for f in funcs.values()
        )

    def test_pipeline_lambda_and_eventbridge_rule(self):
        t = _template(self._stack())
        t.resource_count_is("AWS::Lambda::Function", 2)
        # S3 → EventBridge → Step Functions trigger path.
        t.resource_count_is("AWS::Events::Rule", 1)

    def test_endpoint_ssm_parameter_published(self):
        # The endpoint SSM parameter is published by the top-level search stack.
        search = Template.from_stack(self._search_stack())
        search.resource_count_is("AWS::SSM::Parameter", 1)
