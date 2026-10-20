#!/usr/bin/env python3
"""
AgentCore Runtime wrapper for Intelligent Analytics Agent
Uses the real Strands Agent with MCP client and AgentCore Memory integration
IMPORTANT: If code updates need to be made it needs to be done in the template file agentcore_wrapper_template.py
IMPORTANT: The agentcore_wrapper.py is dynamically generated in deploy.py during deployment
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp

    logger.info("Successfully imported BedrockAgentCoreApp")
except ImportError as e:
    logger.error(f"Failed to import BedrockAgentCoreApp: {e}")
    raise

# Import the intelligent analytics agent
try:
    from analytics_agent_strands import PublisherAnalyticsAgent

    logger.info("Successfully imported PublisherAnalyticsAgent")
except ImportError as e:
    logger.error(f"Failed to import PublisherAnalyticsAgent: {e}")
    raise

# Get configuration from environment variables (set during deployment)
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL")
MCP_CLIENT_TYPE = os.environ.get("MCP_CLIENT_TYPE", "internal")
PUBLISHER_NAME = os.environ.get("PUBLISHER_NAME", "Publisher")
ANALYTICS_MEMORY_ID = os.environ.get("ANALYTICS_MEMORY_ID")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
AWS_DEFAULT_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")

# Fallback: Try to read configuration from deployment files if env vars are not set
if not MCP_SERVER_URL:
    try:
        import json

        # Try to read from deployment file
        with open("analytics_agent_deployment.json", "r") as f:
            deployment_info = json.load(f)
            MCP_SERVER_URL = deployment_info.get("mcp_server_arn")
            logger.info(f"Loaded MCP server ARN from deployment file: {MCP_SERVER_URL}")
    except Exception as e:
        logger.warning(f"Could not read deployment file: {e}")
        logger.warning(
            "MCP_SERVER_URL is not set and no deployment file was found. "
            "Set the MCP_SERVER_URL (or MCP_SERVER_ARN) environment variable, "
            "or provide analytics_agent_deployment.json."
        )

if not ANALYTICS_MEMORY_ID:
    try:
        # Try to read memory ID from local deployment file
        import os

        deployment_file = "analytics_memory_deployment.json"
        if os.path.exists(deployment_file):
            with open(deployment_file, "r") as f:
                content = f.read().strip()
                if content:
                    memory_deployment = json.loads(content)
                    ANALYTICS_MEMORY_ID = memory_deployment.get("memory_id")
                    if ANALYTICS_MEMORY_ID:
                        logger.info(f"Loaded memory ID from deployment file: {ANALYTICS_MEMORY_ID}")
                    else:
                        logger.warning("Memory ID not found in deployment file")
                else:
                    logger.warning("Deployment file is empty")
        else:
            logger.warning("Deployment file does not exist")
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in deployment file: {e}")
    except Exception as e:
        logger.warning(f"Could not read memory ID from deployment file: {e}")

    # Leave ANALYTICS_MEMORY_ID unset if neither env var nor deployment file
    # provided it; the value is supplied at deploy time.
    if not ANALYTICS_MEMORY_ID:
        logger.warning(
            "ANALYTICS_MEMORY_ID is not set and was not found in a deployment file. "
            "Set the ANALYTICS_MEMORY_ID environment variable or provide "
            "analytics_memory_deployment.json."
        )

logger.info(f"Environment configuration loaded for {PUBLISHER_NAME}")
logger.info(f"MCP Server: {MCP_SERVER_URL}")
logger.info(f"Memory ID: {ANALYTICS_MEMORY_ID}")

# Debug: Log all environment variables to see what's available
logger.info("=== DEBUG: All Environment Variables ===")
for key, value in sorted(os.environ.items()):
    if any(keyword in key.upper() for keyword in ["MCP", "ANALYTICS", "PUBLISHER", "AWS"]):
        logger.info(f"ENV: {key} = {value}")
logger.info("=== END DEBUG ===")

# Try alternative environment variable names
alt_mcp_url = os.environ.get("MCP_SERVER_ARN") or os.environ.get("INTEGRATED_MCP_SERVER_ARN")
if alt_mcp_url and not MCP_SERVER_URL:
    logger.info(f"Found alternative MCP server URL: {alt_mcp_url}")
    MCP_SERVER_URL = alt_mcp_url

# Initialize the AgentCore Runtime App
app = BedrockAgentCoreApp()
logger.info("Intelligent Analytics Agent AgentCore app initialized")

# Global agent instance
analytics_agent = None


async def get_analytics_agent():
    """Get or create the analytics agent instance."""
    global analytics_agent
    if analytics_agent is None:
        try:
            analytics_agent = PublisherAnalyticsAgent(
                mcp_server_url=MCP_SERVER_URL, actor_id="agentcore_user", session_id="agentcore_session"
            )

            # Initialize the agent
            await analytics_agent.initialize_agent()
            logger.info("Intelligent analytics agent initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize analytics agent: {e}")
            raise

    return analytics_agent


@app.entrypoint
def invoke(payload):
    """AgentCore Runtime entrypoint function for intelligent analytics agent"""
    logger.info(f"Intelligent analytics agent received payload: {payload}")

    try:
        # Handle both direct AgentCore calls and Lambda-mediated calls
        if "tool_name" in payload and "parameters" in payload:
            # Lambda-mediated call format
            parameters = payload.get("parameters", {})
            prompt = parameters.get("message", "")
            page_context = parameters.get("page_context", {})
            user_context = parameters.get("user_context", {})
            actor_id = parameters.get("actor_id", "agentcore_user")
            session_id = parameters.get("session_id", "agentcore_session")
            logger.info("Processing Lambda-mediated AgentCore call")
        else:
            # Direct AgentCore call format
            prompt = payload.get("prompt", "")
            page_context = payload.get("page_context", {})
            user_context = payload.get("user_context", {})
            actor_id = payload.get("actor_id", "agentcore_user")
            session_id = payload.get("session_id", "agentcore_session")
            logger.info("Processing direct AgentCore call")

        logger.info(f"Prompt: {prompt[:100]}..., Actor: {actor_id}, Session: {session_id}")

        # Use the intelligent analytics agent
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Get the agent
            agent = loop.run_until_complete(get_analytics_agent())

            # Process the chat message
            response = loop.run_until_complete(
                agent.chat(message=prompt, page_context=page_context, user_context=user_context, session_id=session_id)
            )

            logger.info("Intelligent analytics agent response generated")
            return json.dumps(response)

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error in analytics agent: {str(e)}")

        # Fallback response
        fallback_response = {
            "response": f"I'm sorry, I encountered an error processing your request: {str(e)}. Please try again.",
            "error": str(e),
            "intent_analysis": {"primary_intent": "error", "confidence": 0},
            "suggested_actions": ["Try rephrasing your question", "Check system status"],
            "page_context": payload.get("page_context", {}),
            "actor_id": payload.get("actor_id", "agentcore_user"),
            "session_id": payload.get("session_id", "agentcore_session"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model_used": "error_fallback",
        }

        return json.dumps(fallback_response)


if __name__ == "__main__":
    logger.info("Starting intelligent analytics agent AgentCore app...")
    app.run()
