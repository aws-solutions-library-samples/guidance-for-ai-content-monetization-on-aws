"""
Publisher Analytics Agent - MCP Client Wrapper for Backend Integration

This is a lightweight MCP client wrapper that provides a simple interface for
backend Lambda functions to communicate with the deployed AgentCore analytics agent.

The actual intelligent Strands Agent runs on AgentCore (analytics_agent_strands.py).
This wrapper is used by Lambda functions to call the deployed agent.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PublisherAnalyticsAgentClient:
    """
    MCP Client wrapper for backend integration with deployed AgentCore analytics agent.

    This class provides a simple interface for Lambda functions to call the
    intelligent analytics agent deployed on AgentCore.
    """

    def __init__(self, agent_arn: str = None, region: str = "us-west-2"):
        """
        Initialize the analytics agent client.

        Args:
            agent_arn: AgentCore analytics agent ARN (from environment if not provided)
            region: AWS region for AgentCore client
        """
        self.agent_arn = agent_arn or os.environ.get("ANALYTICS_AGENT_ARN")
        self.region = region
        self._session_greetings = set()

        if not self.agent_arn:
            raise ValueError(
                "Analytics agent ARN must be provided via parameter or ANALYTICS_AGENT_ARN environment variable"
            )

        # Initialize AgentCore client
        try:
            self.bedrock_client = boto3.client("bedrock-agentcore", region_name=self.region)
            logger.info(f"Analytics agent client initialized with ARN: {self.agent_arn}")
        except Exception as e:
            logger.error(f"Failed to initialize bedrock-agentcore client: {e}")
            raise

    async def chat(
        self,
        message: str,
        page_context: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a chat message by calling the deployed AgentCore analytics agent.

        Args:
            message: User's message
            page_context: Current page context information
            user_context: User context information (from Cognito)
            session_id: Session ID for conversation continuity

        Returns:
            Comprehensive chat response from the intelligent agent
        """
        try:
            # Prepare the request payload for AgentCore
            agentcore_payload = {
                "prompt": message,
                "page_context": page_context or {},
                "user_context": user_context or {},
                "actor_id": user_context.get("user_id", "backend_user") if user_context else "backend_user",
                "session_id": session_id or f"backend_session_{datetime.now().timestamp()}",
            }

            logger.info(f"Calling AgentCore analytics agent: {self.agent_arn}")
            logger.debug(f"Payload: {json.dumps(agentcore_payload, indent=2)}")

            # Call the deployed AgentCore analytics agent
            response = self.bedrock_client.invoke_agent_runtime(
                agentRuntimeArn=self.agent_arn,
                payload=json.dumps(agentcore_payload),
                contentType="application/json",
                accept="application/json",
            )

            # Parse the AgentCore response
            response_body = response.get("response")
            if response_body:
                # Read the streaming response
                response_content = response_body.read().decode("utf-8")
                logger.debug(f"AgentCore raw response: {response_content[:200]}...")

                try:
                    # Parse the JSON response from the intelligent agent
                    agent_response = json.loads(response_content)

                    # Ensure the response has the expected structure
                    if not isinstance(agent_response, dict):
                        agent_response = {
                            "response": str(agent_response),
                            "intent_analysis": {"primary_intent": "general_question", "confidence": 0.8},
                            "suggested_actions": [],
                            "page_context": page_context,
                            "user_context": user_context.get("publisher_id") if user_context else None,
                            "session_id": session_id,
                            "model_used": "AgentCore Analytics Agent",
                        }

                    # Add missing fields if needed
                    if "response" not in agent_response:
                        agent_response["response"] = "I'm here to help you with your analytics needs."
                    if "page_context" not in agent_response:
                        agent_response["page_context"] = page_context
                    if "user_context" not in agent_response:
                        agent_response["user_context"] = user_context.get("publisher_id") if user_context else None
                    if "session_id" not in agent_response:
                        agent_response["session_id"] = session_id

                    logger.info("Successfully processed chat request via AgentCore")
                    return agent_response

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse AgentCore response as JSON: {e}")
                    # Return a structured response with the raw content
                    return {
                        "response": response_content,
                        "intent_analysis": {"primary_intent": "general_question", "confidence": 0.7},
                        "suggested_actions": [],
                        "page_context": page_context,
                        "user_context": user_context.get("publisher_id") if user_context else None,
                        "session_id": session_id,
                        "model_used": "AgentCore Analytics Agent (raw)",
                    }
            else:
                logger.error("No response body from AgentCore")
                raise Exception("No response received from AgentCore")

        except Exception as e:
            logger.error(f"Failed to call AgentCore analytics agent: {str(e)}")
            return {
                "response": f"I'm sorry, I encountered an error processing your request: {str(e)}. Please try again.",
                "error": str(e),
                "intent_analysis": {"primary_intent": "error", "confidence": 0},
                "suggested_actions": ["Try rephrasing your question", "Check system status"],
                "page_context": page_context,
                "user_context": user_context.get("publisher_id") if user_context else None,
                "session_id": session_id,
                "model_used": "error_fallback",
            }

    def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the analytics agent client.

        Returns:
            Health status information
        """
        try:
            # Basic connectivity check
            if self.bedrock_client and self.agent_arn:
                return {
                    "status": "healthy",
                    "agent_arn": self.agent_arn,
                    "region": self.region,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            else:
                return {
                    "status": "unhealthy",
                    "error": "Missing bedrock client or agent ARN",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e), "timestamp": datetime.now(timezone.utc).isoformat()}


# Backward compatibility alias
PublisherAnalyticsAgent = PublisherAnalyticsAgentClient


# For local testing and backend integration
if __name__ == "__main__":
    import asyncio

    async def test_client():
        """Test the analytics agent client.

        Requires the ANALYTICS_AGENT_ARN environment variable to point at a
        deployed analytics agent runtime, e.g.:
            export ANALYTICS_AGENT_ARN=arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<name>
        """
        if not os.environ.get("ANALYTICS_AGENT_ARN"):
            print(
                "ANALYTICS_AGENT_ARN is not set. Export it to point at your "
                "deployed analytics agent runtime before running this test."
            )
            return

        try:
            # Initialize client
            client = PublisherAnalyticsAgentClient()

            # Test health check
            health = client.health_check()
            print(f"Health check: {health}")

            # Test chat
            response = await client.chat(
                "Show me my revenue analytics for the last month",
                page_context={"page_type": "analytics"},
                user_context={"publisher_id": "test_publisher", "user_id": "test_user"},
                session_id="test_session",
            )

            print(f"Chat response: {json.dumps(response, indent=2)}")

        except Exception as e:
            print(f"Test failed: {e}")

    asyncio.run(test_client())
