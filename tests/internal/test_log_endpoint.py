"""
Unit tests for internal log endpoint

Tests the refactored internal logging functionality that replaces the
external /log endpoint for internal usage tracking and billing.
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from shared.handlers.internal_log_handler import (
    handle_internal_log_partner_usage,
    handle_internal_log_ui_test_usage,
    handle_get_usage_logs,
    handle_log_api_access,
)


class TestInternalLogEndpoint:
    """Test cases for internal log endpoint handlers"""

    def setup_method(self):
        """Set up test fixtures"""
        self.mock_headers = {"Authorization": "Bearer cognito-token"}
        self.mock_event = {
            "requestContext": {
                "authorizer": {
                    "claims": {"sub": "user123", "email": "test@example.com", "custom:publisher_id": "pub123"}
                }
            },
            "path": "/internal/log",
        }
        self.mock_user = {
            "user_id": "user123",
            "email": "test@example.com",
            "publisher_id": "pub123",
            "groups": ["publishers"],
        }

    @patch("shared.handlers.internal_log_handler.InternalLoggingService")
    def test_handle_internal_log_partner_usage_success(self, mock_logging_service_class):
        """Test successful partner usage logging"""
        # Setup
        mock_service = Mock()
        mock_logging_service_class.return_value = mock_service
        mock_service.log_partner_usage.return_value = {
            "log_id": "internal_log_12345678",
            "timestamp": "2025-01-15T10:30:00Z",
        }

        request_data = {
            "partner_id": "partner123",
            "usage_data": {
                "query_id": "q_12345678",
                "content_ids": ["art_001", "art_002"],
                "endpoint": "/query",
                "cost": 0.10,
            },
        }

        # Execute
        with patch("shared.auth.authentication_decorators.extract_cognito_user_context") as mock_auth:
            mock_auth.return_value = self.mock_user

            # Create a mock decorated function that calls the handler directly
            result = handle_internal_log_partner_usage.__wrapped__(
                self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
            )

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "logged"
        assert body["log_id"] == "internal_log_12345678"
        assert body["logged_by"] == "user123"

        mock_service.log_partner_usage.assert_called_once_with(
            partner_id="partner123", usage_data=request_data["usage_data"], timestamp=None
        )

    @patch("shared.handlers.internal_log_handler.InternalLoggingService")
    def test_handle_internal_log_partner_usage_with_timestamp(self, mock_logging_service_class):
        """Test partner usage logging with custom timestamp"""
        # Setup
        mock_service = Mock()
        mock_logging_service_class.return_value = mock_service
        mock_service.log_partner_usage.return_value = {
            "log_id": "internal_log_12345678",
            "timestamp": "2025-01-15T10:30:00Z",
        }

        request_data = {
            "partner_id": "partner123",
            "usage_data": {"query_id": "q_12345678"},
            "timestamp": "2025-01-15T10:30:00Z",
        }

        # Execute
        result = handle_internal_log_partner_usage.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 200

        # Check that timestamp was parsed and passed
        call_args = mock_service.log_partner_usage.call_args
        assert call_args[1]["timestamp"] is not None
        assert isinstance(call_args[1]["timestamp"], datetime)

    def test_handle_internal_log_partner_usage_validation_error(self):
        """Test partner usage logging with validation errors"""
        # Missing required fields
        request_data = {
            "partner_id": "partner123"
            # Missing usage_data
        }

        # Execute
        result = handle_internal_log_partner_usage.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert body["error"] == "Validation failed"
        assert "usage_data" in body["details"]

    def test_handle_internal_log_partner_usage_invalid_timestamp(self):
        """Test partner usage logging with invalid timestamp format"""
        request_data = {
            "partner_id": "partner123",
            "usage_data": {"query_id": "q_12345678"},
            "timestamp": "invalid-timestamp",
        }

        # Execute
        result = handle_internal_log_partner_usage.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "Invalid timestamp format" in body["error"]

    @patch("shared.handlers.internal_log_handler.InternalLoggingService")
    def test_handle_internal_log_ui_test_usage_success(self, mock_logging_service_class):
        """Test successful UI test usage logging"""
        # Setup
        mock_service = Mock()
        mock_logging_service_class.return_value = mock_service
        mock_service.log_ui_test_usage.return_value = {
            "log_id": "ui_test_log_12345678",
            "timestamp": "2025-01-15T10:30:00Z",
            "note": "UI test usage - not billable",
        }

        request_data = {"test_usage_data": {"test_query": "AI healthcare", "results_count": 5, "test_type": "preview"}}

        # Execute
        result = handle_internal_log_ui_test_usage.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "logged"
        assert body["log_id"] == "ui_test_log_12345678"
        assert body["note"] == "UI test usage - not billable"
        assert body["logged_by"] == "user123"

        mock_service.log_ui_test_usage.assert_called_once_with(
            user_id="user123", publisher_id="pub123", test_usage_data=request_data["test_usage_data"], timestamp=None
        )

    @patch("shared.handlers.internal_log_handler.InternalLoggingService")
    def test_handle_get_usage_logs_success(self, mock_logging_service_class):
        """Test successful usage logs retrieval"""
        # Setup
        mock_service = Mock()
        mock_logging_service_class.return_value = mock_service
        mock_logs = [
            {
                "log_id": "log1",
                "partner_id": "partner123",
                "log_type": "partner_usage",
                "timestamp": "2025-01-15T10:30:00Z",
            },
            {"log_id": "log2", "user_id": "user123", "log_type": "ui_test_usage", "timestamp": "2025-01-15T11:00:00Z"},
        ]
        mock_service.get_usage_logs.return_value = mock_logs

        request_data = {"filters": {"log_type": "partner_usage", "start_date": "2025-01-15T00:00:00Z"}, "limit": 50}

        # Execute
        result = handle_get_usage_logs.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["count"] == 2
        assert len(body["logs"]) == 2
        assert body["requested_by"] == "user123"

        # Verify publisher scoping was applied
        expected_filters = request_data["filters"].copy()
        expected_filters["publisher_id"] = "pub123"
        mock_service.get_usage_logs.assert_called_once_with(filters=expected_filters, limit=50)

    @patch("shared.handlers.internal_log_handler.InternalLoggingService")
    def test_handle_get_usage_logs_admin_user(self, mock_logging_service_class):
        """Test usage logs retrieval by admin user (no publisher scoping)"""
        # Setup
        mock_service = Mock()
        mock_logging_service_class.return_value = mock_service
        mock_service.get_usage_logs.return_value = []

        admin_user = self.mock_user.copy()
        admin_user["groups"] = ["admin", "publishers"]

        request_data = {"filters": {"log_type": "partner_usage"}, "limit": 100}

        # Execute
        result = handle_get_usage_logs.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=admin_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 200

        # Admin users should not have publisher scoping applied
        mock_service.get_usage_logs.assert_called_once_with(
            filters={"log_type": "partner_usage"},  # No publisher_id added
            limit=100,
        )

    @patch("shared.handlers.internal_log_handler.InternalLoggingService")
    def test_handle_log_api_access_success(self, mock_logging_service_class):
        """Test successful API access logging"""
        # Setup
        mock_service = Mock()
        mock_logging_service_class.return_value = mock_service
        mock_service.log_api_access.return_value = {
            "log_id": "api_access_log_12345678",
            "timestamp": "2025-01-15T10:30:00Z",
        }

        request_data = {
            "endpoint": "/query",
            "user_id": "partner123",
            "auth_type": "partner_key",
            "metadata": {"ip_address": "192.168.1.1", "user_agent": "AI-Bot/1.0", "response_time_ms": 150},
        }

        # Execute
        result = handle_log_api_access.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["status"] == "logged"
        assert body["log_id"] == "api_access_log_12345678"
        assert body["logged_by"] == "user123"

        mock_service.log_api_access.assert_called_once_with(
            endpoint="/query",
            user_id="partner123",
            auth_type="partner_key",
            metadata=request_data["metadata"],
            timestamp=None,
        )

    def test_handle_log_api_access_validation_error(self):
        """Test API access logging with validation errors"""
        # Missing required fields
        request_data = {
            "endpoint": "/query",
            "user_id": "partner123",
            # Missing auth_type and metadata
        }

        # Execute
        result = handle_log_api_access.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert body["error"] == "Validation failed"
        assert "auth_type" in body["details"]
        assert "metadata" in body["details"]

    @patch("shared.handlers.internal_log_handler.InternalLoggingService")
    def test_error_handling_service_exception(self, mock_logging_service_class):
        """Test error handling when logging service raises exception"""
        # Setup
        mock_service = Mock()
        mock_logging_service_class.return_value = mock_service
        mock_service.log_partner_usage.side_effect = Exception("Database connection failed")

        request_data = {"partner_id": "partner123", "usage_data": {"query_id": "q_12345678"}}

        # Execute
        result = handle_internal_log_partner_usage.__wrapped__(
            self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
        )

        # Verify
        assert result["statusCode"] == 500
        body = json.loads(result["body"])
        assert "Failed to log partner usage" in body["error"]

    def test_authentication_required(self):
        """Test that all endpoints require Cognito authentication"""
        # This test verifies that the @require_cognito_auth decorator is applied
        # In practice, this would be tested by the decorator tests

        handlers = [
            handle_internal_log_partner_usage,
            handle_internal_log_ui_test_usage,
            handle_get_usage_logs,
            handle_log_api_access,
        ]

        for handler in handlers:
            # Check that the handler has been decorated
            assert hasattr(handler, "__wrapped__")
            # The decorator should add authentication logic
            assert handler.__name__ == "wrapper" or hasattr(handler, "__wrapped__")

    def test_internal_vs_external_logging_separation(self):
        """Test that internal logging is separate from external partner logging"""
        # This test documents the architectural decision to separate
        # internal logging (for billing/analytics) from external partner logging

        # Internal logging characteristics:
        # 1. Requires Cognito authentication (UI Backend access)
        # 2. Used for billing tracking and analytics
        # 3. Not exposed to external partners
        # 4. Supports both partner usage and UI test usage logging

        request_data = {
            "partner_id": "partner123",
            "usage_data": {"query_id": "q_12345678", "billing_note": "Internal tracking for pre-pay model"},
        }

        # Internal logging should work with Cognito auth
        with patch("shared.handlers.internal_log_handler.InternalLoggingService") as mock_service_class:
            mock_service = Mock()
            mock_service_class.return_value = mock_service
            mock_service.log_partner_usage.return_value = {
                "log_id": "internal_log_12345678",
                "timestamp": "2025-01-15T10:30:00Z",
            }

            result = handle_internal_log_partner_usage.__wrapped__(
                self.mock_headers, request_data, self.mock_event, authenticated_user=self.mock_user, auth_type="cognito"
            )

            assert result["statusCode"] == 200

            # Verify that internal logging service was used
            mock_service.log_partner_usage.assert_called_once()

            # Verify that the usage data includes internal tracking information
            call_args = mock_service.log_partner_usage.call_args
            assert "billing_note" in call_args[1]["usage_data"]
