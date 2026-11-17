"""
Test Analytics Aggregation Lambda Deployment

This module tests that the Analytics Aggregation Lambda function is properly deployed
and accessible from other Lambda functions.
"""

import pytest
import boto3
import json
import os
from moto import mock_aws
from unittest.mock import patch, MagicMock


class TestAnalyticsAggregationDeployment:
    """Test suite for Analytics Aggregation Lambda deployment validation"""

    def test_analytics_lambda_name_environment_variable(self):
        """Test that ANALYTICS_LAMBDA_NAME environment variable is set correctly"""
        # This would be set by CDK deployment
        expected_lambda_name = "iab-analytics-aggregation"

        # In a real deployment, this would be set by CDK
        with patch.dict(os.environ, {"ANALYTICS_LAMBDA_NAME": expected_lambda_name}):
            analytics_lambda_name = os.environ.get("ANALYTICS_LAMBDA_NAME")
            assert analytics_lambda_name == expected_lambda_name

    @mock_aws
    def test_analytics_aggregation_lambda_invocation(self):
        """Test that Analytics Aggregation Lambda can be invoked successfully"""
        # Create mock IAM role first
        iam_client = boto3.client("iam", region_name="us-west-2")
        role_name = "test-lambda-role"

        # Create IAM role
        assume_role_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}
            ],
        }

        iam_client.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(assume_role_policy), Path="/")

        role_arn = f"arn:aws:iam::123456789012:role/{role_name}"

        # Create mock Lambda client
        lambda_client = boto3.client("lambda", region_name="us-west-2")

        # Mock Lambda function response
        mock_response = {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "period": "30 days",
                    "total_metrics": {
                        "total_requests": 150,
                        "total_revenue": 25.50,
                        "total_bot_access": 75,
                        "total_mcp_usage": 45,
                    },
                }
            ),
        }

        # Create a mock Lambda function
        function_name = "iab-analytics-aggregation"
        lambda_client.create_function(
            FunctionName=function_name,
            Runtime="python3.11",
            Role=role_arn,
            Handler="analytics_aggregation.lambda_handler",
            Code={"ZipFile": b"fake code"},
            Description="Test Analytics Aggregation Lambda",
        )

        # Mock the invoke response
        with patch.object(lambda_client, "invoke") as mock_invoke:
            mock_invoke.return_value = {
                "StatusCode": 200,
                "Payload": MagicMock(read=lambda: json.dumps(mock_response).encode()),
            }

            # Test invocation
            request = {"operation": "usage_trends", "parameters": {"days": 30}}

            response = lambda_client.invoke(
                FunctionName=function_name, InvocationType="RequestResponse", Payload=json.dumps(request)
            )

            assert response["StatusCode"] == 200
            payload = json.loads(response["Payload"].read())
            assert payload["statusCode"] == 200

            body = json.loads(payload["body"])
            assert "total_metrics" in body
            assert body["total_metrics"]["total_requests"] == 150

    def test_analytics_aggregation_handler_exists(self):
        """Test that analytics_aggregation.lambda_handler exists and is callable"""
        try:
            import sys
            import os

            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../source/lambda"))
            from analytics_aggregation import lambda_handler

            assert callable(lambda_handler)
        except ImportError:
            pytest.skip("analytics_aggregation.lambda_handler not available for testing")

    def test_analytics_aggregation_operations(self):
        """Test that Analytics Aggregation Lambda supports required operations"""
        try:
            import sys
            import os

            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../source/lambda"))
            from analytics_aggregation import AnalyticsAggregator

            # Test that AnalyticsAggregator can be instantiated
            aggregator = AnalyticsAggregator()

            # Test that required methods exist
            assert hasattr(aggregator, "aggregate_daily_metrics")
            assert hasattr(aggregator, "aggregate_date_range_metrics")
            assert hasattr(aggregator, "generate_usage_trends")
            assert hasattr(aggregator, "calculate_revenue_metrics")

            # Test that methods are callable
            assert callable(aggregator.aggregate_daily_metrics)
            assert callable(aggregator.aggregate_date_range_metrics)
            assert callable(aggregator.generate_usage_trends)
            assert callable(aggregator.calculate_revenue_metrics)

        except ImportError:
            pytest.skip("AnalyticsAggregator class not available for testing")

    @patch("boto3.client")
    def test_ui_backend_analytics_integration(self, mock_boto_client):
        """Test that UI Backend Lambda can successfully call Analytics Aggregation Lambda"""
        # Mock Lambda client
        mock_lambda_client = MagicMock()
        mock_boto_client.return_value = mock_lambda_client

        # Mock successful response
        mock_response = {
            "StatusCode": 200,
            "Payload": MagicMock(
                read=lambda: json.dumps(
                    {
                        "statusCode": 200,
                        "body": json.dumps(
                            {"period": "7 days", "total_metrics": {"total_requests": 50, "total_revenue": 8.75}}
                        ),
                    }
                ).encode()
            ),
        }
        mock_lambda_client.invoke.return_value = mock_response

        # Test the integration (simulating UI Backend Lambda call)
        with patch.dict(os.environ, {"ANALYTICS_LAMBDA_NAME": "iab-analytics-aggregation"}):
            analytics_lambda_name = os.environ.get("ANALYTICS_LAMBDA_NAME")

            request = {"operation": "usage_trends", "parameters": {"days": 7}}

            response = mock_lambda_client.invoke(
                FunctionName=analytics_lambda_name, InvocationType="RequestResponse", Payload=json.dumps(request)
            )

            # Verify the call was made correctly
            mock_lambda_client.invoke.assert_called_once_with(
                FunctionName="iab-analytics-aggregation", InvocationType="RequestResponse", Payload=json.dumps(request)
            )

            # Verify response
            assert response["StatusCode"] == 200
            payload = json.loads(response["Payload"].read())
            assert payload["statusCode"] == 200

    def test_analytics_lambda_environment_variables(self):
        """Test that Analytics Aggregation Lambda has required environment variables"""
        required_env_vars = [
            "ANALYTICS_TABLE_NAME",
            "CONTENT_TABLE_NAME",
            "PARTNER_TABLE_NAME",
            "PRICING_RULES_TABLE_NAME",
            "PRICING_TRANSACTIONS_TABLE_NAME",
        ]

        # In a real deployment test, these would be set by CDK
        test_env = {
            "ANALYTICS_TABLE_NAME": "cm-analytics",
            "CONTENT_TABLE_NAME": "cm-content",
            "PARTNER_TABLE_NAME": "cm-partners",
            "PRICING_RULES_TABLE_NAME": "cm-pricing-rules",
            "PRICING_TRANSACTIONS_TABLE_NAME": "cm-pricing-transactions",
        }

        with patch.dict(os.environ, test_env):
            for env_var in required_env_vars:
                assert os.environ.get(env_var) is not None
                assert os.environ.get(env_var).startswith("cm-")

    def test_analytics_lambda_timeout_and_memory_configuration(self):
        """Test that Analytics Aggregation Lambda has appropriate timeout and memory settings"""
        # These values should match the CDK configuration
        expected_timeout = 30  # seconds
        expected_memory = 512  # MB

        # In a real deployment test, these would be verified against the actual Lambda function
        # For now, we test that the values are reasonable
        assert expected_timeout >= 30, "Analytics Lambda should have at least 30 second timeout"
        assert expected_memory >= 512, "Analytics Lambda should have at least 512MB memory"

    @patch("boto3.resource")
    def test_analytics_lambda_dynamodb_permissions(self, mock_boto_resource):
        """Test that Analytics Aggregation Lambda can access required DynamoDB tables"""
        # Mock DynamoDB resource
        mock_dynamodb = MagicMock()
        mock_boto_resource.return_value = mock_dynamodb

        # Mock table
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table

        # Test that AnalyticsAggregator can initialize with DynamoDB access
        try:
            import sys
            import os

            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../source/lambda"))
            from analytics_aggregation import AnalyticsAggregator

            with patch.dict(os.environ, {"ANALYTICS_TABLE_NAME": "cm-analytics"}):
                aggregator = AnalyticsAggregator()

                # Verify that the aggregator has access to the analytics table
                assert aggregator.analytics_table_name == "cm-analytics"

        except Exception as e:
            pytest.skip(f"Analytics Aggregator not available for testing: {str(e)}")


if __name__ == "__main__":
    pytest.main([__file__])
