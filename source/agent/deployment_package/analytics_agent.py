"""
Publisher Analytics Agent - MCP Client Implementation

This agent acts as an intelligent MCP client that communicates with the integrated
MCP server to provide analytics assistance to publisher business users.
"""

import logging
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mcp_client import MCPClient, create_mcp_tools
from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Model configuration
MODEL_ID = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"

SYSTEM_PROMPT = """You are an intelligent analytics assistant for a publisher's content management system. 
Your role is to help business users understand their content performance, bot usage patterns, and revenue metrics through natural language conversations.

You are context-aware and understand which page the user is currently viewing. You can provide:
- Page-specific help and guidance
- Analytics insights relevant to the current context
- Suggestions for actions users can take on the current page
- Navigation assistance to relevant sections

You have access to comprehensive analytics data through your tools about:
- Content access patterns and popularity
- AI bot usage and behavior
- Revenue and monetization metrics  
- Content performance trends
- User engagement analytics

When you use tools to get data, present the information in a natural, conversational way. Focus on:
- Key insights and trends
- Business implications
- Actionable recommendations
- Clear explanations of what the data means

If you receive mock data (indicated by "mock_data": true), treat it as real data for demonstration purposes and provide meaningful analysis. Always format numbers clearly (e.g., "$2,840.50" for revenue, "15,420" for counts).

When presenting data:
- Use clear, conversational language
- Highlight the most important metrics first
- Explain trends and what they mean for the business
- Provide specific, actionable recommendations
- Format numbers and percentages clearly

Always consider the user's current page context and provide relevant suggestions. Use your tools to get fresh data when users ask specific questions about performance, revenue, or analytics."""


class PublisherAnalyticsAgent:
    """
    Publisher Analytics Agent using MCP Client to communicate with integrated MCP server.

    This agent provides intelligent analytics assistance by leveraging the MCP server's
    analytics tools instead of making direct API calls.
    """

    def __init__(self, mcp_server_url: str = None):
        """
        Initialize the analytics agent with MCP client integration.

        Args:
            mcp_server_url: URL of the MCP server (defaults to localhost:8000 for local testing)
        """
        # Initialize MCP client
        self.mcp_client = MCPClient(mcp_server_url)
        self._session_greetings = set()

        # Create MCP tools for Strands Agent
        mcp_tools = create_mcp_tools(self.mcp_client)

        # Create the Bedrock model
        model = BedrockModel(model_id=MODEL_ID)

        # Create the Strands Agent with MCP tools
        self.agent = Agent(model=model, tools=mcp_tools, system_prompt=SYSTEM_PROMPT)

        logger.info(f"PublisherAnalyticsAgent initialized with MCP client: {self.mcp_client.server_url}")

    async def initialize_agent(self):
        """Initialize and test MCP client connection."""
        try:
            # Test MCP server connection
            health_result = await self.mcp_client.health_check()
            if health_result.get("status") == "healthy":
                logger.info("MCP server connection successful")
                return True
            else:
                logger.error(f"MCP server health check failed: {health_result}")
                return False
        except Exception as e:
            logger.error(f"Failed to initialize MCP client: {str(e)}")
            return False

    def _is_initial_greeting(self, message: str) -> bool:
        """Determine if a message is an initial greeting vs a specific query."""
        message_lower = message.lower().strip()

        # Check if message is a general greeting or very short
        greeting_patterns = [
            "hi",
            "hello",
            "hey",
            "help",
            "start",
            "begin",
            "welcome",
            "what can you do",
            "how can you help",
            "show me",
            "tell me about",
        ]

        # Check if message is very short (likely a greeting)
        if len(message_lower.split()) <= 3:
            return any(pattern in message_lower for pattern in greeting_patterns)

        # Check for specific analytics queries (not greetings)
        specific_query_patterns = [
            "analyze",
            "show me",
            "revenue",
            "bot behavior",
            "content performance",
            "usage analytics",
            "insights",
            "metrics",
            "data",
            "trends",
            "breakdown",
        ]

        # If it contains specific query patterns, it's not a greeting
        if any(pattern in message_lower for pattern in specific_query_patterns):
            return False

        # Default to greeting for ambiguous cases
        return True

    def _generate_contextual_greeting(
        self, page_context: Optional[Dict[str, Any]] = None, user_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate a contextual greeting based on current page and user context."""
        greetings = {
            "analytics": [
                "I can help you analyze your content performance and revenue metrics.",
                "Let's dive into your analytics data and find insights.",
                "I'm here to help you understand your content performance trends.",
            ],
            "content": [
                "I can help you optimize your content library and improve discoverability.",
                "Let's work on making your content more valuable to AI bots.",
                "I'm here to help you manage and optimize your content strategy.",
            ],
            "bot_control": [
                "I can help you understand bot behavior and optimize your partnerships.",
                "Let's analyze your bot access patterns and pricing strategies.",
                "I'm here to help you manage bot relationships and maximize revenue.",
            ],
            "system": [
                "I can help you monitor system performance and configuration.",
                "Let's check your system status and optimize settings.",
                "I'm here to help you manage your platform configuration.",
            ],
            "home": [
                "Welcome! I can help you navigate your publisher platform and analyze performance.",
                "I'm here to help you understand your overall platform performance.",
                "Let's explore your dashboard and identify key insights.",
            ],
        }

        page_type = "home"  # Default
        if page_context:
            page_type = page_context.get("page_type", "home")

        base_greeting = random.choice(greetings.get(page_type, greetings["home"]))
        return base_greeting

    def _prepare_context_message(
        self, message: str, page_context: Optional[Dict[str, Any]] = None, user_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Prepare a context-enhanced message for the Strands Agent."""
        context_parts = [message]

        # Add page context information
        if page_context:
            page_type = page_context.get("page_type", "unknown")
            context_parts.append(f"\nCurrent page context: User is on the {page_type} page.")

            if page_context.get("context_elements"):
                elements = page_context["context_elements"]
                if elements:
                    context_parts.append("Available page data:")
                    for element in elements[:3]:  # Limit to avoid overwhelming the model
                        context_parts.append(
                            f"- {element.get('name', 'unknown')}: {element.get('value', 'N/A')} ({element.get('description', 'No description')})"
                        )

        # Add user context information
        if user_context:
            publisher_id = user_context.get("publisher_id")
            if publisher_id:
                context_parts.append(f"\nUser context: Publisher ID {publisher_id}")

        return "\n".join(context_parts)

    async def chat(
        self,
        message: str,
        page_context: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a chat message using Strands Agent with MCP server tools.

        Args:
            message: User's message
            page_context: Current page context information
            user_context: User context information
            session_id: Session ID for conversation continuity

        Returns:
            Comprehensive chat response with metadata
        """
        try:
            # Generate contextual greeting for new sessions
            greeting = None
            if session_id and session_id not in self._session_greetings:
                greeting = self._generate_contextual_greeting(page_context, user_context)
                self._session_greetings.add(session_id)

            # Prepare context-enhanced message for the Strands Agent
            context_message = self._prepare_context_message(message, page_context, user_context)

            # Use the Strands Agent to generate response (it will use MCP tools as needed)
            try:
                agent_response = self.agent(context_message)

                # Extract the response text from Strands Agent response
                if hasattr(agent_response, "message"):
                    message_obj = agent_response.message
                    if isinstance(message_obj, dict):
                        if "content" in message_obj and isinstance(message_obj["content"], list):
                            response_text = message_obj["content"][0].get("text", str(message_obj))
                        else:
                            response_text = str(message_obj)
                    else:
                        response_text = str(message_obj)
                elif isinstance(agent_response, str):
                    response_text = agent_response
                else:
                    response_text = str(agent_response)

            except Exception as agent_error:
                logger.warning(f"Strands Agent error, using fallback response: {agent_error}")
                response_text = f"I understand you're asking about: '{message}'. Let me help you with that using my analytics capabilities through the MCP server."

            # Prepare comprehensive response
            chat_response = {
                "response": response_text,
                "intent_analysis": {"primary_intent": "general_question", "confidence": 0.8},
                "suggested_actions": [],
                "page_context": page_context,
                "user_context": user_context.get("publisher_id") if user_context else None,
                "session_id": session_id,
                "model_used": "Strands Agent with MCP Client",
            }

            # For new sessions, only add greeting if the user message seems like an initial greeting
            # Don't prepend greeting to specific queries
            if greeting and self._is_initial_greeting(message):
                chat_response["greeting"] = greeting
                chat_response["response"] = greeting
            # Otherwise, just use the processed response without greeting

            logger.info(f"Generated MCP client response for session: {session_id}")
            return chat_response

        except Exception as e:
            logger.error(f"Failed to process chat message with MCP client: {str(e)}")
            return {
                "response": "I'm sorry, I encountered an error processing your request. Please try again.",
                "error": str(e),
                "intent_analysis": {"primary_intent": "error", "confidence": 0},
                "suggested_actions": [],
                "page_context": page_context,
                "session_id": session_id,
                "model_used": "error",
            }

    async def close(self):
        """Close the MCP client connection."""
        await self.mcp_client.close()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


# For local testing
if __name__ == "__main__":
    import asyncio

    async def test_local():
        """Test the analytics agent locally."""
        # Use local MCP server
        agent = PublisherAnalyticsAgent("http://localhost:8000")

        # Initialize and test connection
        if await agent.initialize_agent():
            print("✅ Analytics agent connected to local MCP server")

            # Test chat
            response = await agent.chat(
                "Show me my revenue analytics for the last month",
                page_context={"page_type": "analytics"},
                session_id="test_session",
            )

            print(f"Response: {response['response']}")
        else:
            print("❌ Failed to connect to local MCP server")
            print("Make sure to run: make test-http")

        await agent.close()

    asyncio.run(test_local())
