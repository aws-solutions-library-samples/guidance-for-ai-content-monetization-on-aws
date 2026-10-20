#!/usr/bin/env python3
"""
Local API Server for Testing Analytics Agent Integration
This server provides endpoints for the UI to interact with the enhanced analytics agent.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request
from flask_cors import CORS

# Try to import python-dotenv for environment configuration
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
    logger = logging.getLogger(__name__)
    logger.warning("python-dotenv not available. Install with: pip install python-dotenv")

from analytics_agent_strands import PublisherAnalyticsAgent

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Global analytics agent instance
analytics_agent = None


def get_analytics_agent():
    """Get or create analytics agent instance."""
    global analytics_agent
    if analytics_agent is None:
        try:
            # Load environment variables from analytics_agent/.env if it exists
            from dotenv import load_dotenv

            env_path = os.path.join("analytics_agent", ".env")
            if os.path.exists(env_path):
                load_dotenv(env_path)
                logger.info(f"Loaded environment from {env_path}")

            analytics_agent = PublisherAnalyticsAgent()
            logger.info("Analytics agent initialized with Strands Agent")
        except Exception as e:
            logger.error(f"Failed to initialize analytics agent: {e}")
            # Create a fallback agent without Strands integration
            analytics_agent = PublisherAnalyticsAgent()
    return analytics_agent


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "timestamp": datetime.now(UTC).isoformat(), "service": "Analytics Agent API"})


@app.route("/api/analytics/chat", methods=["POST"])
def chat_with_agent():
    """
    Chat endpoint for the analytics agent.

    Expected payload:
    {
        "message": "User's message",
        "page_context": {
            "page_type": "analytics|content|bot_control|system|home",
            "url_path": "/current/path",
            "current_data": {...}
        },
        "user_context": {
            "publisher_id": "user_id",
            "access_level": "admin|viewer"
        },
        "session_id": "optional_session_id"
    }
    """
    try:
        data = request.get_json()

        if not data or "message" not in data:
            return jsonify({"error": "Message is required"}), 400

        message = data["message"]
        page_context = data.get("page_context")
        user_context = data.get("user_context", {})
        session_id = data.get("session_id")

        # Extract auth token from headers if available
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]  # Remove 'Bearer ' prefix
            user_context["auth_token"] = auth_token
            logger.info("Received auth token from UI")
        else:
            logger.info("No auth token provided, using local testing mode with mock data")

        # Get analytics agent
        agent = get_analytics_agent()

        # Process chat message
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                agent.chat(message=message, page_context=page_context, user_context=user_context, session_id=session_id)
            )
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/api/analytics/context-help", methods=["POST"])
def get_context_help():
    """
    Get contextual help for the current page.

    Expected payload:
    {
        "page_type": "analytics|content|bot_control|system|home",
        "url_path": "/current/path",
        "current_data": {...},
        "user_question": "optional specific question"
    }
    """
    try:
        data = request.get_json()

        if not data or "page_type" not in data:
            return jsonify({"error": "Page type is required"}), 400

        page_type = data["page_type"]
        current_data = data.get("current_data")
        user_question = data.get("user_question")
        url_path = data.get("url_path")

        # Get analytics agent
        agent = get_analytics_agent()

        # Get context help
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                agent.get_page_context_help(
                    page_type=page_type, current_data=current_data, user_question=user_question, url_path=url_path
                )
            )
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in context help endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/api/analytics/page-actions", methods=["POST"])
def suggest_page_actions():
    """
    Get suggested actions for the current page.

    Expected payload:
    {
        "page_type": "analytics|content|bot_control|system|home",
        "user_intent": "What the user wants to accomplish",
        "current_filters": {...},
        "current_data": {...},
        "url_path": "/current/path"
    }
    """
    try:
        data = request.get_json()

        if not data or "page_type" not in data or "user_intent" not in data:
            return jsonify({"error": "Page type and user intent are required"}), 400

        page_type = data["page_type"]
        user_intent = data["user_intent"]
        current_filters = data.get("current_filters")
        current_data = data.get("current_data")
        url_path = data.get("url_path")

        # Get analytics agent
        agent = get_analytics_agent()

        # Get page actions
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                agent.suggest_page_actions(
                    page_type=page_type,
                    user_intent=user_intent,
                    current_filters=current_filters,
                    current_data=current_data,
                    url_path=url_path,
                )
            )
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in page actions endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/api/analytics/usage", methods=["GET"])
def get_usage_analytics():
    """
    Get usage analytics data.

    Query parameters:
    - date_range: Time period (last_7_days, last_30_days, custom:YYYY-MM-DD:YYYY-MM-DD)
    - content_type: Filter by content type
    - bot_filter: Filter by specific bot or partner
    """
    try:
        date_range = request.args.get("date_range", "last_7_days")
        content_type = request.args.get("content_type")
        bot_filter = request.args.get("bot_filter")

        # Mock user context for local testing
        user_context = {"publisher_id": "local_test_publisher", "access_level": "admin"}

        # Get analytics agent
        agent = get_analytics_agent()

        # Get usage analytics
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                agent.get_usage_analytics(
                    date_range=date_range, content_type=content_type, bot_filter=bot_filter, user_context=user_context
                )
            )
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in usage analytics endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/api/analytics/content-performance", methods=["POST"])
def analyze_content_performance():
    """
    Analyze content performance.

    Expected payload:
    {
        "content_ids": ["id1", "id2", ...]
    }
    """
    try:
        data = request.get_json()

        if not data or "content_ids" not in data:
            return jsonify({"error": "Content IDs are required"}), 400

        content_ids = data["content_ids"]

        # Mock user context for local testing
        user_context = {"publisher_id": "local_test_publisher", "access_level": "admin"}

        # Get analytics agent
        agent = get_analytics_agent()

        # Analyze content performance
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                agent.analyze_content_performance(content_ids=content_ids, user_context=user_context)
            )
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in content performance endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/api/analytics/insights", methods=["POST"])
def generate_insights():
    """
    Generate business insights.

    Expected payload:
    {
        "metric": "revenue|access_patterns|bot_behavior|content_popularity",
        "time_period": "last_30_days"
    }
    """
    try:
        data = request.get_json()

        if not data or "metric" not in data:
            return jsonify({"error": "Metric is required"}), 400

        metric = data["metric"]
        time_period = data.get("time_period", "last_30_days")

        # Get analytics agent
        agent = get_analytics_agent()

        # Generate insights
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(agent.generate_insights(metric=metric, time_period=time_period))
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in insights endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/api/analytics/revenue", methods=["GET"])
def get_revenue_metrics():
    """
    Get revenue metrics.

    Query parameters:
    - time_period: Analysis time period
    - breakdown_by: How to break down the revenue data
    """
    try:
        time_period = request.args.get("time_period", "last_30_days")
        breakdown_by = request.args.get("breakdown_by", "content_type")

        # Get analytics agent
        agent = get_analytics_agent()

        # Get revenue metrics
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(
                agent.get_revenue_metrics(time_period=time_period, breakdown_by=breakdown_by)
            )
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in revenue metrics endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


@app.route("/api/analytics/bot-behavior", methods=["GET"])
def analyze_bot_behavior():
    """
    Analyze bot behavior.

    Query parameters:
    - bot_id: Specific bot to analyze (optional)
    - time_period: Analysis time period
    """
    try:
        bot_id = request.args.get("bot_id")
        time_period = request.args.get("time_period", "last_7_days")

        # Get analytics agent
        agent = get_analytics_agent()

        # Analyze bot behavior
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            response = loop.run_until_complete(agent.analyze_bot_behavior(bot_id=bot_id, time_period=time_period))
        finally:
            loop.close()

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in bot behavior endpoint: {str(e)}")
        return jsonify({"error": "Internal server error", "message": str(e)}), 500


# Mock data endpoints for UI testing
@app.route("/api/mock/dashboard-data", methods=["GET"])
def get_mock_dashboard_data():
    """Get mock dashboard data for UI testing."""
    return jsonify(
        {
            "total_revenue": 5420.75,
            "total_content": 156,
            "active_bots": 12,
            "recent_activity": [
                {
                    "timestamp": "2024-01-15T10:30:00Z",
                    "action": "Content uploaded",
                    "details": "AI in Healthcare: Future Trends",
                },
                {"timestamp": "2024-01-15T09:45:00Z", "action": "Bot access", "details": "OpenAI accessed 15 articles"},
                {
                    "timestamp": "2024-01-15T09:15:00Z",
                    "action": "Revenue generated",
                    "details": "$45.50 from Anthropic",
                },
            ],
            "alerts": [
                {"type": "info", "message": "New bot partner request pending approval"},
                {"type": "warning", "message": "Content processing queue has 3 items"},
            ],
        }
    )


@app.route("/api/mock/analytics-data", methods=["GET"])
def get_mock_analytics_data():
    """Get mock analytics data for UI testing."""
    return jsonify(
        {
            "date_range": "last_30_days",
            "revenue": 2840.50,
            "access_count": 15420,
            "bot_activity": {
                "total_requests": 15420,
                "blocked_requests": 342,
                "top_bots": ["OpenAI", "Anthropic", "Google"],
            },
            "popular_content": [
                {"id": "content_123", "title": "AI in Healthcare: Future Trends", "requests": 245},
                {"id": "content_456", "title": "Machine Learning Best Practices", "requests": 198},
                {"id": "content_789", "title": "Data Privacy in AI Systems", "requests": 167},
            ],
        }
    )


if __name__ == "__main__":
    print("🚀 Starting Analytics Agent API Server...")
    print("📊 Analytics Agent with Context-Aware Features")
    print("🌐 Server will be available at: http://localhost:5001")

    # Check configuration
    use_mock = os.getenv("USE_MOCK_DATA", "false").lower() == "true"
    iab_api_base = os.getenv("IAB_API_BASE_URL", "not configured")

    print("⚙️  Configuration:")
    print(f"   - Mock data mode: {'ENABLED' if use_mock else 'DISABLED'}")
    print(f"   - IAB API base: {iab_api_base}")
    print(f"   - Auth mode: {'Cognito + API Key fallback' if not use_mock else 'Local testing (no auth required)'}")

    print("📋 Available endpoints:")
    print("  - POST /api/analytics/chat - Chat with analytics agent")
    print("  - POST /api/analytics/context-help - Get contextual help")
    print("  - POST /api/analytics/page-actions - Get page action suggestions")
    print("  - GET  /api/analytics/usage - Get usage analytics")
    print("  - POST /api/analytics/content-performance - Analyze content performance")
    print("  - POST /api/analytics/insights - Generate insights")
    print("  - GET  /api/analytics/revenue - Get revenue metrics")
    print("  - GET  /api/analytics/bot-behavior - Analyze bot behavior")
    print("  - GET  /api/mock/dashboard-data - Mock dashboard data")
    print("  - GET  /api/mock/analytics-data - Mock analytics data")
    print("  - GET  /health - Health check")

    if use_mock:
        print("\n💡 Running in MOCK DATA mode - no real API calls will be made")
        print("   This is perfect for local UI testing without authentication")
    else:
        print("\n🔐 Running in LIVE API mode - requires proper authentication")
        print("   Make sure your UI is passing valid Cognito tokens")

    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true", host="0.0.0.0", port=5001)
