#!/usr/bin/env python3
"""
Publisher Analytics Agent - Clean Strands Agent Implementation

This agent is a pure Strands agent that provides intelligent analytics assistance
without any external MCP client dependencies. It uses Claude 3.7 Sonnet's reasoning
capabilities and provides mock analytics data for demonstration purposes.
"""

import asyncio
import json
import logging
import os
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3
from boto3.session import Session
from botocore.exceptions import ClientError
from strands import Agent
from strands.models import BedrockModel
from strands.tools import tool

# Import the existing AgentCoreMCPClient
try:
    from .agentcore_mcp_client import AgentCoreMCPClient

    AGENTCORE_MCP_AVAILABLE = True
except ImportError:
    try:
        from agentcore_mcp_client import AgentCoreMCPClient

        AGENTCORE_MCP_AVAILABLE = True
    except ImportError:
        logging.warning("AgentCoreMCPClient not available. Using mock data only.")
        AGENTCORE_MCP_AVAILABLE = False

try:
    from bedrock_agentcore.memory import MemoryClient
    from bedrock_agentcore.memory.constants import StrategyType

    AGENTCORE_MEMORY_AVAILABLE = True
except ImportError:
    logging.warning("AgentCore Memory not available. Install bedrock-agentcore for memory functionality.")
    AGENTCORE_MEMORY_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# AWS session for memory
boto_session = Session()
REGION = boto_session.region_name or "us-west-2"

# Model configuration
MODEL_ID = "us.anthropic.claude-3-7-sonnet-20250219-v1:0"

# System prompt for intelligent analytics
SYSTEM_PROMPT = """You are an intelligent analytics assistant for a publisher's content management system. 
Your role is to help business users understand their content performance, bot usage patterns, and revenue metrics through natural, conversational interactions.

## Critical: Query Analysis and Tool Selection

ALWAYS analyze the user's query to determine what specific information they need:

**Revenue Questions** ("revenue", "money", "earnings", "monetization") → Use get_revenue_metrics
**Bot Questions** ("bot", "AI", "partnership", "access", "behavior") → Use analyze_bot_behavior  
**Content Questions** ("content", "performance", "popular", "trending") → Use analyze_content_performance
**Usage Questions** ("usage", "analytics", "metrics", "data", "trends") → Use get_usage_analytics

## Tool Usage Rules

1. **ALWAYS call appropriate tools** for data-driven questions - never provide generic responses
2. **Analyze the tool results** and provide insights, not just raw data
3. **Be specific** - if user asks about "bot behavior", call analyze_bot_behavior, don't give generic advice
4. **Use real data** - your tools provide analytics data, use them to provide accurate information
5. **Combine tools** when needed - revenue + content performance for comprehensive analysis

Remember: You have access to analytics data through your tools. Use them to provide accurate, data-driven insights instead of generic responses."""


class PublisherAnalyticsAgent:
    """
    Intelligent Publisher Analytics Agent - Pure Strands Agent Implementation.

    This agent provides intelligent analytics assistance using Claude 3.7 Sonnet
    and built-in analytics tools (no external MCP client dependencies).
    """

    def __init__(self, mcp_server_url: str = None, actor_id: str = None, session_id: str = None):
        """
        Initialize the intelligent analytics agent.

        Args:
            mcp_server_url: MCP server ARN for AgentCore integration
            actor_id: Actor ID for memory persistence
            session_id: Session ID for conversation continuity
        """
        # Set up context
        self.actor_id = actor_id or f"publisher_{uuid.uuid4().hex[:8]}"
        self.session_id = session_id or str(uuid.uuid4())

        # Initialize MCP client if available and not in mock mode
        self.mcp_client = None
        use_mock_analytics = os.getenv("USE_MOCK_ANALYTICS", "false").lower() == "true"
        demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"

        if not (use_mock_analytics or demo_mode) and AGENTCORE_MCP_AVAILABLE and mcp_server_url:
            try:
                self.mcp_client = AgentCoreMCPClient(server_arn=mcp_server_url)
                logger.info("Analytics agent initialized with AgentCore MCP client")
            except Exception as e:
                logger.warning(f"Failed to initialize MCP client: {e}. Using mock data.")
                self.mcp_client = None
        else:
            logger.info(
                f"Analytics agent initialized in mock mode (USE_MOCK_ANALYTICS={use_mock_analytics}, DEMO_MODE={demo_mode})"
            )

        # Create the Bedrock model
        self.model = BedrockModel(model_id=MODEL_ID)

        # Create analytics tools for the agent (mock or real based on configuration)
        self.tools = self._create_analytics_tools()

        # Create the Strands Agent
        self.agent = Agent(model=self.model, tools=self.tools, system_prompt=SYSTEM_PROMPT)

        logger.info(
            f"Intelligent PublisherAnalyticsAgent initialized - Actor: {self.actor_id}, MCP: {'enabled' if self.mcp_client else 'mock'}"
        )

    def _create_analytics_tools(self) -> List:
        """Create analytics tools that provide intelligent analysis (mock or real based on configuration)."""

        # Check environment variables for mock mode
        use_mock_analytics = os.getenv("USE_MOCK_ANALYTICS", "false").lower() == "true"
        demo_mode = os.getenv("DEMO_MODE", "false").lower() == "true"

        if (use_mock_analytics or demo_mode) or not self.mcp_client:
            logger.info("Creating mock analytics tools")
            return self._create_mock_analytics_tools()
        else:
            logger.info("Creating real MCP analytics tools")
            return self._create_mcp_analytics_tools()

    def _create_mock_analytics_tools(self) -> List:
        """Create mock analytics tools for demo purposes."""

        @tool
        async def get_usage_analytics_mock(
            date_range: str = "last_7_days", content_type: Optional[str] = None, bot_filter: Optional[str] = None
        ) -> str:
            """
            Get content usage analytics data (MOCK VERSION for demo purposes).

            Args:
                date_range: Time period for analysis (last_7_days, last_30_days, last_90_days)
                content_type: Filter by content type (article, video, image, audio)
                bot_filter: Filter by specific bot or partner name

            Returns:
                Mock usage analytics including access counts, popular content, and trends
            """
            try:
                # Generate intelligent mock analytics data for demonstration
                param_hash = (
                    hash(f"{date_range}_{content_type}_{bot_filter}")
                    if any([date_range, content_type, bot_filter])
                    else 0
                )

                analytics_data = {
                    "total_accesses": 1250 + (abs(param_hash) % 500),
                    "unique_bots": 15 + (abs(param_hash) % 10),
                    "revenue": 2100.50 + (abs(param_hash) % 1000),
                    "period": date_range,
                    "content_type_filter": content_type or "all",
                    "bot_filter": bot_filter or "all",
                    "top_content": [
                        {"title": "Healthcare AI Trends", "accesses": 450 + (abs(param_hash) % 200), "revenue": 680.25},
                        {
                            "title": "Tech Industry Analysis",
                            "accesses": 380 + (abs(param_hash) % 150),
                            "revenue": 520.75,
                        },
                    ],
                    "insights": [
                        f"Usage analytics for {date_range}",
                        f"Content type: {content_type or 'all types'}",
                        f"Bot filter: {bot_filter or 'all bots'}",
                        "Healthcare content shows strong engagement",
                        "Peak usage occurs during business hours",
                    ],
                }

                return json.dumps(analytics_data, indent=2)

            except Exception as e:
                logger.error(f"Error getting usage analytics: {str(e)}")
                return f"Error retrieving usage analytics: {str(e)}"

        @tool
        async def get_revenue_metrics_mock(
            time_period: str = "last_30_days", breakdown_by: str = "content_type"
        ) -> str:
            """
            Get detailed revenue and monetization metrics (MOCK VERSION for demo purposes).

            Args:
                time_period: Analysis time period
                breakdown_by: How to break down revenue (content_type, bot_partner, time_period)

            Returns:
                Mock revenue metrics with breakdowns and projections
            """
            try:
                # Generate intelligent mock revenue data for demonstration
                param_hash = hash(f"{time_period}_{breakdown_by}")

                revenue_data = {
                    "total_revenue": 4250.75 + (abs(param_hash) % 2000),
                    "period": time_period,
                    "breakdown_by": breakdown_by,
                    "breakdown": {
                        "articles": 2550.25 + (abs(param_hash) % 500),
                        "videos": 1200.50 + (abs(param_hash) % 300),
                        "reports": 500.00 + (abs(param_hash) % 200),
                    },
                    "growth_metrics": {
                        "growth_rate": 15.5 + (abs(param_hash) % 20) - 10,
                        "trend": "positive" if (abs(param_hash) % 2) else "stable",
                    },
                    "insights": [
                        f"Revenue analysis for {time_period}",
                        f"Breakdown by {breakdown_by}",
                        "Healthcare content is your top revenue driver",
                        "Video content shows strong monetization potential",
                    ],
                }

                return json.dumps(revenue_data, indent=2)

            except Exception as e:
                logger.error(f"Error getting revenue metrics: {str(e)}")
                return f"Error retrieving revenue metrics: {str(e)}"

        @tool
        async def analyze_bot_behavior_mock(bot_id: Optional[str] = None, time_period: str = "last_7_days") -> str:
            """
            Analyze AI bot usage patterns and behavior (MOCK VERSION for demo purposes).

            Args:
                bot_id: Specific bot to analyze (optional, analyzes all if not provided)
                time_period: Analysis time period

            Returns:
                Mock bot behavior analysis with usage patterns and partnership insights
            """
            try:
                # Generate intelligent mock bot behavior data for demonstration
                param_hash = hash(f"{bot_id}_{time_period}")

                bot_data = {
                    "bot_analysis": {
                        "total_bots": 18 + (abs(param_hash) % 10),
                        "active_bots": 15 + (abs(param_hash) % 8),
                        "analyzed_bot": bot_id or "all_bots",
                        "time_period": time_period,
                        "top_performing_bots": [
                            {"name": "ChatGPT", "requests": 520 + (abs(param_hash) % 200), "revenue": 890.25},
                            {"name": "Claude", "requests": 380 + (abs(param_hash) % 150), "revenue": 650.50},
                            {"name": "Gemini", "requests": 350 + (abs(param_hash) % 100), "revenue": 559.75},
                        ],
                        "usage_patterns": {
                            "peak_hours": ["9-11 AM", "2-4 PM"],
                            "preferred_content": ["articles", "summaries"],
                            "analysis_period": time_period,
                        },
                        "partnership_insights": [
                            f"Bot behavior analysis for {time_period}",
                            f"Specific bot: {bot_id or 'all bots'}",
                            "ChatGPT generates highest revenue per request",
                            "Claude shows most consistent usage patterns",
                            "New bot partnerships are growing 20% monthly",
                        ],
                    }
                }

                return json.dumps(bot_data, indent=2)

            except Exception as e:
                logger.error(f"Error analyzing bot behavior: {str(e)}")
                return f"Error analyzing bot behavior: {str(e)}"

        @tool
        async def analyze_content_performance_mock(content_ids: List[str]) -> str:
            """
            Analyze detailed performance metrics for specific content items (MOCK VERSION for demo purposes).

            Args:
                content_ids: List of content identifiers to analyze

            Returns:
                Mock content performance analysis with engagement metrics and recommendations
            """
            try:
                # Generate intelligent mock content performance data
                param_hash = hash(str(content_ids))

                performance_data = {
                    "content_analysis": [
                        {
                            "content_id": content_id,
                            "title": f"Sample Content {i + 1}",
                            "total_views": 1250 + (abs(param_hash) % 500) + i * 100,
                            "revenue": 183.50 + (abs(param_hash) % 100) + i * 50,
                            "engagement_score": 0.87 - (i * 0.1),
                        }
                        for i, content_id in enumerate(content_ids[:5])  # Limit to 5 items
                    ],
                    "summary": {
                        "total_revenue": sum(
                            183.50 + (abs(param_hash) % 100) + i * 50 for i in range(min(5, len(content_ids)))
                        ),
                        "avg_engagement": 0.82,
                        "trending_topics": ["AI", "Healthcare", "Technology"],
                    },
                    "insights": [
                        f"Analyzed {min(5, len(content_ids))} content items",
                        "Healthcare content shows highest engagement",
                        "Video content outperforms articles by 25%",
                    ],
                }

                return json.dumps(performance_data, indent=2)

            except Exception as e:
                logger.error(f"Error analyzing content performance: {str(e)}")
                return f"Error analyzing content performance: {str(e)}"

        return [
            get_usage_analytics_mock,
            get_revenue_metrics_mock,
            analyze_bot_behavior_mock,
            analyze_content_performance_mock,
        ]

    def _create_mcp_analytics_tools(self) -> List:
        """Create real MCP analytics tools that call the deployed MCP server."""

        @tool
        async def get_usage_analytics(
            date_range: str = "last_7_days", content_type: Optional[str] = None, bot_filter: Optional[str] = None
        ) -> str:
            """
            Get content usage analytics data via MCP server.

            Args:
                date_range: Time period for analysis (last_7_days, last_30_days, last_90_days)
                content_type: Filter by content type (article, video, image, audio)
                bot_filter: Filter by specific bot or partner name

            Returns:
                Real usage analytics from deployed MCP server
            """
            try:
                if not self.mcp_client:
                    return "Error: MCP client not available. Please check configuration."

                result = await self.mcp_client.get_usage_analytics(
                    date_range=date_range, content_type=content_type, bot_filter=bot_filter
                )

                if result.get("success"):
                    return json.dumps(result.get("data", {}), indent=2)
                else:
                    return f"Error retrieving usage analytics: {result.get('error', 'Unknown error')}"

            except Exception as e:
                logger.error(f"Error getting usage analytics via MCP: {str(e)}")
                return f"Error retrieving usage analytics: {str(e)}"

        @tool
        async def get_revenue_metrics(time_period: str = "last_30_days", breakdown_by: str = "content_type") -> str:
            """
            Get detailed revenue and monetization metrics via MCP server.

            Args:
                time_period: Analysis time period
                breakdown_by: How to break down revenue (content_type, bot_partner, time_period)

            Returns:
                Real revenue metrics from deployed MCP server
            """
            try:
                if not self.mcp_client:
                    return "Error: MCP client not available. Please check configuration."

                result = await self.mcp_client.get_revenue_metrics(time_period=time_period, breakdown_by=breakdown_by)

                if result.get("success"):
                    return json.dumps(result.get("data", {}), indent=2)
                else:
                    return f"Error retrieving revenue metrics: {result.get('error', 'Unknown error')}"

            except Exception as e:
                logger.error(f"Error getting revenue metrics via MCP: {str(e)}")
                return f"Error retrieving revenue metrics: {str(e)}"

        @tool
        async def analyze_bot_behavior(bot_id: Optional[str] = None, time_period: str = "last_7_days") -> str:
            """
            Analyze AI bot usage patterns and behavior via MCP server.

            Args:
                bot_id: Specific bot to analyze (optional, analyzes all if not provided)
                time_period: Analysis time period

            Returns:
                Real bot behavior analysis from deployed MCP server
            """
            try:
                if not self.mcp_client:
                    return "Error: MCP client not available. Please check configuration."

                result = await self.mcp_client.analyze_bot_behavior(bot_id=bot_id, time_period=time_period)

                if result.get("success"):
                    return json.dumps(result.get("data", {}), indent=2)
                else:
                    return f"Error analyzing bot behavior: {result.get('error', 'Unknown error')}"

            except Exception as e:
                logger.error(f"Error analyzing bot behavior via MCP: {str(e)}")
                return f"Error analyzing bot behavior: {str(e)}"

        @tool
        async def analyze_content_performance(content_ids: List[str]) -> str:
            """
            Analyze detailed performance metrics for specific content items via MCP server.

            Args:
                content_ids: List of content identifiers to analyze

            Returns:
                Real content performance analysis from deployed MCP server
            """
            try:
                if not self.mcp_client:
                    return "Error: MCP client not available. Please check configuration."

                result = await self.mcp_client.analyze_content_performance(content_ids)

                if result.get("success"):
                    return json.dumps(result.get("data", {}), indent=2)
                else:
                    return f"Error analyzing content performance: {result.get('error', 'Unknown error')}"

            except Exception as e:
                logger.error(f"Error analyzing content performance via MCP: {str(e)}")
                return f"Error analyzing content performance: {str(e)}"

        return [get_usage_analytics, get_revenue_metrics, analyze_bot_behavior, analyze_content_performance]

    async def initialize_agent(self):
        """Initialize the analytics agent."""
        try:
            # Pure Strands agent - no external connections needed
            logger.info("Analytics agent initialized successfully as pure Strands agent")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize analytics agent: {str(e)}")
            return False

    async def chat(
        self,
        message: str,
        page_context: Optional[Dict[str, Any]] = None,
        user_context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process a chat message using the intelligent Strands agent.

        Args:
            message: User's message
            page_context: Current page context
            user_context: User context information
            session_id: Session ID for conversation continuity

        Returns:
            Comprehensive chat response
        """
        try:
            # Use the Strands agent to process the message
            response = self.agent(message)

            # Extract the response text
            response_text = response.message["content"][0]["text"]

            # Analyze intent (simple keyword-based for now)
            intent = self._analyze_intent(message)

            # Check if tools were used (if response contains structured data)
            tools_used = any(
                keyword in response_text.lower() for keyword in ["revenue", "analytics", "data", "metrics", "total_"]
            )

            return {
                "response": response_text,
                "intent_analysis": {"primary_intent": intent, "confidence": 0.8},
                "suggested_actions": [],
                "page_context": page_context or {},
                "user_context": user_context.get("publisher_id") if user_context else None,
                "session_id": session_id or self.session_id,
                "model_used": "Claude 3.7 Sonnet via Pure Strands Agent",
                "tools_used": tools_used,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as e:
            logger.error(f"Chat processing failed: {str(e)}")
            return {
                "response": f"I'm sorry, I encountered an error processing your request: {str(e)}. Please try again.",
                "error": str(e),
                "intent_analysis": {"primary_intent": "error", "confidence": 0},
                "suggested_actions": ["Try rephrasing your question", "Check system status"],
                "page_context": page_context or {},
                "user_context": user_context.get("publisher_id") if user_context else None,
                "session_id": session_id or self.session_id,
                "model_used": "error_fallback",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    def _analyze_intent(self, message: str) -> str:
        """Simple intent analysis based on keywords."""
        message_lower = message.lower()

        if any(word in message_lower for word in ["revenue", "money", "earnings", "monetization"]):
            return "revenue"
        elif any(word in message_lower for word in ["bot", "ai", "partnership", "behavior"]):
            return "bot_behavior"
        elif any(word in message_lower for word in ["content", "performance", "popular", "trending"]):
            return "content_performance"
        elif any(word in message_lower for word in ["usage", "analytics", "metrics", "data"]):
            return "usage_analytics"
        elif any(word in message_lower for word in ["help", "how to", "what can", "guide"]):
            return "help"
        else:
            return "general_question"


# For testing
if __name__ == "__main__":

    async def test_clean_agent():
        """Test the clean analytics agent."""

        print("🧪 Testing Clean Analytics Agent")

        try:
            # Initialize agent
            agent = PublisherAnalyticsAgent(actor_id="test_user", session_id="test_session")

            # Test initialization
            init_success = await agent.initialize_agent()
            print(f"✅ Agent initialization: {'SUCCESS' if init_success else 'FAILED'}")

            # Test chat
            response = await agent.chat(
                "Show me my revenue analytics for the last month",
                page_context={"page_type": "analytics"},
                user_context={"publisher_id": "sample_publisher"},
                session_id="test_session",
            )

            print("✅ Chat response received")
            print(f"   Intent: {response.get('intent_analysis', {}).get('primary_intent')}")
            print(f"   Model: {response.get('model_used')}")
            print(f"   Tools used: {response.get('tools_used')}")
            print(f"   Response length: {len(response.get('response', ''))}")

        except Exception as e:
            print(f"❌ Test failed: {str(e)}")

    asyncio.run(test_clean_agent())
