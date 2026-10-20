"""
GEO Enrichment Pipeline Stack

Standard Step Functions workflow (extract → classify+enrich → embed → index →
persist) triggered by S3 ObjectCreated events on the content bucket's ingest/
prefix, plus the OpenSearch Serverless NextGen collection group that backs
hybrid search.

Trigger path is S3 → EventBridge → Step Functions (bucket has EventBridge
notifications enabled in the main stack). A direct S3→Lambda notification
would put the notification resource in the bucket's stack and create a
dependency cycle between the two stacks; EventBridge keeps the coupling
one-way (this stack only reads the bucket/table references).
"""


from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

# Fallback defaults if cdk.json context.iab is absent. Real values live in
# cdk.json → context.iab; the stack reads them in __init__ (see below).
DEFAULT_COLLECTION_NAME = "cm-content-search"
DEFAULT_COLLECTION_GROUP_NAME = "iab-content-group"
DEFAULT_OPENSEARCH_INDEX = "iab-content-hybrid"
# SSM parameter lets consumers in other stacks (partner API /query) discover
# the collection endpoint at runtime without a cross-stack env-var dependency.
DEFAULT_ENDPOINT_SSM_PARAM = "/iab-llm/opensearch/endpoint"
DEFAULT_CLASSIFIER_MODEL_ID = "global.anthropic.claude-sonnet-4-6"
DEFAULT_LICENSE_ID = "lic_default_v1"

# Per-function execution role name prefixes from ContentMonetizationStack. Each role is
# region-suffixed (role_name=f"iab-llm-<fn>-role-{region}") so the same account can deploy to
# multiple regions without an IAM name clash; the full name is composed at the use-site with
# self.region. The two functions that SEARCH OpenSearch — partner_api (/query) and ui_backend
# (discovery/search routes) — are granted read-only search access in the data access policy
# below. (These replaced the former single shared execution role.)
SEARCH_READER_ROLE_NAME_PREFIXES = (
    "iab-llm-partner-api-role",
    "iab-llm-ui-backend-role",
)


class PipelineStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        content_table_name: str,
        content_bucket_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Reference the content table by name rather than importing the Table
        # construct from ContentMonetizationStack. from_table_name builds the
        # ARN locally (no CloudFormation cross-stack Export/ImportValue), which
        # keeps the stacks decoupled (M0) and lets ContentMonetizationStack
        # rename/replace the table without the export being "in use" here.
        content_table = dynamodb.Table.from_table_name(
            self, "ImportedContentTable", content_table_name
        )

        # Same decoupling for the content bucket: reference it by its
        # deterministic name (iab-llm-content-{account}-{region}) via
        # from_bucket_name so no cross-stack Export/ImportValue is created.
        content_bucket = s3.Bucket.from_bucket_name(
            self, "ImportedContentBucket", content_bucket_name
        )

        # Centralized config from cdk.json (context.iab); fall back to the
        # module defaults above so synth still works without the context block.
        cfg = self.node.try_get_context("iab") or {}
        OPENSEARCH_INDEX = cfg.get("openSearchIndexName", DEFAULT_OPENSEARCH_INDEX)
        ENDPOINT_SSM_PARAM = cfg.get("openSearchEndpointSsmParam", DEFAULT_ENDPOINT_SSM_PARAM)
        CLASSIFIER_MODEL_ID = cfg.get("classifierModelId", DEFAULT_CLASSIFIER_MODEL_ID)
        LICENSE_ID = cfg.get("defaultLicenseId", DEFAULT_LICENSE_ID)
        # NOTE: openSearchCollectionName / openSearchCollectionGroupName are read
        # by app.py (which owns the top-level SearchCollectionStack), not here —
        # PipelineStack no longer instantiates the collection.

        # ── OpenSearch Serverless collection → SearchCollectionStack (top-level) ──
        # The collection, its NextGen group, enc/net/data-access policies, and
        # the endpoint SSM parameter live in the free-standing (top-level)
        # SearchCollectionStack so a per-release SolutionVersion tag bump cannot
        # REPLACE the collection (its Tags property is Update-requires-Replacement;
        # see search_collection_stack.py module docstring). That stack names the
        # pipeline + backfill roles (below) as its WRITER principals by their
        # fixed composed ARN strings, and the partner_api / ui_backend roles as
        # readers. Consumers discover the endpoint at runtime from the SSM
        # parameter BY NAME — no cross-stack export.

        # ── Pipeline Lambda (least-privilege role, NOT the shared demo role) ──
        # Fixed, region-suffixed role name so the top-level search stack can
        # compose this role's ARN as a writer-principal STRING (no cross-stack
        # export).
        pipeline_role = iam.Role(
            self,
            "PipelineLambdaRole",
            role_name=f"iab-llm-geo-pipeline-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Least-privilege role for the GEO enrichment pipeline steps",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
        )
        pipeline_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"{content_bucket.bucket_arn}/ingest/*"],
            )
        )
        pipeline_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject"],
                resources=[
                    f"{content_bucket.bucket_arn}/content/*",
                    f"{content_bucket.bucket_arn}/embeddings/*",
                    f"{content_bucket.bucket_arn}/pipeline/*",
                ],
            )
        )
        pipeline_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    # Claude (classify/enrich) via cross-region inference profiles
                    # + the regional foundation models the profile routes to
                    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/*anthropic.claude*",
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude*",
                    # Titan v2 (embeddings)
                    "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0",
                ],
            )
        )
        pipeline_role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:PutItem"],
                resources=[content_table.table_arn],
            )
        )

        pipeline_lambda = _lambda.Function(
            self,
            "PipelineStepsFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/content_pipeline_deployment.zip"),
            timeout=Duration.minutes(5),
            memory_size=1024,
            role=pipeline_role,
            environment={
                "CONTENT_TABLE_NAME": content_table.table_name,
                "CONTENT_BUCKET_NAME": content_bucket.bucket_name,
                # No OPENSEARCH_ENDPOINT env: the collection now lives in the
                # nested search stack, so its endpoint is not a same-scope ref
                # here. opensearch_client._resolve_endpoint() falls back to the
                # SSM parameter (/iab-llm/opensearch/endpoint) the nested stack
                # publishes; the ssm:GetParameter grant is added below.
                "OPENSEARCH_INDEX": OPENSEARCH_INDEX,
                "CLASSIFIER_MODEL_ID": CLASSIFIER_MODEL_ID,
                "DEFAULT_LICENSE_ID": LICENSE_ID,
                "HISTORY_TTL_DAYS": "90",
                "REGION": self.region,
            },
            description="GEO pipeline steps: extract / classify+enrich / embed / index / persist",
        )

        backfill_role = iam.Role(
            self,
            "SearchProjectionBackfillRole",
            role_name=f"iab-llm-geo-backfill-role-{self.region}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Least-privilege role for explicit OpenSearch projection backfills",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
        )
        backfill_role.add_to_policy(
            iam.PolicyStatement(
                actions=["dynamodb:Scan"],
                resources=[content_table.table_arn],
            )
        )
        backfill_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[
                    f"{content_bucket.bucket_arn}/content/*",
                    f"{content_bucket.bucket_arn}/embeddings/*",
                ],
            )
        )
        backfill_lambda = _lambda.Function(
            self,
            "SearchProjectionBackfillFunction",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/search_projection_backfill_deployment.zip"),
            timeout=Duration.minutes(15),
            memory_size=1024,
            role=backfill_role,
            environment={
                "CONTENT_TABLE_NAME": content_table.table_name,
                "CONTENT_BUCKET_NAME": content_bucket.bucket_name,
                # No OPENSEARCH_ENDPOINT env (collection is in the nested search
                # stack); resolved from the SSM parameter at runtime.
                "OPENSEARCH_INDEX": OPENSEARCH_INDEX,
                "REGION": self.region,
            },
            description="Explicit migration for rebuilding the OpenSearch content projection",
        )

        # ── Search collection: OWNED BY A TOP-LEVEL STACK (app.py) ──
        # The AOSS collection, its NextGen group, enc/net/data-access policies,
        # and the endpoint SSM parameter live in the free-standing
        # SearchCollectionStack (see infrastructure/search_collection_stack.py).
        # It is top-level so SolutionVersion is never one of ITS stack-level tags
        # (app.py tags it with tag_solution_no_version), which is what keeps the
        # collection's immutable Tags property from being mutated — and thus the
        # collection from being REPLACED — on a release bump. PipelineStack keeps
        # the writer roles (they attach to the Lambdas here) and grants them AOSS
        # IAM access on a WILDCARD collection ARN, so there is no reference back
        # into the search stack (zero cross-stack coupling in either direction).
        # The search stack's data-access policy names these roles by their fixed
        # composed ARN strings; the Lambdas resolve the endpoint from the SSM
        # parameter by name at runtime.
        collection_arn = f"arn:aws:aoss:{self.region}:{self.account}:collection/*"
        for role in (pipeline_role, backfill_role):
            role.add_to_policy(
                iam.PolicyStatement(actions=["aoss:APIAccessAll"], resources=[collection_arn])
            )
            role.add_to_policy(
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[
                        f"arn:aws:ssm:{self.region}:{self.account}:parameter{ENDPOINT_SSM_PARAM}"
                    ],
                )
            )

        # ── Step Functions: Standard workflow (demo-visible history,
        #    exactly-once, no 5-min cap). States pass S3 pointers only. ──
        def invoke(step_id: str, payload: dict) -> tasks.LambdaInvoke:
            task = tasks.LambdaInvoke(
                self,
                step_id,
                lambda_function=pipeline_lambda,
                payload=sfn.TaskInput.from_object(payload),
                payload_response_only=True,
            )
            task.add_retry(
                errors=["ThrottlingException", "Lambda.TooManyRequestsException", "States.TaskFailed"],
                interval=Duration.seconds(5),
                backoff_rate=2.0,
                max_attempts=3,
            )
            return task

        state = {
            "content_id": sfn.JsonPath.string_at("$.content_id"),
            "bucket": sfn.JsonPath.string_at("$.bucket"),
            "source_key": sfn.JsonPath.string_at("$.source_key"),
            "extracted_key": sfn.JsonPath.string_at("$.extracted_key"),
            "md_key": sfn.JsonPath.string_at("$.md_key"),
            "title": sfn.JsonPath.string_at("$.title"),
            "content_type": sfn.JsonPath.string_at("$.content_type"),
            "markdown_schema_version": sfn.JsonPath.string_at("$.markdown_schema_version"),
        }

        extract_task = invoke(
            "Extract",
            {
                "step": "extract",
                "bucket": sfn.JsonPath.string_at("$.bucket"),
                "key": sfn.JsonPath.string_at("$.key"),
            },
        )
        classify_task = invoke(
            "ClassifyEnrich",
            {
                "step": "classify_enrich",
                **state,
            },
        )
        embed_task = invoke(
            "Embed",
            {
                "step": "embed",
                **state,
                "enrichment_key": sfn.JsonPath.string_at("$.enrichment_key"),
            },
        )
        index_task = invoke(
            "IndexOpenSearch",
            {
                "step": "index",
                **state,
                "enrichment_key": sfn.JsonPath.string_at("$.enrichment_key"),
                "embedding_key": sfn.JsonPath.string_at("$.embedding_key"),
            },
        )
        persist_task = invoke(
            "PersistDynamoDB",
            {
                "step": "persist",
                **state,
                "enrichment_key": sfn.JsonPath.string_at("$.enrichment_key"),
                "embedding_key": sfn.JsonPath.string_at("$.embedding_key"),
            },
        )

        definition = extract_task.next(classify_task).next(embed_task).next(index_task).next(persist_task)

        state_machine = sfn.StateMachine(
            self,
            "GeoPipeline",
            state_machine_name="iab-geo-enrichment-pipeline",
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(30),
            logs=sfn.LogOptions(
                destination=logs.LogGroup(
                    self,
                    "GeoPipelineLogs",
                    log_group_name="/aws/stepfunctions/iab-geo-enrichment-pipeline",
                    retention=logs.RetentionDays.ONE_MONTH,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
                level=sfn.LogLevel.ERROR,
            ),
        )

        # ── Trigger: S3 ObjectCreated on ingest/ prefix → EventBridge → SFN ──
        # Prefix scoping is load-bearing: the same bucket holds pipeline
        # OUTPUTS (content/, embeddings/, pipeline/) — an unscoped trigger
        # would re-trigger on its own writes.
        dlq = sqs.Queue(
            self,
            "IngestTriggerDLQ",
            queue_name="iab-geo-pipeline-trigger-dlq",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )

        ingest_rule = events.Rule(
            self,
            "IngestObjectCreatedRule",
            description="Start the GEO pipeline when an object lands under ingest/",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {"name": [content_bucket.bucket_name]},
                    "object": {"key": [{"prefix": "ingest/"}]},
                },
            ),
        )
        ingest_rule.add_target(
            targets.SfnStateMachine(
                state_machine,
                input=events.RuleTargetInput.from_object(
                    {
                        "bucket": events.EventField.from_path("$.detail.bucket.name"),
                        "key": events.EventField.from_path("$.detail.object.key"),
                    }
                ),
                dead_letter_queue=dlq,
                retry_attempts=3,
            )
        )

        # ── Outputs ──
        CfnOutput(
            self,
            "StateMachineArn",
            value=state_machine.state_machine_arn,
            description="GEO enrichment pipeline state machine ARN",
        )
        CfnOutput(
            self,
            "PipelineLambdaName",
            value=pipeline_lambda.function_name,
            description="Pipeline steps Lambda function name",
        )
        CfnOutput(
            self,
            "SearchProjectionBackfillLambdaName",
            value=backfill_lambda.function_name,
            description="Explicit OpenSearch projection backfill Lambda name",
        )
        CfnOutput(self, "TriggerDLQUrl", value=dlq.queue_url, description="DLQ for failed pipeline trigger deliveries")
