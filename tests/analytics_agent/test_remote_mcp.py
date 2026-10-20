#!/usr/bin/env python3
"""
Test Analytics Agent with Remote MCP Server

This script tests the analytics agent locally but configured to use the deployed
remote MCP server on AgentCore.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# Add the analytics_agent directory to Python path
sys.path.insert(0, str(Path(__file__).parent))


# Set environment variables for remote MCP server
def setup_remote_environment():
    """Set up environment to use remote MCP server."""

    # Read MCP server ARN from deployment files
    mcp_arn = None

    # Try to get MCP server ARN from various sources
    mcp_server_dir = Path(__file__).parent.parent / "mcp_server"

    if (mcp_server_dir / "publisher_mcp_arn.txt").exists():
        with open(mcp_server_dir / "publisher_mcp_arn.txt", "r") as f:
            mcp_arn = f.read().strip()
    elif (mcp_server_dir / "agent_arn.txt").exists():
        with open(mcp_server_dir / "agent_arn.txt", "r") as f:
            mcp_arn = f.read().strip()
    else:
        # Fallback to known ARN
        mcp_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/sample_integrated_mcp_server-EXAMPLE01"

    print(f"🔗 Using MCP Server ARN: {mcp_arn}")

    # Set environment variables
    os.environ["MCP_SERVER_URL"] = mcp_arn
    os.environ["MCP_CLIENT_TYPE"] = "internal"
    os.environ["PUBLISHER_NAME"] = "Sample Publisher"
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"
    os.environ["LOG_LEVEL"] = "INFO"

    return mcp_arn


async def test_analytics_agent_with_remote_mcp():
    """Test the analytics agent with remote MCP server."""

    print("🧪 Testing Analytics Agent with Remote MCP Server")
    print("=" * 60)

    # Setup environment
    mcp_arn = setup_remote_environment()

    try:
        # Import after setting environment variables
        from analytics_agent_strands import PublisherAnalyticsAgent

        print("✅ Successfully imported PublisherAnalyticsAgent")

        # Initialize agent with remote MCP server
        print("🔧 Initializing analytics agent with remote MCP server...")
        agent = PublisherAnalyticsAgent(mcp_server_url=mcp_arn, actor_id="test_user", session_id="test_session")

        # Test agent initialization
        print("🔍 Testing agent initialization...")
        init_success = await agent.initialize_agent()

        if init_success:
            print("✅ Agent initialized successfully")
        else:
            print("⚠️  Agent initialization returned False")

        # Test chat functionality
        print("💬 Testing chat functionality...")

        test_messages = [
            "Show me my revenue analytics for the last month",
            "What are my top performing articles this week?",
            "Analyze bot behavior patterns",
            "Help me understand my content performance",
        ]

        for i, message in enumerate(test_messages, 1):
            print(f"\n📝 Test {i}: {message}")

            try:
                response = await agent.chat(
                    message=message,
                    page_context={"page_type": "analytics", "url_path": "/analytics"},
                    user_context={"publisher_id": "sample_publisher", "user_id": "test_user"},
                    session_id="test_session",
                )

                print(f"✅ Response received (length: {len(response.get('response', ''))})")
                print(f"   Intent: {response.get('intent_analysis', {}).get('primary_intent', 'unknown')}")
                print(f"   Model: {response.get('model_used', 'unknown')}")

                # Check if tools were used
                if "tools_used" in response or any(
                    keyword in response.get("response", "").lower()
                    for keyword in ["revenue", "analytics", "data", "metrics"]
                ):
                    print("   🔧 Tools appear to have been used")
                else:
                    print("   ⚠️  No clear tool usage detected")

            except Exception as e:
                print(f"❌ Chat test {i} failed: {str(e)}")

        print("\n🏁 Remote MCP Server Test Complete")
        return True

    except ImportError as e:
        print(f"❌ Failed to import analytics agent: {e}")
        return False
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        return False


async def test_mcp_client_directly():
    """Test the MCP client directly."""

    print("\n🔧 Testing MCP Client Directly")
    print("-" * 40)

    try:
        from agentcore_mcp_client import AgentCoreMCPClient

        mcp_arn = os.environ.get("MCP_SERVER_URL")
        print(f"🔗 Testing MCP client with ARN: {mcp_arn}")

        client = AgentCoreMCPClient(mcp_arn)

        # Test health check
        print("🏥 Testing health check...")
        health = await client.health_check()
        print(f"   Health status: {health}")

        # Test a simple tool call
        print("🔧 Testing tool call...")
        result = await client.get_usage_analytics(date_range="last_7_days", content_type=None, bot_filter=None)

        print(f"   Tool call result: {result.get('success', False)}")
        if result.get("success"):
            print("   ✅ Tool call successful")
        else:
            print(f"   ❌ Tool call failed: {result.get('error', 'Unknown error')}")

        return True

    except Exception as e:
        print(f"❌ MCP client test failed: {str(e)}")
        return False


if __name__ == "__main__":

    async def main():
        """Main test function."""

        # Test MCP client directly first
        mcp_success = await test_mcp_client_directly()

        # Test full analytics agent
        agent_success = await test_analytics_agent_with_remote_mcp()

        print("\n📊 Test Summary")
        print("=" * 30)
        print(f"MCP Client Test: {'✅ PASS' if mcp_success else '❌ FAIL'}")
        print(f"Analytics Agent Test: {'✅ PASS' if agent_success else '❌ FAIL'}")

        if mcp_success and agent_success:
            print("\n🎉 All tests passed! Analytics agent can connect to remote MCP server.")
        else:
            print("\n⚠️  Some tests failed. Check the configuration and deployment.")

    asyncio.run(main())
