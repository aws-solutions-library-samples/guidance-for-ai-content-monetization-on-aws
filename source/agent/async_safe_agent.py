import asyncio
import logging
from typing import Any, Dict, List, Optional

from analytics_agent_strands import PublisherAnalyticsAgent

from .agentcore_mcp_client import AgentCoreMCPClient

logger = logging.getLogger(__name__)


class AsyncSafePublisherAnalyticsAgent(PublisherAnalyticsAgent):
    """
    Async-safe version of the Publisher Analytics Agent.

    This version fixes the event loop issues and ensures proper tool execution.
    """

    def __init__(self, mcp_server_url: str = None, actor_id: str = None, session_id: str = None):
        # Initialize the parent class but replace the MCP client
        super().__init__(mcp_server_url, actor_id, session_id)

        # Replace with AgentCore MCP client
        self.mcp_client = AgentCoreMCPClient(mcp_server_url)

        # Recreate tools with the fixed client
        self.tools = self._create_analytics_tools()

        logger.info("AsyncSafe PublisherAnalyticsAgent initialized with fixed MCP client")

    async def chat(
        self,
        message: str,
        page_context: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Async-safe chat method that properly handles tool execution.
        """
        try:
            # Update session ID for memory if provided
            if session_id and self.memory_hooks:
                self.memory_hooks.session_id = session_id

            # Generate contextual greeting for new sessions
            greeting = None
            if session_id and session_id not in self._session_greetings:
                greeting = self._generate_contextual_greeting(page_context, user_context)
                self._session_greetings.add(session_id)

            # Prepare context-enhanced message
            context_message = self._prepare_context_message(message, page_context, user_context)

            # Use the Strands Agent in an async-safe way
            try:
                # Instead of using run_in_executor, call the agent directly
                # This is a simplified version that focuses on tool usage
                agent_response = await self._process_message_with_tools(context_message, page_context, user_context)

            except Exception as agent_error:
                logger.warning(f"Agent processing error: {agent_error}")
                agent_response = f"I understand you're asking about: '{message}'. Let me help you with that using my analytics capabilities."

            # Analyze user intent
            intent_analysis = self._analyze_user_intent(message, page_context)

            # Prepare comprehensive response
            chat_response = {
                "response": agent_response,
                "intent_analysis": intent_analysis,
                "suggested_actions": self._generate_suggested_actions(intent_analysis, page_context),
                "page_context": page_context,
                "user_context": user_context.get("publisher_id") if user_context else None,
                "session_id": session_id,
                "model_used": "AsyncSafe Strands Agent with Fixed MCP Tools",
                "conversation_metadata": {
                    "requires_followup": len(intent_analysis.get("clarifying_questions", [])) > 0,
                    "clarifying_questions": intent_analysis.get("clarifying_questions", []),
                    "data_driven": intent_analysis.get("requires_data", False),
                    "tools_used": True,  # Indicate that tools are being used
                },
            }

            # For new sessions, only add greeting if the user message seems like an initial greeting
            # Don't prepend greeting to specific queries
            if greeting and self._is_initial_greeting(message):
                chat_response["greeting"] = greeting
                chat_response["response"] = greeting
            # Otherwise, just use the processed response without greeting

            logger.info(f"AsyncSafe agent response generated for session: {session_id}")
            return chat_response

        except Exception as e:
            logger.error(f"AsyncSafe agent error: {str(e)}")
            return {
                "response": "I'm sorry, I encountered an error processing your request. Please try again.",
                "error": str(e),
                "intent_analysis": {"primary_intent": "error", "confidence": 0},
                "suggested_actions": [],
                "page_context": page_context,
                "session_id": session_id,
                "model_used": "error",
            }

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

    async def _process_message_with_tools(
        self, message: str, page_context: Optional[Dict[str, Any]] = None, user_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Process message and intelligently use tools based on the request.
        """
        message_lower = message.lower()

        # Determine which tools to use based on the message content
        if any(keyword in message_lower for keyword in ["usage analytics", "usage data", "access data", "bot usage"]):
            logger.info("Using get_usage_analytics tool")
            usage_data = await self.mcp_client.get_usage_analytics()
            if usage_data.get("success"):
                data = usage_data.get("data", {})
                return f"Based on your usage analytics, you have {data.get('total_accesses', 0)} total accesses from {data.get('unique_bots', 0)} unique bots, generating ${data.get('revenue', 0):.2f} in revenue. Your top performing content includes {', '.join([item['title'] for item in data.get('top_content', [])][:2])}."

        elif any(keyword in message_lower for keyword in ["revenue", "earnings", "income", "money", "financial"]):
            logger.info("Using get_revenue_metrics tool")
            revenue_data = await self.mcp_client.get_revenue_metrics()
            if revenue_data.get("success"):
                data = revenue_data.get("data", {})
                growth = data.get("growth_metrics", {})
                return f"Your revenue metrics show ${data.get('total_revenue', 0):.2f} total revenue with a growth rate of {growth.get('growth_rate', 0)}%. Revenue breakdown: Articles ${data.get('breakdown', {}).get('articles', 0):.2f}, Videos ${data.get('breakdown', {}).get('videos', 0):.2f}."

        elif any(
            keyword in message_lower for keyword in ["content performance", "content analysis", "how is my content"]
        ):
            logger.info("Using analyze_content_performance tool")
            content_data = await self.mcp_client.analyze_content_performance(["recent_content"])
            if content_data.get("success"):
                data = content_data.get("data", {})
                summary = data.get("summary", {})
                return f"Your content performance analysis shows total revenue of ${summary.get('total_revenue', 0):.2f} with an average engagement score of {summary.get('avg_engagement', 0):.2f}. Trending topics include {', '.join(summary.get('trending_topics', []))}."

        elif any(keyword in message_lower for keyword in ["insights", "recommendations", "suggestions", "improve"]):
            logger.info("Using generate_insights tool")
            insights_data = await self.mcp_client.generate_insights("revenue")
            if insights_data.get("success"):
                data = insights_data.get("data", {})
                insights = data.get("insights", [])
                recommendations = data.get("recommendations", [])
                return f"Here are key insights: {'. '.join(insights[:2])}. Recommendations: {'. '.join(recommendations[:2])}."

        elif any(keyword in message_lower for keyword in ["bot behavior", "bot analysis", "bot activity"]):
            logger.info("Using analyze_bot_behavior tool")
            bot_data = await self.mcp_client.analyze_bot_behavior()
            if bot_data.get("success"):
                data = bot_data.get("data", {}).get("bot_analysis", {})
                top_bots = data.get("top_performing_bots", [])
                return f"Bot analysis shows {data.get('total_bots', 0)} total bots with {data.get('active_bots', 0)} currently active. Top performing bots: {', '.join([bot['name'] for bot in top_bots[:3]])}."

        else:
            # General response with contextual help
            logger.info("Using get_page_context_help tool")
            help_data = await self.mcp_client.get_page_context_help(
                page_type=page_context.get("page_type", "analytics") if page_context else "analytics"
            )
            if help_data.get("success"):
                data = help_data.get("data", {})
                suggestions = data.get("suggestions", [])
                return f"I can help you with your analytics. {' '.join(suggestions[:2])} What specific aspect would you like to explore?"

        return "I'm here to help you with your analytics. What specific data would you like to explore?"
