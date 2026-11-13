#!/usr/bin/env python3
"""
Test AgentCore Gateway Authentication

This script tests the gateway authentication by making a direct request
to verify the token validation.
"""

import base64
import json
import os

import boto3
import requests


def test_cognito_token():
    """Test getting a Cognito token and calling the gateway."""

    # Cognito configuration (override via env for your deployment)
    user_pool_id = os.environ.get("VITE_USER_POOL_ID", "us-west-2_EXAMPLE01")
    client_id = os.environ.get("VITE_USER_POOL_CLIENT_ID", "EXAMPLECLIENTID0123456789")
    region = os.environ.get("AWS_REGION", "us-west-2")

    # Gateway URL
    gateway_url = "https://analytics-agent-gateway-v2-a29zdszsbw.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"

    print("🔐 Testing Cognito authentication with gateway...")
    print(f"   User Pool ID: {user_pool_id}")
    print(f"   Client ID: {client_id}")
    print(f"   Gateway URL: {gateway_url}")

    # Get a test token using AWS CLI (this simulates what the browser should do)
    try:
        # First, let's check if we can get user pool info
        cognito_client = boto3.client("cognito-idp", region_name=region)

        # Describe the user pool to verify it exists
        user_pool = cognito_client.describe_user_pool(UserPoolId=user_pool_id)
        print(f"✅ User pool found: {user_pool['UserPool']['Name']}")

        # Describe the client
        client_info = cognito_client.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
        print(f"✅ Client found: {client_info['UserPoolClient']['ClientName']}")

        # Check what auth flows are supported
        auth_flows = client_info["UserPoolClient"].get("ExplicitAuthFlows", [])
        print(f"   Supported auth flows: {auth_flows}")

        # For testing, we need to create a test user and get a token
        # This is what the browser would do through Amplify
        print("\n💡 To test authentication:")
        print("1. The browser needs to authenticate through Cognito UI")
        print("2. Get an access token with the correct audience")
        print("3. The token's 'aud' claim must match the client ID")

        # Let's decode a sample token structure to show what we expect
        print("\n📋 Expected JWT token structure:")
        print(f"   aud: {client_id}")
        print(f"   iss: https://cognito-idp.{region}.amazonaws.com/{user_pool_id}")
        print("   token_use: access")

        return True

    except Exception as e:
        print(f"❌ Error testing Cognito: {str(e)}")
        return False


def decode_jwt_token(token):
    """Decode a JWT token to see its contents."""
    try:
        # Split the token
        parts = token.split(".")
        if len(parts) != 3:
            print("❌ Invalid JWT token format")
            return None

        # Decode the payload (second part)
        payload = parts[1]
        # Add padding if needed
        payload += "=" * (4 - len(payload) % 4)

        decoded = base64.b64decode(payload)
        payload_json = json.loads(decoded)

        print("🔍 JWT Token Payload:")
        print(f"   iss: {payload_json.get('iss')}")
        print(f"   aud: {payload_json.get('aud')}")
        print(f"   sub: {payload_json.get('sub')}")
        print(f"   token_use: {payload_json.get('token_use')}")
        print(f"   client_id: {payload_json.get('client_id')}")

        return payload_json

    except Exception as e:
        print(f"❌ Error decoding JWT: {str(e)}")
        return None


def test_gateway_request(token=None):
    """Test making a request to the gateway."""

    gateway_url = "https://analytics-agent-gateway-v2-a29zdszsbw.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"

    headers = {"Content-Type": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    # MCP request payload
    mcp_request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "analytics_chat",
            "arguments": {"message": "test gateway authentication", "actor_id": "test_user"},
        },
    }

    try:
        print("\n🌐 Testing gateway request...")
        print(f"   URL: {gateway_url}")
        print(f"   Has token: {'Yes' if token else 'No'}")

        response = requests.post(gateway_url, headers=headers, json=mcp_request, timeout=30)

        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.text[:200]}...")

        if response.status_code == 401:
            print("❌ Authentication failed - token issue")
        elif response.status_code == 200:
            print("✅ Request successful")
        else:
            print(f"⚠️  Unexpected status: {response.status_code}")

        return response.status_code == 200

    except Exception as e:
        print(f"❌ Error making gateway request: {str(e)}")
        return False


if __name__ == "__main__":
    print("🧪 AgentCore Gateway Authentication Test")
    print("=" * 50)

    # Test Cognito configuration
    test_cognito_token()

    # Test gateway without token
    test_gateway_request()

    print("\n📝 Next steps:")
    print("1. Ensure the browser is getting tokens with the correct 'aud' claim")
    print("2. Verify the gateway is configured with the right client ID")
    print("3. Check that the Cognito user pool client supports the auth flow")
