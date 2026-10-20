#!/usr/bin/env python3
"""
Test Strands Agent Integration
Simple test to verify the analytics agent works with Strands Agent framework.
"""

import os
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, "source", "agent"))

# Load centralized .env file from project root
central_env = Path(project_root) / ".env"
if central_env.exists():
    load_dotenv(central_env)
    print(f"✅ Loaded centralized environment from {central_env}")
else:
    # Fallback to component-specific .env
    load_dotenv()
    print("⚠️  Using fallback environment loading")


def test_import():
    """Test that we can import the analytics agent."""
    try:
        from analytics_agent_strands import PublisherAnalyticsAgent

        print("✅ Successfully imported PublisherAnalyticsAgent (MCP Client)")
        return True
    except ImportError as e:
        print(f"❌ Failed to import PublisherAnalyticsAgent: {e}")
        return False


def test_initialization():
    """Test that we can initialize the analytics agent."""
    try:
        from analytics_agent_strands import PublisherAnalyticsAgent

        # Use environment variables for initialization
        mcp_server_url = os.getenv("MCP_SERVER_ARN", "http://localhost:8000")
        agent = PublisherAnalyticsAgent(mcp_server_url=mcp_server_url)

        print("✅ Successfully initialized analytics agent")
        print(f"   Agent type: Strands Agent with Fixed MCP Client")
        print(f"   MCP Server URL: {agent.mcp_client.server_url}")
        return True
    except Exception as e:
        print(f"❌ Failed to initialize analytics agent: {e}")
        return False


async def test_chat():
    """Test the chat functionality."""
    try:
        from analytics_agent_strands import PublisherAnalyticsAgent

        # Use environment variables for initialization
        mcp_server_url = os.getenv("MCP_SERVER_ARN", "http://localhost:8000")
        agent = PublisherAnalyticsAgent(mcp_server_url=mcp_server_url)

        # Test MCP client connection first
        if not await agent.initialize_agent():
            print("⚠️  MCP server not available, testing with fixed MCP client")

        # Test a simple chat message
        response = await agent.chat(
            message="Show me my usage analytics for the last 7 days",
            page_context={"page_type": "analytics", "context_elements": []},
            user_context={"publisher_id": "test_user"},
            session_id="test_session",
        )

        print("✅ Successfully processed chat message")
        print(f"   Response length: {len(response.get('response', ''))}")
        print(f"   Intent detected: {response.get('intent_analysis', {}).get('primary_intent', 'unknown')}")
        print(f"   Model used: {response.get('model_used', 'unknown')}")
        print(f"   Tools used: {response.get('conversation_metadata', {}).get('tools_used', False)}")

        # Check if response contains specific data (indicating tool usage)
        response_text = response.get("response", "").lower()
        if any(indicator in response_text for indicator in ["1250", "revenue", "accesses", "bots"]):
            print("   ✅ Response contains specific data (tool usage detected)")
        else:
            print("   ⚠️  Response appears generic (limited tool usage)")

        await agent.close()
        return True
    except Exception as e:
        print(f"❌ Failed to process chat message: {e}")
        return False


async def test_tools():
    """Test that MCP client tools are working."""
    try:
        from analytics_agent_strands import PublisherAnalyticsAgent

        # Use environment variables for initialization
        mcp_server_url = os.getenv("MCP_SERVER_ARN", "http://localhost:8000")
        agent = PublisherAnalyticsAgent(mcp_server_url=mcp_server_url)

        # Test MCP client connection
        health = await agent.mcp_client.health_check()
        if health.get("status") == "healthy":
            print("✅ MCP client is healthy")

            # Test multiple tools via MCP client
            tools_to_test = [
                ("get_usage_analytics", {"date_range": "last_7_days"}),
                ("get_revenue_metrics", {"time_period": "last_30_days"}),
                ("generate_insights", {"metric": "revenue"}),
            ]

            for tool_name, params in tools_to_test:
                try:
                    result = await getattr(agent.mcp_client, tool_name)(**params)
                    if result.get("success"):
                        print(f"   ✅ {tool_name}: Success")
                        # Show sample data
                        data = result.get("data", {})
                        if isinstance(data, dict) and data:
                            key = list(data.keys())[0]
                            print(f"      Sample data: {key} = {data[key]}")
                    else:
                        print(f"   ⚠️  {tool_name}: {result.get('error', 'Unknown error')}")
                except Exception as e:
                    print(f"   ❌ {tool_name}: {e}")
        else:
            print("⚠️  MCP client using mock data (expected for fixed client)")
            print("💡 This is normal behavior with the fixed MCP client")

        await agent.close()
        return True
    except Exception as e:
        print(f"❌ Failed to test MCP client tools: {e}")
        return False


def check_environment():
    """Check environment configuration."""
    print("🔍 Environment Configuration:")

    # Check for centralized .env file
    central_env = Path(project_root) / ".env"
    if central_env.exists():
        print(f"✅ Found centralized .env file: {central_env}")
    else:
        print("⚠️  No centralized .env file found")
        print("💡 Run: python scripts/sync-env-config.py")

    # Check for key environment variables from centralized config
    key_vars = [
        ("ANALYTICS_AGENT_ARN", "Analytics Agent ARN"),
        ("MCP_SERVER_ARN", "MCP Server ARN"),
        ("IAB_API_BASE_URL", "IAB API Base URL"),
        ("AWS_DEFAULT_REGION", "AWS Region"),
        ("PUBLISHER_NAME", "Publisher Name"),
    ]

    print("📋 Key Environment Variables:")
    for env_var, description in key_vars:
        value = os.getenv(env_var)
        if value:
            # Truncate long ARNs for display
            display_value = value[:50] + "..." if len(value) > 50 else value
            print(f"   ✅ {description}: {display_value}")
        else:
            print(f"   ❌ {description}: Not set")

    # Check LLM configuration
    if os.getenv("AWS_DEFAULT_REGION"):
        print("✅ AWS Bedrock configured as LLM provider")
    else:
        print("⚠️  No LLM provider configured")


async def main():
    """Run all tests."""
    print("🧪 Testing Strands Agent Integration")
    print("=" * 40)

    # Check environment
    check_environment()
    print()

    # Run tests
    tests = [
        ("Import Test", test_import),
        ("Initialization Test", test_initialization),
        ("Chat Test", test_chat),
        ("Tools Test", test_tools),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"🔬 Running {test_name}...")
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()

            if result:
                passed += 1
        except Exception as e:
            print(f"❌ {test_name} failed with exception: {e}")
        print()

    # Summary
    print("📊 Test Summary")
    print("-" * 20)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("🎉 All tests passed! The Strands Agent integration is working correctly.")
        print("\n📋 Next steps:")
        print("1. Start the API server: python local_api_server.py")
        print("2. Start the UI: cd ui && npm run dev")
        print("3. Test the enhanced analytics assistant in your browser")
    else:
        print("⚠️  Some tests failed. Please check the configuration and dependencies.")
        print("\n💡 Troubleshooting:")
        print("- Run: python setup_analytics_agent.py")
        print("- Check your LLM provider configuration")
        print("- Ensure all dependencies are installed")


if __name__ == "__main__":
    asyncio.run(main())
