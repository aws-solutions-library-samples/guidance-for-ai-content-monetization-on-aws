"""
Local Strands Agent Configuration
This module configures the analytics agent to run locally with LLM integration.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class LocalStrandsConfig:
    """Configuration for running Strands Agent locally."""

    def __init__(self):
        """Initialize local configuration."""
        self.model_config = self._get_model_config()
        self.agent_config = self._get_agent_config()

    def _get_model_config(self) -> Dict[str, Any]:
        """Get model configuration for local or cloud LLM."""
        # Check for local model configuration
        local_model_url = os.getenv("LOCAL_LLM_URL", "http://localhost:11434")  # Ollama default
        local_model_name = os.getenv("LOCAL_LLM_MODEL", "llama3.1:8b")

        # Check for cloud model configuration
        openai_api_key = os.getenv("OPENAI_API_KEY")
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        aws_region = os.getenv("AWS_DEFAULT_REGION", "us-west-2")

        # Determine which model to use based on available configuration
        if os.getenv("USE_LOCAL_LLM", "false").lower() == "true":
            return {
                "provider": "ollama",
                "model": local_model_name,
                "base_url": local_model_url,
                "temperature": 0.7,
                "max_tokens": 2000,
            }
        elif openai_api_key:
            return {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": openai_api_key,
                "temperature": 0.7,
                "max_tokens": 2000,
            }
        elif anthropic_api_key:
            return {
                "provider": "anthropic",
                "model": "claude-3-haiku-20240307",
                "api_key": anthropic_api_key,
                "temperature": 0.7,
                "max_tokens": 2000,
            }
        else:
            # Fallback to AWS Bedrock if available
            return {
                "provider": "bedrock",
                "model": "anthropic.claude-3-haiku-20240307-v1:0",
                "region": aws_region,
                "temperature": 0.7,
                "max_tokens": 2000,
            }

    def _get_agent_config(self) -> Dict[str, Any]:
        """Get agent configuration."""
        return {
            "name": "PublisherAnalyticsAgent",
            "description": "Context-aware analytics assistant for publisher content management",
            "system_prompt": """You are an intelligent analytics assistant for a publisher's content management system. 
Your role is to help business users understand their content performance, bot usage patterns, and revenue metrics through natural language conversations.

You are context-aware and understand which page the user is currently viewing. You can provide:
- Page-specific help and guidance
- Analytics insights relevant to the current context
- Suggestions for actions users can take on the current page
- Navigation assistance to relevant sections

You have access to comprehensive analytics data about:
- Content access patterns and popularity
- AI bot usage and behavior
- Revenue and monetization metrics  
- Content performance trends
- User engagement analytics

Always provide actionable insights and recommendations. When presenting data, focus on business value and strategic implications. Use clear, non-technical language unless technical details are specifically requested.

When users ask questions, consider their current page context and provide relevant suggestions. If they need to navigate to a different section to accomplish their goal, suggest the appropriate page actions.

When users ask questions about their content performance, always query the latest data and provide context about trends and comparisons to previous periods.""",
            "tools": [
                "get_usage_analytics",
                "analyze_content_performance",
                "generate_insights",
                "create_custom_report",
                "get_revenue_metrics",
                "analyze_bot_behavior",
                "get_page_context_help",
                "suggest_page_actions",
            ],
            "memory_config": {"type": "local", "max_messages": 50, "retention_hours": 24},
        }

    def get_llm_client(self):
        """Get configured LLM client based on provider."""
        provider = self.model_config["provider"]

        if provider == "ollama":
            return self._get_ollama_client()
        elif provider == "openai":
            return self._get_openai_client()
        elif provider == "anthropic":
            return self._get_anthropic_client()
        elif provider == "bedrock":
            return self._get_bedrock_client()
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _get_ollama_client(self):
        """Get Ollama client for local LLM."""
        try:
            import ollama

            return ollama.Client(host=self.model_config["base_url"])
        except ImportError:
            logger.error("Ollama client not available. Install with: pip install ollama")
            return None

    def _get_openai_client(self):
        """Get OpenAI client."""
        try:
            from openai import OpenAI

            return OpenAI(api_key=self.model_config["api_key"])
        except ImportError:
            logger.error("OpenAI client not available. Install with: pip install openai")
            return None

    def _get_anthropic_client(self):
        """Get Anthropic client."""
        try:
            import anthropic

            return anthropic.Anthropic(api_key=self.model_config["api_key"])
        except ImportError:
            logger.error("Anthropic client not available. Install with: pip install anthropic")
            return None

    def _get_bedrock_client(self):
        """Get AWS Bedrock client."""
        try:
            import boto3

            return boto3.client("bedrock-runtime", region_name=self.model_config["region"])
        except ImportError:
            logger.error("Boto3 not available. Install with: pip install boto3")
            return None

    async def generate_response(self, messages: list, tools_output: Optional[Dict[str, Any]] = None) -> str:
        """Generate response using configured LLM."""
        client = self.get_llm_client()
        if not client:
            return "I'm sorry, I'm having trouble connecting to the language model. Please check your configuration."

        provider = self.model_config["provider"]

        try:
            if provider == "ollama":
                return await self._generate_ollama_response(client, messages, tools_output)
            elif provider == "openai":
                return await self._generate_openai_response(client, messages, tools_output)
            elif provider == "anthropic":
                return await self._generate_anthropic_response(client, messages, tools_output)
            elif provider == "bedrock":
                return await self._generate_bedrock_response(client, messages, tools_output)
        except Exception as e:
            logger.error(f"Error generating response with {provider}: {e}")
            return "I encountered an error while processing your request. Please try again."

    async def _generate_ollama_response(
        self, client, messages: list, tools_output: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate response using Ollama."""
        # Format messages for Ollama
        formatted_messages = []
        for msg in messages:
            formatted_messages.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})

        # Add tools output if available
        if tools_output:
            formatted_messages.append(
                {"role": "system", "content": f"Tool execution results: {json.dumps(tools_output, indent=2)}"}
            )

        response = client.chat(
            model=self.model_config["model"],
            messages=formatted_messages,
            options={"temperature": self.model_config["temperature"], "num_predict": self.model_config["max_tokens"]},
        )

        return response["message"]["content"]

    async def _generate_openai_response(
        self, client, messages: list, tools_output: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate response using OpenAI."""
        # Add tools output if available
        if tools_output:
            messages.append(
                {"role": "system", "content": f"Tool execution results: {json.dumps(tools_output, indent=2)}"}
            )

        response = client.chat.completions.create(
            model=self.model_config["model"],
            messages=messages,
            temperature=self.model_config["temperature"],
            max_tokens=self.model_config["max_tokens"],
        )

        return response.choices[0].message.content

    async def _generate_anthropic_response(
        self, client, messages: list, tools_output: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate response using Anthropic."""
        # Format messages for Anthropic
        system_message = self.agent_config["system_prompt"]
        user_messages = []

        for msg in messages:
            if msg.get("role") == "user":
                user_messages.append(msg["content"])

        # Add tools output if available
        if tools_output:
            user_messages.append(f"Tool execution results: {json.dumps(tools_output, indent=2)}")

        response = client.messages.create(
            model=self.model_config["model"],
            system=system_message,
            messages=[{"role": "user", "content": "\n\n".join(user_messages)}],
            temperature=self.model_config["temperature"],
            max_tokens=self.model_config["max_tokens"],
        )

        return response.content[0].text

    async def _generate_bedrock_response(
        self, client, messages: list, tools_output: Optional[Dict[str, Any]] = None
    ) -> str:
        """Generate response using AWS Bedrock."""
        import json

        # Format for Bedrock
        prompt = self.agent_config["system_prompt"] + "\n\n"
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt += f"{role.title()}: {content}\n\n"

        # Add tools output if available
        if tools_output:
            prompt += f"Tool Results: {json.dumps(tools_output, indent=2)}\n\n"

        prompt += "Assistant: "

        body = {
            "prompt": prompt,
            "max_tokens_to_sample": self.model_config["max_tokens"],
            "temperature": self.model_config["temperature"],
            "stop_sequences": ["\n\nHuman:", "\n\nUser:"],
        }

        response = client.invoke_model(modelId=self.model_config["model"], body=json.dumps(body))

        response_body = json.loads(response["body"].read())
        return response_body["completion"]


def get_local_strands_config() -> LocalStrandsConfig:
    """Get local Strands configuration instance."""
    return LocalStrandsConfig()
