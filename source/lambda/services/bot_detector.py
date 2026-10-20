"""
Bot Detection Service

This module provides bot detection logic for identifying AI bots and automated
systems accessing the Content Monetization API through direct API calls.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BotDetector:
    """
    Service for detecting AI bots and automated systems from request metadata.
    """

    def __init__(self):
        """Initialize the bot detector with known bot patterns."""
        self.known_bot_patterns = [
            r"OpenAI-GPT",
            r"Claude-\d+-\w+",  # Matches Claude-3-Sonnet, Claude-3-Haiku, etc.
            r"Claude-\d+",  # Fallback for Claude-3, etc.
            r"Anthropic",
            r"GPT-\d+",
            r"ChatGPT",
            r"Bedrock-Agent",
            r"AWS-Bedrock",
            r"LangChain",
            r"LlamaIndex",
            r"Hugging.*Face",
            r"Transformers",
            r"AutoGPT",
            r"AgentGPT",
            r"BabyAGI",
            r"LangGraph",
            r"CrewAI",
            r"Semantic.*Kernel",
        ]

        self.bot_user_agent_indicators = [
            "bot",
            "crawler",
            "spider",
            "scraper",
            "agent",
            "automated",
            "python-requests",
            "curl",
            "wget",
            "http",
            "api-client",
            "sdk",
        ]

        self.api_key_patterns = [
            r"sk-[a-zA-Z0-9]{48}",  # OpenAI API key pattern
            r"ak-[a-zA-Z0-9]{32}",  # Anthropic API key pattern
            r"AKIA[0-9A-Z]{16}",  # AWS Access Key pattern
            r"[a-f0-9]{32}",  # Generic 32-char hex key
            r"[a-zA-Z0-9]{40}",  # Generic 40-char key
        ]

    def is_bot_request(self, requester_info: Optional[Dict[str, Any]]) -> bool:
        """
        Detect if request is from an AI bot (direct API access).

        Args:
            requester_info: Dictionary containing request metadata

        Returns:
            bool: True if request appears to be from a bot
        """
        try:
            if requester_info is None:
                return False

            # Check user agent patterns
            if self._check_user_agent_patterns(requester_info):
                return True

            # Check authentication method (API keys vs user sessions)
            if self._check_authentication_method(requester_info):
                return True

            # Check request headers for bot indicators
            if self._check_request_headers(requester_info):
                return True

            # Check request patterns (high frequency, no human-like delays)
            if self._check_request_patterns(requester_info):
                return True

            return False

        except Exception as e:
            logger.error(f"Error in bot detection: {str(e)}")
            return False

    def extract_bot_id(self, requester_info: Optional[Dict[str, Any]]) -> str:
        """
        Extract bot identifier from request metadata.

        Args:
            requester_info: Dictionary containing request metadata

        Returns:
            str: Bot identifier or 'unknown_bot'
        """
        try:
            if requester_info is None:
                return "unknown_bot"

            user_agent = requester_info.get("user_agent", "")

            # Extract bot ID from user agent using known patterns
            for pattern in self.known_bot_patterns:
                match = re.search(pattern, user_agent, re.IGNORECASE)
                if match:
                    return match.group(0).lower().replace(" ", "-")

            # Check for custom bot identifiers in headers
            headers = requester_info.get("headers", {})
            if "x-bot-id" in headers:
                return headers["x-bot-id"]

            if "x-client-name" in headers:
                return headers["x-client-name"].lower().replace(" ", "-")

            # Fallback to API key or IP-based identification
            api_key_id = requester_info.get("api_key_id")
            if api_key_id:
                return f"api-key-{api_key_id[:8]}"

            # Use IP address as last resort
            ip_address = requester_info.get("ip_address", "unknown")
            return f"bot-{ip_address.replace('.', '-')}"

        except Exception as e:
            logger.error(f"Error extracting bot ID: {str(e)}")
            return "unknown_bot"

    def get_bot_type(self, requester_info: Optional[Dict[str, Any]]) -> str:
        """
        Determine the type/category of bot from request metadata.

        Args:
            requester_info: Dictionary containing request metadata

        Returns:
            str: Bot type category
        """
        try:
            if requester_info is None:
                return "unknown_bot"

            user_agent = requester_info.get("user_agent", "").lower()

            # LLM/AI Assistant bots
            if any(pattern in user_agent for pattern in ["gpt", "claude", "anthropic", "openai"]):
                return "llm_assistant"

            # Agent frameworks
            if any(pattern in user_agent for pattern in ["langchain", "llamaindex", "autogpt", "crewai"]):
                return "agent_framework"

            # AWS/Cloud service bots
            if any(pattern in user_agent for pattern in ["bedrock", "aws", "lambda"]):
                return "cloud_service"

            # Generic API clients
            if any(pattern in user_agent for pattern in ["python-requests", "curl", "http"]):
                return "api_client"

            # Web scrapers/crawlers
            if any(pattern in user_agent for pattern in ["bot", "crawler", "spider", "scraper"]):
                return "web_crawler"

            return "unknown_bot"

        except Exception as e:
            logger.error(f"Error determining bot type: {str(e)}")
            return "unknown_bot"

    def assess_bot_behavior(self, requester_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Assess bot behavior patterns for analytics.

        Args:
            requester_info: Dictionary containing request metadata

        Returns:
            dict: Bot behavior assessment
        """
        try:
            return {
                "bot_id": self.extract_bot_id(requester_info),
                "bot_type": self.get_bot_type(requester_info),
                "is_automated": self.is_bot_request(requester_info),
                "confidence_score": self._calculate_bot_confidence(requester_info),
                "behavior_indicators": self._get_behavior_indicators(requester_info),
            }

        except Exception as e:
            logger.error(f"Error assessing bot behavior: {str(e)}")
            return {
                "bot_id": "unknown_bot",
                "bot_type": "unknown_bot",
                "is_automated": False,
                "confidence_score": 0.0,
                "behavior_indicators": [],
            }

    def _check_user_agent_patterns(self, requester_info: Dict[str, Any]) -> bool:
        """Check user agent for bot patterns."""
        user_agent = requester_info.get("user_agent", "").lower()

        # Check known bot patterns
        for pattern in self.known_bot_patterns:
            if re.search(pattern, user_agent, re.IGNORECASE):
                return True

        # Check general bot indicators
        for indicator in self.bot_user_agent_indicators:
            if indicator in user_agent:
                return True

        return False

    def _check_authentication_method(self, requester_info: Dict[str, Any]) -> bool:
        """Check if authentication method indicates bot access."""
        auth_method = requester_info.get("authentication_method", "")

        # API key authentication is typically used by bots
        if auth_method == "api_key":
            return True

        # Check for API key patterns in headers
        headers = requester_info.get("headers", {})
        auth_header = headers.get("authorization", "")

        for pattern in self.api_key_patterns:
            if re.search(pattern, auth_header):
                return True

        return False

    def _check_request_headers(self, requester_info: Dict[str, Any]) -> bool:
        """Check request headers for bot indicators."""
        headers = requester_info.get("headers", {})

        # Common bot headers
        bot_headers = ["x-bot-id", "x-automated-client", "x-api-client", "x-sdk-version", "x-client-version"]

        for header in bot_headers:
            if header.lower() in [h.lower() for h in headers.keys()]:
                return True

        # Check for missing typical browser headers
        browser_headers = ["accept-language", "accept-encoding", "cache-control"]
        header_names_lower = [h.lower() for h in headers.keys()]
        missing_headers = sum(1 for h in browser_headers if h not in header_names_lower)

        # If missing most browser headers, likely a bot
        return missing_headers >= 2

    def _check_request_patterns(self, requester_info: Dict[str, Any]) -> bool:
        """Check request patterns that indicate bot behavior."""
        # This would analyze request timing, frequency, etc.
        # For now, return False as we don't have historical data
        return False

    def _calculate_bot_confidence(self, requester_info: Dict[str, Any]) -> float:
        """Calculate confidence score for bot detection."""
        confidence = 0.0

        if self._check_user_agent_patterns(requester_info):
            confidence += 0.4

        if self._check_authentication_method(requester_info):
            confidence += 0.3

        if self._check_request_headers(requester_info):
            confidence += 0.2

        if self._check_request_patterns(requester_info):
            confidence += 0.1

        return min(confidence, 1.0)

    def _get_behavior_indicators(self, requester_info: Dict[str, Any]) -> List[str]:
        """Get list of behavior indicators that suggest bot activity."""
        indicators = []

        if self._check_user_agent_patterns(requester_info):
            indicators.append("bot_user_agent")

        if self._check_authentication_method(requester_info):
            indicators.append("api_key_auth")

        if self._check_request_headers(requester_info):
            indicators.append("non_browser_headers")

        if self._check_request_patterns(requester_info):
            indicators.append("automated_patterns")

        return indicators
