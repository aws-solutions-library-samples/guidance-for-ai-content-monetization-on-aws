"""CDK assertions for the content delete: deindexer, rule, DLQ, alarm and IAM scope.

A content DELETE marks the record "deleting" and emits content.deleted. The pipeline stack's
deindexer completes it. One DLQ catches both kinds of failure, and the dashboard backend's only
new grant is events:PutEvents on the default bus.
"""

import ast
import json
from pathlib import Path

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from infrastructure.pipeline_stack import PipelineStack

ACCOUNT, REGION = "000000000000", "us-west-2"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEINDEXER_DESCRIPTION = "Completes a content delete: search document, objects, jobs, then the record"


@pytest.fixture(scope="module")
def pipeline():
    stack = PipelineStack(
        App(),
        "ContentDeindexerTestStack",
        content_table_name="cm-content",
        content_bucket_name=f"iab-llm-content-{ACCOUNT}-{REGION}",
        jobs_table_name="cm-jobs",
        env=Environment(account=ACCOUNT, region=REGION),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def main_stack():
    from infrastructure.content_monetization_stack import ContentMonetizationStack

    return Template.from_stack(ContentMonetizationStack(App(), "ContentDeleteMainTestStack"))


def _one(template, resource_type, predicate=lambda props: True):
    matches = {
        lid: res for lid, res in template.find_resources(resource_type).items() if predicate(res.get("Properties", {}))
    }
    assert len(matches) == 1, f"expected one {resource_type}, found {sorted(matches)}"
    return next(iter(matches.items()))


def _deindexer(template):
    return _one(template, "AWS::Lambda::Function", lambda p: p.get("Description") == DEINDEXER_DESCRIPTION)


def _dlq(template):
    return _one(template, "AWS::SQS::Queue", lambda p: p.get("QueueName") == "iab-content-deindex-dlq")


def _statements_for_role(template, role_logical_id):
    statements = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        refs = [r.get("Ref") for r in policy["Properties"].get("Roles", []) if isinstance(r, dict)]
        if role_logical_id in refs:
            statements.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _actions(statement):
    action = statement.get("Action", [])
    return [action] if isinstance(action, str) else list(action)


def test_deindexer_function_has_its_environment(pipeline):
    _, fn = _deindexer(pipeline)
    props = fn["Properties"]
    assert props["Handler"] == "handler.lambda_handler"
    env_vars = props["Environment"]["Variables"]
    assert env_vars["CONTENT_TABLE_NAME"] == "cm-content"
    assert env_vars["JOBS_TABLE_NAME"] == "cm-jobs"
    assert env_vars["CONTENT_BUCKET_NAME"] == f"iab-llm-content-{ACCOUNT}-{REGION}"
    assert env_vars["OPENSEARCH_INDEX"]
    # The endpoint is resolved from SSM by name at runtime, like the other writers.
    assert "OPENSEARCH_ENDPOINT" not in env_vars


def test_rule_matches_the_shared_event_contract_and_dead_letters_to_the_dlq(pipeline):
    from shared.content_delete import CONTENT_DELETED_DETAIL_TYPE, CONTENT_EVENT_SOURCE

    fn_id, _ = _deindexer(pipeline)
    dlq_id, _ = _dlq(pipeline)
    _, rule = _one(
        pipeline,
        "AWS::Events::Rule",
        lambda p: p.get("EventPattern", {}).get("source") == [CONTENT_EVENT_SOURCE],
    )
    props = rule["Properties"]
    assert props["EventPattern"] == {"source": ["cm.content"], "detail-type": ["content.deleted"]}
    assert CONTENT_DELETED_DETAIL_TYPE == "content.deleted"
    assert "EventBusName" not in props  # the default bus
    (target,) = props["Targets"]
    assert target["Arn"] == {"Fn::GetAtt": [fn_id, "Arn"]}
    assert target["DeadLetterConfig"] == {"Arn": {"Fn::GetAtt": [dlq_id, "Arn"]}}
    assert target["RetryPolicy"] == {"MaximumRetryAttempts": 3, "MaximumEventAgeInSeconds": 3600}


def test_failed_runs_go_to_the_same_dlq(pipeline):
    """The rule's DLQ sees only delivery failures; the function's on-failure destination sees runs."""
    fn_id, _ = _deindexer(pipeline)
    dlq_id, _ = _dlq(pipeline)
    _, config = _one(pipeline, "AWS::Lambda::EventInvokeConfig", lambda p: p.get("FunctionName") == {"Ref": fn_id})
    props = config["Properties"]
    assert props["MaximumRetryAttempts"] == 2
    assert props["DestinationConfig"]["OnFailure"]["Destination"] == {"Fn::GetAtt": [dlq_id, "Arn"]}


def test_dlq_alarm_fires_on_any_message(pipeline):
    dlq_id, dlq = _dlq(pipeline)
    assert dlq["Properties"]["MessageRetentionPeriod"] == 14 * 24 * 3600
    _, alarm = _one(
        pipeline,
        "AWS::CloudWatch::Alarm",
        lambda p: p.get("AlarmName") == f"iab-content-deindex-dlq-not-empty-{REGION}",
    )
    props = alarm["Properties"]
    assert props["MetricName"] == "ApproximateNumberOfMessagesVisible"
    assert props["Dimensions"] == [{"Name": "QueueName", "Value": {"Fn::GetAtt": [dlq_id, "QueueName"]}}]
    assert props["Threshold"] == 0
    assert props["ComparisonOperator"] == "GreaterThanThreshold"
    assert props["TreatMissingData"] == "notBreaching"


def test_deindexer_role_is_named_and_scoped_to_what_it_deletes(pipeline):
    fn_id, fn = _deindexer(pipeline)
    role_id = fn["Properties"]["Role"]["Fn::GetAtt"][0]
    role = pipeline.find_resources("AWS::IAM::Role")[role_id]["Properties"]
    assert role["RoleName"] == f"iab-llm-content-deindexer-role-{REGION}"

    statements = _statements_for_role(pipeline, role_id)
    actions = sorted({a for s in statements for a in _actions(s)})
    assert actions == [
        "aoss:APIAccessAll",
        "dynamodb:DeleteItem",
        "dynamodb:GetItem",
        "dynamodb:Scan",
        "s3:DeleteObject",
        "s3:GetObject",
        "sqs:GetQueueAttributes",
        "sqs:GetQueueUrl",
        "sqs:SendMessage",
        "ssm:GetParameter",
    ]
    rendered = {a: json.dumps(s["Resource"]) for s in statements for a in _actions(s)}
    assert "*" != rendered["s3:DeleteObject"]
    # "/comparison/*": the comparison partition's Markdown (scrape-vs-api-comparison R15, task 8.6a).
    for prefix in ("/content/*", "/comparison/*", "/ingest/*", "/embeddings/*", "/pipeline/*"):
        assert prefix in rendered["s3:DeleteObject"]
    # Read only the two ownership-checked prefixes, and never list the bucket.
    assert "/content/*" in rendered["s3:GetObject"] and "/ingest/*" in rendered["s3:GetObject"]
    # Without GetObject on comparison/* the ownership HEAD is a 403, which the deindexer reads as
    # "already gone", and the partition Markdown would be silently orphaned.
    assert "/comparison/*" in rendered["s3:GetObject"]
    assert "/embeddings/*" not in rendered["s3:GetObject"] and "/pipeline/*" not in rendered["s3:GetObject"]
    assert "s3:ListBucket" not in actions
    assert "table/cm-content" in rendered["dynamodb:GetItem"]
    assert "table/cm-jobs" in rendered["dynamodb:Scan"]
    for statement in statements:
        assert statement["Resource"] != "*", f"unscoped statement: {statement}"


def test_deindexer_role_is_a_collection_writer_principal_in_app():
    """app.py composes the collection's writer principals; the deindexer must be one of them."""
    tree = ast.parse((REPO_ROOT / "app.py").read_text())
    writers = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_search_writer_role_arns" for t in node.targets)
    )
    rendered = [ast.unparse(element) for element in writers.elts]
    assert any("iab-llm-content-deindexer-role-{region}" in arn for arn in rendered), rendered


def _ui_backend_role_id(template):
    roles = template.find_resources("AWS::IAM::Role")
    (role_id,) = [
        lid for lid, res in roles.items() if "iab-llm-ui-backend-role" in json.dumps(res["Properties"].get("RoleName"))
    ]
    return role_id


def test_dashboard_backend_can_only_put_events_on_the_default_bus(main_stack):
    everywhere = [
        statement
        for policy in main_stack.find_resources("AWS::IAM::Policy").values()
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]
        if "events:PutEvents" in _actions(statement)
    ]
    on_backend = [
        s
        for s in _statements_for_role(main_stack, _ui_backend_role_id(main_stack))
        if "events:PutEvents" in _actions(s)
    ]
    assert len(everywhere) == len(on_backend) == 1
    (statement,) = on_backend
    assert _actions(statement) == ["events:PutEvents"]
    assert "event-bus/default" in json.dumps(statement["Resource"])


def test_dashboard_backend_has_no_search_collection_access(main_stack):
    actions = {a for s in _statements_for_role(main_stack, _ui_backend_role_id(main_stack)) for a in _actions(s)}
    assert not {a for a in actions if a.startswith("aoss:")}


def test_content_bucket_expires_noncurrent_versions_after_30_days(main_stack):
    _, bucket = _one(
        main_stack,
        "AWS::S3::Bucket",
        lambda p: "iab-llm-content-" in json.dumps(p.get("BucketName")),
    )
    props = bucket["Properties"]
    assert props["VersioningConfiguration"] == {"Status": "Enabled"}
    assert props["LifecycleConfiguration"]["Rules"] == [
        {
            "Id": "ExpireNoncurrentVersions",
            "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
            "ExpiredObjectDeleteMarker": True,
            "Status": "Enabled",
        }
    ]
