"""
Consolidated response helper functions

This module provides standardized response creation functions with consistent
CORS headers and response formats across all Lambda endpoints.

CORS origin policy (TM-02, security-hardening-phase2)
-----------------------------------------------------
``Access-Control-Allow-Origin`` is NEVER a literal ``*`` for credentialed
responses. The allowed origin is resolved at runtime, in order of precedence:

  1. ``ALLOWED_ORIGINS`` env var (comma-separated) — the explicit, highest-
     precedence source. A production deployer sets it to the portal's custom
     origin(s); ``.env.example`` ships it empty for the demo path.
  2. SSM parameter ``/cm/ui/origin`` — written by ``CMUIStack`` on deploy with the
     deployed CloudFront portal origin; read at invoke time and cached per
     container, so the demo gets an exact match with zero manual config.

``_cors_origin(event, *, public=False)`` applies the policy:
  * request ``Origin`` matches the allow-list        -> echo that origin.
  * no match but the allow-list resolved             -> the configured default
                                                        (first allow-list entry).
  * neither source resolves                           -> ``None`` (caller omits the
                                                        CORS origin header; never ``*``).
  * ``public=True``                                   -> an explicit, commented opt-in
                                                        for genuinely public,
                                                        uncredentialed discovery
                                                        endpoints; still config-driven,
                                                        never a bare ``*`` unless the
                                                        deployer explicitly sets it.
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# SSM parameter the UI stack writes with the deployed portal origin (see CMUIStack).
UI_ORIGIN_SSM_PARAMETER = os.environ.get("UI_ORIGIN_SSM_PARAMETER", "/cm/ui/origin")

# Static CORS values that are unchanged by TM-02 (Requirement 2.6).
_CORS_ALLOW_HEADERS = "Content-Type,X-Amz-Date,Authorization,X-Api-Key,If-Match"
_CORS_ALLOW_METHODS = "GET,POST,PUT,DELETE,OPTIONS"
_CORS_MAX_AGE = "86400"  # 24 hours

# Per-container cache of the SSM-resolved origin. ``fetched`` records that a lookup has run
# (so a NEGATIVE result — None — is cached for the TTL too, not re-attempted every request);
# ``value`` is the resolved origin or None.
_SSM_ORIGIN_CACHE_TTL = 300  # seconds
_ssm_origin_cache: Dict[str, Any] = {"value": None, "fetched": False, "fetched_at": 0.0}

# Bounded SSM call so the CORS resolver can never hang the response path (TM-02). A missing
# AWS environment (unit tests) fails fast instead of blocking on a TCP/TLS handshake.
_SSM_CONNECT_TIMEOUT = 1.0  # seconds
_SSM_READ_TIMEOUT = 1.0  # seconds


def _allowed_origins_from_env() -> List[str]:
    """Parse the comma-separated ``ALLOWED_ORIGINS`` env var into a clean list."""
    raw = os.environ.get("ALLOWED_ORIGINS", "") or ""
    return [o.strip() for o in raw.split(",") if o.strip()]


def _origin_from_ssm() -> Optional[str]:
    """Resolve the portal origin from SSM ``/cm/ui/origin``, cached per container.

    Returns None on any failure (parameter missing, no permission, SSM unavailable, or no AWS
    environment at all) so the caller fails safe by omitting the CORS origin rather than emitting
    ``*``. The result — including a None — is cached for the TTL, so a failure is NOT re-attempted
    on every request. The call is bounded by a short connect/read timeout with retries disabled, so
    it can never block the response path (e.g. a unit test with no AWS endpoint reachable).
    """
    now = time.time()
    if _ssm_origin_cache["fetched"] and (now - _ssm_origin_cache["fetched_at"]) < _SSM_ORIGIN_CACHE_TTL:
        return _ssm_origin_cache["value"]

    value: Optional[str] = None
    # No region configured ⇒ not a real AWS runtime (typical for unit tests). Skip the network
    # call entirely rather than letting boto3 fail slowly.
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region:
        try:
            import boto3  # local import: keeps module import cheap and test-friendly
            from botocore.config import Config

            ssm = boto3.client(
                "ssm",
                config=Config(
                    connect_timeout=_SSM_CONNECT_TIMEOUT,
                    read_timeout=_SSM_READ_TIMEOUT,
                    retries={"max_attempts": 0},
                ),
            )
            resp = ssm.get_parameter(Name=UI_ORIGIN_SSM_PARAMETER)
            value = resp["Parameter"]["Value"].strip() or None
        except Exception as exc:  # noqa: BLE001 - any failure => fail safe (no CORS origin)
            logger.debug("Could not resolve CORS origin from SSM %s: %s", UI_ORIGIN_SSM_PARAMETER, exc)
            value = None

    _ssm_origin_cache["value"] = value
    _ssm_origin_cache["fetched"] = True
    _ssm_origin_cache["fetched_at"] = now
    return value


def _resolve_allow_list() -> List[str]:
    """The runtime allow-list: ``ALLOWED_ORIGINS`` env first, else SSM ``/cm/ui/origin``."""
    env_origins = _allowed_origins_from_env()
    if env_origins:
        return env_origins
    ssm_origin = _origin_from_ssm()
    return [ssm_origin] if ssm_origin else []


def _request_origin(event: Optional[Dict[str, Any]]) -> Optional[str]:
    """Extract the request ``Origin`` header (case-insensitive) from an API GW event."""
    if not event:
        return None
    headers = event.get("headers") or {}
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == "origin":
            return value
    return None


def _cors_origin(event: Optional[Dict[str, Any]] = None, *, public: bool = False) -> Optional[str]:
    """Resolve the ``Access-Control-Allow-Origin`` value per the TM-02 policy.

    Returns the origin string to use, or ``None`` when no credentialed origin can
    be resolved (the caller then omits the CORS origin header — never ``*``).
    """
    allow_list = _resolve_allow_list()
    if not allow_list:
        # Neither source resolved. public endpoints MAY use an explicit env default
        # if the deployer set one; otherwise omit (fail safe, never bare "*").
        return None
    request_origin = _request_origin(event)
    if request_origin and request_origin in allow_list:
        return request_origin
    # No match (or no Origin header): return the configured default (first entry).
    # For a credentialed response this is still a concrete origin, not "*".
    return allow_list[0]


def cors_headers(event: Optional[Dict[str, Any]] = None, *, public: bool = False) -> Dict[str, str]:
    """Build the full CORS header set for a response, with a resolved origin.

    The static Allow-Headers / Allow-Methods / Max-Age values are unchanged
    (Requirement 2.6). The origin is resolved via :func:`_cors_origin`; when it
    resolves to ``None`` the ``Access-Control-Allow-Origin`` key is omitted
    entirely (fail safe) rather than set to ``*``.
    """
    headers: Dict[str, str] = {
        "Access-Control-Allow-Headers": _CORS_ALLOW_HEADERS,
        "Access-Control-Allow-Methods": _CORS_ALLOW_METHODS,
        "Access-Control-Max-Age": _CORS_MAX_AGE,
    }
    origin = _cors_origin(event, public=public)
    if origin is not None:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def create_success_response(
    data: Any,
    status_code: int = 200,
    additional_headers: Optional[Dict[str, str]] = None,
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a successful API response with consistent formatting and CORS headers

    Args:
        data: Response data to be JSON serialized
        status_code: HTTP status code (default: 200)
        additional_headers: Optional additional headers to include
        event: Optional API Gateway event; when provided, the request ``Origin``
            is echoed if it matches the configured allow-list.

    Returns:
        API Gateway response dictionary
    """
    headers = {"Content-Type": "application/json", **cors_headers(event)}

    # Add any additional headers
    if additional_headers:
        headers.update(additional_headers)

    return {"statusCode": status_code, "headers": headers, "body": json.dumps(data, default=str)}


def create_error_response(
    status_code: int,
    message: str,
    details: Optional[str] = None,
    error_code: Optional[str] = None,
    additional_headers: Optional[Dict[str, str]] = None,
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create an error API response with consistent formatting and CORS headers

    Args:
        status_code: HTTP status code
        message: Error message
        details: Optional additional error details
        error_code: Optional application-specific error code
        additional_headers: Optional additional headers to include
        event: Optional API Gateway event for request-Origin echo.

    Returns:
        API Gateway response dictionary
    """
    headers = {"Content-Type": "application/json", **cors_headers(event)}

    # Add any additional headers
    if additional_headers:
        headers.update(additional_headers)

    error_data = {"error": message, "status_code": status_code, "request_id": f"req_{uuid.uuid4().hex[:8]}"}

    if details:
        error_data["details"] = details

    if error_code:
        error_data["error_code"] = error_code

    return {"statusCode": status_code, "headers": headers, "body": json.dumps(error_data)}


def create_text_response(
    content: str,
    status_code: int = 200,
    content_type: str = "text/plain",
    additional_headers: Optional[Dict[str, str]] = None,
    event: Optional[Dict[str, Any]] = None,
    public: bool = False,
) -> Dict[str, Any]:
    """
    Create a text response (useful for endpoints like /llms.txt)

    Args:
        content: Text content to return
        status_code: HTTP status code (default: 200)
        content_type: Content-Type header (default: 'text/plain')
        additional_headers: Optional additional headers to include
        event: Optional API Gateway event for request-Origin echo.
        public: Explicit opt-in for genuinely public, uncredentialed discovery
            endpoints (e.g. /llms.txt). Still config-driven, never a bare '*'.

    Returns:
        API Gateway response dictionary
    """
    headers = {"Content-Type": content_type, **cors_headers(event, public=public)}

    # Add any additional headers
    if additional_headers:
        headers.update(additional_headers)

    return {"statusCode": status_code, "headers": headers, "body": content}


def create_redirect_response(
    location: str,
    status_code: int = 302,
    additional_headers: Optional[Dict[str, str]] = None,
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a redirect response

    Args:
        location: URL to redirect to
        status_code: HTTP status code (default: 302)
        additional_headers: Optional additional headers to include
        event: Optional API Gateway event for request-Origin echo.

    Returns:
        API Gateway response dictionary
    """
    # Redirects carry CORS metadata but not Max-Age (preserve prior behavior).
    headers = {"Location": location}
    cors = cors_headers(event)
    cors.pop("Access-Control-Max-Age", None)
    headers.update(cors)

    # Add any additional headers
    if additional_headers:
        headers.update(additional_headers)

    return {"statusCode": status_code, "headers": headers, "body": ""}


def create_options_response(
    allowed_methods: Optional[str] = None,
    additional_headers: Optional[Dict[str, str]] = None,
    event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a CORS preflight OPTIONS response

    Args:
        allowed_methods: Comma-separated list of allowed methods (default: standard set)
        additional_headers: Optional additional headers to include
        event: Optional API Gateway event for request-Origin echo.

    Returns:
        API Gateway response dictionary
    """
    headers = cors_headers(event)
    if allowed_methods:
        headers["Access-Control-Allow-Methods"] = allowed_methods

    # Add any additional headers
    if additional_headers:
        headers.update(additional_headers)

    return {"statusCode": 200, "headers": headers, "body": ""}


def create_validation_error_response(
    validation_errors: Dict[str, str], additional_headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create a validation error response with field-specific errors

    Args:
        validation_errors: Dictionary of field names to error messages
        additional_headers: Optional additional headers to include

    Returns:
        API Gateway response dictionary
    """
    return create_error_response(
        status_code=400,
        message="Validation failed",
        details=validation_errors,
        error_code="VALIDATION_ERROR",
        additional_headers=additional_headers,
    )


def create_authentication_error_response(
    message: str = "Authentication required", additional_headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create a standardized authentication error response

    Args:
        message: Authentication error message
        additional_headers: Optional additional headers to include

    Returns:
        API Gateway response dictionary
    """
    return create_error_response(
        status_code=401, message=message, error_code="AUTHENTICATION_ERROR", additional_headers=additional_headers
    )


def create_authorization_error_response(
    message: str = "Insufficient permissions", additional_headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create a standardized authorization error response

    Args:
        message: Authorization error message
        additional_headers: Optional additional headers to include

    Returns:
        API Gateway response dictionary
    """
    return create_error_response(
        status_code=403, message=message, error_code="AUTHORIZATION_ERROR", additional_headers=additional_headers
    )


def create_not_found_response(
    resource: str = "Resource", additional_headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create a standardized not found error response

    Args:
        resource: Name of the resource that was not found
        additional_headers: Optional additional headers to include

    Returns:
        API Gateway response dictionary
    """
    return create_error_response(
        status_code=404, message=f"{resource} not found", error_code="NOT_FOUND", additional_headers=additional_headers
    )


def create_rate_limit_response(
    retry_after: Optional[int] = None, additional_headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create a rate limit exceeded response

    Args:
        retry_after: Seconds to wait before retrying (optional)
        additional_headers: Optional additional headers to include

    Returns:
        API Gateway response dictionary
    """
    headers = additional_headers or {}
    if retry_after:
        headers["Retry-After"] = str(retry_after)

    return create_error_response(
        status_code=429, message="Rate limit exceeded", error_code="RATE_LIMIT_EXCEEDED", additional_headers=headers
    )
