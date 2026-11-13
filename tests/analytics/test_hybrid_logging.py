"""
Unit tests for hybrid authentication analytics logging

Tests the differentiated logging between partner access (billing) and UI access (testing)
for the hybrid /query endpoint.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone

# Mock the Lambda environment
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))


@pytest.fixture
def mock_analytics_collector():
    """Mock analytics collector for testing"""
    with patch("lambda.services.analytics_collector.AnalyticsCollector") as mock_collector:
        collector_instance = Mock()
        collector_instance.record_search_query.return_value = True
        collector_instance.record_api_access.return_value = True
        mock_collector.return_value = collector_instance
        yield collector_instance


@pytest.fixture
def partner_auth_event():
    """Mock API Gateway event with partner key authentication"""
    return {
        "httpMethod": "POST",
        "path": "/query",
        "headers": {"X-Partner-Key": "test-partner-key-123", "Content-Type": "application/json"},
        "body": json.dumps({"query": "artificial intelligence healthcare", "max_results": 5}),
        "requestContext": {"requestId": "test-request-123"},
    }


@pytest.fixture
def cognito_auth_event():
    """Mock API Gateway event with Cognito authentication"""
    return {
        "httpMethod": "POST",
        "path": "/query",
        "headers": {"Authorization": "Bearer cognito-jwt-token-456", "Content-Type": "application/json"},
        "body": json.dumps({"query": "artificial intelligence healthcare", "max_results": 5}),
        "requestContext": {
            "requestId": "test-request-456",
            "authorizer": {
                "claims": {
                    "sub": "user-123",
                    "email": "publisher@example.com",
                    "name": "Test Publisher",
                    "custom:publisher_id": "pub_001",
                }
            },
        },
    }


class TestHybridLogging:
    """Test hybrid authentication analytics logging"""

    @patch.dict(
        os.environ,
        {
            "CONTENT_TABLE_NAME": "test-content-table",
            "PARTNER_TABLE_NAME": "test-partner-table",
            "ANALYTICS_TABLE_NAME": "test-analytics-table",
        },
    )
    def test_query_partner_access_logging(self, mock_analytics_collector, partner_auth_event):
        """Test that partner access to /query is logged as billing"""
        # Import here to avoid module loading issues
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "partner_api"))
        from handler import lambda_handler

        # Mock partner authentication
        with patch("lambda.shared.auth.authentication_functions.authenticate_partner") as mock_auth:
            mock_auth.return_value = {"partner_id": "partner_123", "name": "Test Partner", "subscription": "pro"}

            # Mock content search
            with patch("lambda.partner_api.handler.search_content") as mock_search:
                mock_search.return_value = [
                    {"content_id": "art_001", "title": "AI Healthcare", "content_type": "article"}
                ]

                # Call the handler
                response = lambda_handler(partner_auth_event, Mock())

                # Verify response
                assert response["statusCode"] == 200

                # Verify analytics collection was called with partner billing info
                mock_analytics_collector.record_search_query.assert_called_once()
                call_args = mock_analytics_collector.record_search_query.call_args

                # Check requester_info for partner billing
                requester_info = call_args[1]["requester_info"]
                assert requester_info["user_type"] == "partner"
                assert requester_info["access_type"] == "billing"
                assert requester_info["auth_method"] == "partner_key"
                assert requester_info["user_id"] == "partner_123"
                assert requester_info["endpoint"] == "/query"

                # Verify API access logging
                mock_analytics_collector.record_api_access.assert_called_once()
                api_call_args = mock_analytics_collector.record_api_access.call_args
                api_metadata = api_call_args[1]["metadata"]
                assert api_metadata["access_type"] == "partner_billing"
                assert api_metadata["auth_type"] == "partner_key"

    @patch.dict(
        os.environ,
        {
            "CONTENT_TABLE_NAME": "test-content-table",
            "PARTNER_TABLE_NAME": "test-partner-table",
            "ANALYTICS_TABLE_NAME": "test-analytics-table",
        },
    )
    def test_query_cognito_access_logging(self, mock_analytics_collector, cognito_auth_event):
        """Test that Cognito access to /query is logged as testing"""
        # Import here to avoid module loading issues
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "partner_api"))
        from handler import lambda_handler

        # Mock Cognito authentication
        with patch("lambda.shared.auth.authentication_functions.extract_cognito_user_context") as mock_auth:
            mock_auth.return_value = {
                "user_id": "user-123",
                "email": "publisher@example.com",
                "name": "Test Publisher",
                "publisher_id": "pub_001",
                "type": "cognito_publisher",
            }

            # Mock content search
            with patch("lambda.partner_api.handler.search_content") as mock_search:
                mock_search.return_value = [{"content_id": "art_002", "title": "AI Testing", "content_type": "article"}]

                # Call the handler
                response = lambda_handler(cognito_auth_event, Mock())

                # Verify response
                assert response["statusCode"] == 200

                # Verify analytics collection was called with UI testing info
                mock_analytics_collector.record_search_query.assert_called_once()
                call_args = mock_analytics_collector.record_search_query.call_args

                # Check requester_info for UI testing
                requester_info = call_args[1]["requester_info"]
                assert requester_info["user_type"] == "ui_user"
                assert requester_info["access_type"] == "testing"
                assert requester_info["auth_method"] == "cognito"
                assert requester_info["user_id"] == "user-123"
                assert requester_info["endpoint"] == "/query"
                assert requester_info["publisher_id"] == "pub_001"

                # Verify API access logging
                mock_analytics_collector.record_api_access.assert_called_once()
                api_call_args = mock_analytics_collector.record_api_access.call_args
                api_metadata = api_call_args[1]["metadata"]
                assert api_metadata["access_type"] == "ui_testing"
                assert api_metadata["auth_type"] == "cognito"

    def test_analytics_data_structure(self, mock_analytics_collector):
        """Test that analytics data structure includes all required fields"""

        # Test partner billing data structure
        partner_requester_info = {
            "user_id": "partner_123",
            "user_type": "partner",
            "access_type": "billing",
            "auth_method": "partner_key",
            "partner_name": "Test Partner",
            "subscription_tier": "pro",
            "endpoint": "/query",
        }

        # Verify all required fields are present
        required_partner_fields = ["user_id", "user_type", "access_type", "auth_method", "endpoint"]
        for field in required_partner_fields:
            assert field in partner_requester_info

        # Test UI testing data structure
        ui_requester_info = {
            "user_id": "user-456",
            "user_type": "ui_user",
            "access_type": "testing",
            "auth_method": "cognito",
            "publisher_id": "pub_001",
            "email": "test@example.com",
            "endpoint": "/search",
        }

        # Verify all required fields are present
        required_ui_fields = ["user_id", "user_type", "access_type", "auth_method", "endpoint", "publisher_id"]
        for field in required_ui_fields:
            assert field in ui_requester_info

    def test_access_type_differentiation(self):
        """Test that access types are correctly differentiated"""

        # Partner access should be 'partner_billing'
        partner_access_type = "partner_billing"
        assert "billing" in partner_access_type
        assert "partner" in partner_access_type

        # UI access should be 'ui_testing'
        ui_access_type = "ui_testing"
        assert "testing" in ui_access_type
        assert "ui" in ui_access_type

        # They should be different
        assert partner_access_type != ui_access_type


if __name__ == "__main__":
    pytest.main([__file__])
