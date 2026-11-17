#!/usr/bin/env python3
"""
Integration tests for UI Backend Lambda Handler

Tests the complete integration of the UI Backend Lambda function.
"""

import json
import pytest
import os
import sys
from unittest.mock import Mock, patch

# Add the lambda directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "ui_backend"))

# Import the handler
from ui_backend.handler import lambda_handler


class TestUIBackendIntegration:
    """Integration test cases for UI Backend Lambda handler"""

    def setup_method(self):
        """Set up test fixtures"""
        self.mock_context = Mock()
        self.mock_context.aws_request_id = "integration-test-request-id"

        # Base event structure with Cognito authentication
        self.base_event = {
            "httpMethod": "GET",
            "path": "/analytics/metrics",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer test-token"},
            "body": None,
            "queryStringParameters": None,
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:username": "integration_test_user",
                        "email": "integration@example.com",
                        "custom:publisher_id": "integration-publisher",
                        "custom:publisher_name": "Integration Test Publisher",
                        "sub": "integration-user-123",
                        "custom:access_level": "admin",
                    }
                }
            },
        }

    def test_internal_log_integration(self):
        """Test internal log endpoint integration"""
        event = self.base_event.copy()
        event["path"] = "/log"
        event["httpMethod"] = "POST"
        event["body"] = json.dumps({"event_type": "ui_test", "data": {"test": "integration log data"}})

        response = lambda_handler(event, self.mock_context)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "logged" in body
        assert body["logged"] is True
        assert "log_id" in body

    def test_authentication_required(self):
        """Test that all endpoints require authentication"""
        # Remove authentication context
        event = self.base_event.copy()
        event["requestContext"] = {}
        event["path"] = "/analytics/metrics"

        response = lambda_handler(event, self.mock_context)

        # Should return 401 or 403 for missing authentication
        assert response["statusCode"] in [401, 403]

    def test_cors_headers_present(self):
        """Test that CORS headers are present in responses"""
        event = self.base_event.copy()
        event["path"] = "/analytics/metrics"
        event["httpMethod"] = "GET"

        response = lambda_handler(event, self.mock_context)

        assert response["statusCode"] == 200
        assert "headers" in response
        headers = response["headers"]
        assert "Access-Control-Allow-Origin" in headers
        assert "Access-Control-Allow-Headers" in headers
        assert "Access-Control-Allow-Methods" in headers


if __name__ == "__main__":
    pytest.main([__file__])
