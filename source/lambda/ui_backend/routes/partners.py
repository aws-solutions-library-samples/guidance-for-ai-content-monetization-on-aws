"""Partners domain Route_Module (``/partners`` and everything beneath it).

Thin per-domain adapter for the ui_backend partners domain. Exposes the uniform
``route(event, method, path)`` entry and delegates every read and write to
``services.partners_service.PartnersService`` — the Service_Layer single home for the
partners domain. Only event parsing, auth, status mapping and response formatting live here.

Operations (the ``/partners`` contract in ``openapi-spec.yaml``):

- ``GET    /partners``                           -> ``get_partners``          200
- ``POST   /partners``                           -> ``create_partner``        201 (key value shown once)
- ``GET    /partners/{id}``                      -> ``get_partner``           200 (``PartnerDetail``)
- ``PUT    /partners/{id}``                      -> ``update_partner``        200
- ``DELETE /partners/{id}``                      -> ``delete_partner``        200 (soft delete; keys removed)
- ``POST   /partners/{id}/keys``                 -> ``rotate_key``            201 (key value shown once)
- ``DELETE /partners/{id}/keys/{key_id}``        -> ``retire_key``            204

Service exceptions map to the contract's error codes: ``PartnerNotFoundError`` -> 404,
``NoPartnerFieldsError`` / ``PartnerValidationError`` -> 400 ``validation_error``,
``PartnerConflictError`` -> 409 ``conflict``. Every operation is Cognito-authenticated via
``@require_cognito_auth``; any other method+path under the prefix returns 404 without
invoking the service. Imports are absolute from the Canonical_Import_Root ``source/lambda/``.
"""

import json
import logging
from typing import Any, Dict

from services.partners_service import (
    NoPartnerFieldsError,
    PartnerConflictError,
    PartnerNotFoundError,
    PartnersService,
    PartnerValidationError,
)
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.response_validator import validate_response

logger = logging.getLogger(__name__)


def _parse_request_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON request body from the event.

    The dispatcher validates the body up front and rejects invalid JSON with a 400, so by
    the time this route runs the body is either absent or valid. Mirrors the monolith's
    ``request_data = json.loads(body)`` parse and falls back to ``{}`` when absent.
    """
    body = event.get("body", "")
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


@validate_response("/partners", "GET")
@require_cognito_auth
def handle_get_partners(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """Handle ``GET /partners`` by delegating the read to the partners service."""
    try:
        user = kwargs["authenticated_user"]
        publisher_id = user.get("publisher_id", "default_publisher")

        partners_data = PartnersService().get_partners(publisher_id)
        return create_success_response(partners_data)

    except Exception as e:
        logger.error(f"Error in handle_get_partners: {str(e)}")
        return create_error_response(500, "Failed to retrieve partner data")


def _service_call(operation, *args, success_status: int = 200, label: str = "partner operation") -> Dict[str, Any]:
    """Run one service operation and map its exceptions to the contract's error responses.

    The 404 / 400 / 500 bodies for a partner id keep the wording the pre-lifecycle ``PUT``
    handler produced (the golden characterization tests pin it); the new 409 carries the
    contract's ``conflict`` code.
    """
    partner_id = args[0] if args and isinstance(args[0], str) else None
    try:
        result = operation(*args)
    except PartnerNotFoundError as exc:
        return create_error_response(404, f"Partner {partner_id or exc} not found")
    except NoPartnerFieldsError:
        return create_error_response(400, "No valid fields to update")
    except PartnerValidationError as exc:
        return create_error_response(400, str(exc), error_code="validation_error")
    except PartnerConflictError as exc:
        return create_error_response(409, str(exc), error_code="conflict")
    except Exception as exc:  # noqa: BLE001 - the route is the boundary that turns faults into 500s
        logger.error(f"Error in {label}: {exc}")
        return create_error_response(500, f"Failed to {label}: {exc}")
    if success_status == 204:
        return {"statusCode": 204, "headers": create_success_response({})["headers"], "body": ""}
    return create_success_response(result, status_code=success_status)


def _path_parts(event: Dict[str, Any]) -> list:
    return [p for p in (event.get("path") or "").split("/") if p]


def _partner_id(event: Dict[str, Any]) -> str:
    """``/partners/{partner_id}[...]`` -> partner_id, or raise for an invalid path."""
    parts = _path_parts(event)
    if len(parts) < 2 or parts[0] != "partners" or not parts[1]:
        raise ValueError("Invalid partner ID in path")
    return parts[1]


def _key_id(event: Dict[str, Any]) -> str:
    """``/partners/{partner_id}/keys/{key_id}`` -> key_id, or raise for an invalid path."""
    parts = _path_parts(event)
    if len(parts) != 4 or parts[2] != "keys" or not parts[3]:
        raise ValueError("Invalid key ID in path")
    return parts[3]


def _with_path_ids(event: Dict[str, Any], operation, *, needs_key: bool = False, **call_kwargs) -> Dict[str, Any]:
    """Resolve path ids then run the service call; a malformed path is a 400 before any service work."""
    try:
        ids = [_partner_id(event)] + ([_key_id(event)] if needs_key else [])
    except ValueError as exc:
        return create_error_response(400, str(exc), error_code="validation_error")
    return _service_call(operation, *ids, **call_kwargs)


@require_cognito_auth
def handle_create_partner(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """``POST /partners``: create a partner and (for a direct identity) its first key."""
    return _service_call(PartnersService().create_partner, request_data, success_status=201, label="create partner")


@require_cognito_auth
def handle_get_partner(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """``GET /partners/{partner_id}``: the partner with its keys, tier limits and month usage."""
    return _with_path_ids(event, PartnersService().get_partner, label="get partner")


@require_cognito_auth
def handle_update_partner(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """``PUT /partners/{partner_id}``: update metadata; tier/status changes carry key side effects."""
    try:
        partner_id = _partner_id(event)
    except ValueError as exc:
        return create_error_response(400, str(exc))
    response = _service_call(PartnersService().update_partner, partner_id, request_data, label="update partner")
    if response["statusCode"] == 200:
        logger.info(f"Successfully updated partner {partner_id}")
    return response


@require_cognito_auth
def handle_delete_partner(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """``DELETE /partners/{partner_id}``: soft-delete the partner and remove its keys."""
    return _with_path_ids(event, PartnersService().delete_partner, label="delete partner")


@require_cognito_auth
def handle_rotate_key(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """``POST /partners/{partner_id}/keys``: provision a new key and disable the previous ones."""
    return _with_path_ids(event, PartnersService().rotate_key, success_status=201, label="rotate key")


@require_cognito_auth
def handle_retire_key(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """``DELETE /partners/{partner_id}/keys/{key_id}``: permanently delete one key."""
    return _with_path_ids(event, PartnersService().retire_key, needs_key=True, success_status=204, label="retire key")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Uniform dispatch entry for the partners domain: method + path shape -> handler.

    Any method+path under the prefix that is not one of the seven contract operations
    returns 404 without invoking the service.
    """
    headers = event.get("headers") or {}
    request_data = _parse_request_data(event)
    parts = [p for p in path.split("/") if p]  # ["partners", id?, "keys"?, key_id?]

    if parts == ["partners"]:
        if method == "GET":
            return handle_get_partners(headers, request_data, event)
        if method == "POST":
            return handle_create_partner(headers, request_data, event)
    elif len(parts) == 2 and parts[0] == "partners":
        if method == "GET":
            return handle_get_partner(headers, request_data, event)
        if method == "PUT":
            return handle_update_partner(headers, request_data, event)
        if method == "DELETE":
            return handle_delete_partner(headers, request_data, event)
    elif len(parts) == 3 and parts[0] == "partners" and parts[2] == "keys" and method == "POST":
        return handle_rotate_key(headers, request_data, event)
    elif len(parts) == 4 and parts[0] == "partners" and parts[2] == "keys" and method == "DELETE":
        return handle_retire_key(headers, request_data, event)

    return create_error_response(404, "Endpoint not found")
