#!/usr/bin/env python3
"""
Local test script for analytics Lambda function

This script tests the analytics Lambda handler locally to debug AgentCore communication.
"""

import json
import os
import sys
import logging
from datetime import datetime, timezone

# Set up logging to see all messages
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Add the ui_lambda directory to the path
sys.path.insert(0, "ui_lambda")

# Import the analytics handler
from analytics_handler import lambda_handler


def test_analytics_lambda():
    """Test the analytics Lambda function locally"""

    # Set environment variables
    os.environ["LOG_LEVEL"] = "INFO"
    os.environ["ANALYTICS_AGENT_ARN"] = (
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/sample_analytics_agent-EXAMPLE01"
    )
    os.environ["AWS_DEFAULT_REGION"] = "us-west-2"

    # Create a test event that mimics API Gateway
    test_event = {
        "httpMethod": "POST",
        "path": "/analytics-agent/chat",
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer test-token"},
        "body": json.dumps(
            {
                "message": "What are my top performing articles this week?",
                "page_context": {
                    "page_type": "analytics",
                    "url_path": "/analytics",
                    "current_data": {
                        "page_name": "Analytics Dashboard",
                        "available_actions": ["view_reports", "export_data"],
                    },
                },
                "session_id": "test-session-123",
            }
        ),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "cognito:username": "test_user",
                    "email": "test@example.com",
                    "custom:publisher_id": "test_publisher",
                    "custom:publisher_name": "Test Publisher",
                    "sub": "test-user-id-123",
                    "token_use": "id",
                    "auth_time": str(int(datetime.now().timestamp())),
                    "iss": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_EXAMPLE02",
                    "exp": str(int(datetime.now().timestamp()) + 3600),
                }
            }
        },
    }

    # Mock context object
    class MockContext:
        def __init__(self):
            self.function_name = "test-analytics-lambda"
            self.function_version = "$LATEST"
            self.invoked_function_arn = "arn:aws:lambda:us-west-2:123456789012:function:test-analytics-lambda"
            self.memory_limit_in_mb = 256
            self.remaining_time_in_millis = lambda: 30000

    context = MockContext()

    print("🧪 Testing Analytics Lambda locally...")
    print(f"📋 Analytics Agent ARN: {os.environ['ANALYTICS_AGENT_ARN']}")
    print(f"🌍 AWS Region: {os.environ['AWS_DEFAULT_REGION']}")
    print(f"📝 Log Level: {os.environ['LOG_LEVEL']}")
    print()

    try:
        # Call the Lambda handler
        response = lambda_handler(test_event, context)

        print("✅ Lambda execution completed!")
        print(f"📊 Status Code: {response.get('statusCode')}")
        print(f"📋 Headers: {json.dumps(response.get('headers', {}), indent=2)}")

        # Parse and display the response body
        body = response.get("body", "{}")
        try:
            parsed_body = json.loads(body)
            print(f"📄 Response Body:")
            print(json.dumps(parsed_body, indent=2))
        except json.JSONDecodeError:
            print(f"📄 Response Body (raw): {body}")

    except Exception as e:
        print(f"❌ Lambda execution failed: {str(e)}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_analytics_lambda()
