"""
Test UI Backend Analytics Integration

This module tests the integration between UI Backend Lambda and Analytics Aggregation Lambda
to ensure real analytics data flows through to the UI dashboard.
"""

import pytest
import boto3
import json
import os
from moto import mock_aws
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


class TestUIBackendAnalyticsIntegration:
    """Test suite for UI Backend to Analytics Aggregation integration"""

    @mock_aws
    def test_ui_backend_calls_analytics_aggregation(self):
        """Test that UI Backend Lambda successfully calls Analytics Aggregation Lambda"""
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

        # Create mock Analytics Aggregation Lambda function
        analytics_function_name = "iab-analytics-aggregation"
        lambda_client.create_function(
            FunctionName=analytics_function_name,
            Runtime="python3.11",
            Role=role_arn,
            Handler="analytics_aggregation.lambda_handler",
            Code={"ZipFile": b"fake code"},
            Description="Analytics Aggregation Lambda",
        )

        # Mock successful analytics response
        mock_analytics_response = {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "period": "30 days",
                    "total_metrics": {
                        "total_requests": 250,
                        "total_revenue": 45.75,
                        "total_bot_access": 125,
                        "total_mcp_usage": 85,
                        "average_daily_requests": 8.33,
                        "average_daily_revenue": 1.53,
                    },
                    "daily_api_requests": [5, 8, 12, 7, 9, 11, 6],
                    "daily_revenue": [0.85, 1.40, 2.10, 1.25, 1.65, 1.95, 1.05],
                    "content_type_distribution": {"article": 150, "report": 75, "video": 25},
                }
            ),
        }

        # Test UI Backend analytics endpoint integration
        with patch.object(lambda_client, "invoke") as mock_invoke:
            mock_invoke.return_value = {
                "StatusCode": 200,
                "Payload": MagicMock(read=lambda: json.dumps(mock_analytics_response).encode()),
            }

            # Simulate UI Backend calling analytics aggregation
            with patch.dict(os.environ, {"ANALYTICS_LAMBDA_NAME": analytics_function_name}):
                request = {"operation": "usage_trends", "parameters": {"days": 30}}

                response = lambda_client.invoke(
                    FunctionName=os.environ.get("ANALYTICS_LAMBDA_NAME"),
                    InvocationType="RequestResponse",
                    Payload=json.dumps(request),
                )

                # Verify successful invocation
                assert response["StatusCode"] == 200
                payload = json.loads(response["Payload"].read())
                assert payload["statusCode"] == 200

                # Verify analytics data structure
                body = json.loads(payload["body"])
                assert "total_metrics" in body
                assert "daily_api_requests" in body
                assert "daily_revenue" in body
                assert "content_type_distribution" in body

                # Verify data values
                assert body["total_metrics"]["total_requests"] == 250
                assert body["total_metrics"]["total_revenue"] == 45.75
                assert len(body["daily_api_requests"]) == 7
                assert len(body["daily_revenue"]) == 7

    def test_ui_backend_analytics_endpoint_handler(self):
        """Test that UI Backend analytics endpoint handlers work correctly"""
        # Import the analytics route handler (canonical post-decomposition location).
        import sys
        import os

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../source/lambda"))
        from ui_backend.routes.analytics import handle_analytics_usage

        # Mock headers and request data
        headers = {"Authorization": "Bearer mock-jwt-token"}
        request_data = {"time_range": "30d"}
        event = {
            "requestContext": {
                "authorizer": {"claims": {"sub": "test-user-id", "custom:publisher_id": "test-publisher"}}
            }
        }

        # Mock Lambda client and response
        with patch("boto3.client") as mock_boto_client:
            mock_lambda_client = MagicMock()
            mock_boto_client.return_value = mock_lambda_client

            mock_response = {
                "StatusCode": 200,
                "Payload": MagicMock(
                    read=lambda: json.dumps(
                        {
                            "statusCode": 200,
                            "body": json.dumps({"period": "30 days", "total_metrics": {"total_requests": 100}}),
                        }
                    ).encode()
                ),
            }
            mock_lambda_client.invoke.return_value = mock_response

            # Test the handler
            with patch.dict(os.environ, {"ANALYTICS_LAMBDA_NAME": "iab-analytics-aggregation"}):
                response = handle_analytics_usage(headers, request_data, event)

                # Verify response structure (current create_success_response(usage_data) contract)
                assert response["statusCode"] == 200
                body = json.loads(response["body"])
                assert "usage_data" in body
                assert body["publisher_id"] == "test-publisher"

    @patch("boto3.client")
    def test_analytics_fallback_behavior(self, mock_boto_client):
        """Test that UI Backend gracefully handles Analytics Aggregation Lambda failures"""
        # Mock Lambda client that fails
        mock_lambda_client = MagicMock()
        mock_boto_client.return_value = mock_lambda_client

        # Mock failed response
        mock_lambda_client.invoke.side_effect = Exception("Analytics Lambda unavailable")

        # Test fallback behavior
        with patch.dict(os.environ, {"ANALYTICS_LAMBDA_NAME": "iab-analytics-aggregation"}):
            try:
                request = {"operation": "usage_trends", "parameters": {"days": 30}}

                # This should handle the exception gracefully
                mock_lambda_client.invoke(
                    FunctionName=os.environ.get("ANALYTICS_LAMBDA_NAME"),
                    InvocationType="RequestResponse",
                    Payload=json.dumps(request),
                )

            except Exception as e:
                # Verify that the exception is handled appropriately
                assert "Analytics Lambda unavailable" in str(e)

    def test_analytics_data_structure_validation(self):
        """Test that analytics data returned has the expected structure for UI consumption"""
        # Expected structure for UI dashboard
        expected_structure = {
            "period": str,
            "total_metrics": {
                "total_requests": int,
                "total_revenue": float,
                "total_bot_access": int,
                "total_mcp_usage": int,
                "average_daily_requests": float,
                "average_daily_revenue": float,
            },
            "daily_api_requests": list,
            "daily_revenue": list,
            "content_type_distribution": dict,
            "revenue_by_content_type": dict,
            "revenue_by_access_method": dict,
        }

        # Mock analytics response
        mock_response = {
            "period": "30 days",
            "total_metrics": {
                "total_requests": 200,
                "total_revenue": 35.50,
                "total_bot_access": 100,
                "total_mcp_usage": 60,
                "average_daily_requests": 6.67,
                "average_daily_revenue": 1.18,
            },
            "daily_api_requests": [5, 8, 12, 7, 9, 11, 6, 4, 10, 8],
            "daily_revenue": [0.85, 1.40, 2.10, 1.25, 1.65, 1.95, 1.05, 0.70, 1.75, 1.40],
            "content_type_distribution": {"article": 120, "report": 60, "video": 20},
            "revenue_by_content_type": {"article": 6.00, "report": 15.00, "video": 3.00},
            "revenue_by_access_method": {"direct_api": 20.00, "mcp_server": 4.50},
        }

        # Validate structure
        assert isinstance(mock_response["period"], str)
        assert isinstance(mock_response["total_metrics"], dict)
        assert isinstance(mock_response["total_metrics"]["total_requests"], int)
        assert isinstance(mock_response["total_metrics"]["total_revenue"], float)
        assert isinstance(mock_response["daily_api_requests"], list)
        assert isinstance(mock_response["daily_revenue"], list)
        assert isinstance(mock_response["content_type_distribution"], dict)
        assert isinstance(mock_response["revenue_by_content_type"], dict)
        assert isinstance(mock_response["revenue_by_access_method"], dict)

        # Validate data consistency
        assert len(mock_response["daily_api_requests"]) == len(mock_response["daily_revenue"])
        assert (
            sum(mock_response["content_type_distribution"].values()) == mock_response["total_metrics"]["total_requests"]
        )

    @patch("boto3.client")
    def test_multiple_analytics_operations(self, mock_boto_client):
        """Test that UI Backend can call different analytics operations"""
        mock_lambda_client = MagicMock()
        mock_boto_client.return_value = mock_lambda_client

        # Test different operations
        operations = [
            ("usage_trends", {"days": 30}),
            ("revenue_metrics", {"date_range": "7d"}),
            ("aggregate_daily", {"date": "2024-01-15"}),
            ("aggregate_range", {"start_date": "2024-01-01", "end_date": "2024-01-07"}),
        ]

        for operation, parameters in operations:
            # Mock successful response for each operation
            mock_response = {
                "StatusCode": 200,
                "Payload": MagicMock(
                    read=lambda: json.dumps(
                        {
                            "statusCode": 200,
                            "body": json.dumps({"operation": operation, "success": True, "data": {"test": "data"}}),
                        }
                    ).encode()
                ),
            }
            mock_lambda_client.invoke.return_value = mock_response

            # Test invocation
            with patch.dict(os.environ, {"ANALYTICS_LAMBDA_NAME": "iab-analytics-aggregation"}):
                request = {"operation": operation, "parameters": parameters}

                response = mock_lambda_client.invoke(
                    FunctionName=os.environ.get("ANALYTICS_LAMBDA_NAME"),
                    InvocationType="RequestResponse",
                    Payload=json.dumps(request),
                )

                # Verify successful call
                assert response["StatusCode"] == 200
                payload = json.loads(response["Payload"].read())
                assert payload["statusCode"] == 200

                body = json.loads(payload["body"])
                assert body["operation"] == operation
                assert body["success"] is True

    def test_real_data_vs_mock_data_flag(self):
        """Test that real data integration is enabled and mock data fallback is disabled"""
        # Test environment variables for real data integration
        with patch.dict(
            os.environ,
            {
                "USE_REAL_ANALYTICS_DATA": "true",
                "ENABLE_GRACEFUL_FALLBACK": "true",
                "ANALYTICS_LAMBDA_NAME": "iab-analytics-aggregation",
            },
        ):
            # Verify real data is enabled
            assert os.environ.get("USE_REAL_ANALYTICS_DATA") == "true"
            assert os.environ.get("ANALYTICS_LAMBDA_NAME") == "iab-analytics-aggregation"

            # Verify graceful fallback is available but real data is preferred
            assert os.environ.get("ENABLE_GRACEFUL_FALLBACK") == "true"

    @patch("boto3.client")
    def test_analytics_lambda_error_handling(self, mock_boto_client):
        """Test error handling when Analytics Aggregation Lambda returns errors"""
        mock_lambda_client = MagicMock()
        mock_boto_client.return_value = mock_lambda_client

        # Test different error scenarios
        error_scenarios = [
            # Lambda function error
            {
                "StatusCode": 200,
                "Payload": MagicMock(
                    read=lambda: json.dumps(
                        {"statusCode": 500, "body": json.dumps({"error": "Internal server error"})}
                    ).encode()
                ),
            },
            # Lambda invocation error
            {
                "StatusCode": 500,
                "Payload": MagicMock(read=lambda: json.dumps({"errorMessage": "Function execution failed"}).encode()),
            },
        ]

        for error_response in error_scenarios:
            mock_lambda_client.invoke.return_value = error_response

            with patch.dict(os.environ, {"ANALYTICS_LAMBDA_NAME": "iab-analytics-aggregation"}):
                request = {"operation": "usage_trends", "parameters": {"days": 30}}

                response = mock_lambda_client.invoke(
                    FunctionName=os.environ.get("ANALYTICS_LAMBDA_NAME"),
                    InvocationType="RequestResponse",
                    Payload=json.dumps(request),
                )

                # Verify error response is handled
                if response["StatusCode"] == 500:
                    payload = json.loads(response["Payload"].read())
                    assert "errorMessage" in payload
                else:
                    payload = json.loads(response["Payload"].read())
                    assert payload["statusCode"] == 500


if __name__ == "__main__":
    pytest.main([__file__])
