"""CDK assertions for managed llms.txt infrastructure."""

import json
from pathlib import Path

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infrastructure import custom_resources
from infrastructure.content_monetization_stack import ContentMonetizationStack

BUNDLED_MANIFEST = (
    (Path(__file__).resolve().parents[2] / "assets" / "bootstrap-llms.txt").read_text(encoding="utf-8").rstrip("\n")
)


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
    assert "llms-txt/bootstrap/llms.txt" in rendered
    # The custom resource's handler is the packaged reconciler module, not a template string.
    _bootstrap_function(template)


def _bootstrap_custom_resource(template):
    resources = template.find_resources("AWS::CloudFormation::CustomResource")
    bootstrap = [r for r in resources.values() if "Manifest" in r["Properties"]]
    assert len(bootstrap) == 1, f"expected exactly one bootstrap custom resource, found {len(bootstrap)}"
    return bootstrap[0]


def test_bootstrap_custom_resource_carries_the_committed_manifest_as_a_property():
    """The manifest is a PROPERTY, not a Lambda environment variable.

    CloudFormation decides whether to send a custom resource an Update from that resource's own
    properties, so an asset edit only reaches the handler if the text itself is a property. While
    the text lived in an environment variable, editing `assets/bootstrap-llms.txt` updated the
    function and never invoked the handler, leaving a hand-maintained version property as the only
    trigger — which was never bumped once in the asset's history.
    """
    manifest = _bootstrap_custom_resource(_template())["Properties"]["Manifest"]

    assert manifest == BUNDLED_MANIFEST
    assert "BOOTSTRAP_MANIFEST" not in json.dumps(_template().to_json()), (
        "the manifest must no longer travel as a Lambda environment variable"
    )


def test_deployed_bootstrap_manifest_advertises_only_the_canonical_endpoints():
    """Pins the value, not the key names: the defect was a correct asset and a stale deployment."""
    manifest = _bootstrap_custom_resource(_template())["Properties"]["Manifest"]

    assert "/info" in manifest
    assert "/query" in manifest
    assert "/search" not in manifest


def _bootstrap_policy_document(template):
    policies = [
        policy
        for policy in template.find_resources("AWS::IAM::Policy").values()
        if "llms-txt/bootstrap/llms.txt" in json.dumps(policy["Properties"]["PolicyDocument"])
    ]
    assert len(policies) == 1
    return policies[0]["Properties"]["PolicyDocument"]


def _bootstrap_function(template):
    functions = [
        fn
        for fn in template.find_resources("AWS::Lambda::Function").values()
        if fn["Properties"].get("Handler") == "llms_txt_bootstrap.handler"
    ]
    assert len(functions) == 1, f"expected exactly one bootstrap handler, found {len(functions)}"
    return functions[0]


def test_bootstrap_handler_is_a_packaged_asset_not_inline_code():
    """The reconciler ships as a Lambda asset, so CloudFormation's 4096-byte `ZipFile` cap does not apply."""
    code = _bootstrap_function(_template())["Properties"]["Code"]

    assert "ZipFile" not in code
    assert {"S3Bucket", "S3Key"} <= set(code)


def test_bootstrap_asset_contains_only_the_handler_package():
    """The asset directory is the handler package itself; bytecode caches are excluded so the asset hash is stable."""
    package_dir = Path(custom_resources.__file__).resolve().parent
    shipped = sorted(p.name for p in package_dir.iterdir() if p.name != "__pycache__")

    assert shipped == ["__init__.py", "llms_txt_bootstrap.py"]


def test_bootstrap_outcome_is_surfaced_as_a_stack_output():
    """CloudFormation does not show custom resource response Data in stack events, so without a
    Fn::GetAtt the `preserved` outcome (nothing refreshed) would be invisible to a green deploy."""
    outputs = _template().find_outputs("*")

    action = outputs["LlmsTxtBootstrapAction"]["Value"]
    assert action["Fn::GetAtt"][1] == "BootstrapAction"


def test_bootstrap_role_is_scoped_to_the_single_bootstrap_key():
    """The reconciler added a read but no object permission: GetObject already covered it."""
    document = _bootstrap_policy_document(_template())

    object_actions = {
        action
        for statement in document["Statement"]
        for action in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])
    }
    assert object_actions == {"s3:GetObject", "s3:PutObject", "s3:ListBucket"}
    assert "*" not in json.dumps(document).replace("llms-txt/bootstrap/llms.txt", "")
    # Nothing grants the reconciler the draft, published, or legacy-root keys.
    for out_of_scope in ("llms-txt/drafts", "llms-txt/published"):
        assert out_of_scope not in json.dumps(document)


def test_bootstrap_list_bucket_is_unconditioned_so_a_missing_key_answers_404():
    """S3 returns 403 AccessDenied, not 404, for a GetObject on a missing key unless the caller
    also holds s3:ListBucket on the bucket. A GetObject request carries no `s3:prefix`, so a
    StringLike condition on it never matches and the grant would not cover that implicit check:
    the seeding branch would fail a fresh install with an opaque AccessDenied on a key the role
    demonstrably can read. Pins the grant as unconditioned, and still bucket-scoped and list-only.
    """
    statements = _bootstrap_policy_document(_template())["Statement"]

    listing = [s for s in statements if "s3:ListBucket" in json.dumps(s["Action"])]
    assert len(listing) == 1
    assert listing[0]["Action"] == "s3:ListBucket"
    assert "Condition" not in listing[0]


def test_worker_policy_is_scoped_to_content_jobs_and_manifest_prefixes():
    # No `s3:prefix` assertion: the worker's own ListBucket is deliberately unconditioned (a
    # missing manifest must answer NoSuchKey, not AccessDenied), and the bootstrap role — the
    # template's only former source of that condition key — is unconditioned for the same reason.
    # See test_bootstrap_list_bucket_is_unconditioned_so_a_missing_key_answers_404.
    rendered = json.dumps(_template().to_json())
    assert "llms-txt/*" in rendered
    assert "dynamodb:TransactWriteItems" in rendered
    assert "bedrock:InvokeModel" in rendered
    assert "s3:ListBucket" in rendered


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
