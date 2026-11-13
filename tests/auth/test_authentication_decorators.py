"""
Unit tests for authentication decorators

Tests the new standardized authentication decorators with consistent function signatures.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from shared.auth.authentication_decorators import (
    require_partner_auth,
    require_cognito_auth,
    require_partner_or_cognito_auth,
    optional_auth,
    require_full_auth,  # Deprecated
)


class TestAuthenticationDecorators:
    """Test cases for authentication decorators"""

    def setup_method(self):
        """Set up test fixtures"""
        self.mock_headers = {"Content-Type": "application/json"}
        self.mock_request_data = {"query": "test query"}
        self.mock_event = {
            "requestContext": {
                "authorizer": {
                    "claims": {"sub": "user123", "email": "test@example.com", "custom:publisher_id": "pub123"}
                }
            },
            "path": "/test",
        }
        self.mock_kwargs = {}

    @patch("shared.auth.authentication_decorators.resolve_partner_from_event")
    def test_require_partner_auth_success(self, mock_resolve_partner):
        """Test successful partner authentication"""
        # Setup
        mock_partner = {"partner_id": "partner123", "name": "Test Partner"}
        mock_resolve_partner.return_value = mock_partner

        @require_partner_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"partner_id": kwargs["authenticated_user"]["partner_id"], "auth_type": kwargs["auth_type"]}
                ),
            }

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["partner_id"] == "partner123"
        assert body["auth_type"] == "partner_key"
        mock_resolve_partner.assert_called_once_with(self.mock_event)

    @patch("shared.auth.authentication_decorators.resolve_partner_from_event")
    def test_require_partner_auth_failure(self, mock_resolve_partner):
        """Test failed partner authentication"""
        # Setup
        mock_resolve_partner.return_value = None

        @require_partner_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {"statusCode": 200, "body": "success"}

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert body["error_code"] == "invalid_api_key"

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    def test_require_cognito_auth_success_cognito(self, mock_authenticate_jwt, mock_extract_cognito):
        """Test successful Cognito authentication"""
        # Setup
        mock_cognito_user = {"user_id": "user123", "email": "test@example.com", "publisher_id": "pub123"}
        mock_extract_cognito.return_value = mock_cognito_user

        @require_cognito_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"user_id": kwargs["authenticated_user"]["user_id"], "auth_type": kwargs["auth_type"]}
                ),
            }

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["user_id"] == "user123"
        assert body["auth_type"] == "cognito"
        mock_extract_cognito.assert_called_once_with(self.mock_event)
        mock_authenticate_jwt.assert_not_called()

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    def test_require_cognito_auth_success_jwt(self, mock_authenticate_jwt, mock_extract_cognito):
        """Test successful JWT authentication fallback"""
        # Setup
        mock_extract_cognito.return_value = None
        mock_jwt_user = {"user_id": "user456", "type": "custom_publisher"}
        mock_authenticate_jwt.return_value = mock_jwt_user

        @require_cognito_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"user_id": kwargs["authenticated_user"]["user_id"], "auth_type": kwargs["auth_type"]}
                ),
            }

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["user_id"] == "user456"
        assert body["auth_type"] == "jwt"
        mock_extract_cognito.assert_called_once_with(self.mock_event)
        mock_authenticate_jwt.assert_called_once_with(self.mock_headers)

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    def test_require_cognito_auth_failure(self, mock_authenticate_jwt, mock_extract_cognito):
        """Test failed Cognito authentication"""
        # Setup
        mock_extract_cognito.return_value = None
        mock_authenticate_jwt.return_value = None

        @require_cognito_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {"statusCode": 200, "body": "success"}

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 401
        body = json.loads(result["body"])
        assert "Invalid or missing Cognito token" in body["error"]

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    @patch("shared.auth.authentication_decorators.resolve_partner_from_event")
    def test_require_partner_or_cognito_auth_cognito_priority(
        self, mock_resolve_partner, mock_authenticate_jwt, mock_extract_cognito
    ):
        """Test that Cognito authentication has priority in hybrid decorator"""
        # Setup
        mock_cognito_user = {"user_id": "user123", "type": "cognito"}
        mock_partner = {"partner_id": "partner123", "name": "Test Partner"}

        mock_extract_cognito.return_value = mock_cognito_user
        mock_authenticate_jwt.return_value = None
        mock_resolve_partner.return_value = mock_partner

        @require_partner_or_cognito_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "user_id": kwargs["authenticated_user"].get("user_id"),
                        "partner_id": kwargs["authenticated_user"].get("partner_id"),
                        "auth_type": kwargs["auth_type"],
                    }
                ),
            }

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["user_id"] == "user123"
        assert body["partner_id"] is None
        assert body["auth_type"] == "cognito"

        # Cognito should be tried first, partner auth should not be called
        mock_extract_cognito.assert_called_once_with(self.mock_event)
        mock_resolve_partner.assert_not_called()

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    @patch("shared.auth.authentication_decorators.resolve_partner_from_event")
    def test_require_partner_or_cognito_auth_partner_fallback(
        self, mock_resolve_partner, mock_authenticate_jwt, mock_extract_cognito
    ):
        """Test partner authentication fallback in hybrid decorator"""
        # Setup
        mock_extract_cognito.return_value = None
        mock_authenticate_jwt.return_value = None
        mock_partner = {"partner_id": "partner123", "name": "Test Partner"}
        mock_resolve_partner.return_value = mock_partner

        @require_partner_or_cognito_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"partner_id": kwargs["authenticated_user"]["partner_id"], "auth_type": kwargs["auth_type"]}
                ),
            }

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["partner_id"] == "partner123"
        assert body["auth_type"] == "partner_key"

        # All auth methods should be tried
        mock_extract_cognito.assert_called_once_with(self.mock_event)
        mock_authenticate_jwt.assert_called_once_with(self.mock_headers)
        mock_resolve_partner.assert_called_once_with(self.mock_event)

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    @patch("shared.auth.authentication_decorators.resolve_partner_from_event")
    def test_require_partner_or_cognito_auth_failure(
        self, mock_resolve_partner, mock_authenticate_jwt, mock_extract_cognito
    ):
        """Test failed hybrid authentication"""
        # Setup
        mock_extract_cognito.return_value = None
        mock_authenticate_jwt.return_value = None
        mock_resolve_partner.return_value = None

        @require_partner_or_cognito_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {"statusCode": 200, "body": "success"}

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 401
        body = json.loads(result["body"])
        assert "Authentication required: API key or Cognito token" in body["error"]
        # Partners branch on the code, not the message; it is registered in openapi-spec.yaml.
        assert body["error_code"] == "authentication_required"

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    @patch("shared.auth.authentication_decorators.resolve_partner_from_event")
    def test_optional_auth_with_authentication(
        self, mock_resolve_partner, mock_authenticate_jwt, mock_extract_cognito
    ):
        """Test optional authentication with valid credentials"""
        # Setup
        mock_partner = {"partner_id": "partner123", "name": "Test Partner"}
        mock_extract_cognito.return_value = None
        mock_authenticate_jwt.return_value = None
        mock_resolve_partner.return_value = mock_partner

        @optional_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "authenticated": kwargs["authenticated_user"] is not None,
                        "auth_type": kwargs["auth_type"],
                        "partner_id": kwargs["authenticated_user"]["partner_id"]
                        if kwargs["authenticated_user"]
                        else None,
                    }
                ),
            }

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["authenticated"] is True
        assert body["auth_type"] == "partner_key"
        assert body["partner_id"] == "partner123"

    @patch("shared.auth.authentication_decorators.extract_cognito_user_context")
    @patch("shared.auth.authentication_decorators.authenticate_jwt")
    @patch("shared.auth.authentication_decorators.resolve_partner_from_event")
    def test_optional_auth_without_authentication(
        self, mock_resolve_partner, mock_authenticate_jwt, mock_extract_cognito
    ):
        """Test optional authentication without credentials"""
        # Setup
        mock_extract_cognito.return_value = None
        mock_authenticate_jwt.return_value = None
        mock_resolve_partner.return_value = None

        @optional_auth
        def test_handler(headers, request_data, event, **kwargs):
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {"authenticated": kwargs["authenticated_user"] is not None, "auth_type": kwargs["auth_type"]}
                ),
            }

        # Execute
        result = test_handler(self.mock_headers, self.mock_request_data, self.mock_event, **self.mock_kwargs)

        # Verify
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["authenticated"] is False
        assert body["auth_type"] == "public"

    def test_require_full_auth_deprecation_warning(self):
        """Test that require_full_auth shows deprecation warning"""
        with pytest.warns(DeprecationWarning, match="require_full_auth is deprecated"):

            @require_full_auth
            def test_handler(headers, request_data, event, **kwargs):
                return {"statusCode": 200, "body": "success"}

    def test_standardized_function_signature(self):
        """Test that all decorators work with standardized function signature"""
        # This test ensures that the decorators properly handle the standardized signature

        @require_partner_or_cognito_auth
        def test_handler(headers: dict, request_data: dict, event: dict, **kwargs) -> dict:
            # Verify that all expected parameters are available
            assert isinstance(headers, dict)
            assert isinstance(request_data, dict)
            assert isinstance(event, dict)
            assert "authenticated_user" in kwargs
            assert "auth_type" in kwargs

            return {
                "statusCode": 200,
                "body": json.dumps({"signature_test": "passed", "auth_type": kwargs["auth_type"]}),
            }

        # Mock authentication to return a valid user
        with patch("shared.auth.authentication_decorators.extract_cognito_user_context") as mock_cognito:
            mock_cognito.return_value = {"user_id": "test123", "type": "cognito"}

            result = test_handler(
                headers={"Authorization": "Bearer token"},
                request_data={"test": "data"},
                event={"path": "/test"},
                extra_param="should_be_in_kwargs",
            )

            assert result["statusCode"] == 200
            body = json.loads(result["body"])
            assert body["signature_test"] == "passed"
            assert body["auth_type"] == "cognito"
