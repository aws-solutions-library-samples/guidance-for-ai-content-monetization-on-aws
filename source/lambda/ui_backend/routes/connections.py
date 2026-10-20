"""
Connections Route_Module (``/connections*``) — integration-adapters (GitLab #35, task 9.1).

Thin per-domain route adapter for the ui_backend thin dispatcher, exposing the uniform
``route(event, method, path)`` entry. It serves the internal, Cognito-authenticated
Connections API defined contract-first in ``openapi-spec.yaml`` (design.md "Connections API
(internal, Cognito)"). All ``/connections*`` operations are ``x-audience: internal`` and are
NOT on the partner surface (Requirement 7.1).

Responsibilities (conforming to the contract shapes):

* ``GET  /connections/adapters``       -> adapter catalog via ``Registry.list_adapters()``
* ``GET  /connections``                -> list active Connections (non-secret config + secret_ref)
* ``POST /connections``                -> activate a Connection (secret -> Secrets Manager, store ARN)
* ``PUT  /connections/{id}``           -> edit config / rotate secret (secret -> Secrets Manager)
* ``DELETE /connections/{id}``         -> deactivate
* ``POST /connections/{id}/test``      -> invoke adapter ``test()`` (fail-informative)
* ``GET  /connections/{id}/status``    -> invoke adapter ``status()``
* ``GET  /connections/mirrors/{domain}`` -> read-only mirrored external config

Secret handling (Requirements 6.4, 7.2 — enforced by construction): a UI-entered ``secret`` is a
write-only request field. The handler writes it to AWS Secrets Manager and persists only the
resulting ``secret_ref`` (ARN/name) on the :class:`~shared.utils.models.Connection` record — which
has no secret-value field, so a plaintext credential can neither be persisted nor returned. Reads
reconstruct a ``Connection`` (again, no secret-value field), so a response can never echo a secret.

The Registry is constructed with the real ``ConnectionsStore`` injected and a ``SecretResolver``
backed by ``secretsmanager`` (both general ``boto3`` — not adapter-owned / outside the task-14
guardrail scope). ``secretsmanager`` is only touched here for secret persistence/resolution, never
for a real domain operation.

Auth: every handler is wrapped with ``@require_cognito_auth`` (Cognito-internal gate, Requirement
7.1). Imports are absolute, rooted at ``source/lambda/`` (the Canonical_Import_Root), declared at
module top level, no relative/bare-sibling forms and no ``sys.path`` manipulation. This module
imports no other Route_Module.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import boto3
from integrations.registry import Registry
from services.connections_store import ConnectionNotFoundError, ConnectionsStore
from shared.auth import require_cognito_auth
from shared.utils.models import Connection
from shared.utils.response_helpers import create_error_response, create_success_response

logger = logging.getLogger(__name__)


# ── Path prefixes / segments (contract templates use {id} and {domain}) ──
_ADAPTERS_PATH = "/connections/adapters"
_MIRRORS_PREFIX = "/connections/mirrors/"
_ROOT_PATH = "/connections"


def _parse_request_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the JSON request body from the event.

    The dispatcher validates the body up front and rejects invalid JSON with a 400, so by the
    time this route runs the body is either absent or valid. Mirrors the sibling route modules'
    ``request_data = json.loads(body)`` parse and falls back to ``{}`` when absent.
    """
    body = event.get("body", "")
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}
    return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store() -> ConnectionsStore:
    """Return a ``cm-connections`` store instance (single home for connection persistence)."""
    return ConnectionsStore()


def _secretsmanager_client():
    """Lazily build a ``secretsmanager`` client (general boto3, not adapter-owned)."""
    region = os.environ.get("AWS_REGION")
    if region:
        return boto3.client("secretsmanager", region_name=region)
    return boto3.client("secretsmanager")


def _default_secret_resolver(secret_ref: str) -> str:
    """Resolve a Secrets Manager reference to its value (injected into the Registry)."""
    client = _secretsmanager_client()
    response = client.get_secret_value(SecretId=secret_ref)
    return response.get("SecretString", "")


def _registry() -> Registry:
    """Build a Registry wired with the real store + a secretsmanager-backed resolver."""
    return Registry(store=_store(), secret_resolver=_default_secret_resolver)


def _write_secret(connection_id: str, secret_value: str) -> str:
    """Persist ``secret_value`` in Secrets Manager and return the resulting reference (ARN/name).

    Creates a new secret named for the Connection, or updates it if it already exists (rotation).
    The secret value never leaves this function on the record path — only the returned reference is
    persisted (Requirements 6.4, 7.2).
    """
    client = _secretsmanager_client()
    secret_name = f"cm-connections/{connection_id}"
    try:
        response = client.create_secret(Name=secret_name, SecretString=secret_value)
        return response.get("ARN", secret_name)
    except client.exceptions.ResourceExistsException:
        # Rotation: update the existing secret's value in place.
        response = client.put_secret_value(SecretId=secret_name, SecretString=secret_value)
        return response.get("ARN", secret_name)


def _connection_public_dict(connection: Connection) -> Dict[str, Any]:
    """Serialize a Connection for an API response — non-secret config + secret_ref only.

    :class:`Connection` has no secret-value field, so this can never include a secret
    (Requirement 7.2).
    """
    return {
        "connection_id": connection.connection_id,
        "domain": connection.domain,
        "adapter": connection.adapter,
        "brand": connection.brand,
        "config": dict(connection.config or {}),
        "secret_ref": connection.secret_ref,
        "active": connection.active,
        "created_at": connection.created_at,
        "updated_at": connection.updated_at,
    }


# ── Handlers (Cognito-internal) ──


@require_cognito_auth
def handle_list_adapters(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """GET /connections/adapters — adapter catalog grouped by domain (Requirement 6.1)."""
    try:
        catalog = _registry().list_adapters()
        adapters = {
            domain: [
                {
                    "domain": d.domain,
                    "adapter": d.adapter,
                    "available": d.available,
                    "has_active_connection": d.has_active_connection,
                }
                for d in descriptors
            ]
            for domain, descriptors in catalog.items()
        }
        return create_success_response({"adapters": adapters})
    except Exception as e:
        logger.error(f"Error in handle_list_adapters: {str(e)}")
        return create_error_response(500, "Failed to list adapters")


@require_cognito_auth
def handle_list_connections(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """GET /connections — list active Connections (non-secret config + secret_ref only)."""
    try:
        query_params = event.get("queryStringParameters") or {}
        domain = query_params.get("domain")
        connections = _store().list(domain=domain)
        return create_success_response(
            {"connections": [_connection_public_dict(c) for c in connections]}
        )
    except Exception as e:
        logger.error(f"Error in handle_list_connections: {str(e)}")
        return create_error_response(500, "Failed to list connections")


@require_cognito_auth
def handle_create_connection(
    headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
) -> Dict[str, Any]:
    """POST /connections — activate a Connection.

    A supplied ``secret`` (write-only) is forwarded to Secrets Manager and only the resulting
    ``secret_ref`` is persisted; the response never echoes the secret (Requirements 6.4, 7.2).
    """
    try:
        if "domain" not in request_data:
            return create_error_response(400, "domain is required")
        if "adapter" not in request_data:
            return create_error_response(400, "adapter is required")

        connection_id = request_data.get("connection_id") or f"conn_{uuid.uuid4().hex[:12]}"
        secret_ref = ""
        secret_value = request_data.get("secret")
        if secret_value:
            secret_ref = _write_secret(connection_id, secret_value)

        connection = Connection(
            connection_id=connection_id,
            domain=request_data["domain"],
            adapter=request_data["adapter"],
            brand=request_data.get("brand"),
            config=dict(request_data.get("config") or {}),
            secret_ref=secret_ref,
            active=bool(request_data.get("active", True)),
        )
        stored = _store().put(connection)
        return create_success_response(
            {"connection": _connection_public_dict(stored)}, status_code=201
        )
    except Exception as e:
        logger.error(f"Error in handle_create_connection: {str(e)}")
        return create_error_response(500, "Failed to activate connection")


@require_cognito_auth
def handle_update_connection(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    connection_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """PUT /connections/{id} — edit config / rotate secret (secret -> Secrets Manager)."""
    try:
        fields: Dict[str, Any] = {}
        for key in ("domain", "adapter", "brand", "config", "active"):
            if key in request_data:
                fields[key] = request_data[key]

        secret_value = request_data.get("secret")
        if secret_value:
            fields["secret_ref"] = _write_secret(connection_id, secret_value)

        updated = _store().update(connection_id, fields)
        return create_success_response({"connection": _connection_public_dict(updated)})
    except ConnectionNotFoundError:
        return create_error_response(404, "Connection not found")
    except Exception as e:
        logger.error(f"Error in handle_update_connection: {str(e)}")
        return create_error_response(500, "Failed to update connection")


@require_cognito_auth
def handle_delete_connection(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    connection_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """DELETE /connections/{id} — deactivate a Connection."""
    try:
        _store().delete(connection_id)
        return create_success_response({"deleted": True, "connection_id": connection_id})
    except ConnectionNotFoundError:
        return create_error_response(404, "Connection not found")
    except Exception as e:
        logger.error(f"Error in handle_delete_connection: {str(e)}")
        return create_error_response(500, "Failed to delete connection")


@require_cognito_auth
def handle_test_connection(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    connection_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """POST /connections/{id}/test — invoke the adapter's safe, read-only ``test()``.

    Fail-informative (Requirements 6.5, 7.3): a failed check returns ``{ok: False, message}`` with
    a 200 rather than raising past the API; the UI surfaces the diagnostic.
    """
    try:
        connection = _store().get(connection_id)
        if connection is None:
            return create_error_response(404, "Connection not found")

        adapter = _registry().resolve(connection.domain, connection.brand)
        if adapter is None:
            return create_success_response(
                {"ok": False, "message": "Connection is not wired (no active adapter resolved)"}
            )

        result = adapter.test()
        return create_success_response(
            {"ok": bool(getattr(result, "ok", False)), "message": getattr(result, "message", "")}
        )
    except Exception as e:
        # test() itself should be fail-informative; guard anyway so the API never 500s on a
        # connectivity/auth diagnostic.
        logger.error(f"Error in handle_test_connection: {str(e)}")
        return create_success_response({"ok": False, "message": f"Test failed: {str(e)}"})


@require_cognito_auth
def handle_connection_status(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    connection_id: str,
    **kwargs,
) -> Dict[str, Any]:
    """GET /connections/{id}/status — invoke the adapter's ``status()`` health check."""
    try:
        connection = _store().get(connection_id)
        if connection is None:
            return create_error_response(404, "Connection not found")

        adapter = _registry().resolve(connection.domain, connection.brand)
        if adapter is None:
            return create_success_response(
                {"healthy": False, "detail": "Connection is not wired (no active adapter resolved)"}
            )

        status = adapter.status()
        return create_success_response(
            {
                "healthy": bool(getattr(status, "healthy", False)),
                "detail": getattr(status, "detail", ""),
            }
        )
    except Exception as e:
        logger.error(f"Error in handle_connection_status: {str(e)}")
        return create_error_response(500, "Failed to get connection status")


@require_cognito_auth
def handle_get_mirror(
    headers: Dict[str, str],
    request_data: Dict[str, Any],
    event: Dict[str, Any],
    domain: str,
    **kwargs,
) -> Dict[str, Any]:
    """GET /connections/mirrors/{domain} — read-only mirrored external config (Requirement 6.9).

    Mirrored config is authored on the external side (e.g. the AWS Marketplace listing config
    mirror) and surfaced read-only here; there is no edit affordance in this API (federation rule).
    The concrete mirror source is owned by the marketplace vertical (analysis §9.8); until it lands
    this returns an empty mirror for a known domain and 404 for an unknown one.
    """
    try:
        known_domains = {"content", "marketplace", "telemetry", "access"}
        if domain not in known_domains:
            return create_error_response(404, "No mirror for the domain")
        # Read-only: no mirror source wired yet (owned by the marketplace vertical). Return an
        # empty, non-editable mirror so the UI can render a read-only (empty) panel.
        return create_success_response({"domain": domain, "mirror": {}})
    except Exception as e:
        logger.error(f"Error in handle_get_mirror: {str(e)}")
        return create_error_response(500, "Failed to read mirror")


def route(event: Dict[str, Any], method: str, path: str) -> Dict[str, Any]:
    """Dispatch a ``/connections*`` request to the appropriate Connections handler.

    Mirrors the sibling route modules' explicit path/method routing. Path-parameter segments
    (``{id}``, ``{domain}``) are parsed from the concrete request path here so the handlers
    receive them as arguments.
    """
    headers = event.get("headers") or {}
    request_data = _parse_request_data(event)

    # Static / catalog routes first.
    if path == _ADAPTERS_PATH and method == "GET":
        return handle_list_adapters(headers, request_data, event)

    if path == _ROOT_PATH and method == "GET":
        return handle_list_connections(headers, request_data, event)

    if path == _ROOT_PATH and method == "POST":
        return handle_create_connection(headers, request_data, event)

    # Mirror read-only route: /connections/mirrors/{domain}
    if path.startswith(_MIRRORS_PREFIX) and method == "GET":
        domain = path[len(_MIRRORS_PREFIX):]
        if domain:
            return handle_get_mirror(headers, request_data, event, domain=domain)
        return create_error_response(404, "Connections endpoint not found")

    # Parameterized routes: /connections/{id}, /connections/{id}/test, /connections/{id}/status
    if path.startswith("/connections/"):
        remainder = path[len("/connections/"):]
        segments = [s for s in remainder.split("/") if s != ""]

        if len(segments) == 2 and segments[1] == "test" and method == "POST":
            return handle_test_connection(headers, request_data, event, connection_id=segments[0])

        if len(segments) == 2 and segments[1] == "status" and method == "GET":
            return handle_connection_status(headers, request_data, event, connection_id=segments[0])

        if len(segments) == 1:
            if method == "PUT":
                return handle_update_connection(
                    headers, request_data, event, connection_id=segments[0]
                )
            if method == "DELETE":
                return handle_delete_connection(
                    headers, request_data, event, connection_id=segments[0]
                )

    return create_error_response(404, "Connections endpoint not found")
