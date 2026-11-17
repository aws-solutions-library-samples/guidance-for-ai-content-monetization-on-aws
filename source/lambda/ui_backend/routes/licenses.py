"""
Licenses Route_Module (``/licenses*``) — license lifecycle (GitLab #3, task 4.2).

Thin per-domain route adapter for the ui_backend thin dispatcher, exposing the uniform
``route(event, method, path)`` entry. It serves the **internal, Cognito-authenticated** plane of
the RSL license API declared contract-first in ``openapi-spec.yaml`` (design.md "§2 Contract chain
→ Link 2"). The `ROUTE_TABLE` entry added in task 4.1 (``Route("licenses", "/licenses", False,
ALL_METHODS)``) routes every ``/licenses*`` path here, so this module owns method+path dispatch.

Operations (contract shapes; ``x-audience`` per Requirement 4.1/4.2):

* ``GET    /licenses``                     -> ``license_service.list_licenses``       (internal)
* ``POST   /licenses``                     -> ``license_service.create_license``      (internal)
* ``GET    /licenses/{id}``                -> ``license_service.get_license``         (internal read of the
  public document surface; the unauthenticated partner-plane variant is served separately)
* ``PUT    /licenses/{id}``                -> ``license_service.update_license``      (internal)
* ``DELETE /licenses/{id}``                -> ``license_service.archive_license``     (internal, SOFT revoke)
* ``POST   /licenses/{id}/renew``          -> ``license_service.renew_license``       (internal)
* ``GET    /licenses/{id}/versions``       -> ``license_service.get_license_versions`` (internal)
* ``POST   /licenses/{id}/validate``       -> ``license_service.validate_license``    (the Cognito
  variant; the contract declares ``PartnerKeyAuth | CognitoAuth``, and the partner-key variant is
  served on the partner plane — both are legitimate)

Reuse, not re-implementation (Requirement 3.2): every operation delegates to the Service_Layer
single home ``services/license_service.py``, which owns **all** ``cm-licenses`` Licenses_Table
access. This module holds **no domain logic and no table access** — it extracts path parameters,
dispatches on method+path, passes the authenticated username through, and returns the service's
response verbatim (the service functions already return full Lambda proxy responses).

Auth: every handler is wrapped with ``@require_cognito_auth`` (the internal-plane gate,
Requirement 4.1/4.5). The decorator resolves the caller from the API Gateway Cognito authorizer
claims (falling back to a JWT bearer token) and injects the resulting identity dict as the
``authenticated_user`` kwarg, returning 401 ``"Invalid or missing Cognito token"`` when neither
resolves — so a handler body only ever runs for an authenticated caller. The service functions
*also* 401 on a missing ``username``; that redundancy is deliberate defence in depth (design.md
A3) so even a misconfigured route cannot write anonymously, and both layers are kept.

Imports are absolute, rooted at ``source/lambda/`` (the Canonical_Import_Root), declared at module
top level — no relative/bare-sibling forms, no ``sys.path`` manipulation, no
``try/except ImportError`` shim. This module imports no other Route_Module.
"""

import json
import logging
from typing import Any, Dict, Optional

from services.license_service import (
    archive_license,
    create_license,
    get_license,
    get_license_versions,
    list_licenses,
    renew_license,
    update_license,
    validate_license,
)
from shared.auth import require_cognito_auth
from shared.utils.response_helpers import create_error_response

logger = logging.getLogger(__name__)


# ── Path prefixes / segments (contract templates use {license_id}) ──
_ROOT_PATH = "/licenses"
_ID_PREFIX = "/licenses/"


def _parse_request_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON request body from the event.

    The dispatcher validates the body up front and rejects invalid JSON with a 400, so by the
    time this route runs the body is either absent or valid. Mirrors the sibling route modules'
    ``request_data = json.loads(body)`` parse and falls back to ``{}`` when absent (the contract
    makes the ``renew`` body optional).
    """
    body = event.get("body", "")
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


def _username(user: Optional[Dict[str, Any]]) -> Optional[str]:
    """Resolve the authenticated caller's identifier for the service's ``username`` argument.

    ``@require_cognito_auth`` supplies the identity dict as ``authenticated_user``: the Cognito
    authorizer path carries ``username`` (``cognito:username``) plus ``user_id`` (``sub``) and
    ``email``, while the JWT fallback path carries ``user_id`` only. Preferring ``username`` and
    falling back to ``user_id``/``email`` keeps the audit stamps (``created_by`` / ``updated_by`` /
    ``revoked_by`` / ``renewed_by``) populated for both credential shapes.
    """
    if not user:
        return None
    return user.get("username") or user.get("user_id") or user.get("email")


# ── Handlers (Cognito-internal) ──


@require_cognito_auth
def handle_list_licenses(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """GET /licenses — list license documents (``status`` filter defaults to ``active``)."""
    user = kwargs["authenticated_user"]
    query_params = event.get("queryStringParameters") or {}
    return list_licenses(query_params, _username(user))


@require_cognito_auth
def handle_create_license(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """POST /licenses — create a license document (201 with a ``Location`` header)."""
    user = kwargs["authenticated_user"]
    return create_license(request_data, _username(user))


@require_cognito_auth
def handle_get_license(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    license_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """GET /licenses/{license_id} — read the latest active license document (404 when revoked)."""
    return get_license(license_id)


@require_cognito_auth
def handle_update_license(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    license_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """PUT /licenses/{license_id} — update terms; the service appends a new version."""
    user = kwargs["authenticated_user"]
    return update_license(license_id, request_data, _username(user))


@require_cognito_auth
def handle_revoke_license(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    license_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """DELETE /licenses/{license_id} — SOFT revoke (new ``revoked`` version, 204, idempotent).

    Delegates to ``archive_license``: the name is retained in the service because
    ``comp_licensing`` re-exports it, but the behavior is the soft revoke the contract documents.
    """
    user = kwargs["authenticated_user"]
    return archive_license(license_id, _username(user))


@require_cognito_auth
def handle_renew_license(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    license_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """POST /licenses/{license_id}/renew — revoked -> active as a new version (409 when active)."""
    user = kwargs["authenticated_user"]
    return renew_license(license_id, request_data, _username(user))


@require_cognito_auth
def handle_license_versions(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    license_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """GET /licenses/{license_id}/versions — full version history for audit."""
    user = kwargs["authenticated_user"]
    return get_license_versions(license_id, _username(user))


@require_cognito_auth
def handle_validate_license(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    license_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """POST /licenses/{license_id}/validate — the Cognito-authenticated validate variant.

    The path parameter is authoritative for which license is validated, so it is stamped onto the
    payload the service consumes (the service reads ``license_id`` from the request data and
    accepts either the CoMP ``aisysuse`` shape or the simplified ``intended_use`` shape).
    """
    payload = {**request_data, "license_id": license_id}
    return validate_license(payload)


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Dispatch a ``/licenses*`` request to the appropriate license handler.

    Mirrors the sibling route modules' explicit path/method routing. The ``{license_id}`` segment
    is parsed from the concrete request path here so the handlers receive it as an argument. An
    unmatched method/path combination returns a deliberate **404** (``"License endpoint not
    found"``), matching the ``routes/connections.py`` precedent and the dispatcher's own
    ``Endpoint not found`` mapping — the route module never 405s.
    """
    headers = event.get("headers") or {}
    request_data = _parse_request_data(event)

    # Collection routes.
    if path == _ROOT_PATH:
        if method == "GET":
            return handle_list_licenses(headers, request_data, event)
        if method == "POST":
            return handle_create_license(headers, request_data, event)
        return create_error_response(404, "License endpoint not found")

    # Parameterized routes: /licenses/{id}, /licenses/{id}/renew, /licenses/{id}/versions,
    # /licenses/{id}/validate.
    if path.startswith(_ID_PREFIX):
        remainder = path[len(_ID_PREFIX):]
        segments = [s for s in remainder.split("/") if s != ""]

        if len(segments) == 1:
            license_id = segments[0]
            if method == "GET":
                return handle_get_license(headers, request_data, event, license_id=license_id)
            if method == "PUT":
                return handle_update_license(headers, request_data, event, license_id=license_id)
            if method == "DELETE":
                return handle_revoke_license(headers, request_data, event, license_id=license_id)

        if len(segments) == 2:
            license_id, action = segments
            if action == "renew" and method == "POST":
                return handle_renew_license(headers, request_data, event, license_id=license_id)
            if action == "versions" and method == "GET":
                return handle_license_versions(headers, request_data, event, license_id=license_id)
            if action == "validate" and method == "POST":
                return handle_validate_license(headers, request_data, event, license_id=license_id)

    return create_error_response(404, "License endpoint not found")
