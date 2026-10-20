"""
Bot-control Route_Module (``/bot-control/*``).

Thin per-domain route adapter for the ui_backend thin dispatcher (Requirements 4.1, 4.2,
4.3). It exposes the uniform ``route(event, method, path)`` entry and delegates the
domain's business logic and all data-store access to ``services.bot_control_service``
(Requirement 1.2) — it performs no bot-control domain logic and no DynamoDB access itself.
The bot-control domain's logic now lives directly in the service (the former bot-control API
root module was deleted); this route simply forwards to it.

Authentication note: the bot-control path is not wrapped by ``@require_cognito_auth`` in
the pre-refactor monolith (``handle_bot_control_endpoints`` delegated straight to the
bot-control single source of truth, which performs its own request validation). To preserve
behavior exactly (Requirement 2.5), this module does not add a Cognito auth wrapper.

Imports are absolute, rooted at ``source/lambda/`` (the Canonical_Import_Root), declared at
module top level, with no relative/bare-sibling forms and no ``sys.path`` manipulation
(Requirements 5.1, 15.1, 15.2). This module imports no other Route_Module (Requirement 4.4).
"""

from typing import Any, Dict

from services.bot_control_service import BotControlService

# Module-level service instance; the service is stateless (it holds no per-request state),
# so a single shared instance is reused across invocations.
_service = BotControlService()


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Dispatch a ``/bot-control/*`` request to the bot-control service.

    Args:
        event: The API Gateway proxy event.
        method: The HTTP method (accepted for the uniform route signature; the bot-control
            service resolves method + path internally from the event's own
            ``httpMethod``/``path``).
        path: The request path (accepted for the uniform route signature).

    Returns:
        The API Gateway proxy response produced by the bot-control service.
    """
    return _service.handle_request(event, None)
