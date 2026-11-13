import json
import os
from pathlib import Path
from typing import Dict

from aws_cdk import CfnOutput, CfnParameter, CustomResource, Duration, RemovalPolicy, Stack, custom_resources
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_lambda_destinations as lambda_destinations
from aws_cdk import aws_lambda_event_sources as lambda_event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from cdk_nag import NagSuppressions
from constructs import Construct

from .authentication_stack import AuthenticationStack
from .constructs.monetization_api import MonetizationApi
from .constructs.native_content_edge import NativeContentEdge

PARTNER_API_URL_PARAMETER = "/cm/partner-api/url"

# TM-02 (security-hardening-phase2): the deployed portal origin the backend echoes in CORS.
# CMUIStack writes the CloudFront origin here on deploy; the backend Lambdas read it at invoke
# time (see shared.utils.response_helpers._origin_from_ssm) so there is no deploy-order cycle and
# no cross-stack export. ALLOWED_ORIGINS (comma-separated) is the explicit, higher-precedence
# override; it is read from cdk.json context (iab.allowedOrigins) so a dev deploy can include the
# local Vite origin (http://localhost:3000) while production sets the portal's custom domain(s).
UI_ORIGIN_SSM_PARAMETER = "/cm/ui/origin"


class ContentMonetizationStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Centralized config from cdk.json (context.iab). Environment-specific
        # values (MediaLake pool, MCP server ARN) that used to be hardcoded.
        cfg = self.node.try_get_context("iab") or {}
        mcp_server_arn = cfg.get(
            "mcpServerArn",
            "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/sample_integrated_mcp_server-EXAMPLE01",
        )

        # --- Cognito Parameters (passed at deploy time via --parameters) ---
        cognito_user_pool_id_param = CfnParameter(
            self, "CognitoUserPoolId", type="String", description="Cognito User Pool ID for authentication", default=""
        )
        cognito_user_pool_client_id_param = CfnParameter(
            self,
            "CognitoUserPoolClientId",
            type="String",
            description="Cognito User Pool Client ID for authentication",
            default="",
        )
        cognito_identity_pool_id_param = CfnParameter(
            self,
            "CognitoIdentityPoolId",
            type="String",
            description="Cognito Identity Pool ID for AWS resource access",
            default="",
        )

        # DynamoDB table for content metadata.
        # Single partition key (content_id). Re-processing the same content
        # overwrites the record in place (idempotent — same S3 object → same
        # content_id → same item). Content versioning was removed in ADR-004:
        # nothing read the v{ts} history rows, so the composite key + double-write
        # + reader-side LATEST filtering were pure cost. PITR covers accidental
        # loss; re-ingest re-derives the record.
        # Fixed table name "cm-content" (start of the #24 iab-llm-* -> cm-* migration).
        # Safe to set now: the key schema is stable, and because the previously-deployed table
        # has a CloudFormation-generated physical name, the rename replaces cleanly (the new
        # cm-content table is created, then the old differently-named table is deleted — no
        # self-collision). Consumers still read the name from CONTENT_TABLE_NAME / the stack output.
        content_table = dynamodb.Table(
            self,
            "ContentTable",
            table_name="cm-content",
            partition_key=dynamodb.Attribute(name="content_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Add GSI for querying by content type and IAB categories
        content_table.add_global_secondary_index(
            index_name="content-type-index",
            partition_key=dynamodb.Attribute(name="content_type", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )

        # URL-addressable content retrieval (#14, REQ-1.1). `url_hash` is sha256 of the
        # canonicalized URL, computed at ingest, so `GET /content?url=<canonical>` resolves a
        # document by the URL an agent cites rather than by an opaque content_id. The index is
        # the lookup path for that endpoint; without it the read would be a table scan.
        content_table.add_global_secondary_index(
            index_name="url_hash-index",
            partition_key=dynamodb.Attribute(name="url_hash", type=dynamodb.AttributeType.STRING),
        )

        # DynamoDB table for partner identity and credentials. Keyed on the partner, not on a
        # credential hash: PK ``PARTNER#<partner_id>`` with an SK of ``META`` for the account record
        # and ``KEY#<api_key_id>`` for each API Gateway key bound to it. Changing the key schema
        # replaces the table; records are re-seeded by ``make seed-data``.
        # No fixed physical name: the partner table is the one table whose key schema changed
        # (partner_key_hash -> PK/SK). A generated name lets CloudFormation replace it create-before-
        # delete on any future key change instead of failing an in-place UpdateTable. Consumers get the
        # name from the PARTNER_TABLE_NAME env var / the PartnerTableName stack output, never a literal.
        partner_table = dynamodb.Table(
            self,
            "PartnerTable",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Resolve the partner behind an API Gateway key id (``requestContext.identity.apiKeyId``)
        # with one query. Only the attributes the request path needs are projected.
        partner_table.add_global_secondary_index(
            index_name="api-key-id-index",
            partition_key=dynamodb.Attribute(name="api_key_id", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.INCLUDE,
            non_key_attributes=["partner_id", "usage_plan_id", "status"],
        )

        # DynamoDB table for processing jobs
        jobs_table = dynamodb.Table(
            self,
            "JobsTable",
            table_name="cm-jobs",
            partition_key=dynamodb.Attribute(name="job_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Add GSI for querying jobs by status
        jobs_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )

        # DynamoDB table for analytics metrics and bot access tracking
        analytics_table = dynamodb.Table(
            self,
            "AnalyticsTable",
            table_name="cm-analytics",
            partition_key=dynamodb.Attribute(name="metric_date", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="metric_type_timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Add GSI for querying analytics by metric type and timestamp
        analytics_table.add_global_secondary_index(
            index_name="metric-type-index",
            partition_key=dynamodb.Attribute(name="metric_type", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
        )

        # DynamoDB table for bot control rules and WAF synchronization
        bot_rules_table = dynamodb.Table(
            self,
            "BotRulesTable",
            table_name="cm-bot-rules",
            partition_key=dynamodb.Attribute(name="rule_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Add GSI for querying bot rules by status and priority
        bot_rules_table.add_global_secondary_index(
            index_name="status-priority-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="priority", type=dynamodb.AttributeType.NUMBER),
        )

        # DynamoDB table for versioned pricing rules with effective dates and audit trail
        pricing_rules_table = dynamodb.Table(
            self,
            "PricingRulesTable",
            table_name="cm-pricing-rules",
            partition_key=dynamodb.Attribute(name="rule_set_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="version_timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Add GSI for querying pricing rules by effective date
        pricing_rules_table.add_global_secondary_index(
            index_name="effective-date-index",
            partition_key=dynamodb.Attribute(name="effective_date", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="version_timestamp", type=dynamodb.AttributeType.STRING),
        )

        # Add GSI for querying active pricing rules
        pricing_rules_table.add_global_secondary_index(
            index_name="status-effective-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="effective_date", type=dynamodb.AttributeType.STRING),
        )

        # DynamoDB table for pricing transactions and historical cost tracking
        pricing_transactions_table = dynamodb.Table(
            self,
            "PricingTransactionsTable",
            table_name="cm-pricing-transactions",
            partition_key=dynamodb.Attribute(name="transaction_date", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="transaction_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Add GSI for querying transactions by content ID
        pricing_transactions_table.add_global_secondary_index(
            index_name="content-id-index",
            partition_key=dynamodb.Attribute(name="content_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="transaction_timestamp", type=dynamodb.AttributeType.STRING),
        )

        # Add GSI for querying transactions by pricing rule version
        pricing_transactions_table.add_global_secondary_index(
            index_name="pricing-version-index",
            partition_key=dynamodb.Attribute(name="pricing_rule_version", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="transaction_timestamp", type=dynamodb.AttributeType.STRING),
        )

        # DynamoDB table for CoMP RSL license documents with versioning
        licenses_table = dynamodb.Table(
            self, "LicensesTable",
            table_name="cm-licenses",
            partition_key=dynamodb.Attribute(
                name="license_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="version",
                type=dynamodb.AttributeType.NUMBER
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True
        )

        # Add GSI for querying licenses by status
        licenses_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="updated_at",
                type=dynamodb.AttributeType.STRING
            )
        )

        # Add GSI for querying licenses by creator
        licenses_table.add_global_secondary_index(
            index_name="created-by-index",
            partition_key=dynamodb.Attribute(
                name="created_by",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING
            )
        )

        # DynamoDB table for IAB taxonomy data
        taxonomy_table = dynamodb.Table(
            self,
            "TaxonomyTable",
            table_name="cm-taxonomy",
            partition_key=dynamodb.Attribute(name="category_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Add GSI for querying taxonomy by tier level
        taxonomy_table.add_global_secondary_index(
            index_name="tier-level-index",
            partition_key=dynamodb.Attribute(name="tier_level", type=dynamodb.AttributeType.NUMBER),
            sort_key=dynamodb.Attribute(name="name", type=dynamodb.AttributeType.STRING),
        )

        # Add GSI for querying taxonomy by parent category
        taxonomy_table.add_global_secondary_index(
            index_name="parent-id-index",
            partition_key=dynamodb.Attribute(name="parent_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="category_id", type=dynamodb.AttributeType.STRING),
        )

        # DynamoDB table for UI-managed integration Connection config (integration-adapters, #35).
        # The one entity that spec persists: an activated adapter instance (a Connection) — its
        # non-secret config plus a Secrets Manager reference (secret_ref), NEVER a plaintext
        # credential. Control-plane connection-management config, so it lives with the other cm-*
        # API tables in this stack; consumers read the name from CONNECTIONS_TABLE_NAME (referenced
        # by name — no cross-stack export).
        connections_table = dynamodb.Table(
            self,
            "ConnectionsTable",
            table_name="cm-connections",
            partition_key=dynamodb.Attribute(name="connection_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # Raw usage-event store — the canonical billable-event truth (cm-usage-events).
        #
        # license-lifecycle-usage-events task 9.1 (Requirements 8.3, 8.4). This is the ONE raw
        # store every billable unit lands in, written exactly once by the
        # services/usage_emitter.py choke point. cm-analytics stays a *derived* view fed from
        # this table's stream and is owned by #16 — no raw truth is written into it. The legacy
        # iab-access-logs store folds into this table (task 9.4), which is why the TTL attribute
        # is named "ttl": the folded writer keeps the attribute name it already uses.
        #
        # Key schema — append-optimized, partner-and-day bucketed:
        #
        #   PK = PARTNER#<partner_id>#<YYYY-MM-DD>   SK = <accessed_at>#<request_id>
        #
        # The write is an unconditional PutItem: the SK is unique per event (ISO-8601 instant
        # plus request id), so an append never collides and never needs a read-modify-write. The
        # day bucket in the PK bounds partition growth and spreads the write hot spot a bare
        # partner_id PK would create on a busy partner, matching the date-bucketed convention the
        # other time-series tables here already use (cm-analytics keys on metric_date,
        # cm-pricing-transactions on transaction_date).
        #
        # The natural read — "what did this partner consume over this period", i.e. the billing
        # run — is served directly: one Query per partner-day with an SK range, no filter, no
        # scan. A month of billing for one partner is a bounded fan-out of small queries rather
        # than one unbounded partition read.
        #
        # No GSI. Content-level and cross-partner aggregation is exactly what the stream-derived
        # cm-analytics view exists for (#16), so a GSI here would add write amplification to the
        # hot append path for a read nobody performs. A GSI is an additive, non-breaking change
        # if #16 ever needs to query raw events directly.
        #
        # The UsageEvent fields (partner_id, content_id, accessed_at, ...) are also stored as
        # plain top-level attributes, not only folded into PK/SK, so the event stays readable
        # without parsing composite keys.
        #
        # Stream: NEW_IMAGE. A raw event is immutable and append-only, so an old image is always
        # null and NEW_AND_OLD_IMAGES would ship a second, empty copy of every record. KEYS_ONLY
        # would force #16's aggregator to read each item back to see price_charged / currency /
        # content_type — the very fields it aggregates. NEW_IMAGE hands the derived view the
        # whole event in the stream record.
        #
        # Consumers read the name from USAGE_EVENTS_TABLE_NAME (referenced by name — no
        # cross-stack export); the in-code fallback default is this literal "cm-usage-events".
        usage_events_table = dynamodb.Table(
            self,
            "UsageEventsTable",
            table_name="cm-usage-events",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_IMAGE,
            time_to_live_attribute="ttl",  # set at emit from USAGE_EVENTS_TTL_DAYS
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
        )

        # S3 bucket for content storage
        # EventBridge notifications feed the GEO pipeline trigger (PipelineStack
        # rule scoped to the ingest/ prefix) without coupling the two stacks.
        content_bucket = s3.Bucket(
            self,
            "ContentBucket",
            bucket_name=f"iab-llm-content-{self.account}-{self.region}",
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            auto_delete_objects=True,  # For demo purposes
            event_bridge_enabled=True,
            versioned=True,
            # Deleted and replaced objects leave noncurrent versions behind; expire them after 30
            # days and remove the delete markers left with no versions. No code reads old versions.
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireNoncurrentVersions",
                    noncurrent_version_expiration=Duration.days(30),
                    expired_object_delete_marker=True,
                )
            ],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.POST, s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
        )

        # Cross-stack references consumed by PipelineStack
        self.content_table = content_table
        self.content_bucket = content_bucket

        # Deploy-time reconciler for the public llms.txt bootstrap fallback.
        #
        # The bundled manifest travels as the custom resource's `Manifest` PROPERTY, not as a
        # Lambda environment variable: CloudFormation decides whether to send a custom resource an
        # Update from that resource's own properties, so with the text in an environment variable
        # an edited asset updated the function and never invoked the handler — leaving a
        # hand-maintained version property as the only trigger, which was never bumped once in the
        # asset's history. As a property, an asset edit always invokes the handler.
        #
        # The handler then refreshes the stored object only where provenance proves it is an
        # unedited deployment seed, and preserves operator-authored content. It records the digest
        # of what it wrote in S3 user metadata (not object tagging), so the GetObject/PutObject
        # grants below on that single key already cover it and this role needs no new actions.
        # Rule and branch matrix: .kiro/specs/llms-txt-management (design, Bootstrap).
        bootstrap_manifest = (Path(__file__).resolve().parents[1] / "assets" / "bootstrap-llms.txt").read_text(
            encoding="utf-8"
        ).rstrip("\n")
        bootstrap_role = iam.Role(
            self,
            "LlmsTxtBootstrapRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
        )
        bootstrap_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject"],
                resources=[f"{content_bucket.bucket_arn}/llms-txt/bootstrap/llms.txt"],
            )
        )
        # ListBucket, unconditioned, so a GetObject on a missing bootstrap key answers NoSuchKey
        # instead of AccessDenied: S3 returns 403 rather than 404 for a missing object unless the
        # caller also holds s3:ListBucket on the bucket. A GetObject request never populates
        # `s3:prefix`, so a StringLike condition on it never matches and the grant would not apply
        # to that implicit check — which would turn the seeding branch (a fresh install, or any
        # environment where the seed never landed) into an opaque AccessDenied rollback. Same
        # reasoning, and same shape, as `llms_txt_worker_role` below. No object actions are added:
        # reads and writes stay scoped to the single bootstrap key granted above.
        bootstrap_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[content_bucket.bucket_arn],
            )
        )
        bootstrap_function = _lambda.Function(
            self,
            "LlmsTxtBootstrapFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="llms_txt_bootstrap.handler",
            code=_lambda.Code.from_asset(
                str(Path(__file__).resolve().parent / "custom_resources"),
                exclude=["__pycache__", "*.pyc"],
            ),
            timeout=Duration.seconds(30),
            role=bootstrap_role,
            environment={"BUCKET_NAME": content_bucket.bucket_name},
        )
        bootstrap_provider = custom_resources.Provider(
            self,
            "LlmsTxtBootstrapProvider",
            on_event_handler=bootstrap_function,
        )
        bootstrap_cr = CustomResource(
            self,
            "LlmsTxtBootstrap",
            service_token=bootstrap_provider.service_token,
            properties={"Manifest": bootstrap_manifest},
        )
        # Which branch the reconciler took, as a stack output. CloudFormation does not surface
        # custom resource response Data in stack events, so without a Fn::GetAtt reference the
        # outcome would live only in the provider's CloudWatch log: `preserved` (nothing refreshed)
        # would be indistinguishable from `refreshed` in a green deployment. `cdk deploy` prints
        # outputs, so the deploy job shows seeded/unchanged/refreshed/preserved/skipped.
        CfnOutput(
            self,
            "LlmsTxtBootstrapAction",
            value=bootstrap_cr.get_att_string("BootstrapAction"),
            description="Action the llms.txt bootstrap reconciler took on this deployment",
        )

        # Per-function Lambda execution roles — least privilege per function.
        #
        # Replaces the former single shared execution role, which attached one union policy
        # to all three functions, so the partner-facing Lambda held WAF-update,
        # bedrock-agentcore-invoke, Secrets-Manager-write and Lambda-invoke on `function:*`
        # that it never calls. Each role below grants only what a trace of that function's
        # handler and the service modules it bundles showed it actually invokes, following
        # the existing `llms_txt_worker_role` least-privilege pattern. Statements that need a
        # function ARN or the API-Gateway/SSM resources are added after those resources are
        # constructed, further below, and are attached to the one owning role.
        #
        # Fixed role names keep the region suffix so a dual-region deploy does not collide on
        # the account-global IAM role name.
        # (security-hardening-phase3: shared-role split + wildcard-resource scoping.)
        _basic_exec = iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")

        # Shared CloudWatch Logs statement (all three write their own log group).
        def _logs_statement() -> iam.PolicyStatement:
            return iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/lambda/*"],
            )

        def _ddb_arns(*tables) -> list:
            arns = []
            for t in tables:
                arns.append(t.table_arn)
                arns.append(f"{t.table_arn}/index/*")
            return arns

        # ── partner_api role ──────────────────────────────────────────────
        # Public/partner endpoints + CoMP + search. Traced calls: DynamoDB read/write on
        # content/analytics/jobs/licenses/taxonomy; DynamoDB read-only on the partner table
        # (the partner-key authorizer in shared/auth/authentication_functions.py resolves the
        # API key to a partner via the api-key-id-index GSI, then reads the partner META item);
        # S3 on the content bucket; Bedrock InvokeModel for embeddings + llms_txt drafting;
        # lambda:InvokeFunction of the llms.txt worker; aoss on the collection; ssm /iab-llm/*
        # (OpenSearch endpoint). NOT WAF, NOT bedrock-agentcore, NOT Secrets Manager, NOT
        # apigateway, NOT cloudwatch. partner_api never WRITES the partner table (that is
        # ui_backend's PartnersService), so its partner-table grant is read-only.
        partner_api_role = iam.Role(
            self,
            "PartnerApiExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            role_name=f"iab-llm-partner-api-role-{self.region}",
            description="Execution role for the partner_api Lambda (least privilege)",
            managed_policies=[_basic_exec],
        )
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchGetItem",
                    "dynamodb:BatchWriteItem",
                ],
                resources=_ddb_arns(content_table, analytics_table, jobs_table, licenses_table, taxonomy_table),
            )
        )
        # Partner table: READ-ONLY (partner-key authorizer resolves the key via the GSI, then
        # reads the partner META item). partner_api does not create/modify partners.
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:BatchGetItem"],
                resources=_ddb_arns(partner_table),
            )
        )
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:ConditionCheckItem", "dynamodb:TransactWriteItems"],
                resources=[jobs_table.table_arn],
            )
        )
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                resources=[content_bucket.bucket_arn, f"{content_bucket.bucket_arn}/*"],
            )
        )
        # Bedrock InvokeModel scoped to the Titan embed + Claude inference-profile ARNs in
        # use (mirrors llms_txt_worker_role). ListFoundationModels/GetFoundationModel are
        # not called by partner_api, so they are omitted (they were in the former union policy).
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*anthropic.claude*",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude*",
                    "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text*",
                    "arn:aws:bedrock:*::foundation-model/cohere.embed*",
                ],
            )
        )
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["aoss:APIAccessAll"],
                resources=[f"arn:aws:aoss:{self.region}:{self.account}:collection/*"],
            )
        )
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["ssm:GetParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/iab-llm/*"],
            )
        )
        partner_api_role.add_to_policy(_logs_statement())

        # ── ui_backend role ──────────────────────────────────────────────
        # Dashboard/management: the widest role. Traced calls: DynamoDB on
        # content/partner/jobs/analytics/bot_rules/pricing_rules/pricing_transactions/
        # connections/taxonomy; S3 on the content bucket; bedrock-agentcore invoke of the
        # analytics-agent runtime; Bedrock InvokeModel (llms_txt drafting); lambda invoke of
        # the analytics-aggregation fn and the llms.txt worker; Secrets Manager on
        # cm-connections/*; wafv2 on the managed WebACL (bot-control sync); cloudwatch metric
        # read. apigateway/ssm statements are added after those resources exist, below. NOT aoss.
        # Raw usage-event store: APPEND-ONLY (task 9.1). The emit choke point only ever
        # PutItem/BatchWriteItem here; it never reads, updates or deletes a billable event, so
        # the grant is deliberately narrower than this role's other DynamoDB access. An
        # immutable event store that the emitter could rewrite would not be an audit trail.
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:PutItem", "dynamodb:BatchWriteItem"],
                resources=[usage_events_table.table_arn],
            )
        )

        # The usage-event choke point resolves its telemetry sink from the Connections store
        # before it can emit, so an emitting role needs READ on cm-connections. Without it the
        # resolve raises AccessDenied, usage_emitter swallows it (a telemetry outage must not
        # fail the response) and the billable event is silently lost -- the failure mode this
        # grant exists to prevent. Read-only: Connections are authored by ui_backend's API.
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
                resources=_ddb_arns(connections_table),
            )
        )

        # Resolving a Connection reads its Secrets Manager reference, and the Registry fails
        # CLOSED when that read fails -- an unreadable secret means the adapter is not wired, so
        # without this grant the emit silently drops every billable event. Scoped to this
        # solution's secret name prefix.
        # Scoped to where the Connections API actually writes Connection secrets:
        # `cm-connections/<connection_id>` (ui_backend/routes/connections.py). The earlier
        # `iab-llm-*` scope never matched a Connection secret, so a Connection that DID carry a
        # credential failed AccessDenied, the registry failed closed, and the emit dropped the
        # event silently (GitLab #58).
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:cm-connections/*"
                ],
            )
        )

        ui_backend_role = iam.Role(
            self,
            "UIBackendExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            role_name=f"iab-llm-ui-backend-role-{self.region}",
            description="Execution role for the ui_backend Lambda (least privilege)",
            managed_policies=[_basic_exec],
        )
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchGetItem",
                    "dynamodb:BatchWriteItem",
                ],
                resources=_ddb_arns(
                    content_table,
                    partner_table,
                    jobs_table,
                    analytics_table,
                    bot_rules_table,
                    pricing_rules_table,
                    pricing_transactions_table,
                    connections_table,
                    taxonomy_table,
                ),
            )
        )
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:ConditionCheckItem", "dynamodb:TransactWriteItems"],
                resources=[jobs_table.table_arn],
            )
        )
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                resources=[content_bucket.bucket_arn, f"{content_bucket.bucket_arn}/*"],
            )
        )
        # A content DELETE is a soft delete plus a content.deleted event; the pipeline stack's
        # deindexer completes it. This is the dashboard backend's only grant for that path: it
        # has no access to the search collection.
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["events:PutEvents"],
                resources=[f"arn:aws:events:{self.region}:{self.account}:event-bus/default"],
            )
        )
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*anthropic.claude*",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude*",
                ],
            )
        )
        # bedrock-agentcore scoped to the analytics-agent runtime ARN. ListAgentRuntimes has
        # no resource-level scoping and is isolated into its own statement below with a comment.
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:InvokeAgentRuntime", "bedrock-agentcore:GetAgentRuntime"],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*",
                ],
            )
        )
        ui_backend_role.add_to_policy(
            # bedrock-agentcore:ListAgentRuntimes has no resource-level scoping; isolated with `*`.
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock-agentcore:ListAgentRuntimes"],
                resources=["*"],
            )
        )
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "secretsmanager:CreateSecret",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret",
                ],
                resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:cm-connections/*"],
            )
        )
        # wafv2 scoped to the managed WebACL/rule-group the bot-control sync manages. The
        # WebACL is created out-of-stack (name iab-bot-control-webacl); its id is not synth-
        # knowable here, so scope to the account/region WebACL+rulegroup space and accept the
        # id wildcard. ui_backend only (bot-control is a UI-backend concern).
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "wafv2:GetWebACL",
                    "wafv2:GetRuleGroup",
                    "wafv2:UpdateWebACL",
                    "wafv2:ListWebACLs",
                    "wafv2:ListRuleGroups",
                ],
                resources=[
                    f"arn:aws:wafv2:{self.region}:{self.account}:regional/webacl/*",
                    f"arn:aws:wafv2:{self.region}:{self.account}:regional/rulegroup/*",
                ],
            )
        )
        # cloudwatch:GetMetricStatistics has no resource-level scoping (the CloudWatch metric
        # API does not support resource ARNs for this action), so "*" is retained here.
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:GetMetricStatistics"],
                resources=["*"],
            )
        )
        ui_backend_role.add_to_policy(_logs_statement())

        # ── analytics_aggregation role ───────────────────────────────────
        # Standalone microservice. Traced calls: DynamoDB Query (read-only) on
        # analytics/pricing_transactions/pricing_rules, plus logs. Nothing else.
        # Raw usage-event store: APPEND-ONLY (task 9.1). The emit choke point only ever
        # PutItem/BatchWriteItem here; it never reads, updates or deletes a billable event, so
        # the grant is deliberately narrower than this role's other DynamoDB access. An
        # immutable event store that the emitter could rewrite would not be an audit trail.
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:PutItem", "dynamodb:BatchWriteItem"],
                resources=[usage_events_table.table_arn],
            )
        )

        # The usage-event choke point resolves its telemetry sink from the Connections store
        # before it can emit, so an emitting role needs READ on cm-connections. Without it the
        # resolve raises AccessDenied, usage_emitter swallows it (a telemetry outage must not
        # fail the response) and the billable event is silently lost -- the failure mode this
        # grant exists to prevent. Read-only: Connections are authored by ui_backend's API.
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"],
                resources=_ddb_arns(connections_table),
            )
        )

        # Resolving a Connection reads its Secrets Manager reference, and the Registry fails
        # CLOSED when that read fails -- an unreadable secret means the adapter is not wired, so
        # without this grant the emit silently drops every billable event. Scoped to this
        # solution's secret name prefix.
        # Same correction as the partner_api_role grant above: Connection secrets live under
        # `cm-connections/*`, not `iab-llm-*` (GitLab #58). ui_backend already holds a broader
        # create/put/get grant on this prefix for authoring Connections; this read-only statement
        # is the emit path's grant and is kept separate so the two intents stay legible.
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:cm-connections/*"
                ],
            )
        )

        analytics_aggregation_role = iam.Role(
            self,
            "AnalyticsAggregationExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            role_name=f"iab-llm-analytics-aggregation-role-{self.region}",
            description="Execution role for the analytics_aggregation Lambda (read-only, least privilege)",
            managed_policies=[_basic_exec],
        )
        analytics_aggregation_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["dynamodb:GetItem", "dynamodb:Query"],
                resources=_ddb_arns(analytics_table, pricing_transactions_table, pricing_rules_table),
            )
        )
        analytics_aggregation_role.add_to_policy(_logs_statement())

        # Cognito User Pool ARN constructed from the parameter for API Gateway authorizer
        cognito_user_pool_arn = (
            f"arn:aws:cognito-idp:{self.region}:{self.account}:userpool/{cognito_user_pool_id_param.value_as_string}"
        )

        # Authentication Stack (Nested Stack)
        auth_stack = AuthenticationStack(
            self, "AuthenticationStack", partner_table=partner_table, content_table=content_table
        )

        # Get Analytics Agent ARN from context or environment
        analytics_agent_arn = self.node.try_get_context("analytics-agent-arn") or os.environ.get(
            "ANALYTICS_AGENT_ARN", "REPLACEME"
        )

        # Common environment variables for Lambda functions
        common_env = {
            "CONTENT_TABLE_NAME": content_table.table_name,
            "PARTNER_TABLE_NAME": partner_table.table_name,
            # Legacy readers (access_logger, access_rules_engine, authentication_service) use this name.
            "PARTNERS_TABLE_NAME": partner_table.table_name,
            "JOBS_TABLE_NAME": jobs_table.table_name,
            "ANALYTICS_TABLE_NAME": analytics_table.table_name,
            "BOT_RULES_TABLE_NAME": bot_rules_table.table_name,
            "PRICING_RULES_TABLE_NAME": pricing_rules_table.table_name,
            "PRICING_TRANSACTIONS_TABLE_NAME": pricing_transactions_table.table_name,
            "LICENSES_TABLE_NAME": licenses_table.table_name,
            "TAXONOMY_TABLE_NAME": taxonomy_table.table_name,
            "CONNECTIONS_TABLE_NAME": connections_table.table_name,
            # Raw billable-event store (task 9.1). The emit choke point and the
            # dynamo_usage_events sink both resolve the name from here; the in-code
            # fallback is the same "cm-usage-events" literal.
            "USAGE_EVENTS_TABLE_NAME": usage_events_table.table_name,
            # USAGE_EVENTS_TTL_DAYS is what the emitter turns into the item's "ttl" epoch
            # attribute; 90 days matches the retention the legacy iab-access-logs writer
            # (access_logger.LOG_TTL_DAYS) and the pipeline's HISTORY_TTL_DAYS convention
            # already use, so the fold does not change retention.
            "USAGE_EVENTS_TTL_DAYS": "90",
            "CONTENT_BUCKET_NAME": content_bucket.bucket_name,
            "USER_POOL_ID": cognito_user_pool_id_param.value_as_string,
            "USER_POOL_CLIENT_ID": cognito_user_pool_client_id_param.value_as_string,
            "REGION": self.region,
            # WAF Bot Control configuration
            "WAF_WEB_ACL_NAME": "iab-bot-control-webacl",
            "WAF_WEB_ACL_SCOPE": "REGIONAL",
            # Integrated MCP Server configuration
            "MCP_SERVER_ARN": mcp_server_arn,
            "MCP_ENABLE_EXTERNAL_TOOLS": "true",
            "MCP_ENABLE_ANALYTICS_TOOLS": "true",
            "MCP_TOOL_VISIBILITY_CONTROL": "true",
            # Analytics Agent configuration
            "ANALYTICS_LAMBDA_NAME": "iab-analytics-aggregation",
            "ANALYTICS_AGENT_ARN": analytics_agent_arn,
            # TM-02 CORS origin resolution. ALLOWED_ORIGINS (comma-separated) is the explicit
            # override from cdk.json context (iab.allowedOrigins); empty by default so the demo
            # resolves the portal origin from the SSM parameter below. A dev deploy sets it to
            # include http://localhost:3000 for local-UI-against-deployed-API; production sets the
            # portal's custom domain(s). UI_ORIGIN_SSM_PARAMETER names the SSM parameter CMUIStack
            # writes with the deployed CloudFront origin (read at invoke time, no deploy cycle).
            "ALLOWED_ORIGINS": ",".join(cfg.get("allowedOrigins", [])),
            "UI_ORIGIN_SSM_PARAMETER": UI_ORIGIN_SSM_PARAMETER,
        }

        llms_txt_dlq = sqs.Queue(
            self,
            "LlmsTxtWorkerDLQ",
            queue_name="cm-llms-txt-worker-dlq",
            encryption=sqs.QueueEncryption.KMS_MANAGED,
            enforce_ssl=True,
            retention_period=Duration.days(14),
            visibility_timeout=Duration.minutes(90),
        )
        NagSuppressions.add_resource_suppressions(
            llms_txt_dlq,
            [
                {
                    "id": "AwsSolutions-SQS3",
                    "reason": "This queue is itself the terminal DLQ for failed asynchronous llms.txt events.",
                }
            ],
        )
        llms_txt_worker_role = iam.Role(
            self,
            "LlmsTxtWorkerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
        )
        llms_txt_worker_role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:Scan"],
                resources=[content_table.table_arn],
            )
        )
        llms_txt_worker_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:TransactWriteItems",
                ],
                resources=[jobs_table.table_arn],
            )
        )
        llms_txt_worker_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject"],
                resources=[
                    f"{content_bucket.bucket_arn}/content/*",
                    f"{content_bucket.bucket_arn}/llms-txt/*",
                    # The published-manifest diff reads the legacy root object (LEGACY_PUBLIC_KEY).
                    f"{content_bucket.bucket_arn}/llms.txt",
                ],
            )
        )
        # ListBucket so a missing manifest object resolves as NoSuchKey (the service falls back to
        # the bootstrap manifest) instead of AccessDenied, which would fail the generation run.
        llms_txt_worker_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[content_bucket.bucket_arn],
            )
        )
        llms_txt_worker_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*anthropic.claude*",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude*",
                ],
            )
        )
        llms_txt_worker = _lambda.Function(
            self,
            "LlmsTxtWorkerFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/llms_txt_worker_deployment.zip"),
            timeout=Duration.minutes(15),
            memory_size=1024,
            role=llms_txt_worker_role,
            environment={
                **common_env,
                "LLMS_TXT_MODEL_ID": cfg.get("llmsTxtModelId", "global.anthropic.claude-sonnet-4-6"),
                "LLMS_TXT_BATCH_CHAR_LIMIT": str(cfg.get("llmsTxtBatchCharLimit", 24000)),
                "LLMS_TXT_TOP_N_PER_SECTION": str(cfg.get("llmsTxtTopNPerSection", 10)),
                "LLMS_TXT_LOCK_TIMEOUT_SECONDS": str(
                    cfg.get("llmsTxtLockTimeoutSeconds", 25200)
                ),
                "LLMS_TXT_ATTEMPT_TIMEOUT_SECONDS": "900",
                "PUBLISHER_NAME": cfg.get("publisherName", "Publisher"),
            },
            description="Asynchronous managed llms.txt snapshot and draft generator",
        )
        _lambda.EventInvokeConfig(
            self,
            "LlmsTxtWorkerAsyncConfig",
            function=llms_txt_worker,
            retry_attempts=2,
            max_event_age=Duration.hours(6),
            on_failure=lambda_destinations.SqsDestination(llms_txt_dlq),
        )
        llms_txt_worker.add_event_source(
            lambda_event_sources.SqsEventSource(
                llms_txt_dlq,
                batch_size=10,
            )
        )
        common_env["LLMS_TXT_WORKER_FUNCTION_NAME"] = llms_txt_worker.function_name

        # Partner API Lambda function (new architecture)
        partner_api_lambda = _lambda.Function(
            self,
            "PartnerApiFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/partner_api_deployment.zip"),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=common_env,
            role=partner_api_role,
            description="Partner API Lambda - handles public and partner-authenticated endpoints",
        )

        # UI Backend Lambda function (new architecture)
        ui_backend_lambda = _lambda.Function(
            self,
            "UIBackendFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/ui_backend_deployment.zip"),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=common_env,
            role=ui_backend_role,
            description="UI Backend Lambda - handles internal dashboard endpoints with Cognito auth",
        )

        # Analytics Aggregation Lambda function (standalone microservice)
        analytics_aggregation_lambda = _lambda.Function(
            self,
            "AnalyticsAggregationFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="analytics_aggregation.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/shared_services_deployment.zip"),
            timeout=Duration.seconds(30),
            memory_size=512,
            environment=common_env,
            role=analytics_aggregation_role,
            description="Analytics Aggregation Lambda - calculates metrics and revenue data from DynamoDB",
        )

        # CloudWatch Log Group for Analytics Aggregation Lambda
        analytics_aggregation_log_group = logs.LogGroup(
            self,
            "AnalyticsAggregationLogGroup",
            log_group_name=f"/aws/lambda/{analytics_aggregation_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Update UI Backend Lambda environment to include analytics Lambda function name
        ui_backend_lambda.add_environment("ANALYTICS_LAMBDA_NAME", analytics_aggregation_lambda.function_name)

        # Comparison run route (spec scrape-vs-api-comparison, R7.3 / D5-b): which hosts the route may
        # fetch server-side. Unset -> the deployment's own portal host only (the fixture demo works,
        # nothing else is reachable). "*" -> any public host that passes the SSRF guard. Or a
        # comma-separated list of hosts / "*.suffix" entries. This is the deployer's explicit opt-in,
        # never inherited: the fetch runs from this account and the pipeline writes a Markdown
        # rendition of the fetched article into this catalogue. CDK context `comparison-allowed-hosts`
        # wins over the COMPARISON_ALLOWED_HOSTS environment variable at deploy time.
        comparison_allowed_hosts = self.node.try_get_context("comparison-allowed-hosts") or os.environ.get(
            "COMPARISON_ALLOWED_HOSTS", ""
        )
        if str(comparison_allowed_hosts).strip():
            ui_backend_lambda.add_environment("COMPARISON_ALLOWED_HOSTS", str(comparison_allowed_hosts).strip())

        # The customer's own hosts (scrape-vs-api-comparison R15.4): a run on one of them is "owned"
        # (an already-published article is reused; it may claim a canonical URL only on these hosts),
        # every other run is "external". Unset -> every run is external, the safe default. Same
        # syntax as the allow-list (exact hosts or "*.suffix"); CDK context
        # `comparison-publisher-hosts` wins over COMPARISON_PUBLISHER_HOSTS at deploy time.
        comparison_publisher_hosts = self.node.try_get_context("comparison-publisher-hosts") or os.environ.get(
            "COMPARISON_PUBLISHER_HOSTS", ""
        )
        if str(comparison_publisher_hosts).strip():
            ui_backend_lambda.add_environment("COMPARISON_PUBLISHER_HOSTS", str(comparison_publisher_hosts).strip())

        # lambda:InvokeFunction grants: added here, after the target functions exist, so each
        # is scoped to a concrete function ARN rather than the former `function:*` wildcard.
        # partner_api and ui_backend both invoke the llms.txt worker
        # (llms_txt_service); ui_backend also invokes analytics-aggregation (analytics_service).
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[llms_txt_worker.function_arn],
            )
        )
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["lambda:InvokeFunction"],
                resources=[llms_txt_worker.function_arn, analytics_aggregation_lambda.function_arn],
            )
        )

        # Both REST APIs are generated from openapi-spec.yaml at synth. MonetizationApi owns the
        # contract->gateway concern: the Cognito internal API (partner-explicit paths ->
        # partner_api; else /{proxy+} -> ui_backend) and the native-API-key partner API whose
        # operation set is derived from the contract. Neither definition is committed.
        monetization_api = MonetizationApi(
            self,
            "MonetizationApi",
            region=self.region,
            account=self.account,
            partner_api_fn=partner_api_lambda,
            ui_backend_fn=ui_backend_lambda,
            cognito_user_pool_arn=cognito_user_pool_arn,
        )
        api = monetization_api.api
        partner_api = monetization_api.partner_api
        # Single partner-URL fact (Q8). Any Lambda referenced by the API definitions (directly, or
        # through an env var chain such as partner_api -> worker name) would form a cycle if the
        # URL were injected as an environment variable, so it is published to Parameter Store and
        # read at runtime; request handlers fall back to the Host that reached them.
        ssm.StringParameter(
            self,
            "PartnerApiUrlParameter",
            parameter_name=PARTNER_API_URL_PARAMETER,
            string_value=partner_api.url,
            description="Partner API base URL advertised in llms.txt and GET /info",
        )
        common_env_targets = (llms_txt_worker, partner_api_lambda, ui_backend_lambda)
        for fn in common_env_targets:
            fn.add_environment("PARTNER_API_URL_PARAMETER", PARTNER_API_URL_PARAMETER)
        llms_txt_worker_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["ssm:GetParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{PARTNER_API_URL_PARAMETER}"],
            )
        )
        # partner_api builds the advertised partner-API link from this parameter (partner_api_url).
        partner_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["ssm:GetParameter"],
                resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{PARTNER_API_URL_PARAMETER}"],
            )
        )
        # TM-02: scoped read of the UI-origin parameter CMUIStack publishes, for CORS resolution.
        # Every request-serving role that calls shared cors_headers() needs it: cors_headers ->
        # _origin_from_ssm() reads UI_ORIGIN_SSM_PARAMETER when ALLOWED_ORIGINS is unset (the demo
        # default). partner_api and ui_backend both emit CORS; the llms_txt_worker shares the util.
        # analytics_aggregation returns no CORS headers, so it is intentionally excluded.
        for cors_role in (partner_api_role, ui_backend_role, llms_txt_worker_role):
            cors_role.add_to_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["ssm:GetParameter"],
                    resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter{UI_ORIGIN_SSM_PARAMETER}"],
                )
            )

        # Usage plans: one per pricing tier (D2; limits are placeholders to tune), bound to the
        # partner API stage. Quota is a tier property; a tier change moves the key between plans.
        # The service resolves plans by name (``cm-partner-<tier>``): exporting ids into the
        # Lambda environment or role policy would form a plan -> stage -> API -> Lambda cycle.
        tier_limits = {
            "basic": (1_000, 10, 20),
            "standard": (10_000, 50, 100),
            "premium": (100_000, 200, 400),
            "enterprise": (1_000_000, 1_000, 2_000),
        }
        usage_plans: Dict[str, apigateway.UsagePlan] = {}
        for tier, (daily_quota, rate, burst) in tier_limits.items():
            usage_plans[tier] = partner_api.add_usage_plan(
                f"UsagePlan{tier.capitalize()}",
                name=f"cm-partner-{tier}",
                description=f"Partner pricing tier '{tier}'",
                quota=apigateway.QuotaSettings(limit=daily_quota, period=apigateway.Period.DAY),
                throttle=apigateway.ThrottleSettings(rate_limit=rate, burst_limit=burst),
                api_stages=[apigateway.UsagePlanPerApiStage(api=partner_api, stage=partner_api.deployment_stage)],
            )

        # Permanent tiny plan for the deployed quota-exhaustion test (Q11): no key is bound from
        # here. The test creates and deletes its own throwaway partner and key, so the plan stays
        # unused between runs.
        partner_api.add_usage_plan(
            "UsagePlanItestQuota",
            name="cm-partner-itest-quota",
            description="Integration-test plan for quota exhaustion; never bind a real partner's key",
            quota=apigateway.QuotaSettings(limit=3, period=apigateway.Period.DAY),
            throttle=apigateway.ThrottleSettings(rate_limit=5, burst_limit=10),
            api_stages=[apigateway.UsagePlanPerApiStage(api=partner_api, stage=partner_api.deployment_stage)],
        )

        # Test partner key for deployed acceptance and the MCP tooling (Q6): the value is read
        # with apigateway:GET /apikeys/{id}?includeValue=true, never stored anywhere else.
        test_partner_api_key = partner_api.add_api_key(
            "TestPartnerApiKey",
            api_key_name=f"cm-test-partner-{self.account}-{self.region}",
            description="Test partner API key for deployed acceptance and MCP tooling",
        )
        usage_plans["standard"].add_api_key(test_partner_api_key)

        # Key provisioning (PartnersService.provision_key / disable / delete) runs in ui_backend.
        # With the per-function role split these apigateway grants land on the ui_backend
        # role only, so partner_api and analytics_aggregation no longer carry key-management rights.
        ui_backend_lambda.add_environment("USAGE_PLAN_NAME_PREFIX", "cm-partner-")
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["apigateway:POST", "apigateway:GET", "apigateway:PATCH", "apigateway:DELETE"],
                resources=[
                    f"arn:aws:apigateway:{self.region}::/apikeys",
                    f"arn:aws:apigateway:{self.region}::/apikeys/*",
                    f"arn:aws:apigateway:{self.region}::/usageplans/*/keys",
                    f"arn:aws:apigateway:{self.region}::/usageplans/*/keys/*",
                ],
            )
        )
        # create_api_key(tags=...) tags the key in the same call; API Gateway authorizes tagging as
        # apigateway:PUT on the /tags/<encoded key arn> resource, separately from the key itself.
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["apigateway:PUT"],
                resources=[f"arn:aws:apigateway:{self.region}::/tags/*"],
            )
        )
        # Plan lookup by name and per-key usage (read-only; plans are CDK-owned and never mutated here).
        ui_backend_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["apigateway:GET"],
                resources=[
                    f"arn:aws:apigateway:{self.region}::/usageplans",
                    f"arn:aws:apigateway:{self.region}::/usageplans/*",
                    f"arn:aws:apigateway:{self.region}::/usageplans/*/usage",
                ],
            )
        )

        native_edge_enabled = str(cfg.get("nativeEdgeEnabled", False)).lower() == "true"
        native_content_edge = NativeContentEdge(
            self,
            "NativeContentEdge",
            content_bucket=content_bucket,
            api=api,
            enabled=native_edge_enabled,
            publisher_central_origin_id=cfg.get(
                "publisherCentralOriginId",
                "central-content-origin",
            ),
        )
        self.native_content_edge = native_content_edge

        # Output the API Gateway URL
        CfnOutput(
            self,
            "ApiGatewayUrl",
            value=f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/v1",
            description="AI Content Monetization API Gateway URL",
        )

        # Output the DynamoDB table names
        CfnOutput(
            self,
            "PartnerApiUrl",
            value=partner_api.url,
            description="Partner API base URL (native x-api-key authentication)",
        )
        CfnOutput(self, "PartnerApiId", value=partner_api.rest_api_id, description="Partner API Gateway ID")
        CfnOutput(
            self,
            "TestPartnerApiKeyId",
            value=test_partner_api_key.key_id,
            description="API key id of the test partner key (read the value with apigateway get-api-key --include-value)",
        )
        CfnOutput(self, "ContentTableName", value=content_table.table_name, description="DynamoDB Content Table Name")

        CfnOutput(self, "PartnerTableName", value=partner_table.table_name, description="DynamoDB Partner Table Name")

        CfnOutput(self, "JobsTableName", value=jobs_table.table_name, description="DynamoDB Jobs Table Name")

        CfnOutput(
            self,
            "PricingRulesTableName",
            value=pricing_rules_table.table_name,
            description="DynamoDB Pricing Rules Table Name",
        )

        CfnOutput(
            self,
            "PricingTransactionsTableName",
            value=pricing_transactions_table.table_name,
            description="DynamoDB Pricing Transactions Table Name",
        )

        CfnOutput(
            self,
            "ConnectionsTableName",
            value=connections_table.table_name,
            description="DynamoDB Connections (integration Connection config) Table Name",
        )

        # Output S3 bucket name
        CfnOutput(self, "ContentBucketName", value=content_bucket.bucket_name, description="S3 Content Bucket Name")
        CfnOutput(
            self,
            "NativeContentEdgeDomainName",
            value=native_content_edge.distribution.distribution_domain_name,
            description="Disabled-by-default central content edge domain",
        )
        CfnOutput(
            self,
            "NativeContentEdgeDistributionId",
            value=native_content_edge.distribution.distribution_id,
            description="Central content edge distribution ID",
        )
        CfnOutput(
            self,
            "PublisherEdgeRouterFunctionArn",
            value=native_content_edge.publisher_router_function.function_arn,
            description="Unattached publisher-edge Shape 1 router function ARN",
        )

        # Output Cognito details
        CfnOutput(
            self, "UserPoolId", value=cognito_user_pool_id_param.value_as_string, description="Cognito User Pool ID"
        )

        CfnOutput(
            self,
            "UserPoolClientId",
            value=cognito_user_pool_client_id_param.value_as_string,
            description="Cognito User Pool Client ID",
        )

        CfnOutput(
            self,
            "IdentityPoolId",
            value=cognito_identity_pool_id_param.value_as_string,
            description="Cognito Identity Pool ID",
        )

        CfnOutput(self, "CognitoRegion", value=self.region, description="Cognito Region")

        # Authentication Stack Outputs
        CfnOutput(
            self,
            "AuthenticationServiceLambdaArn",
            value=auth_stack.get_auth_service_lambda().function_arn,
            description="ARN of the Authentication Service Lambda function",
        )

        CfnOutput(
            self,
            "AuthorizerLambdaArn",
            value=auth_stack.get_authorizer_lambda().function_arn,
            description="ARN of the API Gateway Authorizer Lambda function",
        )

        CfnOutput(
            self,
            "AccessLoggerLambdaArn",
            value=auth_stack.get_access_logger_lambda().function_arn,
            description="ARN of the Access Logger Lambda function",
        )

        CfnOutput(
            self,
            "JWTSecretArn",
            value=auth_stack.get_jwt_secret().secret_arn,
            description="ARN of the JWT secret in Secrets Manager",
        )

        CfnOutput(
            self,
            "AccessLogTableName",
            value=auth_stack.get_access_log_table().table_name,
            description="Name of the Access Log DynamoDB table",
        )

        CfnOutput(
            self,
            "AccessRulesEngineLambdaArn",
            value=auth_stack.get_access_rules_engine_lambda().function_arn,
            description="ARN of the Access Rules Engine Lambda function",
        )

        # Test Partner API Key Secret
        # Integrated MCP Server Configuration Outputs
        CfnOutput(
            self,
            "MCPServerConfiguration",
            value=json.dumps(
                {
                    "mcp_server_arn": mcp_server_arn,
                    "external_tools_enabled": True,
                    "analytics_tools_enabled": True,
                    "tool_visibility_control": True,
                    "analytics_endpoint": f"https://{api.rest_api_id}.execute-api.{self.region}.amazonaws.com/v1/analytics-agent/chat",
                }
            ),
            description="Integrated MCP Server configuration for analytics agent",
        )

        # Per-function IAM role ARN outputs (one shared execution role split into three)
        CfnOutput(
            self,
            "PartnerApiExecutionRoleArn",
            value=partner_api_role.role_arn,
            description="ARN of the partner_api Lambda execution role",
        )
        CfnOutput(
            self,
            "UIBackendExecutionRoleArn",
            value=ui_backend_role.role_arn,
            description="ARN of the ui_backend Lambda execution role",
        )
        CfnOutput(
            self,
            "AnalyticsAggregationExecutionRoleArn",
            value=analytics_aggregation_role.role_arn,
            description="ARN of the analytics_aggregation Lambda execution role",
        )

        # New Lambda Function Outputs
        CfnOutput(
            self,
            "PartnerApiLambdaArn",
            value=partner_api_lambda.function_arn,
            description="ARN of the Partner API Lambda function",
        )

        CfnOutput(
            self,
            "UIBackendLambdaArn",
            value=ui_backend_lambda.function_arn,
            description="ARN of the UI Backend Lambda function",
        )

        CfnOutput(
            self,
            "PartnerApiLambdaName",
            value=partner_api_lambda.function_name,
            description="Name of the Partner API Lambda function",
        )

        CfnOutput(
            self,
            "UIBackendLambdaName",
            value=ui_backend_lambda.function_name,
            description="Name of the UI Backend Lambda function",
        )

        CfnOutput(
            self,
            "AnalyticsAggregationLambdaArn",
            value=analytics_aggregation_lambda.function_arn,
            description="ARN of the Analytics Aggregation Lambda function",
        )

        CfnOutput(
            self,
            "AnalyticsAggregationLambdaName",
            value=analytics_aggregation_lambda.function_name,
            description="Name of the Analytics Aggregation Lambda function",
        )

        # Analytics Agent Endpoint moved to UI Stack

        # Raw billable-event store name. Exposed as an output so the integration tier can find
        # the table without hardcoding "cm-usage-events" — the 304-exemption test reads this
        # table before and after a conditional request to prove no billable row was written.
        CfnOutput(
            self,
            "UsageEventsTableName",
            value=usage_events_table.table_name,
            description="Raw usage-event store (canonical billable-event truth)",
        )

        CfnOutput(
            self,
            "IntegratedArchitectureConfig",
            value=json.dumps(
                {
                    "deployment_type": "integrated",
                    "mcp_server_type": "agentcore_runtime",
                    "analytics_agent_type": "mcp_client",
                    "authentication": "cognito",
                    "observability_enabled": True,
                    "tool_categories": {
                        "external_tools": [
                            "search_content",
                            "get_content_details",
                            "access_content",
                            "list_categories",
                            "get_pricing_info",
                        ],
                        "internal_tools": [
                            "get_usage_analytics",
                            "analyze_content_performance",
                            "generate_insights",
                            "get_revenue_metrics",
                            "analyze_bot_behavior",
                            "get_page_context_help",
                            "suggest_page_actions",
                        ],
                    },
                }
            ),
            description="Integrated architecture configuration details",
        )
