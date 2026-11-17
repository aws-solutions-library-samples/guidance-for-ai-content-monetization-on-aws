# context_tools.py - Context-aware tools following Strands Agent pattern
import logging
import random
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from strands.tools import tool

logger = logging.getLogger(__name__)


def _detect_page_type(url_path: str, page_data: Optional[Dict[str, Any]] = None) -> str:
    """
    Detect page type from URL path and available page data.

    Args:
        url_path: Current URL path
        page_data: Available data on the page

    Returns:
        Detected page type
    """
    # URL-based detection
    if "/analytics" in url_path or "/dashboard" in url_path:
        return "analytics"
    elif "/content" in url_path or "/upload" in url_path:
        return "content"
    elif "/bot-control" in url_path or "/bots" in url_path:
        return "bot_control"
    elif "/system" in url_path or "/settings" in url_path:
        return "system"
    elif url_path == "/" or "/home" in url_path:
        return "home"

    # Data-based detection fallback
    if page_data:
        if "analytics" in str(page_data).lower() or "metrics" in str(page_data).lower():
            return "analytics"
        elif "content" in str(page_data).lower() or "upload" in str(page_data).lower():
            return "content"
        elif "bot" in str(page_data).lower() or "partner" in str(page_data).lower():
            return "bot_control"

    return "unknown"


def _extract_page_context(page_type: str, current_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Extract detailed context information from the current page.

    Args:
        page_type: Type of page user is on
        current_data: Data currently visible on the page

    Returns:
        Extracted context information
    """
    context = {
        "page_type": page_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "data_available": bool(current_data),
        "context_elements": [],
    }

    if not current_data:
        return context

    # Extract context based on page type
    if page_type == "analytics":
        context["context_elements"] = _extract_analytics_context(current_data)
    elif page_type == "content":
        context["context_elements"] = _extract_content_context(current_data)
    elif page_type == "bot_control":
        context["context_elements"] = _extract_bot_control_context(current_data)
    elif page_type == "system":
        context["context_elements"] = _extract_system_context(current_data)
    elif page_type == "home":
        context["context_elements"] = _extract_home_context(current_data)

    return context


def _extract_analytics_context(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract context elements from analytics page data."""
    elements = []

    # Check for date range filters
    if "date_range" in data or "timeframe" in data:
        elements.append(
            {
                "type": "filter",
                "name": "date_range",
                "value": data.get("date_range", data.get("timeframe", "unknown")),
                "description": "Current time period being analyzed",
            }
        )

    # Check for content type filters
    if "content_type" in data:
        elements.append(
            {
                "type": "filter",
                "name": "content_type",
                "value": data.get("content_type"),
                "description": "Content type filter applied",
            }
        )

    # Check for visible metrics
    metrics = ["revenue", "access_count", "bot_activity", "popular_content"]
    for metric in metrics:
        if metric in data:
            elements.append(
                {
                    "type": "metric",
                    "name": metric,
                    "value": data.get(metric),
                    "description": f"Current {metric} data visible",
                }
            )

    return elements


def _extract_content_context(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract context elements from content management page data."""
    elements = []

    # Check for content list
    if "content_list" in data or "contents" in data:
        content_count = len(data.get("content_list", data.get("contents", [])))
        elements.append(
            {
                "type": "data",
                "name": "content_count",
                "value": content_count,
                "description": f"{content_count} content items visible",
            }
        )

    # Check for upload status
    if "upload_status" in data:
        elements.append(
            {
                "type": "status",
                "name": "upload_status",
                "value": data.get("upload_status"),
                "description": "Content upload status",
            }
        )

    # Check for processing status
    if "processing_status" in data:
        elements.append(
            {
                "type": "status",
                "name": "processing_status",
                "value": data.get("processing_status"),
                "description": "Content processing status",
            }
        )

    return elements


def _extract_bot_control_context(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract context elements from bot control page data."""
    elements = []

    # Check for active bots
    if "active_bots" in data or "bots" in data:
        bot_count = len(data.get("active_bots", data.get("bots", [])))
        elements.append(
            {"type": "data", "name": "active_bots", "value": bot_count, "description": f"{bot_count} active bots"}
        )

    # Check for access rules
    if "access_rules" in data:
        rules_count = len(data.get("access_rules", []))
        elements.append(
            {
                "type": "configuration",
                "name": "access_rules",
                "value": rules_count,
                "description": f"{rules_count} access rules configured",
            }
        )

    # Check for pricing configuration
    if "pricing_config" in data:
        elements.append(
            {
                "type": "configuration",
                "name": "pricing_config",
                "value": data.get("pricing_config"),
                "description": "Pricing configuration status",
            }
        )

    return elements


def _extract_system_context(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract context elements from system management page data."""
    elements = []

    # Check for system status
    if "system_status" in data:
        elements.append(
            {
                "type": "status",
                "name": "system_status",
                "value": data.get("system_status"),
                "description": "Current system status",
            }
        )

    # Check for user count
    if "users" in data or "user_count" in data:
        user_count = data.get("user_count", len(data.get("users", [])))
        elements.append(
            {"type": "data", "name": "user_count", "value": user_count, "description": f"{user_count} users in system"}
        )

    return elements


def _extract_home_context(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract context elements from home dashboard page data."""
    elements = []

    # Check for quick stats
    stats = ["total_revenue", "total_content", "active_bots", "recent_activity"]
    for stat in stats:
        if stat in data:
            elements.append(
                {"type": "quick_stat", "name": stat, "value": data.get(stat), "description": f"Dashboard {stat} metric"}
            )

    # Check for alerts
    if "alerts" in data:
        alert_count = len(data.get("alerts", []))
        elements.append(
            {"type": "alert", "name": "alerts", "value": alert_count, "description": f"{alert_count} active alerts"}
        )

    return elements


@tool
async def get_page_context_help(
    page_type: str,
    current_data: Optional[Dict[str, Any]] = None,
    user_question: Optional[str] = None,
    url_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Provide contextual help based on the current page and available data.

    Args:
        page_type: Type of page user is on (analytics, content, bot_control, system, home)
        current_data: Data currently visible on the page
        user_question: Specific question about the page or data
        url_path: Current URL path for enhanced context detection

    Returns:
        Contextual help and suggestions for the current page
    """
    # Enhanced page type detection if URL path is provided
    if url_path:
        detected_page_type = _detect_page_type(url_path, current_data)
        if detected_page_type != "unknown":
            page_type = detected_page_type

    # Extract detailed page context
    page_context = _extract_page_context(page_type, current_data)

    page_help = {
        "analytics": {
            "description": "This page shows your content performance metrics and analytics",
            "key_metrics": ["Total Access Count", "Revenue", "Popular Content", "Bot Activity"],
            "common_questions": [
                "Which content is performing best?",
                "How much revenue am I generating?",
                "Which bots are accessing my content most?",
                "What are the trending topics?",
                "How do I compare performance across time periods?",
                "What insights can I get from my data?",
            ],
            "available_actions": [
                "Change date range",
                "Filter by content type",
                "Export detailed report",
                "View specific content performance",
                "Generate custom insights",
                "Compare metrics across periods",
            ],
            "contextual_tips": [
                "Use filters to focus on specific content types or time periods",
                "Look for trends in the data to identify optimization opportunities",
                "Export reports for deeper analysis or sharing with stakeholders",
            ],
        },
        "content": {
            "description": "This page allows you to manage your content library",
            "key_features": ["Upload Content", "Edit Metadata", "View Processing Status", "Content Performance"],
            "common_questions": [
                "How is my content being processed?",
                "Which content should I optimize?",
                "What content is most valuable to bots?",
                "How can I improve content discoverability?",
                "What metadata should I add to improve performance?",
                "How do I track content processing status?",
            ],
            "available_actions": [
                "Upload new content",
                "Edit content metadata",
                "View content analytics",
                "Reprocess content",
                "Optimize content tags",
                "Bulk edit metadata",
            ],
            "contextual_tips": [
                "Add relevant IAB taxonomy tags to improve content discoverability",
                "Monitor processing status to ensure content is available to bots",
                "Use analytics to identify which content types perform best",
            ],
        },
        "bot_control": {
            "description": "This page manages AI bot access and partnerships",
            "key_features": ["Access Rules", "Partner Management", "Pricing Configuration", "Bot Activity"],
            "common_questions": [
                "Which bots are accessing my content?",
                "How should I price my content?",
                "Are there any suspicious access patterns?",
                "Which partnerships are most valuable?",
                "How do I configure access rules?",
                "What pricing models work best?",
            ],
            "available_actions": [
                "Configure access rules",
                "Set pricing models",
                "Review bot activity",
                "Manage partner relationships",
                "Monitor access patterns",
                "Adjust pricing strategies",
            ],
            "contextual_tips": [
                "Monitor bot behavior patterns to identify optimization opportunities",
                "Adjust pricing based on content performance and demand",
                "Set up access rules to protect premium content",
            ],
        },
        "system": {
            "description": "This page provides system management and configuration options",
            "key_features": ["System Status", "Configuration", "User Management", "API Settings"],
            "common_questions": [
                "Is the system running normally?",
                "How do I configure API settings?",
                "How can I manage user access?",
                "Where can I find system logs?",
                "How do I monitor system performance?",
                "What are the current system limits?",
            ],
            "available_actions": [
                "Check system health",
                "Update configuration",
                "Manage users",
                "View system logs",
                "Monitor performance metrics",
                "Configure system alerts",
            ],
            "contextual_tips": [
                "Regularly check system status to ensure optimal performance",
                "Review logs to troubleshoot any issues",
                "Configure alerts to be notified of important system events",
            ],
        },
        "home": {
            "description": "This is the main dashboard providing an overview of your publisher platform",
            "key_features": ["Quick Stats", "Recent Activity", "Navigation", "Alerts"],
            "common_questions": [
                "What's my overall performance?",
                "Are there any urgent issues?",
                "Where should I focus my attention?",
                "How do I navigate to specific features?",
                "What are the latest updates?",
                "What actions should I take today?",
            ],
            "available_actions": [
                "View detailed analytics",
                "Check recent activity",
                "Navigate to specific sections",
                "Review alerts and notifications",
                "Access quick actions",
                "View performance summaries",
            ],
            "contextual_tips": [
                "Check alerts regularly for important notifications",
                "Use quick stats to get an overview of your performance",
                "Navigate to specific sections for detailed analysis",
            ],
        },
    }

    help_info = page_help.get(
        page_type,
        {
            "description": "General publisher platform help",
            "common_questions": ["How do I navigate the platform?", "Where can I find analytics?"],
            "available_actions": ["Navigate to specific sections", "Get overview of features"],
            "contextual_tips": ["Explore different sections to understand available features"],
        },
    )

    # Add page context information
    help_info["page_context"] = page_context

    # Provide specific help based on user question
    if user_question:
        help_info["specific_help"] = _generate_specific_help(user_question, page_type, page_context)

    # Add contextual recommendations based on current data
    if current_data:
        help_info["contextual_recommendations"] = _generate_contextual_recommendations(page_type, page_context)

    logger.info(
        f"Provided enhanced context help for {page_type} page with {len(page_context.get('context_elements', []))} context elements"
    )
    return help_info


def _generate_specific_help(user_question: str, page_type: str, page_context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate specific help based on user question and page context."""
    question_lower = user_question.lower()

    specific_help = {"question": user_question, "page_type": page_type, "recommendations": []}

    # Question-specific recommendations
    if "performance" in question_lower or "analytics" in question_lower:
        specific_help["recommendations"].append(
            {
                "action": "View analytics dashboard",
                "description": "Navigate to the analytics section for detailed performance metrics",
                "route": "/analytics",
            }
        )

    if "revenue" in question_lower or "money" in question_lower:
        specific_help["recommendations"].append(
            {
                "action": "Check revenue metrics",
                "description": "Use the revenue analytics tool to see detailed monetization data",
                "tool": "get_revenue_metrics",
            }
        )

    if "bot" in question_lower:
        specific_help["recommendations"].append(
            {
                "action": "Analyze bot behavior",
                "description": "Review bot activity and access patterns",
                "route": "/bot-control",
            }
        )

    if "content" in question_lower:
        specific_help["recommendations"].append(
            {
                "action": "Review content performance",
                "description": "Check which content is performing best",
                "tool": "analyze_content_performance",
            }
        )

    return specific_help


def _generate_contextual_recommendations(page_type: str, page_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate contextual recommendations based on page type and current context."""
    recommendations = []
    context_elements = page_context.get("context_elements", [])

    # Analytics page recommendations
    if page_type == "analytics":
        for element in context_elements:
            if element["type"] == "metric" and element["name"] == "revenue":
                recommendations.append(
                    {
                        "type": "insight",
                        "title": "Revenue Analysis",
                        "description": f"Current revenue: {element['value']}. Consider analyzing trends over time.",
                        "action": "generate_insights",
                        "parameters": {"metric": "revenue"},
                    }
                )

    # Content page recommendations
    elif page_type == "content":
        for element in context_elements:
            if element["type"] == "data" and element["name"] == "content_count":
                if element["value"] > 100:
                    recommendations.append(
                        {
                            "type": "optimization",
                            "title": "Content Optimization",
                            "description": f"You have {element['value']} content items. Consider analyzing which perform best.",
                            "action": "analyze_content_performance",
                        }
                    )

    # Bot control page recommendations
    elif page_type == "bot_control":
        for element in context_elements:
            if element["type"] == "data" and element["name"] == "active_bots":
                recommendations.append(
                    {
                        "type": "monitoring",
                        "title": "Bot Activity Monitoring",
                        "description": f"Monitor behavior of {element['value']} active bots for optimization opportunities.",
                        "action": "analyze_bot_behavior",
                    }
                )

    return recommendations


@tool
async def suggest_page_actions(
    page_type: str,
    user_intent: str,
    current_filters: Optional[Dict[str, Any]] = None,
    current_data: Optional[Dict[str, Any]] = None,
    url_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Suggest relevant actions user can take on the current page.

    Args:
        page_type: Type of page user is on
        user_intent: What the user is trying to accomplish
        current_filters: Current filters or context on the page
        current_data: Data currently visible on the page
        url_path: Current URL path for enhanced context detection

    Returns:
        Suggested actions with navigation routes and UI interactions
    """
    # Enhanced page type detection if URL path is provided
    if url_path:
        detected_page_type = _detect_page_type(url_path, current_data)
        if detected_page_type != "unknown":
            page_type = detected_page_type

    # Extract page context for better action suggestions
    page_context = _extract_page_context(page_type, current_data)

    # Comprehensive action suggestions with priority and context awareness
    action_suggestions = {
        "analytics": {
            "view_detailed_performance": {
                "label": "View Detailed Performance",
                "action": "navigate",
                "route": "/analytics/detailed",
                "description": "See comprehensive performance metrics with advanced filtering",
                "priority": "high",
                "category": "analysis",
            },
            "export_analytics_data": {
                "label": "Export Analytics Data",
                "action": "export",
                "description": "Download your analytics data in CSV or JSON format",
                "priority": "medium",
                "category": "data_export",
            },
            "generate_custom_insights": {
                "label": "Generate Custom Insights",
                "action": "tool",
                "tool": "generate_insights",
                "description": "Get AI-powered insights about your data trends",
                "priority": "high",
                "category": "insights",
            },
        },
        "content": {
            "upload_new_content": {
                "label": "Upload New Content",
                "action": "navigate",
                "route": "/content/upload",
                "description": "Add new content to your library",
                "priority": "high",
                "category": "content_management",
            },
            "analyze_content_performance": {
                "label": "Analyze Content Performance",
                "action": "tool",
                "tool": "analyze_content_performance",
                "description": "See which content performs best with bots",
                "priority": "high",
                "category": "analysis",
            },
        },
        "bot_control": {
            "analyze_bot_behavior": {
                "label": "Analyze Bot Behavior",
                "action": "tool",
                "tool": "analyze_bot_behavior",
                "description": "Get detailed insights into bot usage patterns",
                "priority": "high",
                "category": "analysis",
            },
            "configure_access_rules": {
                "label": "Configure Access Rules",
                "action": "navigate",
                "route": "/bot-control/access-rules",
                "description": "Set up rules for bot access to your content",
                "priority": "high",
                "category": "access_control",
            },
        },
    }

    suggestions = action_suggestions.get(page_type, {})

    # Filter suggestions based on user intent
    relevant_suggestions = {}

    # Ensure user_intent is a string
    if not isinstance(user_intent, str):
        user_intent = str(user_intent) if user_intent else ""

    intent_lower = user_intent.lower()

    for key, suggestion in suggestions.items():
        # Check if intent matches description or label
        if (
            intent_lower in suggestion["description"].lower()
            or intent_lower in suggestion["label"].lower()
            or intent_lower in suggestion.get("category", "").lower()
        ):
            relevant_suggestions[key] = suggestion

    # If no specific matches, return high priority suggestions
    if not relevant_suggestions:
        relevant_suggestions = {k: v for k, v in suggestions.items() if v.get("priority") == "high"}

    # If still no matches, return all suggestions
    if not relevant_suggestions:
        relevant_suggestions = suggestions

    result = {
        "page_type": page_type,
        "user_intent": user_intent,
        "suggestions": relevant_suggestions,
        "page_context": page_context,
        "total_suggestions": len(relevant_suggestions),
    }

    if current_filters:
        result["current_filters"] = current_filters

    logger.info(
        f"Suggested {len(relevant_suggestions)} contextual actions for {page_type} page with intent: {user_intent}"
    )
    return result
