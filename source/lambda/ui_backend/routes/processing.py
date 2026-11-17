"""Processing domain route module (thin adapter).

Serves ``GET /processing/status`` for the ui_backend BFF. This module is intentionally
thin (Requirements 1.3, 4.3): it applies Cognito auth via the unchanged
``@require_cognito_auth`` wrapper, delegates the domain logic to
``services.processing_jobs_service.ProcessingJobsService`` (the single home for the
feature-flagged real/mock selection + graceful fallback), and formats the response with
the shared ``create_success_response`` / ``create_error_response`` helpers.

It is a behavior-preserving replacement for the inline
``ui_backend/handler.py:handle_processing_status`` (Requirement 2): the same
``@validate_response("/processing/status", "GET")`` + ``@require_cognito_auth`` decorator
stack, the same success/error responses, and the same 500 ``"Failed to retrieve
processing status"`` error message on failure.

This module is created ahead of the dispatcher flip (task 4.4); the dispatcher continues
to route ``/processing/status`` to the inline handler until that flip lands (Requirement
16.4). All imports are absolute, rooted at the canonical import root ``source/lambda/``
(Requirement 15.1), declared at module top level with no ``sys.path`` manipulation
(Requirements 5.1, 15.2).
"""

import logging
from typing import Any, Dict

from services.processing_jobs_service import ProcessingJobsService
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.response_validator import validate_response

logger = logging.getLogger(__name__)

_service = ProcessingJobsService()


@validate_response("/processing/status", "GET")
@require_cognito_auth
def _handle_processing_status(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle ``GET /processing/status`` by delegating to the processing-jobs service."""
    try:
        user = kwargs["authenticated_user"]
        logger.info(f"Processing status request for user: {user.get('user_id', 'unknown')}")

        processing_status = _service.get_processing_status()
        return create_success_response(processing_status)

    except Exception as e:
        logger.error(f"Error in handle_processing_status: {str(e)}")
        return create_error_response(500, "Failed to retrieve processing status")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Uniform dispatch entry for the processing domain.

    Preserves the pre-refactor call shape of the (now-deleted) inline handler: it forwards
    the query-string parameters as the ``request_data`` argument (the processing status
    endpoint takes no request body).
    """
    headers = event.get("headers") or {}
    query_params = event.get("queryStringParameters") or {}
    return _handle_processing_status(headers, query_params, event)
