#!/usr/bin/env python3
"""
Test the Docker container locally to simulate AgentCore behavior
"""

import json
import subprocess
import time


def test_docker_container():
    """Test the analytics agent Docker container with different queries"""

    test_queries = [
        {
            "name": "Revenue Query",
            "payload": {
                "prompt": "Help me view revenue metrics",
                "page_context": {"page_type": "analytics"},
                "user_context": {"publisher_id": "test_user"},
                "actor_id": "test_actor",
                "session_id": "test_session_1",
            },
        },
        {
            "name": "Bot Behavior Query",
            "payload": {
                "prompt": "Analyze bot behavior",
                "page_context": {"page_type": "bot_control"},
                "user_context": {"publisher_id": "test_user"},
                "actor_id": "test_actor",
                "session_id": "test_session_2",
            },
        },
        {
            "name": "Content Performance Query",
            "payload": {
                "prompt": "Show me content performance",
                "page_context": {"page_type": "content"},
                "user_context": {"publisher_id": "test_user"},
                "actor_id": "test_actor",
                "session_id": "test_session_3",
            },
        },
    ]

    print("Testing Analytics Agent Docker Container")
    print("=" * 50)

    for test in test_queries:
        print(f"\n🧪 Testing: {test['name']}")
        print(f"Query: {test['payload']['prompt']}")
        print("-" * 30)

        try:
            # Execute docker exec to call the agent directly
            payload_json = json.dumps(test["payload"])

            # Use docker exec to run a Python command inside the container
            cmd = [
                "docker",
                "exec",
                "a923a000d932",
                "python",
                "-c",
                f"""
import json
import asyncio
from analytics_agent_strands import PublisherAnalyticsAgent

async def test():
    agent = PublisherAnalyticsAgent(
        mcp_server_url="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/sample_integrated_mcp_server-EXAMPLE01",
        actor_id="{test["payload"]["actor_id"]}",
        session_id="{test["payload"]["session_id"]}"
    )
    
    response = await agent.chat(
        message="{test["payload"]["prompt"]}",
        page_context={json.dumps(test["payload"]["page_context"])},
        user_context={json.dumps(test["payload"]["user_context"])},
        session_id="{test["payload"]["session_id"]}"
    )
    
    print("RESPONSE:", json.dumps(response, indent=2))

asyncio.run(test())
""",
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                # Extract the response from the output
                output_lines = result.stdout.strip().split("\n")
                response_line = None
                for line in output_lines:
                    if line.startswith("RESPONSE:"):
                        response_line = line[9:].strip()  # Remove 'RESPONSE: '
                        break

                if response_line:
                    try:
                        response_data = json.loads(response_line)
                        print(f"✅ Response: {response_data['response'][:100]}...")
                        print(f"✅ Intent: {response_data['intent_analysis']['primary_intent']}")
                        print(f"✅ Tool: {response_data['intent_analysis'].get('suggested_tool', 'None')}")
                    except json.JSONDecodeError:
                        print(f"✅ Raw Response: {response_line[:100]}...")
                else:
                    print(f"✅ Output: {result.stdout[:200]}...")
            else:
                print(f"❌ Error: {result.stderr}")

        except subprocess.TimeoutExpired:
            print("❌ Test timed out")
        except Exception as e:
            print(f"❌ Test failed: {str(e)}")

    print("\n" + "=" * 50)
    print("Docker container test completed!")


if __name__ == "__main__":
    test_docker_container()
