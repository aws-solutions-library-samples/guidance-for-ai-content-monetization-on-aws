# gateway_config.py - AgentCore Gateway configuration for analytics agent
import json
import logging
import os
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentCoreGatewayConfig:
    """
    Configuration class for AgentCore Gateway integration with analytics agent.

    This class handles the configuration of AgentCore Gateway to:
    1. Handle publisher UI user requests to analytics agent
    2. Pass Cognito user context through Gateway
    3. Provide IAB API access tools with user context
    4. Manage tool registration and discovery
    """

    def __init__(self, gateway_url: str = None, identity_config: Dict[str, Any] = None, iab_api_base: str = None):
        """
        Initialize the Gateway configuration.

        Args:
            gateway_url: AgentCore Gateway URL
            identity_config: AgentCore Identity configuration
            iab_api_base: IAB API base URL
        """
        self.gateway_url = gateway_url or os.getenv("AGENTCORE_GATEWAY_URL")
        self.identity_config = identity_config or {}
        self.iab_api_base = iab_api_base or os.getenv("IAB_API_BASE_URL", "https://api.iab-framework.example.com")

        # Gateway configuration
        self.gateway_config = self._create_base_gateway_config()

        logger.info("Initialized AgentCore Gateway configuration")

    def _create_base_gateway_config(self) -> Dict[str, Any]:
        """Create base Gateway configuration."""
        return {
            "gateway_url": self.gateway_url,
            "authentication": {"type": "cognito", "identity_integration": True, "user_context_required": True},
            "routing": {
                "analytics_agent": {"path": "/analytics-agent", "methods": ["POST"], "authentication_required": True}
            },
            "tools": {"registration_mode": "automatic", "discovery_enabled": True, "user_context_passing": True},
            "rate_limiting": {"enabled": True, "requests_per_minute": 60, "burst_limit": 10},
        }

    def get_analytics_agent_tools_config(self) -> Dict[str, Any]:
        """
        Configure analytics agent tools for Gateway registration.

        Returns:
            Tools configuration for Gateway registration
        """
        tools_config = {
            "get_usage_analytics": {
                "description": "Retrieve content usage analytics data",
                "parameters": {
                    "date_range": {"type": "string", "default": "last_7_days"},
                    "content_type": {"type": "string", "optional": True},
                    "bot_filter": {"type": "string", "optional": True},
                },
                "requires_user_context": True,
                "iab_api_endpoint": "/analytics/usage",
                "access_level": "publisher",
            },
            "analyze_content_performance": {
                "description": "Analyze performance metrics for specific content items",
                "parameters": {"content_ids": {"type": "array", "items": {"type": "string"}}},
                "requires_user_context": True,
                "iab_api_endpoint": "/analytics/content-performance",
                "access_level": "publisher",
            },
            "generate_insights": {
                "description": "Generate business insights from analytics data",
                "parameters": {
                    "metric": {"type": "string"},
                    "time_period": {"type": "string", "default": "last_30_days"},
                },
                "requires_user_context": True,
                "iab_api_endpoint": "/analytics/insights",
                "access_level": "publisher",
            },
            "create_custom_report": {
                "description": "Create custom analytics reports",
                "parameters": {
                    "report_type": {"type": "string"},
                    "filters": {"type": "object"},
                    "format": {"type": "string", "default": "summary"},
                },
                "requires_user_context": True,
                "iab_api_endpoint": "/analytics/reports",
                "access_level": "publisher",
            },
            "get_revenue_metrics": {
                "description": "Get detailed revenue and monetization metrics",
                "parameters": {
                    "time_period": {"type": "string", "default": "last_30_days"},
                    "breakdown_by": {"type": "string", "default": "content_type"},
                },
                "requires_user_context": True,
                "iab_api_endpoint": "/analytics/revenue",
                "access_level": "publisher",
            },
            "analyze_bot_behavior": {
                "description": "Analyze AI bot usage patterns and behavior",
                "parameters": {
                    "bot_id": {"type": "string", "optional": True},
                    "time_period": {"type": "string", "default": "last_7_days"},
                },
                "requires_user_context": True,
                "iab_api_endpoint": "/analytics/bot-behavior",
                "access_level": "publisher",
            },
            "get_page_context_help": {
                "description": "Provide contextual help based on current page",
                "parameters": {
                    "page_type": {"type": "string"},
                    "current_data": {"type": "object", "optional": True},
                    "user_question": {"type": "string", "optional": True},
                },
                "requires_user_context": False,
                "local_processing": True,
                "access_level": "user",
            },
            "suggest_page_actions": {
                "description": "Suggest relevant actions for current page",
                "parameters": {
                    "page_type": {"type": "string"},
                    "user_intent": {"type": "string"},
                    "current_filters": {"type": "object", "optional": True},
                },
                "requires_user_context": False,
                "local_processing": True,
                "access_level": "user",
            },
        }

        logger.info(f"Configured {len(tools_config)} analytics agent tools")
        return tools_config

    def get_iab_api_gateway_tools_config(self) -> Dict[str, Any]:
        """
        Configure Gateway tools for IAB API access with user context.

        Returns:
            IAB API Gateway tools configuration
        """
        iab_tools_config = {
            "iab_search_content": {
                "description": "Search publisher content via IAB API",
                "endpoint": f"{self.iab_api_base.rstrip('/')}/query",
                "method": "POST",
                "authentication": {"type": "bearer_token", "token_source": "user_context.publisher_key"},
                "user_context_mapping": {
                    "publisher_id": "user_context.cognito_username",
                    "access_level": "user_context.groups",
                },
                "parameters": {
                    "query": {"type": "string", "required": True},
                    "content_type": {"type": "array", "items": {"type": "string"}, "optional": True},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
            },
            "iab_get_content_details": {
                "description": "Get content details via IAB API",
                "endpoint": f"{self.iab_api_base}/content/{{content_id}}",
                "method": "GET",
                "authentication": {"type": "bearer_token", "token_source": "user_context.publisher_key"},
                "user_context_mapping": {"publisher_id": "user_context.cognito_username"},
                "parameters": {"content_id": {"type": "string", "required": True}},
            },
            "iab_get_analytics": {
                "description": "Get analytics data via IAB API",
                "endpoint": f"{self.iab_api_base}/analytics/{{endpoint}}",
                "method": "GET",
                "authentication": {"type": "bearer_token", "token_source": "user_context.publisher_key"},
                "user_context_mapping": {
                    "publisher_id": "user_context.cognito_username",
                    "access_scope": "user_context.groups",
                },
                "parameters": {
                    "endpoint": {"type": "string", "required": True},
                    "filters": {"type": "object", "optional": True},
                },
            },
        }

        logger.info(f"Configured {len(iab_tools_config)} IAB API Gateway tools")
        return iab_tools_config

    def create_user_request_routing_config(self) -> Dict[str, Any]:
        """
        Configure Gateway routing for publisher UI user requests.

        Returns:
            User request routing configuration
        """
        routing_config = {
            "routes": {
                "/analytics-agent/chat": {
                    "method": "POST",
                    "target": "analytics_agent",
                    "authentication_required": True,
                    "user_context_extraction": {
                        "cognito_user_id": "cognito:username",
                        "user_groups": "cognito:groups",
                        "publisher_id": "custom:publisher_id",
                        "access_level": "custom:access_level",
                    },
                    "request_transformation": {
                        "add_user_context": True,
                        "add_page_context": True,
                        "add_session_id": True,
                    },
                    "response_transformation": {"include_observability_links": True, "format": "json"},
                },
                "/analytics-agent/tools/{tool_name}": {
                    "method": "POST",
                    "target": "analytics_agent_tool",
                    "authentication_required": True,
                    "user_context_extraction": {
                        "cognito_user_id": "cognito:username",
                        "publisher_id": "custom:publisher_id",
                    },
                    "parameter_validation": True,
                    "rate_limiting": {"per_user": 30, "per_minute": 100},
                },
            },
            "middleware": [
                "authentication",
                "user_context_extraction",
                "rate_limiting",
                "request_logging",
                "error_handling",
            ],
            "error_handling": {
                "authentication_failed": {"status_code": 401, "message": "Authentication required"},
                "rate_limit_exceeded": {"status_code": 429, "message": "Rate limit exceeded"},
                "tool_not_found": {"status_code": 404, "message": "Tool not available"},
            },
        }

        logger.info("Created user request routing configuration")
        return routing_config

    def create_cognito_user_context_config(self) -> Dict[str, Any]:
        """
        Configure Cognito user context passing through Gateway.

        Returns:
            Cognito user context configuration
        """
        context_config = {
            "user_context_extraction": {
                "source": "cognito_jwt",
                "required_claims": ["cognito:username", "email", "custom:publisher_id"],
                "optional_claims": ["cognito:groups", "custom:access_level", "custom:publisher_name"],
            },
            "context_transformation": {
                "user_id": "cognito:username",
                "email": "email",
                "publisher_id": "custom:publisher_id",
                "publisher_name": "custom:publisher_name",
                "groups": "cognito:groups",
                "access_level": "custom:access_level",
            },
            "context_validation": {
                "required_fields": ["user_id", "publisher_id"],
                "publisher_id_format": "^[a-zA-Z0-9_-]+$",
                "access_levels": ["admin", "editor", "viewer"],
            },
            "context_enrichment": {
                "add_timestamp": True,
                "add_session_id": True,
                "add_request_id": True,
                "lookup_publisher_config": True,
            },
        }

        logger.info("Created Cognito user context configuration")
        return context_config

    def get_iam_policy_for_gateway(self) -> Dict[str, Any]:
        """
        Generate IAM policy for AgentCore Gateway to access required resources.

        Returns:
            IAM policy document
        """
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["cognito-idp:GetUser", "cognito-idp:AdminGetUser", "cognito-idp:AdminListGroupsForUser"],
                    "Resource": "*",
                    "Condition": {"StringEquals": {"cognito-idp:username": "${cognito-identity.amazonaws.com:sub}"}},
                },
                {
                    "Effect": "Allow",
                    "Action": ["execute-api:Invoke"],
                    "Resource": [
                        "arn:aws:execute-api:*:*:*/*/POST/analytics/*",
                        "arn:aws:execute-api:*:*:*/*/GET/analytics/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                    "Resource": "arn:aws:logs:*:*:*",
                },
            ],
        }

        logger.info("Generated IAM policy for AgentCore Gateway")
        return policy

    def create_complete_gateway_config(self) -> Dict[str, Any]:
        """
        Create complete AgentCore Gateway configuration.

        Returns:
            Complete Gateway configuration
        """
        complete_config = {
            "gateway": self.gateway_config,
            "analytics_agent_tools": self.get_analytics_agent_tools_config(),
            "iab_api_tools": self.get_iab_api_gateway_tools_config(),
            "user_request_routing": self.create_user_request_routing_config(),
            "cognito_user_context": self.create_cognito_user_context_config(),
            "iam_policy": self.get_iam_policy_for_gateway(),
            "metadata": {
                "created_at": datetime.now(UTC).isoformat(),
                "gateway_url": self.gateway_url,
                "iab_api_base": self.iab_api_base,
            },
        }

        logger.info("Created complete Gateway configuration")
        return complete_config

    def export_config_to_file(self, output_path: str = "gateway_config.json") -> str:
        """
        Export the complete Gateway configuration to a JSON file.

        Args:
            output_path: Path to save the configuration file

        Returns:
            Path to the saved configuration file
        """
        config = self.create_complete_gateway_config()

        with open(output_path, "w") as f:
            json.dump(config, f, indent=2)

        logger.info(f"Exported Gateway configuration to {output_path}")
        return output_path

    def validate_gateway_config(self) -> Dict[str, Any]:
        """
        Validate the Gateway configuration.

        Returns:
            Validation results
        """
        validation_results = {"valid": True, "errors": [], "warnings": []}

        # Check required configuration
        if not self.gateway_url:
            validation_results["errors"].append("Gateway URL not configured")
            validation_results["valid"] = False

        if not self.iab_api_base:
            validation_results["errors"].append("IAB API base URL not configured")
            validation_results["valid"] = False

        # Check tools configuration
        tools_config = self.get_analytics_agent_tools_config()
        if len(tools_config) == 0:
            validation_results["warnings"].append("No analytics agent tools configured")

        # Check IAB API tools
        iab_tools = self.get_iab_api_gateway_tools_config()
        if len(iab_tools) == 0:
            validation_results["warnings"].append("No IAB API tools configured")

        logger.info(f"Gateway configuration validation: {'PASSED' if validation_results['valid'] else 'FAILED'}")
        return validation_results


# Factory function for easy configuration creation
def create_gateway_config(
    gateway_url: str = None, identity_config: Dict[str, Any] = None, iab_api_base: str = None
) -> AgentCoreGatewayConfig:
    """
    Factory function to create an AgentCore Gateway configuration.

    Args:
        gateway_url: AgentCore Gateway URL
        identity_config: AgentCore Identity configuration
        iab_api_base: IAB API base URL

    Returns:
        Configured AgentCoreGatewayConfig instance
    """
    return AgentCoreGatewayConfig(gateway_url, identity_config, iab_api_base)


# CLI utility for testing configuration
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Configure AgentCore Gateway for analytics agent")
    parser.add_argument("--gateway-url", help="AgentCore Gateway URL")
    parser.add_argument("--iab-api-base", help="IAB API base URL")
    parser.add_argument("--output", default="gateway_config.json", help="Output file path")
    parser.add_argument("--validate", action="store_true", help="Validate configuration only")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Create configuration
    config = create_gateway_config(args.gateway_url, None, args.iab_api_base)

    if args.validate:
        # Validation only
        validation = config.validate_gateway_config()
        print(f"Configuration validation: {'PASSED' if validation['valid'] else 'FAILED'}")
        if validation["errors"]:
            print("Errors:", validation["errors"])
        if validation["warnings"]:
            print("Warnings:", validation["warnings"])
    else:
        # Export full configuration
        output_path = config.export_config_to_file(args.output)
        print(f"Configuration exported to: {output_path}")

        # Also validate
        validation = config.validate_gateway_config()
        print(f"Configuration validation: {'PASSED' if validation['valid'] else 'FAILED'}")
