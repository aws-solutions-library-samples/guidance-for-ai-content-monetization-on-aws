"""Analytics-agent Route_Module (``/analytics-agent/*``).

Thin per-domain route adapter for the ui_backend thin dispatcher (Requirements 4.1, 4.2,
4.3). It exposes the uniform ``route(event, method, path)`` entry, applies Cognito auth via
the unchanged ``@require_cognito_auth`` wrapper, and delegates the actual AgentCore
invocation to ``services.agent_runtime_client.AgentRuntimeClient`` (the Service_Layer single
home for the bedrock-agentcore ``invoke_agent_runtime`` call — Requirements 1.2, 7.3). The
route performs no direct AWS/data-store access itself: it parses the request, resolves the
``ANALYTICS_AGENT_ARN`` config, shapes the page context, calls the service, and formats the
response.

Behavior-preserving replacement (Requirement 2) for the inline
``ui_backend/handler.py:handle_analytics_agent_chat`` +
``handle_analytics_agent_endpoints``: the same ``@validate_response("/analytics-agent/chat",
"POST")`` + ``@require_cognito_auth`` decorator stack, the same request validation
(400 ``"Message is required"``), the same 500 ``"Analytics agent ARN not configured"`` when
the ARN env var is unset, the same page-context shaping, the same AgentCore response parsing
and structural normalization, the same nested error messages
(``"Failed to process request with AgentCore: ..."`` /
``"Failed to process analytics agent request: ..."``), and the same
404 ``"Analytics agent endpoint not found"`` for unmatched paths within the prefix.

The ``ANALYTICS_AGENT_ARN`` lookup stays a request-time ``os.environ.get`` read so the
post-deploy ARN injection (``analytics_agent/deploy.py`` patches the env var after the agent
deploys, default ``REPLACEME``) is preserved unchanged (Requirement 14.5).

This module is created ahead of the dispatcher flip (task 11.4); the dispatcher continues to
route ``/analytics-agent/*`` to the inline handler until that flip lands (Requirement 16.4).
Imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``sys.path`` manipulation (Requirements 5.1,
15.2). This module imports no other Route_Module (Requirement 4.4).
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from services.agent_runtime_client import AgentRuntimeClient
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.response_validator import validate_response

logger = logging.getLogger(__name__)

# Module-level client instance; it is stateless apart from a memoized boto3 client, so a
# single shared instance is reused across invocations within a warm container.
_agent_runtime_client = AgentRuntimeClient()


def _parse_request_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON request body from the event.

    Mirrors the dispatcher's ``_parse_request_data`` helper: the dispatcher validates the
    body up front and rejects invalid JSON with a 400, so by the time this route runs the
    body is either absent or valid. Falls back to ``{}`` when the body is absent (or,
    defensively, if it somehow failed to parse).
    """
    body = event.get("body", "")
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


@validate_response("/analytics-agent/chat", "POST")
@require_cognito_auth
def _handle_analytics_agent_chat(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """
    Handle /analytics-agent/chat endpoint - Route requests through analytics agent.

    This function calls the deployed analytics agent on AgentCore (via the
    ``AgentRuntimeClient`` service), which in turn uses the MCP server to access analytics
    tools.
    """
    try:
        user = kwargs["authenticated_user"]
        logger.info("Processing analytics agent chat request")

        # Extract request parameters
        message = request_data.get("message", "")
        page_context = request_data.get("page_context", {})
        session_id = request_data.get("session_id")

        if not message:
            return create_error_response(400, "Message is required")

        # Get Analytics Agent ARN from environment (post-deploy injected; preserved verbatim)
        analytics_agent_arn = os.environ.get("ANALYTICS_AGENT_ARN")
        if not analytics_agent_arn:
            return create_error_response(500, "Analytics agent ARN not configured")

        # Process page context
        processed_page_context = {
            "page_type": page_context.get("page_type", "unknown"),
            "url_path": page_context.get("url_path", "/"),
            "context_elements": page_context.get("context_elements", []),
            "current_filters": page_context.get("current_filters", {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(
            f"Analytics agent request - User: {user.get('email')}, Page: {processed_page_context['page_type']}, Message: {message[:100]}..."
        )

        # Call AgentCore runtime where the analytics agent is deployed
        try:
            logger.info(f"Calling Analytics Agent on AgentCore: {analytics_agent_arn}")

            # Delegate the payload build + invoke_agent_runtime call + response read to the
            # service (single home for the bedrock-agentcore invoke).
            agentcore_response = _agent_runtime_client.invoke(
                agent_runtime_arn=analytics_agent_arn,
                tool_name="analytics_chat",
                client_type="internal",
                parameters={
                    "message": message,
                    "page_context": processed_page_context,
                    "user_context": user,
                    "session_id": session_id,
                },
            )

            # Parse the AgentCore response
            if "output" in agentcore_response:
                output_data = agentcore_response["output"]

                # Check if output_data is already a dictionary (parsed JSON)
                if isinstance(output_data, dict):
                    agent_response = output_data
                    logger.info("AgentCore response was already parsed as JSON")
                else:
                    # Try to parse as JSON string
                    try:
                        agent_response = json.loads(output_data)
                        logger.info("Successfully parsed AgentCore response from JSON string")
                    except json.JSONDecodeError:
                        # If not JSON, create a structured response
                        logger.warning(f"Failed to parse AgentCore response as JSON: {str(output_data)[:100]}...")
                        agent_response = {
                            "response": str(output_data),
                            "intent_analysis": {"primary_intent": "general_question", "confidence": 0.8},
                            "suggested_actions": [],
                            "page_context": processed_page_context,
                            "user_context": user.get("publisher_id"),
                            "session_id": session_id,
                            "model_used": "AgentCore Runtime (parsed)",
                        }

                # Ensure the response has the expected structure
                if not isinstance(agent_response, dict):
                    agent_response = {
                        "response": str(agent_response),
                        "intent_analysis": {"primary_intent": "general_question", "confidence": 0.8},
                        "suggested_actions": [],
                        "page_context": processed_page_context,
                        "user_context": user.get("publisher_id"),
                        "session_id": session_id,
                        "model_used": "AgentCore Runtime (converted)",
                    }

                # Validate required fields and add missing context
                if "response" not in agent_response:
                    agent_response["response"] = str(agent_response)

                # Add context fields if missing
                if "page_context" not in agent_response:
                    agent_response["page_context"] = processed_page_context
                if "user_context" not in agent_response:
                    agent_response["user_context"] = user.get("publisher_id")
                if "session_id" not in agent_response:
                    agent_response["session_id"] = session_id
            else:
                # Fallback response if AgentCore doesn't return expected format
                logger.warning("AgentCore response missing 'output' field")
                agent_response = {
                    "response": f"I understand you're asking about: '{message}'. I'm connected to our analytics platform through AgentCore and ready to help you analyze your content performance and revenue metrics.",
                    "intent_analysis": {"primary_intent": "general_question", "confidence": 0.7},
                    "suggested_actions": [],
                    "page_context": processed_page_context,
                    "user_context": user.get("publisher_id"),
                    "session_id": session_id,
                    "model_used": "AgentCore Runtime (fallback)",
                }

            logger.info(f"AgentCore response generated successfully for user: {user.get('email')}")

            return create_success_response(agent_response)

        except Exception as agentcore_error:
            logger.error(f"AgentCore runtime error: {str(agentcore_error)}")
            return create_error_response(500, f"Failed to process request with AgentCore: {str(agentcore_error)}")

    except Exception as e:
        logger.error(f"Error in handle_analytics_agent_chat: {str(e)}")
        return create_error_response(500, f"Failed to process analytics agent request: {str(e)}")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Uniform dispatch entry for the analytics-agent domain.

    Mirrors the inline ``handle_analytics_agent_endpoints`` router: dispatches
    ``POST /analytics-agent/chat`` to the chat handler and returns
    404 ``"Analytics agent endpoint not found"`` for any other method/path within the
    ``/analytics-agent/`` prefix (Requirement 4.6), without invoking the service.
    """
    headers = event.get("headers") or {}
    request_data = _parse_request_data(event)

    if path == "/analytics-agent/chat" and method == "POST":
        return _handle_analytics_agent_chat(headers, request_data, event)

    return create_error_response(404, "Analytics agent endpoint not found")
