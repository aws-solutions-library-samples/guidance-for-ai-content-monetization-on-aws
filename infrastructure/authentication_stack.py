"""
IAB Authentication Stack - Nested CDK Stack

This stack handles:
- Partner authentication and API key validation
- JWT token generation and verification
- API Gateway Lambda authorizer
- Partner management infrastructure

Requirements covered:
- 2.1: Validate authentication credentials
- 7.3: Implement industry-standard authentication mechanisms
"""

from typing import Optional

from aws_cdk import CfnOutput, Duration, NestedStack, RemovalPolicy
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class AuthenticationStack(NestedStack):
    """
    Nested stack for IAB authentication services
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        partner_table: dynamodb.Table,
        content_table: dynamodb.Table = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.partner_table = partner_table
        self.content_table = content_table

        # Secrets Manager secret for JWT signing key
        self.jwt_secret = secretsmanager.Secret(
            self,
            "JWTSecret",
            secret_name="iab-jwt-secret",
            description="JWT signing secret for IAB authentication service",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template="{}",
                generate_string_key="jwt_secret",
                password_length=64,
                exclude_characters='"@/\\',
            ),
        )

        # DynamoDB table for access logs
        self.access_log_table = dynamodb.Table(
            self,
            "AccessLogTable",
            table_name="iab-access-logs",
            partition_key=dynamodb.Attribute(name="log_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # For demo purposes
            point_in_time_recovery=True,
            time_to_live_attribute="ttl",  # Auto-delete old logs
        )

        # Add GSI for querying logs by partner
        self.access_log_table.add_global_secondary_index(
            index_name="partner-timestamp-index",
            partition_key=dynamodb.Attribute(name="partner_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
        )

        # Add GSI for querying logs by content
        self.access_log_table.add_global_secondary_index(
            index_name="content-timestamp-index",
            partition_key=dynamodb.Attribute(name="content_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
        )

        # Environment variables for authentication Lambda functions
        auth_env = {
            "PARTNERS_TABLE_NAME": self.partner_table.table_name,
            "ACCESS_LOG_TABLE_NAME": self.access_log_table.table_name,
            "JWT_SECRET_NAME": self.jwt_secret.secret_name,
            "JWT_EXPIRY_HOURS": "24",
            "REGION": self.region,
        }

        # IAM role for Authentication Lambda functions
        auth_lambda_role = iam.Role(
            self,
            "AuthenticationLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ],
        )

        # Grant permissions to access DynamoDB tables
        self.partner_table.grant_read_write_data(auth_lambda_role)
        self.access_log_table.grant_read_write_data(auth_lambda_role)

        # Grant access to content table if provided
        if self.content_table:
            self.content_table.grant_read_data(auth_lambda_role)

        # Grant permission to access JWT secret
        self.jwt_secret.grant_read(auth_lambda_role)

        # Grant CloudWatch permissions for custom metrics.
        # cloudwatch:PutMetricData has NO resource-level permissions — AWS requires Resource="*"
        # for this action (there is no metric/namespace ARN to scope to). The grant is instead
        # bounded by the only mechanism the API supports: a cloudwatch:namespace condition
        # restricting writes to the IAB/Authentication and IAB/AccessLogger namespaces. The "*"
        # is therefore an API-level-only wildcard, condition-scoped, not an over-broad grant.
        auth_lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": ["IAB/Authentication", "IAB/AccessLogger"]}},
            )
        )

        # Authentication Service Lambda Function
        self.auth_service_lambda = _lambda.Function(
            self,
            "AuthenticationServiceLambda",
            function_name="iab-authentication-service",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="authentication_service.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/shared_services_deployment.zip"),
            role=auth_lambda_role,
            environment=auth_env,
            timeout=Duration.seconds(30),
            memory_size=256,
            description="IAB Authentication Service - API key validation and JWT token management",
        )

        # API Gateway Lambda Authorizer Function
        self.authorizer_lambda = _lambda.Function(
            self,
            "AuthorizerLambda",
            function_name="iab-api-authorizer",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="authentication_service.authorizer_handler",
            code=_lambda.Code.from_asset("source/lambda/shared_services_deployment.zip"),
            role=auth_lambda_role,
            environment=auth_env,
            timeout=Duration.seconds(10),
            memory_size=128,
            description="IAB API Gateway Authorizer - Partner key and JWT token validation",
        )

        # Access Logger Lambda Function
        self.access_logger_lambda = _lambda.Function(
            self,
            "AccessLoggerLambda",
            function_name="iab-access-logger",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="access_logger.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/shared_services_deployment.zip"),
            role=auth_lambda_role,
            environment=auth_env,
            timeout=Duration.seconds(15),
            memory_size=256,
            description="IAB Access Logger - Log API access for billing and analytics",
        )

        # Access Rules Engine Lambda Function
        self.access_rules_engine_lambda = _lambda.Function(
            self,
            "AccessRulesEngineLambda",
            function_name="iab-access-rules-engine",
            runtime=_lambda.Runtime.PYTHON_3_11,
            handler="access_rules_engine.lambda_handler",
            code=_lambda.Code.from_asset("source/lambda/shared_services_deployment.zip"),
            role=auth_lambda_role,
            environment={
                **auth_env,
                # Table name comes from the construct — the content table no longer
                # has a fixed physical name (composite-key replacement).
                "CONTENT_TABLE_NAME": self.content_table.table_name if self.content_table else "",
            },
            timeout=Duration.seconds(30),
            memory_size=512,
            description="IAB Access Rules Engine - Enforce access policies and geographic restrictions",
        )

        # CloudWatch Log Groups
        auth_service_log_group = logs.LogGroup(
            self,
            "AuthServiceLogGroup",
            log_group_name=f"/aws/lambda/{self.auth_service_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        authorizer_log_group = logs.LogGroup(
            self,
            "AuthorizerLogGroup",
            log_group_name=f"/aws/lambda/{self.authorizer_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        access_logger_log_group = logs.LogGroup(
            self,
            "AccessLoggerLogGroup",
            log_group_name=f"/aws/lambda/{self.access_logger_lambda.function_name}",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.DESTROY,
        )

        access_rules_engine_log_group = logs.LogGroup(
            self,
            "AccessRulesEngineLogGroup",
            log_group_name=f"/aws/lambda/{self.access_rules_engine_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # CloudWatch Metrics and Alarms for Access Patterns

        # Metric for authentication failures
        auth_failure_metric = cloudwatch.Metric(
            namespace="IAB/Authentication",
            metric_name="AuthenticationFailures",
            dimensions_map={"Service": "AI-Content-Monetization"},
            statistic="Sum",
            period=Duration.minutes(5),
        )

        # Metric for access attempts
        access_attempts_metric = cloudwatch.Metric(
            namespace="IAB/AccessLogger",
            metric_name="AccessAttempts",
            dimensions_map={"Service": "AI-Content-Monetization"},
            statistic="Sum",
            period=Duration.minutes(5),
        )

        # Metric for blocked access attempts
        blocked_access_metric = cloudwatch.Metric(
            namespace="IAB/AccessLogger",
            metric_name="BlockedAccess",
            dimensions_map={"Service": "AI-Content-Monetization"},
            statistic="Sum",
            period=Duration.minutes(5),
        )

        # Alarm for high authentication failure rate
        auth_failure_alarm = cloudwatch.Alarm(
            self,
            "AuthenticationFailureAlarm",
            alarm_name="IAB-High-Authentication-Failures",
            alarm_description="High rate of authentication failures detected",
            metric=auth_failure_metric,
            threshold=50,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # Alarm for unusual access patterns
        access_spike_alarm = cloudwatch.Alarm(
            self,
            "AccessSpikeAlarm",
            alarm_name="IAB-Unusual-Access-Pattern",
            alarm_description="Unusual spike in access attempts detected",
            metric=access_attempts_metric,
            threshold=1000,
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # Alarm for high blocked access rate
        blocked_access_alarm = cloudwatch.Alarm(
            self,
            "BlockedAccessAlarm",
            alarm_name="IAB-High-Blocked-Access",
            alarm_description="High rate of blocked access attempts",
            metric=blocked_access_metric,
            threshold=100,
            evaluation_periods=3,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        # CloudWatch Dashboard for Access Patterns
        access_dashboard = cloudwatch.Dashboard(
            self,
            "AccessPatternsDashboard",
            dashboard_name="IAB-Access-Patterns",
            widgets=[
                [
                    cloudwatch.GraphWidget(
                        title="Authentication Metrics",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/Lambda",
                                metric_name="Invocations",
                                dimensions_map={"FunctionName": self.auth_service_lambda.function_name},
                                statistic="Sum",
                                period=Duration.minutes(5),
                            ),
                            auth_failure_metric,
                        ],
                        width=12,
                        height=6,
                    )
                ],
                [
                    cloudwatch.GraphWidget(
                        title="Access Patterns",
                        left=[access_attempts_metric, blocked_access_metric],
                        width=12,
                        height=6,
                    )
                ],
                [
                    cloudwatch.GraphWidget(
                        title="Lambda Performance",
                        left=[
                            cloudwatch.Metric(
                                namespace="AWS/Lambda",
                                metric_name="Duration",
                                dimensions_map={"FunctionName": self.access_logger_lambda.function_name},
                                statistic="Average",
                                period=Duration.minutes(5),
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/Lambda",
                                metric_name="Errors",
                                dimensions_map={"FunctionName": self.access_logger_lambda.function_name},
                                statistic="Sum",
                                period=Duration.minutes(5),
                            ),
                        ],
                        width=12,
                        height=6,
                    )
                ],
                [
                    cloudwatch.SingleValueWidget(
                        title="DynamoDB Metrics",
                        metrics=[
                            cloudwatch.Metric(
                                namespace="AWS/DynamoDB",
                                metric_name="ConsumedReadCapacityUnits",
                                dimensions_map={"TableName": self.access_log_table.table_name},
                                statistic="Sum",
                                period=Duration.minutes(5),
                            ),
                            cloudwatch.Metric(
                                namespace="AWS/DynamoDB",
                                metric_name="ConsumedWriteCapacityUnits",
                                dimensions_map={"TableName": self.access_log_table.table_name},
                                statistic="Sum",
                                period=Duration.minutes(5),
                            ),
                        ],
                        width=12,
                        height=6,
                    )
                ],
            ],
        )

        # Outputs
        CfnOutput(
            self,
            "AuthenticationServiceLambdaArn",
            value=self.auth_service_lambda.function_arn,
            description="ARN of the Authentication Service Lambda function",
        )

        CfnOutput(
            self,
            "AuthorizerLambdaArn",
            value=self.authorizer_lambda.function_arn,
            description="ARN of the API Gateway Authorizer Lambda function",
        )

        CfnOutput(
            self,
            "AccessLoggerLambdaArn",
            value=self.access_logger_lambda.function_arn,
            description="ARN of the Access Logger Lambda function",
        )

        CfnOutput(
            self,
            "JWTSecretArn",
            value=self.jwt_secret.secret_arn,
            description="ARN of the JWT secret in Secrets Manager",
        )

        CfnOutput(
            self,
            "AccessLogTableName",
            value=self.access_log_table.table_name,
            description="Name of the Access Log DynamoDB table",
        )

        CfnOutput(
            self,
            "AccessRulesEngineLambdaArn",
            value=self.access_rules_engine_lambda.function_arn,
            description="ARN of the Access Rules Engine Lambda function",
        )

        CfnOutput(
            self,
            "AccessPatternsDashboardURL",
            value=f"https://{self.region}.console.aws.amazon.com/cloudwatch/home?region={self.region}#dashboards:name={access_dashboard.dashboard_name}",
            description="URL to the Access Patterns CloudWatch Dashboard",
        )

        CfnOutput(
            self,
            "AuthenticationFailureAlarmArn",
            value=auth_failure_alarm.alarm_arn,
            description="ARN of the Authentication Failure Alarm",
        )

        CfnOutput(
            self, "AccessSpikeAlarmArn", value=access_spike_alarm.alarm_arn, description="ARN of the Access Spike Alarm"
        )

        CfnOutput(
            self,
            "BlockedAccessAlarmArn",
            value=blocked_access_alarm.alarm_arn,
            description="ARN of the Blocked Access Alarm",
        )

    def get_auth_service_lambda(self) -> _lambda.Function:
        """Get the authentication service Lambda function"""
        return self.auth_service_lambda

    def get_authorizer_lambda(self) -> _lambda.Function:
        """Get the API Gateway authorizer Lambda function"""
        return self.authorizer_lambda

    def get_access_logger_lambda(self) -> _lambda.Function:
        """Get the access logger Lambda function"""
        return self.access_logger_lambda

    def get_jwt_secret(self) -> secretsmanager.Secret:
        """Get the JWT secret"""
        return self.jwt_secret

    def get_access_log_table(self) -> dynamodb.Table:
        """Get the access log table"""
        return self.access_log_table

    def get_access_rules_engine_lambda(self) -> _lambda.Function:
        """Get the access rules engine Lambda function"""
        return self.access_rules_engine_lambda
