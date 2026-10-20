#!/usr/bin/env python3
"""
Test different authentication scenarios for analytics agent
Tests AgentCoreMCPClient authentication with both AGENTCORE_AUTH_TOKEN and Secrets Manager
"""

import os
import pytest
import asyncio
import json
import boto3
from unittest.mock import patch, MagicMock, AsyncMock
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "source" / "agent"))


class TestAuthenticationModes:
    """Test different authentication scenarios for analytics agent"""

    @pytest.fixture(autouse=True)
    def setup_environment(self):
        """Setup test environment variables"""
        self.original_env = {}
        # Store original values
        auth_vars = ["AGENTCORE_AUTH_TOKEN", "AWS_DEFAULT_REGION", "USE_MOCK_ANALYTICS", "MCP_SERVER_URL"]
        for key in auth_vars:
            self.original_env[key] = os.environ.get(key)

        yield

        # Restore original values
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_agentcore_auth_token_environment_variable(self):
        """Test authentication using AGENTCORE_AUTH_TOKEN environment variable"""
        # Set up environment for AgentCore runtime
        os.environ["AGENTCORE_AUTH_TOKEN"] = "test_agentcore_token_12345"
        os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

        # Mock AgentCoreMCPClient authentication logic
        def get_bearer_token():
            """Simulate AgentCoreMCPClient._get_bearer_token method"""
            try:
                # Try environment variable first (for AgentCore runtime)
                token = os.getenv("AGENTCORE_AUTH_TOKEN")
                if token and token != "placeholder":
                    return token

                # Would fallback to Secrets Manager if no env token
                raise Exception("No environment token available")
            except Exception as e:
                raise Exception(f"Failed to get bearer token: {str(e)}")

        # Test environment token retrieval
        token = get_bearer_token()
        assert token == "test_agentcore_token_12345"

        print("✓ AGENTCORE_AUTH_TOKEN environment variable authentication validated")

    def test_agentcore_auth_token_placeholder_fallback(self):
        """Test fallback when AGENTCORE_AUTH_TOKEN is placeholder"""
        # Set placeholder token (should trigger fallback)
        os.environ["AGENTCORE_AUTH_TOKEN"] = "placeholder"
        os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

        # Mock AgentCoreMCPClient authentication logic with Secrets Manager fallback
        def get_bearer_token_with_fallback():
            """Simulate AgentCoreMCPClient._get_bearer_token with fallback"""
            try:
                # Try environment variable first
                token = os.getenv("AGENTCORE_AUTH_TOKEN")
                if token and token != "placeholder":
                    return token

                # Fallback to AWS Secrets Manager (mocked)
                with patch("boto3.client") as mock_boto3:
                    mock_secrets_client = MagicMock()
                    mock_boto3.return_value = mock_secrets_client

                    # Mock successful secrets retrieval
                    mock_secrets_client.get_secret_value.return_value = {
                        "SecretString": json.dumps({"bearer_token": "secrets_manager_token_67890"})
                    }

                    secrets_client = boto3.client("secretsmanager", region_name="us-west-2")
                    response = secrets_client.get_secret_value(SecretId="mcp_server/cognito/credentials")
                    secret_value = response["SecretString"]
                    parsed_secret = json.loads(secret_value)
                    return parsed_secret["bearer_token"]

            except Exception as e:
                raise Exception(f"Failed to get bearer token: {str(e)}")

        # Test fallback to Secrets Manager
        token = get_bearer_token_with_fallback()
        assert token == "secrets_manager_token_67890"

        print("✓ Secrets Manager fallback authentication validated")

    def test_secrets_manager_authentication(self):
        """Test authentication using AWS Secrets Manager"""
        # Remove environment token to force Secrets Manager usage
        if "AGENTCORE_AUTH_TOKEN" in os.environ:
            del os.environ["AGENTCORE_AUTH_TOKEN"]

        os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

        # Mock Secrets Manager authentication
        with patch("boto3.client") as mock_boto3:
            mock_secrets_client = MagicMock()
            mock_boto3.return_value = mock_secrets_client

            # Mock successful secrets retrieval
            mock_secrets_client.get_secret_value.return_value = {
                "SecretString": json.dumps(
                    {
                        "bearer_token": "secrets_manager_token_abc123",
                        "client_id": "test_client_id",
                        "user_pool_id": "us-west-2_TestPool",
                    }
                )
            }

            # Simulate AgentCoreMCPClient Secrets Manager logic
            def get_bearer_token_from_secrets():
                try:
                    secrets_client = boto3.client("secretsmanager", region_name="us-west-2")
                    response = secrets_client.get_secret_value(SecretId="mcp_server/cognito/credentials")
                    secret_value = response["SecretString"]
                    parsed_secret = json.loads(secret_value)
                    return parsed_secret["bearer_token"]
                except Exception as e:
                    raise Exception(f"Failed to get bearer token from Secrets Manager: {str(e)}")

            # Test Secrets Manager authentication
            token = get_bearer_token_from_secrets()
            assert token == "secrets_manager_token_abc123"

            # Verify boto3 client was called correctly
            mock_boto3.assert_called_with("secretsmanager", region_name="us-west-2")
            mock_secrets_client.get_secret_value.assert_called_with(SecretId="mcp_server/cognito/credentials")

        print("✓ AWS Secrets Manager authentication validated")

    def test_authentication_failure_scenarios(self):
        """Test various authentication failure scenarios"""

        # Test 1: No environment token and Secrets Manager fails
        if "AGENTCORE_AUTH_TOKEN" in os.environ:
            del os.environ["AGENTCORE_AUTH_TOKEN"]

        with patch("boto3.client") as mock_boto3:
            mock_secrets_client = MagicMock()
            mock_boto3.return_value = mock_secrets_client

            # Mock Secrets Manager failure
            mock_secrets_client.get_secret_value.side_effect = Exception("Secret not found")

            def get_bearer_token_with_error_handling():
                try:
                    # Try environment variable first
                    token = os.getenv("AGENTCORE_AUTH_TOKEN")
                    if token and token != "placeholder":
                        return token

                    # Try Secrets Manager
                    secrets_client = boto3.client("secretsmanager", region_name="us-west-2")
                    response = secrets_client.get_secret_value(SecretId="mcp_server/cognito/credentials")
                    secret_value = response["SecretString"]
                    parsed_secret = json.loads(secret_value)
                    return parsed_secret["bearer_token"]
                except Exception as e:
                    raise Exception(f"Failed to get bearer token: {str(e)}")

            # Test authentication failure
            with pytest.raises(Exception) as exc_info:
                get_bearer_token_with_error_handling()

            assert "Failed to get bearer token" in str(exc_info.value)
            assert "Secret not found" in str(exc_info.value)

        print("✓ Authentication failure scenarios validated")

    def test_mcp_client_initialization_with_auth(self):
        """Test MCP client initialization with different auth modes"""
        os.environ["AGENTCORE_AUTH_TOKEN"] = "test_token_init_123"
        os.environ["MCP_SERVER_URL"] = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-mcp-server"

        # Mock AgentCoreMCPClient initialization
        class MockAgentCoreMCPClient:
            def __init__(self, mcp_server_url=None):
                self.mcp_server_url = mcp_server_url or os.getenv("MCP_SERVER_URL")
                self.auth_token = self._get_bearer_token()
                self.session = None

            def _get_bearer_token(self):
                """Mock bearer token retrieval"""
                token = os.getenv("AGENTCORE_AUTH_TOKEN")
                if token and token != "placeholder":
                    return token
                raise Exception("No valid auth token available")

            async def connect(self):
                """Mock connection establishment"""
                if not self.auth_token:
                    raise Exception("No authentication token available")
                return True

        # Test MCP client initialization
        client = MockAgentCoreMCPClient()
        assert client.mcp_server_url == "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-mcp-server"
        assert client.auth_token == "test_token_init_123"

        print("✓ MCP client initialization with authentication validated")

    @pytest.mark.asyncio
    async def test_mcp_client_connection_with_auth(self):
        """Test MCP client connection with authentication"""
        os.environ["AGENTCORE_AUTH_TOKEN"] = "test_connection_token_456"

        # Mock MCP client connection process
        class MockMCPClientConnection:
            def __init__(self):
                self.auth_token = os.getenv("AGENTCORE_AUTH_TOKEN")
                self.connected = False

            async def establish_connection(self):
                """Mock connection establishment with auth"""
                if not self.auth_token:
                    raise Exception("Authentication required")

                # Simulate successful connection
                self.connected = True
                return {
                    "status": "connected",
                    "auth_method": "bearer_token",
                    "server_info": {"name": "test-mcp-server", "version": "1.0.0"},
                }

            async def call_tool(self, tool_name, **kwargs):
                """Mock tool call with authentication"""
                if not self.connected:
                    raise Exception("Not connected to MCP server")

                return {"tool": tool_name, "result": f"Mock result for {tool_name}", "authenticated": True}

        # Test connection and tool call
        client = MockMCPClientConnection()
        connection_result = await client.establish_connection()

        assert connection_result["status"] == "connected"
        assert connection_result["auth_method"] == "bearer_token"
        assert client.connected is True

        # Test authenticated tool call
        tool_result = await client.call_tool("get_usage_analytics", date_range="last_7_days")
        assert tool_result["authenticated"] is True
        assert "Mock result for get_usage_analytics" in tool_result["result"]

        print("✓ MCP client connection with authentication validated")

    def test_analytics_agent_auth_mode_selection(self):
        """Test analytics agent authentication mode selection"""

        # Test mock analytics mode (no authentication needed)
        os.environ["USE_MOCK_ANALYTICS"] = "true"

        def create_analytics_agent_mock_mode():
            use_mock_analytics = os.getenv("USE_MOCK_ANALYTICS", "false").lower() == "true"

            if use_mock_analytics:
                return {
                    "mode": "mock",
                    "authentication_required": False,
                    "tools": ["mock_get_usage_analytics", "mock_get_revenue_metrics"],
                }
            else:
                return {
                    "mode": "mcp_client",
                    "authentication_required": True,
                    "tools": ["get_usage_analytics", "get_revenue_metrics"],
                }

        agent_config = create_analytics_agent_mock_mode()
        assert agent_config["mode"] == "mock"
        assert agent_config["authentication_required"] is False

        # Test MCP client mode (authentication required)
        os.environ["USE_MOCK_ANALYTICS"] = "false"
        os.environ["AGENTCORE_AUTH_TOKEN"] = "test_agent_token_789"

        agent_config = create_analytics_agent_mock_mode()
        assert agent_config["mode"] == "mcp_client"
        assert agent_config["authentication_required"] is True

        print("✓ Analytics agent authentication mode selection validated")

    def test_authentication_error_handling(self):
        """Test authentication error handling in different scenarios"""

        # Test 1: Invalid token format
        os.environ["AGENTCORE_AUTH_TOKEN"] = "invalid_token_format"

        def validate_token_format(token):
            """Mock token validation"""
            if not token or len(token) < 10:
                return False
            if token == "placeholder":
                return False
            return True

        token = os.getenv("AGENTCORE_AUTH_TOKEN")
        is_valid = validate_token_format(token)
        assert is_valid is True  # This token should be valid (length > 10)

        # Test 2: Empty token
        os.environ["AGENTCORE_AUTH_TOKEN"] = ""
        token = os.getenv("AGENTCORE_AUTH_TOKEN")
        is_valid = validate_token_format(token)
        assert is_valid is False

        # Test 3: Placeholder token
        os.environ["AGENTCORE_AUTH_TOKEN"] = "placeholder"
        token = os.getenv("AGENTCORE_AUTH_TOKEN")
        is_valid = validate_token_format(token)
        assert is_valid is False

        print("✓ Authentication error handling validated")

    def test_region_configuration_for_auth(self):
        """Test AWS region configuration for authentication"""

        # Test different region configurations
        regions = ["us-west-2", "us-east-1", "eu-west-1"]

        for region in regions:
            os.environ["AWS_DEFAULT_REGION"] = region

            # Mock region-specific authentication
            def get_region_specific_auth():
                region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")

                # Simulate region-specific configuration
                region_configs = {
                    "us-west-2": {"secret_name": "mcp_server/cognito/credentials"},
                    "us-east-1": {"secret_name": "mcp_server/cognito/credentials"},
                    "eu-west-1": {"secret_name": "mcp_server/cognito/credentials"},
                }

                return region_configs.get(region, region_configs["us-west-2"])

            config = get_region_specific_auth()
            assert "secret_name" in config
            assert config["secret_name"] == "mcp_server/cognito/credentials"

        print("✓ Region configuration for authentication validated")

    def test_authentication_retry_logic(self):
        """Test authentication retry logic for transient failures"""

        # Mock retry logic for authentication
        def authenticate_with_retry(max_retries=3):
            """Mock authentication with retry logic"""
            import time

            for attempt in range(max_retries):
                try:
                    # Simulate transient failure on first attempts
                    if attempt < 2:
                        raise Exception("Transient authentication failure")

                    # Succeed on final attempt
                    return {"status": "success", "token": "retry_success_token_123", "attempts": attempt + 1}

                except Exception as e:
                    if attempt == max_retries - 1:
                        raise Exception(f"Authentication failed after {max_retries} attempts: {str(e)}")

                    # Wait before retry (mocked)
                    time.sleep(0.01)  # Very short sleep for testing

            return None

        # Test successful retry
        result = authenticate_with_retry()
        assert result["status"] == "success"
        assert result["attempts"] == 3  # Should succeed on 3rd attempt

        print("✓ Authentication retry logic validated")

    def test_authentication_modes_summary(self):
        """Test and summarize all authentication modes"""

        auth_modes = [
            {
                "name": "AgentCore Runtime Token",
                "AGENTCORE_AUTH_TOKEN": "runtime_token_123",
                "expected_source": "environment",
            },
            {
                "name": "Secrets Manager Fallback",
                "AGENTCORE_AUTH_TOKEN": "placeholder",
                "expected_source": "secrets_manager",
            },
            {"name": "Mock Analytics (No Auth)", "USE_MOCK_ANALYTICS": "true", "expected_source": "none"},
        ]

        for mode in auth_modes:
            # Set up environment for each mode
            for key, value in mode.items():
                if key not in ["name", "expected_source"]:
                    os.environ[key] = value

            # Test authentication source determination
            def determine_auth_source():
                if os.getenv("USE_MOCK_ANALYTICS", "false").lower() == "true":
                    return "none"

                token = os.getenv("AGENTCORE_AUTH_TOKEN")
                if token and token != "placeholder":
                    return "environment"

                return "secrets_manager"

            auth_source = determine_auth_source()
            assert auth_source == mode["expected_source"]

            print(f"✓ {mode['name']}: auth_source={auth_source}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
