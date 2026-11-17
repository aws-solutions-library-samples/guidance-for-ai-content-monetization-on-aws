"""CDK assertions for the explicit OpenSearch projection backfill."""

import json

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infrastructure.pipeline_stack import PipelineStack


def _template():
    stack = _stack()
    return Template.from_stack(stack)


def _stack():
    return PipelineStack(
        App(),
        "SearchProjectionBackfillStack",
        content_table_name="cm-content",
        content_bucket_name="iab-llm-content-000000000000-us-west-2",
        env=Environment(account="000000000000", region="us-west-2"),
    )


def test_backfill_lambda_is_explicit_and_has_required_environment():
    template = _template()
    functions = template.find_resources("AWS::Lambda::Function")
    backfills = [
        properties["Properties"]
        for properties in functions.values()
        if properties["Properties"].get("Description") == "Explicit migration for rebuilding the OpenSearch content projection"
    ]

    assert len(backfills) == 1
    backfill = backfills[0]
    assert backfill["Handler"] == "handler.lambda_handler"
    assert backfill["Timeout"] == 900
    env_vars = backfill["Environment"]["Variables"]
    assert env_vars["CONTENT_TABLE_NAME"] == "cm-content"
    assert env_vars["CONTENT_BUCKET_NAME"] == "iab-llm-content-000000000000-us-west-2"
    # The collection moved to a nested stack; the endpoint is resolved from SSM
    # by name at runtime, NOT passed as an env var (no cross-boundary reference).
    assert "OPENSEARCH_ENDPOINT" not in env_vars

    template.has_output("SearchProjectionBackfillLambdaName", Match.any_value())


def test_backfill_is_not_an_api_event_source_or_schedule():
    template = _template()
    template.resource_count_is("AWS::Lambda::EventSourceMapping", 0)
    for properties in template.find_resources("AWS::Events::Rule").values():
        assert "projection backfill" not in json.dumps(properties).lower()


def test_backfill_role_has_fixed_name_and_ssm_grant():
    # The backfill role carries a fixed region-suffixed name so its ARN composes
    # as a plain string for the top-level search stack's data-access policy (zero
    # exports), and it is granted ssm:GetParameter to resolve the endpoint by name.
    template = _template()
    rendered = json.dumps(template.to_json())
    assert "iab-llm-geo-backfill-role-us-west-2" in rendered
    assert "ssm:GetParameter" in rendered


def _search_stack():
    # The collection now lives in a free-standing top-level SearchCollectionStack
    # (not nested in / owned by PipelineStack). Build it standalone with the same
    # fixed-name writer/reader role ARN strings app.py composes.
    from infrastructure.search_collection_stack import SearchCollectionStack

    acct, reg = "000000000000", "us-west-2"
    return SearchCollectionStack(
        App(),
        "CMSearchStack",
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
        env=Environment(account=acct, region=reg),
    )


def test_backfill_role_and_opensearch_access_are_narrowed_to_migration_needs():
    template = _template()
    rendered = json.dumps(template.to_json())

    assert "SearchProjectionBackfillRole" in rendered
    assert "dynamodb:Scan" in rendered
    assert "content/*" in rendered
    assert "embeddings/*" in rendered
    # The aoss:APIAccessAll grant is on the backfill role (pipeline template),
    # scoped to a wildcard collection ARN (no cross-stack id reference).
    assert "aoss:APIAccessAll" in rendered
    assert "SearchProjectionBackfillLambdaName" in rendered

    # The AOSS data-access policy now lives in the top-level search stack. The
    # shared Partner API principal is discovery-only; index management and
    # writes remain in the pipeline/backfill principal block.
    search = Template.from_stack(_search_stack())
    policy = next(iter(search.find_resources("AWS::OpenSearchServerless::AccessPolicy").values()))[
        "Properties"
    ]["Policy"]
    # The policy is composed from plain ARN strings (writer/reader role ARNs
    # passed in), so it is a plain JSON string, not an Fn::Join over cross-stack
    # tokens.
    policy_document = json.loads(policy) if isinstance(policy, str) else json.loads(
        "".join(part if isinstance(part, str) else "__principal__" for part in policy["Fn::Join"][1])
    )
    shared_principal_rules = policy_document[1]["Rules"]
    shared_permissions = shared_principal_rules[1]["Permission"]

    assert shared_permissions == ["aoss:DescribeIndex", "aoss:ReadDocument"]
