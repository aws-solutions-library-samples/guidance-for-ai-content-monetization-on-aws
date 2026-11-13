"""
AI Content Monetization API - UI Stack

This stack deploys the React-based demo UI for the AI Content Monetization API framework.
It creates an S3 bucket for static hosting, CloudFront distribution for global delivery,
and supports feature flag configuration for demo vs. production deployments.
"""

import os

import aws_cdk as cdk
from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class UIStack(Stack):
    """
    CDK Stack for deploying the AI Content Monetization API Demo UI

    This stack creates:
    - S3 bucket for static website hosting
    - CloudFront distribution for global content delivery
    - S3 deployment for UI assets
    - Proper security configurations
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        api_gateway_url: str = None,
        enable_demo_mode: bool = True,
        cognito_user_pool_id: str = None,
        cognito_user_pool_client_id: str = None,
        cognito_identity_pool_id: str = None,
        analytics_agent_arn: str = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Configuration
        self.api_gateway_url = api_gateway_url or "https://api.example.com"
        self.enable_demo_mode = enable_demo_mode
        self.cognito_user_pool_id = cognito_user_pool_id
        self.cognito_user_pool_client_id = cognito_user_pool_client_id
        self.cognito_identity_pool_id = cognito_identity_pool_id
        self.analytics_agent_arn = analytics_agent_arn

        # Analytics integration is now handled through main API Gateway

        # Create S3 bucket for static website hosting
        self.ui_bucket = self._create_ui_bucket()

        # Create CloudFront distribution
        self.distribution = self._create_cloudfront_distribution()

        # TM-02 (security-hardening-phase2): publish the deployed portal origin to SSM so the
        # backend Lambdas resolve the CORS Access-Control-Allow-Origin at invoke time (no bare "*",
        # no cross-stack export, no deploy-order cycle — the backend reads it by name). The backend
        # grants ssm:GetParameter scoped to this exact parameter. Referenced by NAME only, matching
        # UI_ORIGIN_SSM_PARAMETER in content_monetization_stack.py.
        ssm.StringParameter(
            self,
            "UiOriginParameter",
            parameter_name="/cm/ui/origin",
            string_value=f"https://{self.distribution.distribution_domain_name}",
            description="Deployed portal (CloudFront) origin; read by the backend for CORS (TM-02)",
        )

        # Deploy UI assets
        self._deploy_ui_assets()

        # Create outputs
        self._create_outputs()

    def _create_ui_bucket(self) -> s3.Bucket:
        """Create S3 bucket for static website hosting"""

        bucket = s3.Bucket(
            self,
            "UIBucket",
            bucket_name=f"iab-llm-ingest-ui-{self.account}-{self.region}",
            # Remove website configuration - we'll use CloudFront for SPA routing
            public_read_access=False,  # We'll use CloudFront OAI
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            auto_delete_objects=True,  # For demo purposes
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        # Add CORS configuration for API calls
        bucket.add_cors_rule(
            allowed_methods=[s3.HttpMethods.GET, s3.HttpMethods.POST, s3.HttpMethods.PUT, s3.HttpMethods.DELETE],
            allowed_origins=["*"],  # Will be restricted by CloudFront
            allowed_headers=["*"],
            max_age=3000,
        )

        return bucket

    def _create_cloudfront_distribution(self) -> cloudfront.Distribution:
        """Create CloudFront distribution for the UI"""

        # Create Origin Access Identity for S3 (OAC not available in this CDK version)
        oai = cloudfront.OriginAccessIdentity(
            self, "UIOriginAccessIdentity", comment="OAI for AI Content Monetization UI"
        )

        # Create CloudFront distribution
        distribution = cloudfront.Distribution(
            self,
            "UIDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(self.ui_bucket, origin_access_identity=oai),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cached_methods=cloudfront.CachedMethods.CACHE_GET_HEAD_OPTIONS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                compress=True,
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(
                        self.api_gateway_url.replace("https://", "").replace("http://", ""),
                        protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.CORS_S3_ORIGIN,
                ),
                # Analytics API behavior will be added after API Gateway is created
            },
            default_root_object="index.html",
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404, response_http_status=200, response_page_path="/index.html", ttl=Duration.minutes(5)
                ),
                cloudfront.ErrorResponse(
                    http_status=403, response_http_status=200, response_page_path="/index.html", ttl=Duration.minutes(5)
                ),
            ],
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,  # US, Canada, Europe
            comment="AI Content Monetization API Demo UI Distribution",
        )

        # Grant CloudFront OAI access to S3 bucket
        self.ui_bucket.grant_read(oai)

        return distribution

    def _deploy_ui_assets(self):
        """Deploy UI assets to S3 bucket"""

        # Check if UI build directory exists
        ui_build_path = os.path.join(os.path.dirname(__file__), "..", "source", "ui", "dist")

        if os.path.exists(ui_build_path):
            # Deploy built UI assets
            s3deploy.BucketDeployment(
                self,
                "UIDeployment",
                sources=[s3deploy.Source.asset(ui_build_path)],
                destination_bucket=self.ui_bucket,
                distribution=self.distribution,
                distribution_paths=["/*"],
                memory_limit=512,
                ephemeral_storage_size=cdk.Size.mebibytes(1024),
            )
        else:
            # Create a placeholder deployment with build instructions and environment configuration
            placeholder_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>AI Content Monetization API - Build Required</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        .code {{ background: #f4f4f4; padding: 10px; border-radius: 4px; }}
        .env-config {{ background: #e8f4f8; padding: 15px; border-radius: 4px; margin: 20px 0; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>AI Content Monetization API UI</h1>
        <p>The UI needs to be built before deployment. Please run:</p>
        <div class="code">
            <pre>cd ui
npm install
{"npm run build:demo" if self.enable_demo_mode else "npm run build:production"}
cdk deploy</pre>
        </div>
        
        <div class="env-config">
            <h3>Environment Configuration</h3>
            <p>Demo Mode: <strong>{"Enabled" if self.enable_demo_mode else "Disabled"}</strong></p>
            <p>API Gateway URL: <strong>{self.api_gateway_url}</strong></p>
            <p>Analytics API URL: <strong>{f"https://{self.analytics_api.rest_api_id}.execute-api.{self.region}.amazonaws.com/prod" if hasattr(self, "analytics_api") else "Not configured"}</strong></p>
            <p>Region: <strong>{self.region}</strong></p>
            {f"<p>Analytics Agent ARN: <strong>{self.analytics_agent_arn}</strong></p>" if self.analytics_agent_arn else ""}
            {f"<p>Cognito User Pool ID: <strong>{self.cognito_user_pool_id}</strong></p>" if self.cognito_user_pool_id else ""}
        </div>
        
        <h3>Required Environment Variables</h3>
        <div class="code">
            <pre>VITE_API_BASE_URL={self.api_gateway_url}
VITE_AWS_REGION={self.region}
VITE_ENABLE_DEMO={"true" if self.enable_demo_mode else "false"}
{f"VITE_ANALYTICS_AGENT_ARN={self.analytics_agent_arn}" if self.analytics_agent_arn else "# VITE_ANALYTICS_AGENT_ARN=<not-configured>"}
VITE_CLOUDWATCH_GENAI_URL=https://{self.region}.console.aws.amazon.com/cloudwatch/home?region={self.region}#/gen-ai-observability/agent-core/agents
{f"VITE_USER_POOL_ID={self.cognito_user_pool_id}" if self.cognito_user_pool_id else "# VITE_USER_POOL_ID=<not-configured>"}
{f"VITE_USER_POOL_CLIENT_ID={self.cognito_user_pool_client_id}" if self.cognito_user_pool_client_id else "# VITE_USER_POOL_CLIENT_ID=<not-configured>"}</pre>
        </div>
    </div>
</body>
</html>"""

            # Create temporary directory with placeholder
            import shutil
            import tempfile

            with tempfile.TemporaryDirectory() as temp_dir:
                placeholder_path = os.path.join(temp_dir, "index.html")
                with open(placeholder_path, "w") as f:
                    f.write(placeholder_content)

                s3deploy.BucketDeployment(
                    self,
                    "UIPlaceholderDeployment",
                    sources=[s3deploy.Source.asset(temp_dir)],
                    destination_bucket=self.ui_bucket,
                    distribution=self.distribution,
                    distribution_paths=["/*"],
                )

    def _create_outputs(self):
        """Create CloudFormation outputs"""

        CfnOutput(self, "UIBucketName", value=self.ui_bucket.bucket_name, description="S3 bucket name for UI assets")

        CfnOutput(self, "UIBucketArn", value=self.ui_bucket.bucket_arn, description="S3 bucket ARN for UI assets")

        CfnOutput(
            self,
            "CloudFrontDistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID",
        )

        CfnOutput(
            self,
            "CloudFrontDomainName",
            value=self.distribution.distribution_domain_name,
            description="CloudFront distribution domain name",
        )

        CfnOutput(
            self,
            "UIUrl",
            value=f"https://{self.distribution.distribution_domain_name}",
            description="AI Content Monetization API Demo UI URL",
        )

        CfnOutput(
            self,
            "ComparisonFixtureUrl",
            value=f"https://{self.distribution.distribution_domain_name}/fixtures/comparison/article.html",
            description="Fixture article for the scrape-vs-API comparison (served from the UI build's public/ assets)",
        )

        CfnOutput(self, "DemoModeEnabled", value=str(self.enable_demo_mode), description="Whether demo mode is enabled")

        CfnOutput(
            self, "APIGatewayURL", value=self.api_gateway_url, description="API Gateway URL configured for the UI"
        )

        # Analytics API outputs removed - using main API Gateway

        if self.analytics_agent_arn:
            CfnOutput(
                self,
                "AnalyticsAgentARN",
                value=self.analytics_agent_arn,
                description="Analytics Agent ARN deployed on AgentCore",
            )

        if self.cognito_user_pool_id:
            CfnOutput(
                self,
                "CognitoUserPoolId",
                value=self.cognito_user_pool_id,
                description="Cognito User Pool ID for authentication",
            )

        if self.cognito_user_pool_client_id:
            CfnOutput(
                self,
                "CognitoUserPoolClientId",
                value=self.cognito_user_pool_client_id,
                description="Cognito User Pool Client ID for authentication",
            )

        if self.cognito_identity_pool_id:
            CfnOutput(
                self,
                "CognitoIdentityPoolId",
                value=self.cognito_identity_pool_id,
                description="Cognito Identity Pool ID for AWS resource access",
            )

        # Observability Configuration Outputs
        CfnOutput(
            self,
            "CloudWatchGenAIUrl",
            value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home?region={self.region}#genai",
            description="CloudWatch GenAI Observability URL for deep links",
        )

        CfnOutput(
            self, "ObservabilityRegion", value=self.region, description="AWS Region for observability configuration"
        )

    @property
    def ui_url(self) -> str:
        """Get the UI URL"""
        return f"https://{self.distribution.distribution_domain_name}"

    @property
    def bucket_name(self) -> str:
        """Get the S3 bucket name"""
        return self.ui_bucket.bucket_name
