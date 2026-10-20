"""Thin Cognito-authenticated route adapter for managed llms.txt."""

import json
import logging
import re
from typing import Any, Dict

from services.llms_txt_service import (
    ConflictError,
    LlmsTxtService,
    NotFoundError,
    PreconditionFailedError,
)
from shared.utils.response_helpers import create_error_response, create_success_response

logger = logging.getLogger(__name__)
_service = LlmsTxtService()
_RUN_PATH = re.compile(r"^/content/llms-txt/runs/([^/]+)$")
_DRAFT_PATH = re.compile(r"^/content/llms-txt/drafts/([^/]+)$")
_PUBLISH_PATH = re.compile(r"^/content/llms-txt/drafts/([^/]+)/publish$")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    actor = _actor(event)
    try:
        if method == "GET" and path == "/content/llms-txt":
            return create_success_response(_service.get_management_state())
        if method == "POST" and path == "/content/llms-txt/runs":
            return create_success_response(_service.start_run(actor), 202)

        run_match = _RUN_PATH.match(path)
        if method == "GET" and run_match:
            return create_success_response(_service.get_run(run_match.group(1)))

        draft_match = _DRAFT_PATH.match(path)
        if method == "PUT" and draft_match:
            request = _request_json(event)
            return create_success_response(
                _service.update_draft(
                    draft_match.group(1),
                    request.get("content"),
                    _if_match(event),
                    actor,
                )
            )

        publish_match = _PUBLISH_PATH.match(path)
        if method == "POST" and publish_match:
            return create_success_response(
                _service.publish_draft(publish_match.group(1), _if_match(event), actor)
            )
        return create_error_response(404, "Endpoint not found")
    except NotFoundError as exc:
        return create_error_response(404, str(exc))
    except ConflictError as exc:
        return create_error_response(409, str(exc))
    except PreconditionFailedError as exc:
        return create_error_response(412, str(exc))
    except Exception:
        logger.exception("Unexpected llms.txt management failure")
        return create_error_response(500, "llms.txt management request failed")


def _actor(event: Dict[str, Any]) -> str:
    claims = event.get("requestContext", {}).get("authorizer", {}).get("claims", {})
    return str(claims.get("sub") or claims.get("email") or "cognito-user")


def _request_json(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    return json.loads(body)


def _if_match(event: Dict[str, Any]) -> str:
    headers = event.get("headers") or {}
    value = headers.get("If-Match") or headers.get("if-match")
    if not value:
        raise PreconditionFailedError("If-Match header is required")
    return str(value)
