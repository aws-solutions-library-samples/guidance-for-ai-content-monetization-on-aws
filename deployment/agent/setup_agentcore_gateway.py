#!/usr/bin/env python3
"""
Setup AgentCore Gateway with Cognito Authentication

This script creates an AgentCore Gateway that accepts Cognito authentication
and routes requests to the analytics agent.
"""

import json
import os
from pathlib import Path

import boto3


def setup_agentcore_gateway():
    """Set up AgentCore Gateway with Cognito authentication."""

    try:
        from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

        print("🚀 Setting up AgentCore Gateway with Cognito authentication...")

        # Initialize the Gateway client
        client = GatewayClient(region_name="us-west-2")

        # Get the analytics agent ARN
        agent_arn_file = Path("analytics_agent_arn.txt")
        if not agent_arn_file.exists():
            print("❌ Analytics agent ARN file not found. Deploy the agent first.")
            return False

        with open(agent_arn_file, "r") as f:
            agent_arn = f.read().strip()

        print(f"📋 Using analytics agent: {agent_arn}")

        # Use existing Cognito configuration from CDK stack
        print("🔐 Using existing Cognito configuration...")

        # Get Cognito config from identity_config.json
        identity_config_file = Path("identity_config.json")
        if not identity_config_file.exists():
            print("❌ Identity config not found. Run: python identity_config.py first")
            return False

        with open(identity_config_file, "r") as f:
            identity_config = json.load(f)

        cognito_config = identity_config["agentcore_identity"]["cognito_config"]
        user_pool_id = cognito_config["user_pool_id"]
        client_id = cognito_config["user_pool_client_id"]
        region = cognito_config["region"]

        # Construct discovery URL
        discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"

        print(f"   User Pool ID: {user_pool_id}")
        print(f"   Client ID: {client_id}")
        print(f"   Discovery URL: {discovery_url}")

        # Create authorization config manually
        # Use allowedClients instead of allowedAudience for Cognito access tokens
        auth_config = {"customJWTAuthorizer": {"discoveryUrl": discovery_url, "allowedClients": [client_id]}}

        cognito_result = {
            "client_info": {"client_id": client_id, "scope": "openid profile email"},
            "discovery_url": discovery_url,
        }

        print("✅ Cognito OAuth setup complete")
        print(f"   Client ID: {cognito_result['client_info']['client_id']}")
        print(f"   Scope: {cognito_result['client_info']['scope']}")

        # Create the gateway
        print("🌐 Creating AgentCore Gateway...")

        gateway = client.create_mcp_gateway(
            name="analytics-agent-gateway-v2",
            role_arn=None,  # Will be created automatically
            authorizer_config=auth_config,
            enable_semantic_search=True,
        )

        print("✅ Gateway created successfully!")

        # Extract gateway URL from response
        gateway_url = gateway.get("gateway_url") or gateway.get("url")
        if not gateway_url:
            # Try to construct from gateway info
            gateway_arn = gateway.get("gateway_arn")
            if gateway_arn:
                gateway_id = gateway_arn.split("/")[-1]
                gateway_url = f"https://{gateway_id}.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"

        print(f"🔗 MCP Endpoint: {gateway_url}")

        # Save gateway configuration
        gateway_config = {
            "gateway_url": gateway_url,
            "oauth_config": {
                "client_id": cognito_result["client_info"]["client_id"],
                "scope": cognito_result["client_info"]["scope"],
                "discovery_url": cognito_result.get("discovery_url"),
            },
            "agent_arn": agent_arn,
            "created_at": str(os.times()),
        }

        with open("gateway_config.json", "w") as f:
            json.dump(gateway_config, f, indent=2)

        print("✅ Gateway configuration saved to gateway_config.json")

        # Update UI environment
        print("🔧 Updating UI environment...")
        ui_env_path = Path("../ui/.env")
        if ui_env_path.exists():
            # Read current .env
            with open(ui_env_path, "r") as f:
                env_content = f.read()

            # Update the analytics API URL to use the gateway
            gateway_url = gateway.get_mcp_url()

            # Replace or add the gateway URL
            if "VITE_ANALYTICS_GATEWAY_URL=" in env_content:
                # Replace existing
                lines = env_content.split("\n")
                for i, line in enumerate(lines):
                    if line.startswith("VITE_ANALYTICS_GATEWAY_URL="):
                        lines[i] = f"VITE_ANALYTICS_GATEWAY_URL={gateway_url}"
                        break
                env_content = "\n".join(lines)
            else:
                # Add new
                env_content += f"\n# AgentCore Gateway Configuration\nVITE_ANALYTICS_GATEWAY_URL={gateway_url}\n"

            # Write back
            with open(ui_env_path, "w") as f:
                f.write(env_content)

            print("✅ UI environment updated with gateway URL")

        print("\n🎉 AgentCore Gateway setup complete!")
        print("\n📋 Next steps:")
        print("1. Update your UI to use the gateway URL instead of direct agent calls")
        print("2. Use the OAuth client credentials for authentication")
        print("3. Test the gateway endpoint")

        return True

    except ImportError:
        print("❌ bedrock-agentcore-starter-toolkit not available")
        print("   Install with: pip install bedrock-agentcore-starter-toolkit")
        return False
    except Exception as e:
        print(f"❌ Error setting up gateway: {str(e)}")
        return False


if __name__ == "__main__":
    success = setup_agentcore_gateway()
    if not success:
        exit(1)
