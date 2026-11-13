#!/usr/bin/env python3
import os
import sys

import aws_cdk as cdk
import boto3
import cdk_nag
import yaml

from infrastructure.bot_control_stack import BotControlStack
from infrastructure.cognito_stack import CognitoStack
from infrastructure.content_monetization_stack import ContentMonetizationStack
from infrastructure.pipeline_stack import (
    DEFAULT_COLLECTION_GROUP_NAME,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_ENDPOINT_SSM_PARAM,
    SEARCH_READER_ROLE_NAME_PREFIXES,
    PipelineStack,
)
from infrastructure.search_collection_stack import SearchCollectionStack
from infrastructure.ui_stack import UIStack


def get_aws_region(default_region="us-west-2"):
    """Get the AWS region from the current AWS profile/session"""
    try:
        # First check environment variable (set by deployment script)
        if os.environ.get("AWS_DEFAULT_REGION"):
            return os.environ.get("AWS_DEFAULT_REGION")

        # Try to get region from AWS profile
        profile_name = os.environ.get("AWS_PROFILE")
        if profile_name:
            session = boto3.Session(profile_name=profile_name)
        else:
            session = boto3.Session()
        return session.region_name or default_region
    except Exception:
        # Fallback to default
        return default_region


def get_aws_account():
    """Get the AWS account ID from the current AWS profile/session"""
    try:
        # Use the specified profile if available
        profile_name = os.environ.get("AWS_PROFILE")
        if profile_name:
            session = boto3.Session(profile_name=profile_name)
            sts = session.client("sts")
        else:
            sts = boto3.client("sts")
        return sts.get_caller_identity()["Account"]
    except Exception:
        return None


app = cdk.App()

# ---------------------------------------------------------------------------
# Parse solution-manifest.yaml — single source of truth for the solution's
# id, name, slug, and version. Stamped into every stack's description + tags so
# CloudFormation reflects the published catalog identity (mirrors the pattern in
# the Customer Data Platform guidance repo).
# ---------------------------------------------------------------------------
manifest_path = os.path.join(os.path.dirname(__file__), "solution-manifest.yaml")
try:
    with open(manifest_path, encoding="utf-8") as f:
        _manifest = yaml.safe_load(f) or {}
except (OSError, yaml.YAMLError) as err:
    print(f"❌ Failed to read or parse solution-manifest.yaml at {manifest_path}: {err}")
    sys.exit(1)

solution = _manifest.get("solution") or {}
# name and version are required; id is optional until an SO#### is assigned
# (Alchemy inventory #6421 has none yet — manifest ships a SOXXXX placeholder).
for _required in ("name", "version"):
    if not solution.get(_required):
        print(f"❌ solution-manifest.yaml missing required field: solution.{_required}")
        sys.exit(1)

solution_name = solution["name"]
solution_version = str(solution["version"])
solution_id = solution.get("id")
# Only fold a real SO#### into the description — never the placeholder.
_has_real_id = bool(solution_id) and solution_id != "SOXXXX"
solution_tag = f"{solution_name} ({solution_id})" if _has_real_id else solution_name


def tag_solution(stack):
    """Stamp the manifest-derived solution identity onto a stack's tags."""
    cdk.Tags.of(stack).add("Project", "AI-Content-Monetization")
    cdk.Tags.of(stack).add("SolutionName", solution_name)
    cdk.Tags.of(stack).add("SolutionVersion", solution_version)
    if _has_real_id:
        cdk.Tags.of(stack).add("SolutionId", solution_id)


def tag_solution_no_version(stack):
    """Governance tags WITHOUT SolutionVersion — for stacks whose resources must
    not be replaced on a release bump.

    Used ONLY for SearchCollectionStack: the AOSS collection's Tags property is
    Update-requires-Replacement, so a per-release SolutionVersion bump would
    REPLACE the collection (wiping the index). Stamping this stack with no
    SolutionVersion stack-level tag means there is nothing for CloudFormation to
    propagate onto the collection at deploy — the one failure path that both the
    in-stack and nested-stack SolutionVersion-remove attempts could not close.
    Every other stack keeps tag_solution (SolutionVersion included)."""
    cdk.Tags.of(stack).add("Project", "AI-Content-Monetization")
    cdk.Tags.of(stack).add("SolutionName", solution_name)
    if _has_real_id:
        cdk.Tags.of(stack).add("SolutionId", solution_id)


# Centralized config (see cdk.json → context.iab). Single source of truth for
# environment-specific values that used to be hardcoded across the stacks.
iab_cfg = app.node.try_get_context("iab") or {}
default_region = iab_cfg.get("defaultRegion", "us-west-2")

# Get region and account from AWS profile or context
region = app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION") or get_aws_region(default_region)
account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT") or get_aws_account()

# For synth-only scenarios (CDK Nag, CI tests), use placeholder if no credentials available
if not account:
    account = os.environ.get("AWS_ACCOUNT_ID", "000000000000")
    print(f"⚠️  No AWS credentials available, using placeholder account: {account}")
if not region:
    region = default_region

# Check deployment options
deploy_bot_control = (
    app.node.try_get_context("deploy-bot-control") or os.environ.get("DEPLOY_BOT_CONTROL", "true").lower() == "true"
)
deploy_ui = app.node.try_get_context("deploy-ui") or os.environ.get("DEPLOY_UI", "true").lower() == "true"
enable_demo_mode = (
    app.node.try_get_context("enable-demo-mode") or os.environ.get("ENABLE_DEMO_MODE", "true").lower() == "true"
)

print(f"🌍 Deploying to region: {region}")
print(f"🏢 Using AWS account: {account}")
print(f"🤖 Bot Control included: {deploy_bot_control}")
print(f"🎨 UI included: {deploy_ui}")
print(f"🎭 Demo mode enabled: {enable_demo_mode}")

# Deploy Cognito Authentication Stack (native auth for this repo)
cognito_stack = CognitoStack(
    app,
    "CMCognitoStack",
    env=cdk.Environment(account=account, region=region),
    description=f"{solution_tag} v{solution_version} - Cognito Authentication (User Pool, Identity Pool, RBAC)",
)
tag_solution(cognito_stack)
cdk.Tags.of(cognito_stack).add("Component", "Authentication")

# Deploy the main Content Monetization Stack
main_stack = ContentMonetizationStack(
    app,
    "ContentMonetizationStack",
    env=cdk.Environment(account=account, region=region),
    description=f"{solution_tag} v{solution_version} - Content Monetization API for publisher content monetization",
)

# GEO Enrichment Pipeline Stack (Step Functions + OpenSearch Serverless)
pipeline_stack = PipelineStack(
    app,
    "CMPipelineStack",
    content_table_name="cm-content",
    content_bucket_name=f"iab-llm-content-{account}-{region}",
    jobs_table_name="cm-jobs",
    env=cdk.Environment(account=account, region=region),
    description=f"{solution_tag} v{solution_version} - GEO Enrichment Pipeline (Step Functions, OpenSearch Serverless, ingest trigger)",
)
pipeline_stack.add_dependency(main_stack)
tag_solution(pipeline_stack)
cdk.Tags.of(pipeline_stack).add("Component", "GEO-Pipeline")

# ── GEO search collection — TOP-LEVEL stack (NOT nested, NOT in PipelineStack) ──
# The AOSS collection's Tags property is Update-requires-Replacement, so if the
# per-release SolutionVersion tag ever lands on it, a version bump REPLACES the
# collection and wipes the index. Two earlier approaches failed against the
# DEPLOYED collection: (1) an in-stack resource-scoped Tags.remove, and (2) a
# nested stack with the same remove — in both, CloudFormation re-applied the
# owning stack's SolutionVersion stack-level tag onto the collection at deploy.
# The fix is a free-standing top-level stack tagged with GOVERNANCE TAGS ONLY
# (tag_solution_no_version — never SolutionVersion), so there is no
# SolutionVersion stack tag anywhere in its hierarchy for CFN to propagate.
#
# No cross-stack exports: the collection's data-access policy names the pipeline
# writer roles + the partner_api/ui_backend reader roles by their FIXED composed
# ARN STRINGS; PipelineStack in turn grants aoss:APIAccessAll on a WILDCARD
# collection ARN, so neither stack holds a CFN reference to the other. The
# collection endpoint is published to SSM by name for runtime discovery.
_search_writer_role_arns = [
    f"arn:aws:iam::{account}:role/iab-llm-geo-pipeline-role-{region}",
    f"arn:aws:iam::{account}:role/iab-llm-geo-backfill-role-{region}",
    f"arn:aws:iam::{account}:role/iab-llm-content-deindexer-role-{region}",
]
_search_reader_role_arns = [
    f"arn:aws:iam::{account}:role/{prefix}-{region}" for prefix in SEARCH_READER_ROLE_NAME_PREFIXES
]
search_stack = SearchCollectionStack(
    app,
    "CMSearchStack",
    collection_name=iab_cfg.get("openSearchCollectionName", DEFAULT_COLLECTION_NAME),
    collection_group_name=iab_cfg.get("openSearchCollectionGroupName", DEFAULT_COLLECTION_GROUP_NAME),
    endpoint_ssm_param=iab_cfg.get("openSearchEndpointSsmParam", DEFAULT_ENDPOINT_SSM_PARAM),
    writer_role_arns=_search_writer_role_arns,
    reader_role_arns=_search_reader_role_arns,
    env=cdk.Environment(account=account, region=region),
    description=f"{solution_tag} - GEO search collection (OpenSearch Serverless), isolated to avoid SolutionVersion-driven replacement",
)
# Depend on the pipeline so the writer Lambda roles exist before this stack's
# AOSS data-access policy references them by ARN (AOSS validates principals).
search_stack.add_dependency(pipeline_stack)
# Governance tags ONLY — deliberately NOT tag_solution (no SolutionVersion), so
# the collection's immutable Tags never carry a per-release value.
tag_solution_no_version(search_stack)
cdk.Tags.of(search_stack).add("Component", "GEO-Pipeline")

# Optionally deploy Bot Control Stack integrated with the main stack
if deploy_bot_control:
    bot_control_stack = BotControlStack(
        app,
        "CMBotControlStack",
        env=cdk.Environment(account=account, region=region),
        description=f"{solution_tag} v{solution_version} - Bot Control Stack",
    )

    # Associate the WebACL with the API Gateway from the main stack
    # Note: This would require exposing the API Gateway stage ARN from the main stack
    # For now, we'll leave this as a manual step or separate deployment

    # Add dependency to ensure proper deployment order
    bot_control_stack.add_dependency(main_stack)

    # Add tags for integrated deployment
    tag_solution(bot_control_stack)
    cdk.Tags.of(bot_control_stack).add("Component", "Bot-Control")

# Optionally deploy UI Stack
ui_stack = None
if deploy_ui:
    # Get API Gateway URL from main stack (placeholder for now)
    api_gateway_url = "https://api.example.com"  # Will be updated to use actual API Gateway URL

    # Get analytics agent ARN from environment or context
    analytics_agent_arn = app.node.try_get_context("analytics-agent-arn") or os.environ.get("ANALYTICS_AGENT_ARN")

    # Cognito configuration is passed at deploy time via --parameters
    # (populated by deploy script from CMCognitoStack outputs)

    ui_stack = UIStack(
        app,
        "CMUIStack",
        api_gateway_url=api_gateway_url,
        enable_demo_mode=enable_demo_mode,
        analytics_agent_arn=analytics_agent_arn,
        env=cdk.Environment(account=account, region=region),
        description=f"{solution_tag} v{solution_version} - Demo UI ({'Demo Mode' if enable_demo_mode else 'Production Mode'})",
    )

    # Add dependency to ensure proper deployment order
    ui_stack.add_dependency(main_stack)

    # Add tags for UI stack
    tag_solution(ui_stack)
    cdk.Tags.of(ui_stack).add("Component", "Demo-UI")
    cdk.Tags.of(ui_stack).add("DemoMode", str(enable_demo_mode))

# Add tags to main stack
tag_solution(main_stack)
cdk.Tags.of(main_stack).add("Component", "Main-API")

# Register cdk-nag AwsSolutions checks — produces CSV reports in cdk.out/
cdk.Aspects.of(app).add(cdk_nag.AwsSolutionsChecks(reports=True, verbose=True))

# Suppress known nag errors to prevent blocking deploy (findings still appear in CSV reports)
nag_suppressions = [
    cdk_nag.NagPackSuppression(
        id="AwsSolutions-APIG1", reason="Access logging configured at account level via CloudWatch"
    ),
    cdk_nag.NagPackSuppression(
        id="AwsSolutions-APIG2", reason="Request validation handled by Lambda integration logic"
    ),
    cdk_nag.NagPackSuppression(id="AwsSolutions-APIG3", reason="WAF association handled by separate Bot Control stack"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-APIG6", reason="CloudWatch logging configured at account level"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-SMG4", reason="Secret rotation not required for test API keys"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-S1", reason="Access logging not required for content bucket in demo"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-S10", reason="SSL enforcement added via bucket policy separately"),
    cdk_nag.NagPackSuppression(
        id="AwsSolutions-IAM4", reason="AWS managed policies acceptable for Lambda execution role"
    ),
    cdk_nag.NagPackSuppression(
        # After the per-function Lambda role split and the wildcard-resource scoping, the Lambda
        # execution roles' DynamoDB and S3 statements are ARN-scoped (no wildcard resource). The
        # IAM5 findings that remain are API-level wildcards the services do not support scoping
        # for, each justified inline where the statement is authored:
        # cloudwatch:GetMetricStatistics / cloudwatch:PutMetricData (no resource-level permission),
        # bedrock:ListFoundationModels / bedrock-agentcore:ListAgentRuntimes (List* require *),
        # apigateway /apikeys and /usageplans/*/keys paths (key-id not synth-knowable),
        # aoss collection/* and logs log-group:/aws/lambda/* path wildcards, plus the
        # CDK-generated BucketNotifications/CustomResource/Provider framework roles (not authored
        # here). No DynamoDB/S3 wildcard remains to excuse. (security-hardening-phase3)
        id="AwsSolutions-IAM5",
        reason=(
            "Residual wildcards are API-level only (cloudwatch PutMetricData/GetMetricStatistics, "
            "bedrock/agentcore List*, apigateway key/usage-plan paths, aoss collection, log-group "
            "path, and CDK-generated framework roles); DynamoDB and S3 are ARN-scoped."
        ),
    ),
    cdk_nag.NagPackSuppression(id="AwsSolutions-COG1", reason="Cognito password policy configured at user pool level"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-COG2", reason="MFA not required for demo deployment"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-COG3", reason="AdvancedSecurityMode not required for demo deployment"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-L1", reason="Python 3.11 runtime is current and supported"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-CFR3", reason="CloudFront access logging not required for demo"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-CFR4", reason="Default CloudFront viewer certificate used for demo"),
    cdk_nag.NagPackSuppression(id="AwsSolutions-CFR7", reason="OAC migration planned; OAI currently in use"),
]

cdk_nag.NagSuppressions.add_stack_suppressions(main_stack, nag_suppressions, apply_to_nested_stacks=True)
cdk_nag.NagSuppressions.add_stack_suppressions(
    pipeline_stack,
    nag_suppressions
    + [
        cdk_nag.NagPackSuppression(
            id="AwsSolutions-SF1", reason="ERROR-level logging enabled; ALL-level not needed for demo"
        ),
        cdk_nag.NagPackSuppression(id="AwsSolutions-SF2", reason="X-Ray tracing not required for demo pipeline"),
        cdk_nag.NagPackSuppression(
            id="AwsSolutions-SQS3", reason="Queue IS the dead-letter queue for the trigger rule"
        ),
    ],
    apply_to_nested_stacks=True,
)
if deploy_bot_control:
    cdk_nag.NagSuppressions.add_stack_suppressions(bot_control_stack, nag_suppressions, apply_to_nested_stacks=True)
if ui_stack:
    cdk_nag.NagSuppressions.add_stack_suppressions(ui_stack, nag_suppressions, apply_to_nested_stacks=True)
cdk_nag.NagSuppressions.add_stack_suppressions(cognito_stack, nag_suppressions, apply_to_nested_stacks=True)

app.synth()
