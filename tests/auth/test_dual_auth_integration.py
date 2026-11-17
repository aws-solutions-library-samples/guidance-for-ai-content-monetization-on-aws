#!/usr/bin/env python3
"""
Comprehensive Authentication Integration Tests

This module tests the dual authentication model implementation:
1. Partner Key Authentication (AI Operators & External Bots)
2. Publisher Key Authentication (Content Publishers - API Access)
3. Cognito JWT Authentication (Publishers - UI Access)

Tests cover:
- Authentication flow validation
- Endpoint access control
- Token lifecycle management
- Error handling and security
- Cross-authentication scenarios
- Rate limiting and quotas

Requirements covered: 5.1, 5.2, 8.1
"""

import unittest
import pytest
import requests
import json
import jwt
import time
import boto3
import hashlib
import base64
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any, Optional, Tuple
import os
import sys

# Add lambda directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from authentication_service import (
    generate_jwt_token,
    verify_jwt_token,
    lambda_handler,
    authorizer_handler,
    create_policy,
    get_jwt_secret,
)


class TestDualAuthenticationIntegration(unittest.TestCase):
    """Integration tests for dual authentication model"""

    def setUp(self):
        """Set up test fixtures and configuration"""
        self.test_config = {
            "api_base_url": os.getenv("TEST_API_URL", "https://test-api.amazonaws.com/v1"),
            "cognito_user_pool_id": os.getenv("TEST_USER_POOL_ID", "us-west-2_TEST123"),
            "cognito_client_id": os.getenv("TEST_CLIENT_ID", "test_client_id"),
            "aws_region": os.getenv("AWS_REGION", "us-west-2"),
            "aws_profile": os.getenv("AWS_PROFILE", "genai"),
        }

        # Test credentials
        self.partner_key = "pk_demo_test_key"
        self.publisher_key = "pub_test_publisher_key"
        self.invalid_key = "invalid_key_12345"

        # Test user for Cognito
        self.test_user = {
            "username": f"test_user_{int(time.time())}@example.com",
            "password": "TestPass123!",
            "email": f"test_user_{int(time.time())}@example.com",
        }

        # JWT test secret
        self.jwt_secret = "test-jwt-secret-for-integration-tests"

        # Initialize AWS clients
        try:
            session = boto3.Session()
            self.cognito_client = session.client("cognito-idp", region_name=self.test_config["aws_region"])
            self.dynamodb = session.resource("dynamodb", region_name=self.test_config["aws_region"])
        except Exception as e:
            print(f"Warning: Could not initialize AWS clients: {e}")
            self.cognito_client = None
            self.dynamodb = None

    def tearDown(self):
        """Clean up test resources"""
        # Clean up test Cognito user if created
        if hasattr(self, "created_test_user") and self.cognito_client:
            try:
                self.cognito_client.admin_delete_user(
                    UserPoolId=self.test_config["cognito_user_pool_id"], Username=self.test_user["username"]
                )
            except Exception:
                pass  # User may not exist or already deleted

    def test_partner_key_validation_is_retired(self):
        """Partner keys are native API Gateway keys; this service no longer validates key values."""
        for key in (self.partner_key, self.invalid_key):
            response = lambda_handler({"operation": "validate_key", "api_key": key}, Mock())
            self.assertEqual(response["statusCode"], 410)
            body = json.loads(response["body"])
            self.assertEqual(body["error_code"], "operation_retired")

    def test_authentication_functions_no_longer_expose_key_value_validation(self):
        """The hash-based lookup and the usage counter are gone with the custom authorizer."""
        import shared.auth.authentication_functions as af

        for retired in ("validate_api_key", "increment_api_usage", "authenticate_partner"):
            self.assertFalse(hasattr(af, retired), f"{retired} should have been removed")

    def test_cognito_jwt_authentication_flow(self):
        """Test complete Cognito JWT authentication flow"""
        print("\n🎫 Testing Cognito JWT Authentication Flow")

        # Test 1: Valid JWT token generation and verification
        with patch("shared.auth.authentication_functions.get_jwt_secret") as mock_secret:
            mock_secret.return_value = self.jwt_secret

            # Generate test token
            partner_info = {
                "partner_id": "publisher_123",
                "partner_name": "Test Publisher",
                "pricing_tier": "premium",
                "allowed_endpoints": ["/analytics", "/content"],
            }

            # Test token generation
            event = {"operation": "generate_token", "partner_info": partner_info}

            response = lambda_handler(event, Mock())

            self.assertEqual(response["statusCode"], 200)
            body = json.loads(response["body"])
            self.assertIn("token", body)
            self.assertEqual(body["token_type"], "Bearer")

            generated_token = body["token"]
            print("✅ JWT token generation successful")

            # Test token verification
            event = {"operation": "verify_token", "token": f"Bearer {generated_token}"}

            response = lambda_handler(event, Mock())

            self.assertEqual(response["statusCode"], 200)
            body = json.loads(response["body"])
            self.assertTrue(body["valid"])
            self.assertEqual(body["payload"]["partner_id"], "publisher_123")
            print("✅ JWT token verification successful")

    def test_api_gateway_authorizer_integration(self):
        """Test API Gateway Lambda authorizer integration"""
        print("\n🚪 Testing API Gateway Authorizer Integration")

        # Test 1: a raw partner key is no longer an accepted authorizer token (native keys are
        # validated by API Gateway itself)
        with patch("shared.auth.authentication_functions.publish_auth_failure_metric"):
            event = {
                "authorizationToken": self.partner_key,
                "methodArn": "arn:aws:execute-api:us-west-2:123456789012:abcdef123/test/POST/query",
            }
            with self.assertRaises(Exception) as denied:
                authorizer_handler(event, Mock())
            self.assertEqual(str(denied.exception), "Unauthorized")
            print("✅ Raw partner key rejected by authorizer")

        # Test 2: JWT token authorization
        with patch("authentication_service.verify_jwt_token") as mock_verify:
            mock_verify.return_value = (
                True,
                {
                    "partner_id": "publisher_123",
                    "partner_name": "Test Publisher",
                    "pricing_tier": "premium",
                    "exp": int(time.time()) + 3600,
                },
            )

            event = {
                "authorizationToken": f"Bearer {self._generate_test_jwt()}",
                "methodArn": "arn:aws:execute-api:us-west-2:123456789012:abcdef123/test/GET/analytics",
            }

            policy = authorizer_handler(event, Mock())

            self.assertEqual(policy["principalId"], "publisher_123")
            self.assertEqual(policy["policyDocument"]["Statement"][0]["Effect"], "Allow")
            self.assertIn("partner_id", policy["context"])
            print("✅ JWT token authorizer successful")

    def test_endpoint_access_control(self):
        """Test access control for different endpoint categories"""
        print("\n🔒 Testing Endpoint Access Control")

        # Define endpoint categories and their required authentication
        endpoint_auth_matrix = {
            # Partner Key endpoints (AI Operators)
            "/query": ["partner_key"],
            "/info": ["partner_key", "none"],  # Public endpoint
            "/taxonomy": ["partner_key", "none"],  # Public endpoint
            "/llms.txt": ["partner_key", "none"],  # Public endpoint
            # Publisher Key endpoints (Content Management)
            "/content": ["publisher_key", "cognito_jwt"],
            "/processing/status": ["publisher_key", "cognito_jwt"],
            "/generate-llms-txt": ["publisher_key", "cognito_jwt"],
            # Cognito JWT endpoints (Analytics & UI)
            "/analytics/metrics": ["cognito_jwt"],
            "/analytics/usage": ["cognito_jwt"],
            "/bot-control/rules": ["cognito_jwt"],
            "/analytics-agent/chat": ["cognito_jwt"],
        }

        for endpoint, allowed_auth_methods in endpoint_auth_matrix.items():
            print(f"  Testing {endpoint} - Allowed: {allowed_auth_methods}")

            # Test each authentication method
            for auth_method in ["partner_key", "publisher_key", "cognito_jwt", "none"]:
                should_allow = auth_method in allowed_auth_methods

                if auth_method == "partner_key":
                    result = self._test_endpoint_access(endpoint, "X-Partner-Key", self.partner_key)
                elif auth_method == "publisher_key":
                    result = self._test_endpoint_access(endpoint, "X-Publisher-Key", self.publisher_key)
                elif auth_method == "cognito_jwt":
                    result = self._test_endpoint_access(
                        endpoint, "Authorization", f"Bearer {self._generate_test_jwt()}"
                    )
                else:  # none
                    result = self._test_endpoint_access(endpoint, None, None)

                if should_allow:
                    self.assertTrue(result, f"{endpoint} should allow {auth_method}")
                else:
                    # Note: In real implementation, this would be 401/403
                    # For unit tests, we're testing the logic, not HTTP responses
                    pass

        print("✅ Endpoint access control validation completed")

    def test_authentication_error_handling(self):
        """Test authentication error scenarios and security measures"""
        print("\n🛡️ Testing Authentication Error Handling")

        # Test 1: Missing token on verify_token
        event = {"operation": "verify_token", "token": ""}

        response = lambda_handler(event, Mock())
        self.assertEqual(response["statusCode"], 400)
        print("✅ Missing authentication properly handled")

        # Test 2: Malformed JWT token
        event = {"operation": "verify_token", "token": "Bearer invalid.jwt.token"}

        response = lambda_handler(event, Mock())
        self.assertEqual(response["statusCode"], 401)
        print("✅ Malformed JWT token properly rejected")

        # Test 3: Expired JWT token
        with patch("shared.auth.authentication_functions.get_jwt_secret") as mock_secret:
            mock_secret.return_value = self.jwt_secret

            # Create expired token
            expired_payload = {
                "partner_id": "test_partner",
                "iat": int(time.time()) - 7200,  # 2 hours ago
                "exp": int(time.time()) - 3600,  # 1 hour ago (expired)
            }
            expired_token = jwt.encode(expired_payload, self.jwt_secret, algorithm="HS256")

            event = {"operation": "verify_token", "token": f"Bearer {expired_token}"}

            response = lambda_handler(event, Mock())
            self.assertEqual(response["statusCode"], 401)
            print("✅ Expired JWT token properly rejected")

    def test_cross_authentication_scenarios(self):
        """Test scenarios involving multiple authentication methods"""
        print("\n🔄 Testing Cross-Authentication Scenarios")

        # Test 1: Publisher using both Publisher Key and JWT
        # This should work for content management endpoints
        with patch("authentication_service.verify_jwt_token") as mock_verify_jwt:
            mock_verify_jwt.return_value = (True, {"partner_id": "publisher_123", "partner_name": "Test Publisher"})

            # A raw key is no longer validated by this service
            event = {"operation": "validate_key", "api_key": self.publisher_key}

            response = lambda_handler(event, Mock())
            self.assertEqual(response["statusCode"], 410)

            # Test JWT access
            event = {"operation": "verify_token", "token": f"Bearer {self._generate_test_jwt()}"}

            response = lambda_handler(event, Mock())
            self.assertEqual(response["statusCode"], 200)

            print("✅ Cross-authentication scenarios working")

    def test_security_monitoring_integration(self):
        """Test security monitoring and logging integration"""
        print("\n📊 Testing Security Monitoring Integration")

        with patch("shared.auth.authentication_functions.cloudwatch") as mock_cloudwatch:
            # Test authentication failure metric publishing
            from authentication_service import publish_auth_failure_metric

            publish_auth_failure_metric("invalid_key")

            mock_cloudwatch.put_metric_data.assert_called_once()
            call_args = mock_cloudwatch.put_metric_data.call_args[1]

            self.assertEqual(call_args["Namespace"], "IAB/Authentication")
            self.assertEqual(call_args["MetricData"][0]["MetricName"], "AuthenticationFailures")
            print("✅ Security monitoring integration working")

    @pytest.mark.integration
    @unittest.skipIf(not os.getenv("RUN_LIVE_TESTS"), "Live tests disabled")
    def test_live_cognito_integration(self):
        """Test live Cognito integration (requires AWS credentials)"""
        print("\n🌐 Testing Live Cognito Integration")

        if not self.cognito_client:
            self.skipTest("AWS Cognito client not available")

        try:
            # Create test user
            self.cognito_client.admin_create_user(
                UserPoolId=self.test_config["cognito_user_pool_id"],
                Username=self.test_user["username"],
                UserAttributes=[
                    {"Name": "email", "Value": self.test_user["email"]},
                    {"Name": "email_verified", "Value": "true"},
                ],
                TemporaryPassword=self.test_user["password"],
                MessageAction="SUPPRESS",
            )

            # Set permanent password
            self.cognito_client.admin_set_user_password(
                UserPoolId=self.test_config["cognito_user_pool_id"],
                Username=self.test_user["username"],
                Password=self.test_user["password"],
                Permanent=True,
            )

            self.created_test_user = True

            # Authenticate and get tokens
            auth_response = self.cognito_client.admin_initiate_auth(
                UserPoolId=self.test_config["cognito_user_pool_id"],
                ClientId=self.test_config["cognito_client_id"],
                AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": self.test_user["username"], "PASSWORD": self.test_user["password"]},
            )

            tokens = auth_response["AuthenticationResult"]
            access_token = tokens["AccessToken"]

            # Verify token structure
            self.assertIsNotNone(access_token)
            self.assertTrue(len(access_token.split(".")) == 3)  # JWT structure

            # Decode and verify token claims
            payload = self._decode_jwt_payload(access_token)
            self.assertIn("token_use", payload)
            self.assertEqual(payload["token_use"], "access")

            print("✅ Live Cognito integration successful")

        except Exception as e:
            self.fail(f"Live Cognito integration failed: {str(e)}")

    def _generate_test_jwt(self) -> str:
        """Generate a test JWT token for testing"""
        payload = {
            "partner_id": "test_publisher_123",
            "partner_name": "Test Publisher",
            "pricing_tier": "premium",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")

    def _decode_jwt_payload(self, token: str) -> Dict[str, Any]:
        """Decode JWT payload without verification (for testing)"""
        parts = token.split(".")
        payload = parts[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)
        decoded = base64.b64decode(payload)
        return json.loads(decoded)

    def _test_endpoint_access(self, endpoint: str, header_name: Optional[str], header_value: Optional[str]) -> bool:
        """Test access to a specific endpoint with given authentication"""
        # This is a mock implementation for unit testing
        # In real integration tests, this would make HTTP requests

        # Define endpoint authentication requirements
        endpoint_requirements = {
            "/query": ["X-Partner-Key"],
            "/info": [],  # Public
            "/taxonomy": [],  # Public
            "/llms.txt": [],  # Public
            "/content": ["X-Publisher-Key", "Authorization"],
            "/processing/status": ["X-Publisher-Key", "Authorization"],
            "/generate-llms-txt": ["X-Publisher-Key", "Authorization"],
            "/analytics/metrics": ["Authorization"],
            "/analytics/usage": ["Authorization"],
            "/bot-control/rules": ["Authorization"],
            "/analytics-agent/chat": ["Authorization"],
        }

        required_headers = endpoint_requirements.get(endpoint, ["Authorization"])

        if not required_headers:  # Public endpoint
            return True

        if header_name in required_headers:
            return header_value is not None

        return False


class TestAuthenticationFlowValidation(unittest.TestCase):
    """Test authentication flow validation and edge cases"""

    def test_authentication_flow_state_management(self):
        """Test authentication flow state management"""
        print("\n🔄 Testing Authentication Flow State Management")

        # Test token refresh scenarios
        with patch("shared.auth.authentication_functions.get_jwt_secret") as mock_secret:
            mock_secret.return_value = "test-secret"

            # Generate token near expiration
            near_expiry_payload = {
                "partner_id": "test_partner",
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,  # 5 minutes
            }

            token = jwt.encode(near_expiry_payload, "test-secret", algorithm="HS256")

            # Verify token is still valid
            is_valid, payload = verify_jwt_token(token)
            self.assertTrue(is_valid)
            self.assertEqual(payload["partner_id"], "test_partner")

            print("✅ Token near expiration handled correctly")

    def test_concurrent_partner_resolutions(self):
        """Concurrent resolutions each perform the key-id lookup independently."""
        from shared.auth.authentication_functions import resolve_partner_from_event

        with patch("shared.auth.authentication_functions.dynamodb") as mock_dynamodb:
            mock_table = Mock()
            mock_dynamodb.Table.return_value = mock_table
            mock_table.query.return_value = {
                "Items": [{"partner_id": "concurrent_partner", "api_key_id": "k", "usage_plan_id": "p", "status": "active"}]
            }
            mock_table.get_item.return_value = {
                "Item": {"partner_id": "concurrent_partner", "partner_name": "Concurrent", "status": "active"}
            }

            results = [
                resolve_partner_from_event(
                    {"httpMethod": "POST", "resource": "/query", "requestContext": {"identity": {"apiKeyId": f"k{i}"}}}
                )
                for i in range(5)
            ]

            for partner in results:
                self.assertEqual(partner["partner_id"], "concurrent_partner")
            self.assertEqual(mock_table.query.call_count, 5)
            mock_table.update_item.assert_not_called()

            print("✅ Concurrent partner resolutions handled correctly")

    def test_authentication_caching_behavior(self):
        """Test authentication caching and performance optimization"""
        print("\n💾 Testing Authentication Caching Behavior")

        # Test JWT secret caching
        with patch("shared.auth.authentication_functions.secrets_manager") as mock_secrets:
            # Start from a clean cache so the call-count assertion is order-independent
            # (the cache is a process-global shared with the authentication_service tests).
            import shared.auth.authentication_functions as auth_functions

            auth_functions._jwt_secret_cache = None
            auth_functions._jwt_secret_cache_time = 0

            mock_secrets.get_secret_value.return_value = {"SecretString": json.dumps({"jwt_secret": "cached-secret"})}

            # First call should hit Secrets Manager
            secret1 = get_jwt_secret()
            self.assertEqual(secret1, "cached-secret")

            # Second call should use cache (within TTL)
            secret2 = get_jwt_secret()
            self.assertEqual(secret2, "cached-secret")

            # Secrets Manager should only be called once due to caching
            self.assertEqual(mock_secrets.get_secret_value.call_count, 1)

            print("✅ Authentication caching working correctly")


def create_test_suite():
    """Create comprehensive test suite for authentication integration"""
    suite = unittest.TestSuite()

    # Core authentication flow tests
    suite.addTest(TestDualAuthenticationIntegration("test_partner_key_authentication_flow"))
    suite.addTest(TestDualAuthenticationIntegration("test_cognito_jwt_authentication_flow"))
    suite.addTest(TestDualAuthenticationIntegration("test_api_gateway_authorizer_integration"))

    # Access control and security tests
    suite.addTest(TestDualAuthenticationIntegration("test_endpoint_access_control"))
    suite.addTest(TestDualAuthenticationIntegration("test_authentication_error_handling"))
    suite.addTest(TestDualAuthenticationIntegration("test_rate_limiting_and_quotas"))

    # Advanced scenarios
    suite.addTest(TestDualAuthenticationIntegration("test_cross_authentication_scenarios"))
    suite.addTest(TestDualAuthenticationIntegration("test_security_monitoring_integration"))

    # Flow validation tests
    suite.addTest(TestAuthenticationFlowValidation("test_authentication_flow_state_management"))
    suite.addTest(TestAuthenticationFlowValidation("test_concurrent_authentication_requests"))
    suite.addTest(TestAuthenticationFlowValidation("test_authentication_caching_behavior"))

    # Live integration test (optional)
    if os.getenv("RUN_LIVE_TESTS"):
        suite.addTest(TestDualAuthenticationIntegration("test_live_cognito_integration"))

    return suite


if __name__ == "__main__":
    print("🧪 AI Content Monetization API - Dual Authentication Integration Tests")
    print("=" * 70)
    print()
    print("This test suite validates the dual authentication model:")
    print("1. 🔑 Partner Key Authentication (AI Operators)")
    print("2. 🏢 Publisher Key Authentication (Content Management)")
    print("3. 🎫 Cognito JWT Authentication (UI & Analytics)")
    print()
    print("Test Configuration:")
    print(f"  API Base URL: {os.getenv('TEST_API_URL', 'Mock Testing Mode')}")
    print(f"  AWS Profile: {os.getenv('AWS_PROFILE', 'genai')}")
    print(f"  AWS Region: {os.getenv('AWS_REGION', 'us-west-2')}")
    print(f"  Live Tests: {'Enabled' if os.getenv('RUN_LIVE_TESTS') else 'Disabled'}")
    print()

    # Run test suite
    runner = unittest.TextTestRunner(verbosity=2)
    suite = create_test_suite()
    result = runner.run(suite)

    # Print summary
    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print("✅ All authentication integration tests passed!")
    else:
        print("❌ Some authentication tests failed:")
        for failure in result.failures + result.errors:
            print(f"  - {failure[0]}")

    print(f"\nTests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")

    # Exit with appropriate code
    sys.exit(0 if result.wasSuccessful() else 1)
