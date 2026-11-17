"""
Test environment configuration for Analytics Agent integration

This test validates that the Analytics Agent ARN is properly configured
in the UI Backend Lambda function environment variables.
"""

import pytest
import boto3
import json
import os
from pathlib import Path


class TestAnalyticsAgentEnvironmentConfiguration:
    """Test Analytics Agent environment configuration"""

    def setup_method(self):
        """Setup test environment"""
        # Use the genai profile for AWS operations
        session = boto3.Session(profile_name="genai", region_name="us-west-2")
        self.lambda_client = session.client("lambda")
        self.cloudformation = session.client("cloudformation")

    def test_analytics_agent_deployment_artifacts_exist(self):
        """Test that analytics agent deployment artifacts exist"""
        # Check for deployment JSON file
        deployment_file = Path("analytics_agent/analytics_agent_deployment.json")
        assert deployment_file.exists(), "Analytics agent deployment.json file should exist"

        # Check for ARN text file
        arn_file = Path("analytics_agent/analytics_agent_arn.txt")
        assert arn_file.exists(), "Analytics agent ARN text file should exist"

        # Validate deployment file content
        with open(deployment_file, "r") as f:
            deployment_data = json.load(f)

        assert "analytics_agent_arn" in deployment_data, "Deployment file should contain analytics_agent_arn"
        assert deployment_data["analytics_agent_arn"].startswith("arn:aws:bedrock-agentcore"), (
            "ARN should be valid AgentCore ARN"
        )

        # Validate ARN file content
        with open(arn_file, "r") as f:
            arn_content = f.read().strip()

        assert arn_content.startswith("arn:aws:bedrock-agentcore"), "ARN file should contain valid AgentCore ARN"
        assert arn_content == deployment_data["analytics_agent_arn"], "ARN file and deployment file should match"

    def test_cdk_stack_outputs_ui_backend_lambda_name(self):
        """Test that CDK stack outputs include UIBackendLambdaName"""
        try:
            response = self.cloudformation.describe_stacks(StackName="ContentMonetizationStack")
            stacks = response.get("Stacks", [])

            assert len(stacks) > 0, "ContentMonetizationStack should exist"

            outputs = stacks[0].get("Outputs", [])
            output_keys = [output.get("OutputKey") for output in outputs]

            assert "UIBackendLambdaName" in output_keys, "CDK stack should output UIBackendLambdaName"

            # Get the actual Lambda name
            ui_backend_lambda_name = None
            for output in outputs:
                if output.get("OutputKey") == "UIBackendLambdaName":
                    ui_backend_lambda_name = output.get("OutputValue")
                    break

            assert ui_backend_lambda_name is not None, "UIBackendLambdaName should have a value"
            assert ui_backend_lambda_name.startswith("ContentMonetizationStack-UIBackendFunction"), (
                "Lambda name should follow expected pattern"
            )

        except Exception as e:
            pytest.fail(f"Failed to get CDK stack outputs: {e}")

    def test_ui_backend_lambda_has_analytics_agent_arn(self):
        """Test that UI Backend Lambda has ANALYTICS_AGENT_ARN environment variable"""
        # Get Lambda function name from CDK outputs
        lambda_name = self._get_ui_backend_lambda_name()
        assert lambda_name is not None, "Should be able to get UI Backend Lambda name"

        try:
            # Get Lambda function configuration
            response = self.lambda_client.get_function_configuration(FunctionName=lambda_name)
            env_vars = response.get("Environment", {}).get("Variables", {})

            assert "ANALYTICS_AGENT_ARN" in env_vars, (
                "UI Backend Lambda should have ANALYTICS_AGENT_ARN environment variable"
            )

            analytics_agent_arn = env_vars["ANALYTICS_AGENT_ARN"]
            assert analytics_agent_arn != "REPLACEME", "ANALYTICS_AGENT_ARN should not be the default placeholder value"
            assert analytics_agent_arn.startswith("arn:aws:bedrock-agentcore"), (
                "ANALYTICS_AGENT_ARN should be valid AgentCore ARN"
            )

            # Verify it matches the deployment artifacts
            deployment_file = Path("analytics_agent/analytics_agent_deployment.json")
            if deployment_file.exists():
                with open(deployment_file, "r") as f:
                    deployment_data = json.load(f)
                expected_arn = deployment_data.get("analytics_agent_arn")
                assert analytics_agent_arn == expected_arn, "Lambda environment ARN should match deployment artifacts"

        except self.lambda_client.exceptions.ResourceNotFoundException:
            pytest.fail(f"UI Backend Lambda function {lambda_name} not found")
        except Exception as e:
            pytest.fail(f"Failed to get Lambda configuration: {e}")

    def test_analytics_agent_arn_consistency(self):
        """Test that Analytics Agent ARN is consistent across all sources"""
        # Get ARN from deployment file
        deployment_file = Path("analytics_agent/analytics_agent_deployment.json")
        arn_file = Path("analytics_agent/analytics_agent_arn.txt")

        deployment_arn = None
        if deployment_file.exists():
            with open(deployment_file, "r") as f:
                deployment_data = json.load(f)
                deployment_arn = deployment_data.get("analytics_agent_arn")

        file_arn = None
        if arn_file.exists():
            with open(arn_file, "r") as f:
                file_arn = f.read().strip()

        # Get ARN from Lambda environment
        lambda_name = self._get_ui_backend_lambda_name()
        lambda_arn = None
        if lambda_name:
            try:
                response = self.lambda_client.get_function_configuration(FunctionName=lambda_name)
                env_vars = response.get("Environment", {}).get("Variables", {})
                lambda_arn = env_vars.get("ANALYTICS_AGENT_ARN")
            except Exception:
                pass

        # All ARNs should be consistent
        arns = [arn for arn in [deployment_arn, file_arn, lambda_arn] if arn and arn != "REPLACEME"]

        assert len(set(arns)) <= 1, f"All Analytics Agent ARNs should be consistent: {arns}"
        assert len(arns) > 0, "At least one valid Analytics Agent ARN should be found"

    def test_cdk_stack_includes_analytics_agent_arn_parameter(self):
        """Test that CDK stack properly handles analytics-agent-arn context parameter"""
        # This test verifies the CDK stack code structure
        stack_file = Path("infrastructure/content_monetization_stack.py")
        assert stack_file.exists(), "CDK stack file should exist"

        with open(stack_file, "r") as f:
            stack_content = f.read()

        # Verify the stack includes analytics agent ARN handling
        assert "analytics_agent_arn" in stack_content, "Stack should handle analytics_agent_arn parameter"
        assert "ANALYTICS_AGENT_ARN" in stack_content, "Stack should set ANALYTICS_AGENT_ARN environment variable"
        assert 'try_get_context("analytics-agent-arn")' in stack_content, (
            "Stack should read analytics-agent-arn from context"
        )

    def _get_ui_backend_lambda_name(self):
        """Helper method to get UI Backend Lambda name from CDK outputs"""
        try:
            # Use the genai profile for CloudFormation operations
            session = boto3.Session(profile_name="genai", region_name="us-west-2")
            cloudformation = session.client("cloudformation")

            response = cloudformation.describe_stacks(StackName="ContentMonetizationStack")
            stacks = response.get("Stacks", [])

            if not stacks:
                return None

            outputs = stacks[0].get("Outputs", [])

            for output in outputs:
                if output.get("OutputKey") == "UIBackendLambdaName":
                    return output.get("OutputValue")

            return None

        except Exception:
            return None


class TestAnalyticsAgentChatEndpoint:
    """Test Analytics Agent chat endpoint functionality"""

    def setup_method(self):
        """Setup test environment"""
        # Use the genai profile for AWS operations
        session = boto3.Session(profile_name="genai", region_name="us-west-2")
        self.lambda_client = session.client("lambda")

    def test_analytics_chat_endpoint_configuration(self):
        """Test that analytics chat endpoint is properly configured"""
        # This would be an integration test that requires the full deployment
        # For now, we'll test the configuration aspects

        # Verify the UI Backend Lambda handler includes analytics agent chat handling
        handler_file = Path("lambda/ui_backend/handler.py")
        assert handler_file.exists(), "UI Backend handler should exist"

        with open(handler_file, "r") as f:
            handler_content = f.read()

        # Verify analytics agent chat endpoint handling
        assert "/analytics-agent/chat" in handler_content, "Handler should include analytics-agent/chat endpoint"
        assert "ANALYTICS_AGENT_ARN" in handler_content, "Handler should use ANALYTICS_AGENT_ARN environment variable"
        assert "bedrock-agentcore" in handler_content, "Handler should use bedrock-agentcore client"


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v"])
