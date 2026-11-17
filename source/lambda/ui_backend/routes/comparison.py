"""Comparison-run route module (thin adapter) — spec ``scrape-vs-api-comparison``, design §3a.

Serves the two Cognito-only operations of the Test stage's scrape-vs-licensed-Markdown comparison:

* ``POST /test/comparison-runs`` → ``services.comparison_service.submit``
* ``GET  /test/comparison-runs/{run_id}`` → ``services.comparison_service.poll``

Thin by design: auth through the shared ``@require_cognito_auth`` wrapper, response validation
against the contract, and method + path-shape dispatch. The guardrails, the measurement and the
record resolution live in the service.
"""

import logging
import re
from typing import Any, Dict

from services import comparison_service
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response
from shared.utils.response_validator import validate_response

logger = logging.getLogger(__name__)

COLLECTION_PATH = "/test/comparison-runs"
_RUN_PATH_RE = re.compile(r"^/test/comparison-runs/(?P<run_id>[^/]+)/?$")


@validate_response("/test/comparison-runs", "POST")
@require_cognito_auth
def _handle_submit(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    try:
        user = kwargs["authenticated_user"]
        return comparison_service.submit(user, request_data, event)
    except Exception as exc:  # the service maps every expected failure to a 4xx itself
        logger.error(f"Error in comparison submit: {exc}")
        return create_error_response(500, "Failed to start the comparison run", event=event)


@validate_response("/test/comparison-runs/{run_id}", "GET")
@require_cognito_auth
def _handle_poll(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    try:
        return comparison_service.poll(request_data["run_id"], event)
    except Exception as exc:
        logger.error(f"Error in comparison poll: {exc}")
        return create_error_response(500, "Failed to read the comparison run", event=event)


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Uniform dispatch entry for the comparison domain."""
    headers = event.get("headers") or {}
    if method == "POST" and path.rstrip("/") == COLLECTION_PATH:
        import json

        body = event.get("body") or ""
        try:
            request_data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            request_data = {}
        return _handle_submit(headers, request_data, event)

    match = _RUN_PATH_RE.match(path) if method == "GET" else None
    if match:
        return _handle_poll(headers, {"run_id": match.group("run_id")}, event)

    return create_error_response(404, f"Route not found: {method} {path}", event=event)
