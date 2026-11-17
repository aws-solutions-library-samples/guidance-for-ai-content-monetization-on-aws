"""Agent runtime client (single home for the bedrock-agentcore invoke).

This service is the Service_Layer single home for invoking the AgentCore agent runtime
(Requirements 1.1, 1.2, 7.3): it owns the boto3 ``bedrock-agentcore`` client, the AgentCore
request-payload envelope build, the ``invoke_agent_runtime`` call, and the reading/parsing
of the returned ``StreamingBody`` response. It is extracted verbatim from the inline
bedrock-agentcore invoke that previously lived in
``ui_backend/handler.py:handle_analytics_agent_chat`` (Requirement 7.3), so that zero direct
``invoke_agent_runtime`` calls remain inline once the dispatcher is flipped (task 11.4).

The client is deliberately **capability-agnostic** (design: "client/invoker of the AgentCore
agent runtime ... not the agent"): the caller supplies the target ``agent_runtime_arn``, the
``tool_name``, the ``client_type``, and the tool ``parameters``. The route module
(``ui_backend/routes/analytics_agent.py``) owns request parsing, the ``ANALYTICS_AGENT_ARN``
config lookup, page-context shaping, and response formatting.

Behavior preservation (Requirement 2): the payload envelope, the ``invoke_agent_runtime``
arguments, the ``StreamingBody`` read/decode, the JSON-vs-string parse of the response
content, and the ``Exception("Failed to call AgentCore: ...")`` wrapping on failure are all
byte-for-byte identical to the pre-refactor inline implementation, so the ui_backend error
responses (which nest this message) are unchanged.

Imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``sys.path`` manipulation (Requirements 5.1,
15.2). The ``boto3`` import is hoisted to module top level (it was a lazy ``import boto3``
inside the inline handler), so an import failure surfaces at load time (Requirement 5.2).
"""

import json
import logging
import os
from typing import Any, Dict

import boto3

logger = logging.getLogger(__name__)

# Default region used when ``AWS_DEFAULT_REGION`` is not set in the environment, matching
# the pre-refactor inline default.
DEFAULT_REGION = "us-west-2"


class AgentRuntimeClient:
    """Client/invoker of the AgentCore agent runtime.

    Owns the boto3 ``bedrock-agentcore`` client, the AgentCore payload build, the
    ``invoke_agent_runtime`` call, and the response-body read/parse. Stateless apart from a
    lazily-created boto3 client that is memoized across invocations within a warm Lambda
    container.
    """

    def __init__(self, region: str = None) -> None:
        """Initialize the client.

        Args:
            region: Optional explicit region. When omitted, resolves from the
                ``AWS_DEFAULT_REGION`` environment variable, falling back to
                ``DEFAULT_REGION`` (``us-west-2``) — matching the inline behavior.
        """
        self._region = region or os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION)
        self._client = None

    def _get_client(self):
        """Return the memoized boto3 ``bedrock-agentcore`` client, creating it on first use."""
        if self._client is None:
            logger.info(f"Initializing bedrock-agentcore client for region: {self._region}")
            self._client = boto3.client("bedrock-agentcore", region_name=self._region)
        return self._client

    def invoke(
        self,
        agent_runtime_arn: str,
        tool_name: str,
        client_type: str,
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Invoke the AgentCore agent runtime and return its parsed response.

        Builds the AgentCore payload envelope, calls ``invoke_agent_runtime``, reads the
        returned ``StreamingBody``, and parses the content as JSON when possible.

        Args:
            agent_runtime_arn: The ARN of the deployed AgentCore agent runtime to invoke.
            tool_name: The AgentCore tool name (e.g. ``"analytics_chat"``).
            client_type: The client type declared in the payload (e.g. ``"internal"``).
            parameters: The tool parameters dict passed through to the agent.

        Returns:
            A dict of the form ``{"output": <parsed JSON | raw string | fallback str>}``,
            identical in shape to the pre-refactor inline ``agentcore_response``.

        Raises:
            Exception: Wraps any invoke/read failure as
                ``Exception(f"Failed to call AgentCore: {error}")``, preserving the inline
                error message the ui_backend response nests.
        """
        agentcore_payload = {
            "tool_name": tool_name,
            "client_type": client_type,
            "parameters": parameters,
        }

        try:
            bedrock_client = self._get_client()

            logger.info(f"Calling AgentCore with ARN: {agent_runtime_arn}")
            logger.info(f"Payload: {json.dumps(agentcore_payload, indent=2)}")

            response = bedrock_client.invoke_agent_runtime(
                agentRuntimeArn=agent_runtime_arn,
                payload=json.dumps(agentcore_payload),
                contentType="application/json",
                accept="application/json",
            )

            logger.info(f"AgentCore response keys: {list(response.keys())}")
            logger.info(f"AgentCore response metadata: {response.get('ResponseMetadata', {})}")

            # Read the response from boto3 AgentCore
            response_body = response.get("response")
            if response_body:
                # boto3 returns a StreamingBody for AgentCore, read it
                response_content = response_body.read().decode("utf-8")
                logger.info(f"AgentCore raw response: {response_content[:200]}...")

                # Try to parse the response content directly as JSON first
                try:
                    # The AgentCore might return JSON directly
                    parsed_response = json.loads(response_content)
                    return {"output": parsed_response}
                except json.JSONDecodeError:
                    # If not JSON, treat as string
                    return {"output": response_content}

            logger.warning("No 'response' field in AgentCore response")
            return {"output": "No response from AgentCore"}

        except Exception as e:
            logger.error(f"AgentCore boto3 error: {str(e)}")
            raise Exception(f"Failed to call AgentCore: {str(e)}")
