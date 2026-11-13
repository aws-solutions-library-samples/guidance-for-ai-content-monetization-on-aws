"""
Unit tests for IAB Authentication Service

Tests cover:
- API key validation
- JWT token generation and verification
- Partner information handling
- Error cases and edge conditions
"""

import unittest
import json
import jwt
import time
import hashlib
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import sys
import os

# Add lambda directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "lambda"))

from authentication_service import (
    generate_jwt_token,
    verify_jwt_token,
    lambda_handler,
    handle_validate_key,
    handle_generate_token,
    handle_verify_token,
    get_jwt_secret,
)
import shared.auth.authentication_functions as auth_functions

# Req 4.3: single JWT test secret constant, >=32 bytes (RFC 7518 §3.2 minimum for HS256).
TEST_JWT_SECRET = "test-jwt-secret-key-for-testing-0123456789"


class TestAuthenticationService(unittest.TestCase):
    """Test cases for authentication service functions"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_api_key = "test-api-key-12345"
        self.test_api_key_hash = hashlib.sha256(self.test_api_key.encode()).hexdigest()

        self.test_partner_info = {
            "partner_id": "partner_123",
            "partner_name": "Test Publisher",
            "pricing_tier": "standard",
            "allowed_endpoints": ["queryContent", "submitLog"],
            "api_usage": 100,
            "api_quota": 10000,
            "status": "active",
        }

        self.test_jwt_secret = TEST_JWT_SECRET

        # Reset the module-level JWT secret cache in authentication_functions so the
        # get_jwt_secret tests observe a fresh Secrets Manager call regardless of test order
        # (the cache is a process-global shared with tests/auth's caching-behavior test).
        auth_functions._jwt_secret_cache = None
        auth_functions._jwt_secret_cache_time = 0

    def test_key_value_validation_is_retired(self):
        """Native API keys are validated by API Gateway; the hash lookup no longer exists."""
        self.assertFalse(hasattr(auth_functions, "validate_api_key"))
        self.assertFalse(hasattr(auth_functions, "increment_api_usage"))
        self.assertTrue(hasattr(auth_functions, "resolve_partner_from_event"))

    @patch("shared.auth.authentication_functions.get_jwt_secret")
    def test_generate_jwt_token_success(self, mock_get_secret):
        """Test successful JWT token generation"""
        mock_get_secret.return_value = self.test_jwt_secret

        # Generate token
        token = generate_jwt_token(self.test_partner_info)

        # Verify token is not empty
        self.assertIsNotNone(token)
        self.assertIsInstance(token, str)

        # Decode and verify token content
        decoded = jwt.decode(token, self.test_jwt_secret, algorithms=["HS256"])
        self.assertEqual(decoded["partner_id"], "partner_123")
        self.assertEqual(decoded["partner_name"], "Test Publisher")
        self.assertEqual(decoded["access_type"], "general")

    @patch("shared.auth.authentication_functions.get_jwt_secret")
    def test_generate_jwt_token_with_content_id(self, mock_get_secret):
        """Test JWT token generation with specific content ID"""
        mock_get_secret.return_value = self.test_jwt_secret

        # Generate token with content ID
        token = generate_jwt_token(self.test_partner_info, content_id="content_456")

        # Decode and verify token content
        decoded = jwt.decode(token, self.test_jwt_secret, algorithms=["HS256"])
        self.assertEqual(decoded["content_id"], "content_456")
        self.assertEqual(decoded["access_type"], "content_specific")

    @patch("shared.auth.authentication_functions.get_jwt_secret")
    def test_verify_jwt_token_success(self, mock_get_secret):
        """Test successful JWT token verification"""
        mock_get_secret.return_value = self.test_jwt_secret

        # Generate a test token
        payload = {
            "partner_id": "partner_123",
            "partner_name": "Test Publisher",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        token = jwt.encode(payload, self.test_jwt_secret, algorithm="HS256")

        # Verify token
        is_valid, decoded_payload = verify_jwt_token(token)

        # Assertions
        self.assertTrue(is_valid)
        self.assertEqual(decoded_payload["partner_id"], "partner_123")
        self.assertEqual(decoded_payload["partner_name"], "Test Publisher")

    @patch("shared.auth.authentication_functions.get_jwt_secret")
    def test_verify_jwt_token_expired(self, mock_get_secret):
        """Test JWT token verification with expired token"""
        mock_get_secret.return_value = self.test_jwt_secret

        # Generate an expired token
        payload = {
            "partner_id": "partner_123",
            "iat": int(time.time()) - 7200,  # 2 hours ago
            "exp": int(time.time()) - 3600,  # 1 hour ago (expired)
        }
        token = jwt.encode(payload, self.test_jwt_secret, algorithm="HS256")

        # Verify token
        is_valid, decoded_payload = verify_jwt_token(token)

        # Assertions
        self.assertFalse(is_valid)
        self.assertIsNone(decoded_payload)

    @patch("shared.auth.authentication_functions.get_jwt_secret")
    def test_verify_jwt_token_invalid(self, mock_get_secret):
        """Test JWT token verification with invalid token"""
        mock_get_secret.return_value = self.test_jwt_secret

        # Test with invalid token
        is_valid, decoded_payload = verify_jwt_token("invalid.token.here")

        # Assertions
        self.assertFalse(is_valid)
        self.assertIsNone(decoded_payload)

    @patch("shared.auth.authentication_functions.secrets_manager")
    def test_get_jwt_secret_success(self, mock_secrets_manager):
        """Test successful JWT secret retrieval"""
        # Mock Secrets Manager response
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"jwt_secret": "test-secret-key"})
        }

        # Test secret retrieval
        secret = get_jwt_secret()

        # Assertions
        self.assertEqual(secret, "test-secret-key")

    @patch("shared.auth.authentication_functions.secrets_manager")
    @patch.dict(os.environ, {"JWT_SECRET": "fallback-secret"})
    def test_get_jwt_secret_fails_closed(self, mock_secrets_manager):
        """TM-01 (Req 1.2): a Secrets Manager failure MUST fail closed — raise, never
        return a hardcoded or environment-default secret, even when JWT_SECRET is set."""
        # Mock Secrets Manager failure
        mock_secrets_manager.get_secret_value.side_effect = Exception("Secret not found")

        # Retrieval must raise the typed error, not fall back
        with self.assertRaises(auth_functions.JwtSecretUnavailableError):
            get_jwt_secret()

    @patch("shared.auth.authentication_functions.secrets_manager")
    def test_get_jwt_secret_never_returns_hardcoded_constant(self, mock_secrets_manager):
        """TM-01 (Req 1.2, 1.4): on any SM failure the retired hardcoded constant is
        never returned (no JWT_SECRET env set, so the old code path would have used it)."""
        mock_secrets_manager.get_secret_value.side_effect = Exception("boom")

        with self.assertRaises(auth_functions.JwtSecretUnavailableError):
            get_jwt_secret()


class TestLambdaHandlers(unittest.TestCase):
    """Test cases for Lambda handler functions"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_api_key = "test-api-key-12345"
        self.test_partner_info = {
            "partner_id": "partner_123",
            "partner_name": "Test Publisher",
            "pricing_tier": "standard",
            "allowed_endpoints": ["queryContent", "submitLog"],
            "api_usage": 100,
            "api_quota": 10000,
        }

    def test_lambda_handler_missing_operation(self):
        """Test lambda handler with missing operation"""
        event = {}
        context = Mock()

        response = lambda_handler(event, context)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing operation parameter", body["error"])

    def test_lambda_handler_invalid_operation(self):
        """Test lambda handler with invalid operation"""
        event = {"operation": "invalid_operation"}
        context = Mock()

        response = lambda_handler(event, context)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Unsupported operation", body["error"])

    def test_handle_validate_key_is_retired(self):
        """validate_key answers 410 for any input: keys are validated at the API Gateway edge."""
        for event in ({"api_key": self.test_api_key}, {"api_key": ""}, {}):
            response = handle_validate_key(event)
            self.assertEqual(response["statusCode"], 410)
            body = json.loads(response["body"])
            self.assertEqual(body["error_code"], "operation_retired")
            self.assertNotIn("partner", body)

    @patch("authentication_service.generate_jwt_token")
    def test_handle_generate_token_success(self, mock_generate):
        """Test successful token generation handler"""
        # Mock token generation
        mock_generate.return_value = "test.jwt.token"

        event = {"partner_info": self.test_partner_info}
        response = handle_generate_token(event)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["token"], "test.jwt.token")
        self.assertEqual(body["token_type"], "Bearer")

    def test_handle_generate_token_missing_partner_info(self):
        """Test token generation handler with missing partner info"""
        event = {}
        response = handle_generate_token(event)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing partner_info parameter", body["error"])

    @patch("authentication_service.verify_jwt_token")
    def test_handle_verify_token_success(self, mock_verify):
        """Test successful token verification handler"""
        # Mock token verification
        test_payload = {"partner_id": "partner_123", "exp": int(time.time()) + 3600}
        mock_verify.return_value = (True, test_payload)

        event = {"token": "Bearer test.jwt.token"}
        response = handle_verify_token(event)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertTrue(body["valid"])
        self.assertEqual(body["payload"]["partner_id"], "partner_123")

    @patch("authentication_service.verify_jwt_token")
    def test_handle_verify_token_failure(self, mock_verify):
        """Test failed token verification handler"""
        # Mock token verification failure
        mock_verify.return_value = (False, None)

        event = {"token": "invalid.jwt.token"}
        response = handle_verify_token(event)

        self.assertEqual(response["statusCode"], 401)
        body = json.loads(response["body"])
        self.assertIn("Invalid or expired token", body["error"])

    def test_handle_verify_token_missing_token(self):
        """Test token verification handler with missing token"""
        event = {}
        response = handle_verify_token(event)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing token parameter", body["error"])


if __name__ == "__main__":
    # Create tests directory if it doesn't exist
    os.makedirs("tests", exist_ok=True)

    # Run tests
    unittest.main(verbosity=2)
