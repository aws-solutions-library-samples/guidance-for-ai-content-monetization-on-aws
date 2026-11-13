# test_gateway_integration.py - Unit tests for AgentCore Gateway integration
import pytest
import pytest_asyncio
import os
import json
from unittest.mock import MagicMock, patch, AsyncMock
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from identity_config import AgentCoreIdentityConfig, create_identity_config
from gateway_config import AgentCoreGatewayConfig, create_gateway_config


class TestAgentCoreIdentityConfig:
    """Test suite for AgentCore Identity configuration."""

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables for testing."""
        with patch.dict(
            os.environ, {"STACK_NAME": "TestIabStack", "AWS_PROFILE": "test-profile", "AWS_REGION": "us-west-2"}
        ):
            yield

    @pytest.fixture
    def mock_boto3_session(self):
        """Mock boto3 session and clients."""
        with patch("analytics_agent.identity_config.boto3.Session") as mock_session:
            # Mock CloudFormation client
            mock_cf_client = MagicMock()
            mock_cf_client.describe_stacks.return_value = {
                "Stacks": [
                    {
                        "Outputs": [
                            {"OutputKey": "CognitoUserPoolId", "OutputValue": "us-west-2_TEST123"},
                            {"OutputKey": "CognitoUserPoolClientId", "OutputValue": "test-client-id-123"},
                            {"OutputKey": "CognitoUserPoolDomain", "OutputValue": "test-auth-domain"},
                            {"OutputKey": "CognitoIdentityPoolId", "OutputValue": "us-west-2:test-identity-pool"},
                            {"OutputKey": "ApiGatewayUrl", "OutputValue": "https://test-api.amazonaws.com/prod"},
                        ]
                    }
                ]
            }

            # Mock Cognito IDP client
            mock_cognito_client = MagicMock()
            mock_cognito_client.describe_user_pool.return_value = {
                "UserPool": {"Name": "TestUserPool", "Id": "us-west-2_TEST123"}
            }
            mock_cognito_client.describe_user_pool_client.return_value = {
                "UserPoolClient": {"ClientName": "TestClient", "ClientId": "test-client-id-123"}
            }

            # Configure mock session
            mock_session_instance = MagicMock()
            mock_session_instance.client.side_effect = lambda service: {
                "cloudformation": mock_cf_client,
                "cognito-idp": mock_cognito_client,
            }[service]

            mock_session.return_value = mock_session_instance
            yield mock_session

    def test_identity_config_initialization(self, mock_env_vars, mock_boto3_session):
        """Test Identity configuration initialization."""
        config = AgentCoreIdentityConfig()

        assert config.stack_name == "TestIabStack"
        assert config.aws_profile == "test-profile"
        assert config.region == "us-west-2"

    def test_get_stack_outputs(self, mock_env_vars, mock_boto3_session):
        """Test retrieving CDK stack outputs."""
        config = AgentCoreIdentityConfig()
        outputs = config.get_stack_outputs()

        assert outputs["CognitoUserPoolId"] == "us-west-2_TEST123"
        assert outputs["CognitoUserPoolClientId"] == "test-client-id-123"
        assert outputs["CognitoUserPoolDomain"] == "test-auth-domain"
        assert outputs["CognitoIdentityPoolId"] == "us-west-2:test-identity-pool"

    def test_get_cognito_config(self, mock_env_vars, mock_boto3_session):
        """Test extracting Cognito configuration."""
        config = AgentCoreIdentityConfig()
        cognito_config = config.get_cognito_config()

        assert cognito_config["user_pool_id"] == "us-west-2_TEST123"
        assert cognito_config["user_pool_client_id"] == "test-client-id-123"
        assert cognito_config["user_pool_domain"] == "test-auth-domain"
        assert cognito_config["region"] == "us-west-2"

    def test_create_agentcore_identity_config(self, mock_env_vars, mock_boto3_session):
        """Test creating AgentCore Identity configuration."""
        config = AgentCoreIdentityConfig()
        identity_config = config.create_agentcore_identity_config()

        assert identity_config["identity_type"] == "cognito"
        assert identity_config["cognito_config"]["user_pool_id"] == "us-west-2_TEST123"
        assert identity_config["cognito_config"]["user_pool_client_id"] == "test-client-id-123"
        assert identity_config["user_context_passing"]["enabled"] is True
        assert identity_config["authentication_flow"]["require_verification"] is True

    def test_validate_cognito_integration(self, mock_env_vars, mock_boto3_session):
        """Test Cognito integration validation."""
        config = AgentCoreIdentityConfig()
        is_valid = config.validate_cognito_integration()

        assert is_valid is True

    def test_get_iam_policy_for_identity(self, mock_env_vars, mock_boto3_session):
        """Test IAM policy generation for Identity."""
        config = AgentCoreIdentityConfig()
        policy = config.get_iam_policy_for_identity()

        assert policy["Version"] == "2012-10-17"
        assert len(policy["Statement"]) >= 2

        # Check Cognito permissions
        cognito_statement = policy["Statement"][0]
        assert "cognito-idp:DescribeUserPool" in cognito_statement["Action"]
        assert "cognito-idp:AdminGetUser" in cognito_statement["Action"]

    def test_export_config_to_file(self, mock_env_vars, mock_boto3_session, tmp_path):
        """Test exporting configuration to file."""
        config = AgentCoreIdentityConfig()
        output_path = tmp_path / "test_identity_config.json"

        result_path = config.export_config_to_file(str(output_path))

        assert result_path == str(output_path)
        assert output_path.exists()

        # Verify file contents
        with open(output_path, "r") as f:
            exported_config = json.load(f)

        assert "agentcore_identity" in exported_config
        assert "iam_policy" in exported_config
        assert "validation_status" in exported_config
        assert "metadata" in exported_config

    def test_create_identity_config_factory(self, mock_env_vars, mock_boto3_session):
        """Test factory function for creating Identity configuration."""
        config = create_identity_config("TestStack", "test-profile", "us-east-1")

        assert isinstance(config, AgentCoreIdentityConfig)
        assert config.stack_name == "TestStack"
        assert config.aws_profile == "test-profile"
        assert config.region == "us-east-1"


class TestAgentCoreGatewayConfig:
    """Test suite for AgentCore Gateway configuration."""

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables for testing."""
        with patch.dict(
            os.environ,
            {
                "AGENTCORE_GATEWAY_URL": "https://test-gateway.amazonaws.com",
                "IAB_API_BASE_URL": "https://test-iab-api.example.com",
            },
        ):
            yield

    def test_gateway_config_initialization(self, mock_env_vars):
        """Test Gateway configuration initialization."""
        config = AgentCoreGatewayConfig()

        assert config.gateway_url == "https://test-gateway.amazonaws.com"
        assert config.iab_api_base == "https://test-iab-api.example.com"
        assert config.gateway_config["gateway_url"] == "https://test-gateway.amazonaws.com"

    def test_get_analytics_agent_tools_config(self, mock_env_vars):
        """Test analytics agent tools configuration."""
        config = AgentCoreGatewayConfig()
        tools_config = config.get_analytics_agent_tools_config()

        # Verify all expected tools are configured
        expected_tools = [
            "get_usage_analytics",
            "analyze_content_performance",
            "generate_insights",
            "create_custom_report",
            "get_revenue_metrics",
            "analyze_bot_behavior",
            "get_page_context_help",
            "suggest_page_actions",
        ]

        for tool in expected_tools:
            assert tool in tools_config
            assert "description" in tools_config[tool]
            assert "parameters" in tools_config[tool]

        # Verify user context requirements
        assert tools_config["get_usage_analytics"]["requires_user_context"] is True
        assert tools_config["get_page_context_help"]["requires_user_context"] is False

    def test_get_iab_api_gateway_tools_config(self, mock_env_vars):
        """Test IAB API Gateway tools configuration."""
        config = AgentCoreGatewayConfig()
        iab_tools = config.get_iab_api_gateway_tools_config()

        # Verify IAB API tools
        expected_iab_tools = ["iab_search_content", "iab_get_content_details", "iab_get_analytics"]

        for tool in expected_iab_tools:
            assert tool in iab_tools
            assert "endpoint" in iab_tools[tool]
            assert "authentication" in iab_tools[tool]
            assert "user_context_mapping" in iab_tools[tool]

        # Verify authentication configuration
        search_tool = iab_tools["iab_search_content"]
        assert search_tool["authentication"]["type"] == "bearer_token"
        assert search_tool["authentication"]["token_source"] == "user_context.publisher_key"

    def test_create_user_request_routing_config(self, mock_env_vars):
        """Test user request routing configuration."""
        config = AgentCoreGatewayConfig()
        routing_config = config.create_user_request_routing_config()

        # Verify routing configuration
        assert "routes" in routing_config
        assert "/analytics-agent/chat" in routing_config["routes"]

        chat_route = routing_config["routes"]["/analytics-agent/chat"]
        assert chat_route["method"] == "POST"
        assert chat_route["authentication_required"] is True
        assert "user_context_extraction" in chat_route

        # Verify middleware
        assert "middleware" in routing_config
        expected_middleware = ["authentication", "user_context_extraction", "rate_limiting"]
        for middleware in expected_middleware:
            assert middleware in routing_config["middleware"]

    def test_create_cognito_user_context_config(self, mock_env_vars):
        """Test Cognito user context configuration."""
        config = AgentCoreGatewayConfig()
        context_config = config.create_cognito_user_context_config()

        # Verify user context extraction
        assert "user_context_extraction" in context_config
        extraction = context_config["user_context_extraction"]
        assert extraction["source"] == "cognito_jwt"
        assert "cognito:username" in extraction["required_claims"]
        assert "email" in extraction["required_claims"]

        # Verify context transformation
        assert "context_transformation" in context_config
        transformation = context_config["context_transformation"]
        assert transformation["user_id"] == "cognito:username"
        assert transformation["publisher_id"] == "custom:publisher_id"

        # Verify validation rules
        assert "context_validation" in context_config
        validation = context_config["context_validation"]
        assert "user_id" in validation["required_fields"]
        assert "publisher_id" in validation["required_fields"]

    def test_get_iam_policy_for_gateway(self, mock_env_vars):
        """Test IAM policy generation for Gateway."""
        config = AgentCoreGatewayConfig()
        policy = config.get_iam_policy_for_gateway()

        assert policy["Version"] == "2012-10-17"
        assert len(policy["Statement"]) >= 3

        # Check Cognito permissions
        cognito_statement = policy["Statement"][0]
        assert "cognito-idp:GetUser" in cognito_statement["Action"]
        assert "cognito-idp:AdminGetUser" in cognito_statement["Action"]

        # Check API Gateway permissions
        api_statement = policy["Statement"][1]
        assert "execute-api:Invoke" in api_statement["Action"]

    def test_create_complete_gateway_config(self, mock_env_vars):
        """Test creating complete Gateway configuration."""
        config = AgentCoreGatewayConfig()
        complete_config = config.create_complete_gateway_config()

        # Verify all configuration sections
        expected_sections = [
            "gateway",
            "analytics_agent_tools",
            "iab_api_tools",
            "user_request_routing",
            "cognito_user_context",
            "iam_policy",
            "metadata",
        ]

        for section in expected_sections:
            assert section in complete_config

        # Verify metadata
        metadata = complete_config["metadata"]
        assert "created_at" in metadata
        assert metadata["gateway_url"] == "https://test-gateway.amazonaws.com"
        assert metadata["iab_api_base"] == "https://test-iab-api.example.com"

    def test_validate_gateway_config(self, mock_env_vars):
        """Test Gateway configuration validation."""
        config = AgentCoreGatewayConfig()
        validation = config.validate_gateway_config()

        assert validation["valid"] is True
        assert len(validation["errors"]) == 0

    def test_validate_gateway_config_missing_url(self):
        """Test Gateway configuration validation with missing URL."""
        config = AgentCoreGatewayConfig(gateway_url=None)
        validation = config.validate_gateway_config()

        assert validation["valid"] is False
        assert len(validation["errors"]) > 0
        assert any("Gateway URL" in error for error in validation["errors"])

    def test_export_config_to_file(self, mock_env_vars, tmp_path):
        """Test exporting Gateway configuration to file."""
        config = AgentCoreGatewayConfig()
        output_path = tmp_path / "test_gateway_config.json"

        result_path = config.export_config_to_file(str(output_path))

        assert result_path == str(output_path)
        assert output_path.exists()

        # Verify file contents
        with open(output_path, "r") as f:
            exported_config = json.load(f)

        assert "gateway" in exported_config
        assert "analytics_agent_tools" in exported_config
        assert "iab_api_tools" in exported_config
        assert "user_request_routing" in exported_config

    def test_create_gateway_config_factory(self, mock_env_vars):
        """Test factory function for creating Gateway configuration."""
        config = create_gateway_config(
            gateway_url="https://custom-gateway.amazonaws.com", iab_api_base="https://custom-iab-api.example.com"
        )

        assert isinstance(config, AgentCoreGatewayConfig)
        assert config.gateway_url == "https://custom-gateway.amazonaws.com"
        assert config.iab_api_base == "https://custom-iab-api.example.com"


class TestGatewayIdentityIntegration:
    """Test suite for Gateway and Identity integration."""

    @pytest.fixture
    def mock_env_vars(self):
        """Mock environment variables for testing."""
        with patch.dict(
            os.environ,
            {
                "STACK_NAME": "TestIabStack",
                "AWS_PROFILE": "test-profile",
                "AWS_REGION": "us-west-2",
                "AGENTCORE_GATEWAY_URL": "https://test-gateway.amazonaws.com",
                "IAB_API_BASE_URL": "https://test-iab-api.example.com",
            },
        ):
            yield

    @pytest.fixture
    def mock_boto3_session(self):
        """Mock boto3 session for integration tests."""
        with patch("analytics_agent.identity_config.boto3.Session") as mock_session:
            mock_cf_client = MagicMock()
            mock_cf_client.describe_stacks.return_value = {
                "Stacks": [
                    {
                        "Outputs": [
                            {"OutputKey": "CognitoUserPoolId", "OutputValue": "us-west-2_INTEGRATION"},
                            {"OutputKey": "CognitoUserPoolClientId", "OutputValue": "integration-client-id"},
                        ]
                    }
                ]
            }

            mock_cognito_client = MagicMock()
            mock_cognito_client.describe_user_pool.return_value = {
                "UserPool": {"Name": "IntegrationPool", "Id": "us-west-2_INTEGRATION"}
            }

            mock_session_instance = MagicMock()
            mock_session_instance.client.side_effect = lambda service: {
                "cloudformation": mock_cf_client,
                "cognito-idp": mock_cognito_client,
            }[service]

            mock_session.return_value = mock_session_instance
            yield mock_session

    def test_identity_gateway_integration(self, mock_env_vars, mock_boto3_session):
        """Test integration between Identity and Gateway configurations."""
        # Create Identity configuration
        identity_config = create_identity_config()
        cognito_config = identity_config.get_cognito_config()

        # Create Gateway configuration with Identity config
        gateway_config = create_gateway_config(identity_config=cognito_config)

        # Verify integration
        assert gateway_config.identity_config == cognito_config

        # Test user context configuration uses Cognito settings
        user_context_config = gateway_config.create_cognito_user_context_config()
        assert "cognito:username" in user_context_config["user_context_extraction"]["required_claims"]

        # Test complete configuration includes both Identity and Gateway
        complete_config = gateway_config.create_complete_gateway_config()
        assert "cognito_user_context" in complete_config
        assert "user_request_routing" in complete_config

    def test_user_context_passing_configuration(self, mock_env_vars, mock_boto3_session):
        """Test user context passing from Cognito through Gateway."""
        identity_config = create_identity_config()
        gateway_config = create_gateway_config(identity_config=identity_config.get_cognito_config())

        # Get user context configuration
        context_config = gateway_config.create_cognito_user_context_config()

        # Verify context extraction from Cognito JWT
        extraction = context_config["user_context_extraction"]
        assert extraction["source"] == "cognito_jwt"
        assert "cognito:username" in extraction["required_claims"]
        assert "custom:publisher_id" in extraction["required_claims"]

        # Verify context transformation
        transformation = context_config["context_transformation"]
        assert transformation["user_id"] == "cognito:username"
        assert transformation["publisher_id"] == "custom:publisher_id"

        # Verify context validation
        validation = context_config["context_validation"]
        assert "user_id" in validation["required_fields"]
        assert "publisher_id" in validation["required_fields"]

    def test_iab_api_integration_with_user_context(self, mock_env_vars, mock_boto3_session):
        """Test IAB API integration with Cognito user context."""
        gateway_config = create_gateway_config()
        iab_tools = gateway_config.get_iab_api_gateway_tools_config()

        # Verify IAB API tools use user context for authentication
        for tool_name, tool_config in iab_tools.items():
            assert "authentication" in tool_config
            assert tool_config["authentication"]["type"] == "bearer_token"
            assert "user_context" in tool_config["authentication"]["token_source"]

            assert "user_context_mapping" in tool_config
            mapping = tool_config["user_context_mapping"]
            assert "publisher_id" in mapping
            assert "user_context.cognito_username" in mapping["publisher_id"]


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
