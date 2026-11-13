# identity_config.py - AgentCore Identity configuration for analytics agent
import json
import logging
import os
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class AgentCoreIdentityConfig:
    """
    Configuration class for AgentCore Identity integration with existing Cognito user pool.

    This class handles the configuration of AgentCore Identity to use the existing
    Cognito user pool from the IAB ingest API CDK stack, enabling seamless user
    authentication and context passing through the Gateway.
    """

    def __init__(self, stack_name: str = None, aws_profile: str = None, region: str = "us-west-2"):
        """
        Initialize the Identity configuration.

        Args:
            stack_name: Name of the CDK stack containing Cognito resources
            aws_profile: AWS profile to use for accessing stack outputs
            region: AWS region where resources are deployed
        """
        self.stack_name = stack_name or os.getenv("STACK_NAME", "ContentMonetizationStack")
        self.aws_profile = aws_profile or os.getenv("AWS_PROFILE", "default")
        self.region = region or os.getenv("AWS_REGION", "us-west-2")

        # Initialize AWS session
        if self.aws_profile != "default":
            self.session = boto3.Session(profile_name=self.aws_profile, region_name=self.region)
        else:
            self.session = boto3.Session(region_name=self.region)

        self.cloudformation = self.session.client("cloudformation")
        self.cognito_idp = self.session.client("cognito-idp")

        # Cache for stack outputs
        self._stack_outputs = None
        self._cognito_config = None

        logger.info(f"Initialized AgentCore Identity config for stack: {self.stack_name}")

    def get_stack_outputs(self) -> Dict[str, str]:
        """
        Retrieve CDK stack outputs containing Cognito configuration.

        Returns:
            Dictionary of stack outputs
        """
        if self._stack_outputs is not None:
            return self._stack_outputs

        try:
            response = self.cloudformation.describe_stacks(StackName=self.stack_name)
            stacks = response.get("Stacks", [])

            if not stacks:
                raise ValueError(f"Stack {self.stack_name} not found")

            stack = stacks[0]
            outputs = stack.get("Outputs", [])

            self._stack_outputs = {output["OutputKey"]: output["OutputValue"] for output in outputs}

            logger.info(f"Retrieved {len(self._stack_outputs)} stack outputs")
            return self._stack_outputs

        except ClientError as e:
            logger.error(f"Failed to get stack outputs: {str(e)}")
            # Return mock configuration for development/testing
            return self._get_mock_stack_outputs()

    def _get_mock_stack_outputs(self) -> Dict[str, str]:
        """Return mock stack outputs for development/testing."""
        return {
            "CognitoUserPoolId": "us-west-2_XXXXXXXXX",
            "CognitoUserPoolClientId": "xxxxxxxxxxxxxxxxxxxxxxxxxx",
            "CognitoUserPoolDomain": "iab-publisher-auth",
            "CognitoIdentityPoolId": "us-west-2:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            "ApiGatewayUrl": "https://xxxxxxxxxx.execute-api.us-west-2.amazonaws.com/prod",
        }

    def get_cognito_config(self) -> Dict[str, Any]:
        """
        Extract Cognito configuration from stack outputs.

        Returns:
            Cognito configuration dictionary
        """
        if self._cognito_config is not None:
            return self._cognito_config

        stack_outputs = self.get_stack_outputs()

        self._cognito_config = {
            "user_pool_id": stack_outputs.get("UserPoolId"),
            "user_pool_client_id": stack_outputs.get("UserPoolClientId"),
            "user_pool_domain": stack_outputs.get("CognitoUserPoolDomain"),
            "identity_pool_id": stack_outputs.get("CognitoIdentityPoolId"),
            "region": self.region,
        }

        # Validate required configuration
        required_fields = ["user_pool_id", "user_pool_client_id"]
        missing_fields = [field for field in required_fields if not self._cognito_config.get(field)]

        if missing_fields:
            logger.warning(f"Missing required Cognito configuration: {missing_fields}")

        logger.info("Retrieved Cognito configuration from stack outputs")
        return self._cognito_config

    def create_agentcore_identity_config(self) -> Dict[str, Any]:
        """
        Create AgentCore Identity configuration using existing Cognito user pool.

        Returns:
            AgentCore Identity configuration dictionary
        """
        cognito_config = self.get_cognito_config()

        identity_config = {
            "identity_type": "cognito",
            "cognito_config": {
                "user_pool_id": cognito_config["user_pool_id"],
                "user_pool_client_id": cognito_config["user_pool_client_id"],
                "region": cognito_config["region"],
            },
            "user_context_passing": {
                "enabled": True,
                "include_user_attributes": True,
                "include_groups": True,
                "context_key": "user_context",
            },
            "authentication_flow": {
                "allow_admin_create_user_only": False,
                "allow_unauthenticated_identities": False,
                "require_verification": True,
            },
            "token_configuration": {
                "access_token_validity": 60,  # minutes
                "id_token_validity": 60,  # minutes
                "refresh_token_validity": 30,  # days
            },
        }

        # Add optional domain configuration if available
        if cognito_config.get("user_pool_domain"):
            identity_config["cognito_config"]["domain"] = cognito_config["user_pool_domain"]

        logger.info("Created AgentCore Identity configuration")
        return identity_config

    def validate_cognito_integration(self) -> bool:
        """
        Validate that the Cognito user pool is accessible and properly configured.

        Returns:
            True if validation passes, False otherwise
        """
        try:
            cognito_config = self.get_cognito_config()
            user_pool_id = cognito_config.get("user_pool_id")

            if not user_pool_id:
                logger.error("No user pool ID found in configuration")
                return False

            # Describe the user pool to validate access
            response = self.cognito_idp.describe_user_pool(UserPoolId=user_pool_id)
            user_pool = response.get("UserPool", {})

            logger.info(f"Validated Cognito user pool: {user_pool.get('Name', 'Unknown')}")

            # Check if user pool client exists
            client_id = cognito_config.get("user_pool_client_id")
            if client_id:
                client_response = self.cognito_idp.describe_user_pool_client(
                    UserPoolId=user_pool_id, ClientId=client_id
                )
                client_name = client_response.get("UserPoolClient", {}).get("ClientName", "Unknown")
                logger.info(f"Validated Cognito user pool client: {client_name}")

            return True

        except ClientError as e:
            logger.error(f"Failed to validate Cognito integration: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during validation: {str(e)}")
            return False

    def get_iam_policy_for_identity(self) -> Dict[str, Any]:
        """
        Generate IAM policy for AgentCore Identity to access Cognito resources.

        Returns:
            IAM policy document
        """
        cognito_config = self.get_cognito_config()
        user_pool_arn = f"arn:aws:cognito-idp:{self.region}:*:userpool/{cognito_config['user_pool_id']}"

        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "cognito-idp:DescribeUserPool",
                        "cognito-idp:DescribeUserPoolClient",
                        "cognito-idp:ListUsers",
                        "cognito-idp:AdminGetUser",
                        "cognito-idp:AdminListGroupsForUser",
                        "cognito-idp:GetUser",
                    ],
                    "Resource": [user_pool_arn, f"{user_pool_arn}/*"],
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "cognito-identity:GetId",
                        "cognito-identity:GetCredentialsForIdentity",
                        "cognito-identity:DescribeIdentityPool",
                    ],
                    "Resource": "*",
                },
            ],
        }

        logger.info("Generated IAM policy for AgentCore Identity")
        return policy

    def export_config_to_file(self, output_path: str = "identity_config.json") -> str:
        """
        Export the complete Identity configuration to a JSON file.

        Args:
            output_path: Path to save the configuration file

        Returns:
            Path to the saved configuration file
        """
        config = {
            "agentcore_identity": self.create_agentcore_identity_config(),
            "iam_policy": self.get_iam_policy_for_identity(),
            "validation_status": self.validate_cognito_integration(),
            "metadata": {"stack_name": self.stack_name, "region": self.region, "generated_at": str(os.times())},
        }

        with open(output_path, "w") as f:
            json.dump(config, f, indent=2)

        logger.info(f"Exported Identity configuration to {output_path}")
        return output_path


# Factory function for easy configuration creation
def create_identity_config(
    stack_name: str = None, aws_profile: str = None, region: str = "us-west-2"
) -> AgentCoreIdentityConfig:
    """
    Factory function to create an AgentCore Identity configuration.

    Args:
        stack_name: Name of the CDK stack containing Cognito resources
        aws_profile: AWS profile to use
        region: AWS region

    Returns:
        Configured AgentCoreIdentityConfig instance
    """
    return AgentCoreIdentityConfig(stack_name, aws_profile, region)


# CLI utility for testing configuration
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Configure AgentCore Identity with existing Cognito")
    parser.add_argument("--stack-name", default="ContentMonetizationStack", help="CDK stack name")
    parser.add_argument("--aws-profile", default="default", help="AWS profile")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    parser.add_argument("--output", default="identity_config.json", help="Output file path")
    parser.add_argument("--validate", action="store_true", help="Validate configuration only")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Create configuration
    config = create_identity_config(args.stack_name, args.aws_profile, args.region)

    if args.validate:
        # Validation only
        is_valid = config.validate_cognito_integration()
        print(f"Configuration validation: {'PASSED' if is_valid else 'FAILED'}")
    else:
        # Export full configuration
        output_path = config.export_config_to_file(args.output)
        print(f"Configuration exported to: {output_path}")

        # Also validate
        is_valid = config.validate_cognito_integration()
        print(f"Configuration validation: {'PASSED' if is_valid else 'FAILED'}")
