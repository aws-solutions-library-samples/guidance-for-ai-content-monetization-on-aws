"""CDK assertions for managed llms.txt infrastructure."""

import json

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infrastructure.content_monetization_stack import ContentMonetizationStack


def _template():
    stack = ContentMonetizationStack(
        App(),
        "LlmsTxtManagementStack",
        env=Environment(account="000000000000", region="us-west-2"),
    )
    return Template.from_stack(stack)


def test_worker_has_async_retry_and_dlq():
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::EventInvokeConfig",
        {
            "MaximumRetryAttempts": 2,
            "MaximumEventAgeInSeconds": 21600,
            "DestinationConfig": {"OnFailure": {"Destination": Match.any_value()}},
        },
    )
    template.has_resource_properties(
        "AWS::SQS::Queue",
        {
            "QueueName": "cm-llms-txt-worker-dlq",
            "KmsMasterKeyId": "alias/aws/sqs",
            "VisibilityTimeout": 5400,
        },
    )
    template.resource_count_is("AWS::Lambda::EventSourceMapping", 1)


def test_worker_environment_and_timeout():
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Description": "Asynchronous managed llms.txt snapshot and draft generator",
            "Timeout": 900,
            "Environment": {
                "Variables": {
                    "CONTENT_TABLE_NAME": Match.any_value(),
                    "JOBS_TABLE_NAME": Match.any_value(),
                    "CONTENT_BUCKET_NAME": Match.any_value(),
                    "LLMS_TXT_MODEL_ID": Match.any_value(),
                    "LLMS_TXT_TOP_N_PER_SECTION": "10",
                    "LLMS_TXT_LOCK_TIMEOUT_SECONDS": "25200",
                    "LLMS_TXT_ATTEMPT_TIMEOUT_SECONDS": "900",
                }
            },
        },
    )


def test_worker_has_no_scheduled_trigger_in_human_triggered_v1():
    template = _template()
    template.resource_count_is("AWS::Events::Rule", 0)


def test_bucket_is_versioned_and_bootstrap_is_custom_resource():
    template = _template()
    template.has_resource_properties(
        "AWS::S3::Bucket",
        {"VersioningConfiguration": {"Status": "Enabled"}},
    )
    rendered = json.dumps(template.to_json())
    assert "managed-llms-txt-bootstrap" in rendered
    assert "BOOTSTRAP_MANIFEST" in rendered
    assert "llms-txt/bootstrap/llms.txt" in rendered


def test_worker_policy_is_scoped_to_content_jobs_and_manifest_prefixes():
    rendered = json.dumps(_template().to_json())
    assert "llms-txt/*" in rendered
    assert "dynamodb:TransactWriteItems" in rendered
    assert "bedrock:InvokeModel" in rendered
    assert "s3:ListBucket" in rendered
    assert "s3:prefix" in rendered
    assert "llms-txt/*" in rendered


def test_ui_backend_role_can_start_and_publish_transactionally():
    # P3-01 (security-hardening-phase3): the former single shared
    # `iab-llm-lambda-execution-role-*` was split into per-function roles built with
    # `add_to_policy`, so the transactional grant now lives on a standalone
    # AWS::IAM::Policy attached to the ui_backend role (not an inline `Policies` block on
    # the role). This asserts the same behavior — ui_backend holds
    # ConditionCheckItem + TransactWriteItems on the JobsTable — against the new structure.
    template = _template()
    template.has_resource_properties(
        "AWS::IAM::Role",
        {"RoleName": "iab-llm-ui-backend-role-us-west-2"},
    )
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Action": [
                                    "dynamodb:ConditionCheckItem",
                                    "dynamodb:TransactWriteItems",
                                ],
                                "Resource": {
                                    "Fn::GetAtt": Match.array_with(
                                        [Match.string_like_regexp("JobsTable")]
                                    )
                                },
                            }
                        )
                    ]
                )
            },
            "Roles": Match.array_with(
                [{"Ref": Match.string_like_regexp("UIBackendExecutionRole")}]
            ),
        },
    )
