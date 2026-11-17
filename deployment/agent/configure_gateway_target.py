#!/usr/bin/env python3
"""
Configure AgentCore Gateway Target to route to Analytics Agent

This script adds the Analytics Agent as a target to the existing gateway
so that MCP requests are properly routed.
"""

import json
from pathlib import Path

import boto3


def configure_gateway_target():
    """Configure the gateway to route to the Analytics Agent."""

    print("🔧 Configuring AgentCore Gateway target...")

    # Initialize the gateway client
    gateway_client = boto3.client("bedrock-agentcore-control", region_name="us-west-2")

    # Read the gateway configuration
    gateway_config_file = Path("analytics_agent/gateway_config.json")
    if not gateway_config_file.exists():
        print("❌ Gateway config not found. Run setup_agentcore_gateway.py first.")
        return False

    with open(gateway_config_file, "r") as f:
        gateway_config = json.load(f)

    agent_arn = gateway_config.get("agent_arn")
    if not agent_arn:
        print("❌ Analytics Agent ARN not found in gateway config.")
        return False

    print(f"📋 Analytics Agent ARN: {agent_arn}")

    # Extract gateway ID from the URL
    # URL format: https://analytics-agent-gateway-v2-a29zdszsbw.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp
    gateway_url = "https://analytics-agent-gateway-v2-a29zdszsbw.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"
    gateway_id = gateway_url.split("//")[1].split(".")[0]  # Extract the ID part

    print(f"📋 Gateway ID: {gateway_id}")

    try:
        # Create the target configuration for the Analytics Agent
        # For AgentCore runtime, we need to use a different approach
        # Let's try using the smithyModel configuration
        target_config = {"mcp": {"smithyModel": {"smithyModelArn": agent_arn}}}

        # Create gateway target
        credential_config = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

        create_target_response = gateway_client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name="AnalyticsAgentTarget",
            description="Routes MCP requests to Analytics Agent",
            targetConfiguration=target_config,
            credentialProviderConfigurations=credential_config,
        )

        target_id = create_target_response["targetId"]
        print(f"✅ Gateway target created: {target_id}")

        # Update the gateway config with target info
        gateway_config["target_id"] = target_id
        gateway_config["target_type"] = "agentRuntime"

        with open(gateway_config_file, "w") as f:
            json.dump(gateway_config, f, indent=2)

        print("✅ Gateway configuration updated")
        print("\n🎉 Gateway is now configured to route to Analytics Agent!")
        print("\n📋 Next steps:")
        print("1. Test the gateway with MCP requests")
        print("2. The UI should now work with the analytics_chat tool")

        return True

    except Exception as e:
        print(f"❌ Error creating gateway target: {str(e)}")
        return False


if __name__ == "__main__":
    success = configure_gateway_target()
    if not success:
        exit(1)
