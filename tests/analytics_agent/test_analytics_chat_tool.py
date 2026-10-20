#!/usr/bin/env python3
"""
Test the analytics_chat tool directly to verify it works
"""

import sys
import os
import json

# Add project root to Python path
project_root = os.path.dirname(__file__)
sys.path.insert(0, project_root)


def test_analytics_chat_tool():
    """Test the analytics_chat tool directly."""
    print("🧪 Testing analytics_chat tool")
    print("=" * 40)

    try:
        from mcp_server.agentcore_wrapper import invoke

        print("✅ Successfully imported AgentCore wrapper")
    except ImportError as e:
        print(f"❌ Failed to import AgentCore wrapper: {e}")
        return False

    # Test analytics_chat tool with internal client type
    test_payload = {
        "tool_name": "analytics_chat",
        "client_type": "internal",
        "parameters": {
            "message": "Show me my analytics dashboard overview",
            "page_context": {"page_type": "analytics"},
            "user_context": {"publisher_id": "test_publisher"},
            "session_id": "test_session",
        },
    }

    try:
        print(f"📤 Sending request: {test_payload['tool_name']}")
        result = invoke(test_payload)
        print("✅ Tool invocation successful")

        # Parse and display result
        if isinstance(result, str):
            try:
                parsed_result = json.loads(result)
                print(f"📋 Result type: {type(parsed_result)}")
                print(f"📋 Tool name: {parsed_result.get('tool_name')}")
                print(f"📋 Client type: {parsed_result.get('client_type')}")
                print(f"📋 Timestamp: {parsed_result.get('timestamp')}")

                if "error" in parsed_result:
                    print(f"❌ Error: {parsed_result['error']}")
                    return False
                else:
                    print(f"✅ Success: {parsed_result.get('result', 'No result field')}")
                    return True

            except json.JSONDecodeError:
                print(f"📋 Raw result: {result}")
                return True
        else:
            print(f"📋 Result: {result}")
            return True

    except Exception as e:
        print(f"❌ Tool invocation failed: {e}")
        return False


if __name__ == "__main__":
    success = test_analytics_chat_tool()
    if success:
        print("\n🎉 analytics_chat tool is working!")
        print("💡 The MCP server needs to be redeployed for the gateway to pick up this change.")
    else:
        print("\n❌ analytics_chat tool test failed!")

    sys.exit(0 if success else 1)
