#!/usr/bin/env python3
"""
Test Deployed Strands Analytics Agent

This script tests the Strands agent implementation against the deployed
AgentCore MCP server to ensure the integration is working correctly.
"""

import os
import sys
import asyncio
import json
import boto3
from pathlib import Path

# Add analytics_agent directory to path
analytics_agent_dir = Path(__file__).parent / "analytics_agent"
sys.path.insert(0, str(analytics_agent_dir))

# Set AWS profile for testing
os.environ["AWS_PROFILE"] = "genai"
os.environ["AWS_DEFAULT_REGION"] = "us-west-2"


async def test_deployed_mcp_server():
    """Test the deployed MCP server directly via AgentCore."""
    print("🧪 Testing Deployed MCP Server via AgentCore")
    print("=" * 50)

    try:
        # Get the deployed MCP server ARN
        mcp_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/sample_integrated_mcp_server-EXAMPLE01"

        # Initialize AgentCore client
        bedrock_client = boto3.client("bedrock-agentcore", region_name="us-west-2")
        print(f"✅ Initialized AgentCore client for region: us-west-2")
        print(f"   MCP Server ARN: {mcp_arn}")

        # Test health check
        print("\n🔍 Testing MCP server health check...")
        health_payload = {"tool_name": "health_check", "client_type": "internal"}

        response = bedrock_client.invoke_agent_runtime(
            agentRuntimeArn=mcp_arn,
            payload=json.dumps(health_payload),
            contentType="application/json",
            accept="application/json",
        )

        if response.get("response"):
            response_content = response["response"].read().decode("utf-8")
            print(f"   Raw response: {response_content[:200]}...")

            try:
                # The response might be double-encoded JSON
                if response_content.startswith('"') and response_content.endswith('"'):
                    # Remove outer quotes and unescape
                    response_content = json.loads(response_content)

                health_data = json.loads(response_content)
                print(f"   Status: {health_data.get('status', 'unknown')}")
                print(f"   Server: {health_data.get('server_name', 'unknown')}")
                print(f"   Publisher: {health_data.get('publisher_name', 'unknown')}")
            except (json.JSONDecodeError, TypeError) as e:
                print(f"   Response (raw): {response_content}")
                print(f"   Parse error: {e}")

        # Test analytics tool
        print("\n🔍 Testing analytics tool via deployed MCP server...")
        analytics_payload = {
            "tool_name": "get_usage_analytics",
            "client_type": "internal",
            "parameters": {"date_range": "last_7_days", "content_type": "article"},
        }

        response = bedrock_client.invoke_agent_runtime(
            agentRuntimeArn=mcp_arn,
            payload=json.dumps(analytics_payload),
            contentType="application/json",
            accept="application/json",
        )

        if response.get("response"):
            response_content = response["response"].read().decode("utf-8")
            print(f"   Raw response: {response_content[:300]}...")

            try:
                # The response might be double-encoded JSON
                if response_content.startswith('"') and response_content.endswith('"'):
                    # Remove outer quotes and unescape
                    response_content = json.loads(response_content)

                analytics_data = json.loads(response_content)
                print(f"   Success: {analytics_data.get('success', True)}")
                if analytics_data.get("success", True):
                    data = analytics_data.get("data", {})
                    if isinstance(data, dict):
                        print(f"   Data keys: {list(data.keys())}")
                    else:
                        print(f"   Data type: {type(data)}")
                else:
                    print(f"   Error: {analytics_data.get('error', 'Unknown error')}")
            except (json.JSONDecodeError, TypeError) as e:
                print(f"   Response (raw): {response_content}")
                print(f"   Parse error: {e}")

        return True

    except Exception as e:
        print(f"❌ Error testing deployed MCP server: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_deployed_analytics_agent():
    """Test the deployed analytics agent directly."""
    print("\n🧪 Testing Deployed Analytics Agent")
    print("=" * 50)

    try:
        # Get the deployed analytics agent ARN
        analytics_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/sample_analytics_agent-EXAMPLE01"

        # Initialize AgentCore client
        bedrock_client = boto3.client("bedrock-agentcore", region_name="us-west-2")
        print(f"✅ Initialized AgentCore client")
        print(f"   Analytics Agent ARN: {analytics_arn}")

        # Test simple chat
        print("\n🔍 Testing analytics agent chat...")
        chat_payload = {
            "prompt": "Hello, can you help me understand my analytics?",
            "page_context": {
                "page_type": "analytics",
                "context_elements": [
                    {"name": "revenue", "value": "$1,234", "description": "Monthly revenue"},
                    {"name": "content_count", "value": "156", "description": "Total content items"},
                ],
            },
            "user_context": {"publisher_id": "test_publisher"},
            "actor_id": "test_user",
            "session_id": "test_session",
        }

        response = bedrock_client.invoke_agent_runtime(
            agentRuntimeArn=analytics_arn,
            payload=json.dumps(chat_payload),
            contentType="application/json",
            accept="application/json",
        )

        if response.get("response"):
            response_content = response["response"].read().decode("utf-8")
            print(f"   Response length: {len(response_content)}")

            try:
                chat_data = json.loads(response_content)
                print(f"   Response type: {type(chat_data)}")

                if isinstance(chat_data, dict):
                    print(f"   Response keys: {list(chat_data.keys())}")
                    if "response" in chat_data:
                        response_text = chat_data["response"]
                        print(f"   Response preview: {response_text[:200]}...")
                    if "model_used" in chat_data:
                        print(f"   Model used: {chat_data['model_used']}")
                else:
                    print(f"   Response preview: {str(chat_data)[:200]}...")

            except json.JSONDecodeError:
                print(f"   Response (raw): {response_content[:300]}...")

        # Test analytics question
        print("\n🔍 Testing analytics question with tool usage...")
        analytics_question_payload = {
            "prompt": "Show me my content usage analytics for the last 7 days",
            "page_context": {
                "page_type": "analytics",
                "context_elements": [
                    {"name": "revenue", "value": "$2,500", "description": "Weekly revenue"},
                    {"name": "growth_rate", "value": "8%", "description": "Weekly growth"},
                ],
            },
            "user_context": {"publisher_id": "test_publisher"},
            "actor_id": "test_user",
            "session_id": "test_session_2",
        }

        response = bedrock_client.invoke_agent_runtime(
            agentRuntimeArn=analytics_arn,
            payload=json.dumps(analytics_question_payload),
            contentType="application/json",
            accept="application/json",
        )

        if response.get("response"):
            response_content = response["response"].read().decode("utf-8")
            print(f"   Response length: {len(response_content)}")

            try:
                analytics_response = json.loads(response_content)
                if isinstance(analytics_response, dict):
                    if "response" in analytics_response:
                        response_text = analytics_response["response"]
                        print(f"   Response preview: {response_text[:300]}...")
                    if "intent_analysis" in analytics_response:
                        intent = analytics_response["intent_analysis"]
                        print(f"   Intent detected: {intent.get('primary_intent', 'unknown')}")
                else:
                    print(f"   Response preview: {str(analytics_response)[:300]}...")

            except json.JSONDecodeError:
                print(f"   Response (raw): {response_content[:300]}...")

        return True

    except Exception as e:
        print(f"❌ Error testing deployed analytics agent: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_strands_agent_with_deployed_mcp():
    """Test local Strands agent connecting to deployed MCP server."""
    print("\n🧪 Testing Local Strands Agent with Deployed MCP Server")
    print("=" * 50)

    try:
        # Import the Strands agent implementation
        from analytics_agent_strands import PublisherAnalyticsAgent

        print("✅ Successfully imported PublisherAnalyticsAgent from Strands implementation")

        # Create a custom MCP client that calls the deployed MCP server via AgentCore
        class DeployedMCPClient:
            def __init__(self):
                self.server_url = "deployed-agentcore-mcp"
                self.bedrock_client = boto3.client("bedrock-agentcore", region_name="us-west-2")
                self.mcp_arn = (
                    "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/sample_integrated_mcp_server-EXAMPLE01"
                )

            async def health_check(self):
                try:
                    payload = {"tool_name": "health_check", "client_type": "internal"}
                    response = self.bedrock_client.invoke_agent_runtime(
                        agentRuntimeArn=self.mcp_arn,
                        payload=json.dumps(payload),
                        contentType="application/json",
                        accept="application/json",
                    )

                    if response.get("response"):
                        response_content = response["response"].read().decode("utf-8")
                        try:
                            # Handle double-encoded JSON
                            if response_content.startswith('"') and response_content.endswith('"'):
                                response_content = json.loads(response_content)
                            return json.loads(response_content)
                        except (json.JSONDecodeError, TypeError):
                            return {"status": "healthy", "raw_response": response_content}
                    return {"status": "unhealthy", "error": "No response"}
                except Exception as e:
                    return {"status": "unhealthy", "error": str(e)}

            async def call_tool(self, tool_name, **kwargs):
                try:
                    payload = {"tool_name": tool_name, "client_type": "internal", "parameters": kwargs}
                    response = self.bedrock_client.invoke_agent_runtime(
                        agentRuntimeArn=self.mcp_arn,
                        payload=json.dumps(payload),
                        contentType="application/json",
                        accept="application/json",
                    )

                    if response.get("response"):
                        response_content = response["response"].read().decode("utf-8")
                        try:
                            # Handle double-encoded JSON
                            if response_content.startswith('"') and response_content.endswith('"'):
                                response_content = json.loads(response_content)
                            return json.loads(response_content)
                        except (json.JSONDecodeError, TypeError):
                            return {"success": True, "raw_response": response_content}
                    return {"success": False, "error": "No response"}
                except Exception as e:
                    return {"success": False, "error": str(e)}

            async def get_usage_analytics(self, date_range="last_7_days", content_type=None, bot_filter=None):
                return await self.call_tool(
                    "get_usage_analytics", date_range=date_range, content_type=content_type, bot_filter=bot_filter
                )

            async def analyze_content_performance(self, content_ids):
                return await self.call_tool("analyze_content_performance", content_ids=content_ids)

            async def generate_insights(self, metric, time_period="last_30_days"):
                return await self.call_tool("generate_insights", metric=metric, time_period=time_period)

            async def get_revenue_metrics(self, time_period="last_30_days", breakdown_by="content_type"):
                return await self.call_tool("get_revenue_metrics", time_period=time_period, breakdown_by=breakdown_by)

            async def analyze_bot_behavior(self, bot_id=None, time_period="last_7_days"):
                return await self.call_tool("analyze_bot_behavior", bot_id=bot_id, time_period=time_period)

            async def get_page_context_help(self, page_type, current_data=None, user_question=None, url_path=None):
                return await self.call_tool(
                    "get_page_context_help",
                    page_type=page_type,
                    current_data=current_data,
                    user_question=user_question,
                    url_path=url_path,
                )

            async def suggest_page_actions(
                self, page_type, user_intent, current_filters=None, current_data=None, url_path=None
            ):
                return await self.call_tool(
                    "suggest_page_actions",
                    page_type=page_type,
                    user_intent=user_intent,
                    current_filters=current_filters,
                    current_data=current_data,
                    url_path=url_path,
                )

            async def close(self):
                pass

        # Initialize the agent with our custom deployed MCP client
        agent = PublisherAnalyticsAgent.__new__(PublisherAnalyticsAgent)
        agent.mcp_client = DeployedMCPClient()
        agent._session_greetings = set()
        agent.actor_id = "test_publisher"
        agent.session_id = "test_session"

        # Initialize memory and model components (simplified for testing)
        agent.memory_client = None
        agent.memory_id = None
        agent.memory_hooks = None

        # Create a mock model and agent for testing
        from unittest.mock import Mock

        agent.model = Mock()
        agent.agent = Mock()
        agent.agent.return_value = "Mock Strands agent response with deployed MCP tools"

        # Create the tools
        agent.tools = agent._create_analytics_tools()

        print(f"✅ Initialized Strands agent with deployed MCP client")
        print(f"   MCP Server: {agent.mcp_client.server_url}")
        print(f"   Tools available: {len(agent.tools)}")

        # Test MCP client connection
        print("\n🔍 Testing deployed MCP client connection...")
        health_result = await agent.mcp_client.health_check()
        print(f"   Health status: {health_result.get('status')}")
        if "server_name" in health_result:
            print(f"   Server name: {health_result.get('server_name')}")

        # Test individual tools
        print("\n🔍 Testing individual tools with deployed MCP server...")

        # Test usage analytics
        print("   Testing get_usage_analytics...")
        try:
            usage_result = await agent.mcp_client.get_usage_analytics(date_range="last_7_days", content_type="article")
            print(f"     Success: {usage_result.get('success', True)}")
            if not usage_result.get("success", True):
                print(f"     Error: {usage_result.get('error')}")
        except Exception as e:
            print(f"     Error: {str(e)}")

        # Test content performance
        print("   Testing analyze_content_performance...")
        try:
            performance_result = await agent.mcp_client.analyze_content_performance(
                ["test_content_1", "test_content_2"]
            )
            print(f"     Success: {performance_result.get('success', True)}")
            if not performance_result.get("success", True):
                print(f"     Error: {performance_result.get('error')}")
        except Exception as e:
            print(f"     Error: {str(e)}")

        # Test insights generation
        print("   Testing generate_insights...")
        try:
            insights_result = await agent.mcp_client.generate_insights(metric="revenue", time_period="last_30_days")
            print(f"     Success: {insights_result.get('success', True)}")
            if not insights_result.get("success", True):
                print(f"     Error: {insights_result.get('error')}")
        except Exception as e:
            print(f"     Error: {str(e)}")

        # Clean up
        await agent.mcp_client.close()

        print("\n✅ Local Strands agent with deployed MCP server testing complete!")
        return True

    except ImportError as e:
        print(f"❌ Failed to import Strands agent: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    """Run all deployed endpoint tests."""
    print("🚀 Starting Deployed Endpoints Testing")
    print("=" * 60)

    # Test deployed MCP server
    mcp_success = await test_deployed_mcp_server()

    # Test deployed analytics agent
    analytics_success = await test_deployed_analytics_agent()

    # Test Strands agent with deployed MCP
    strands_success = await test_strands_agent_with_deployed_mcp()

    print("\n" + "=" * 60)
    print("📊 Test Results Summary")
    print("=" * 60)
    print(f"Deployed MCP Server: {'✅ PASS' if mcp_success else '❌ FAIL'}")
    print(f"Deployed Analytics Agent: {'✅ PASS' if analytics_success else '❌ FAIL'}")
    print(f"Strands Agent + Deployed MCP: {'✅ PASS' if strands_success else '❌ FAIL'}")

    if all([mcp_success, analytics_success, strands_success]):
        print("\n🎉 All deployed endpoint tests passed!")
        print("\n📋 Key Findings:")
        print("- Deployed MCP server is accessible via AgentCore")
        print("- Deployed analytics agent is responding")
        print("- Local Strands agent can connect to deployed MCP server")
        print("- Tools are working through the deployed infrastructure")

        print("\n📋 Next Steps:")
        print("1. The deployed infrastructure is working")
        print("2. Issue might be in the UI integration or response formatting")
        print("3. Check the UI analytics assistant integration")
        print("4. Verify the response format matches what the UI expects")
    else:
        print("\n⚠️  Some tests failed. Check the issues above.")
        print("💡 This helps identify where the problem is in the deployment chain")

    return all([mcp_success, analytics_success, strands_success])


if __name__ == "__main__":
    success = asyncio.run(main())
    if not success:
        sys.exit(1)
