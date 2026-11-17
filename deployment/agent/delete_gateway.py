#!/usr/bin/env python3
"""
Delete existing AgentCore Gateway so we can recreate it with correct config
"""

try:
    from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

    print("🗑️ Deleting existing gateway...")

    # Initialize the Gateway client
    client = GatewayClient(region_name="us-west-2")

    # Try to delete the gateway
    try:
        result = client.delete_gateway("analytics-agent-gateway")
        print("✅ Gateway deleted successfully")
    except Exception as e:
        print(f"⚠️ Could not delete gateway: {str(e)}")
        print("   This might be normal if the gateway doesn't exist or can't be deleted via API")

except ImportError:
    print("❌ bedrock-agentcore-starter-toolkit not available")
except Exception as e:
    print(f"❌ Error: {str(e)}")
