"""
Authentication decorators with standardized function signatures

This module provides authentication decorators that work with standardized function signatures:
(headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs)

Key improvements:
- Renamed @require_full_auth to @require_partner_or_cognito_auth for clarity
- Standardized function signatures across all decorators
- Differentiated logging for partner usage (billing) vs UI usage (testing)
- Simplified parameter handling
"""

import json
import logging
from functools import wraps
from typing import Any, Callable, Dict, Optional

from .authentication_functions import (
    PartnerResolutionError,
    authenticate_jwt,
    extract_cognito_user_context,
    resolve_partner_from_event,
)

logger = logging.getLogger(__name__)


def _partner_denied(exc: PartnerResolutionError) -> Dict[str, Any]:
    from ..utils.response_helpers import create_error_response

    return create_error_response(exc.status_code, exc.message, error_code=exc.error_code)


def require_partner_auth(func: Callable) -> Callable:
    """
    Decorator that requires a resolved partner (native API key validated by API Gateway).
    Used for Partner API endpoints that are exclusively for external partners.

    Not currently applied to any route: partner-plane operations use
    ``require_partner_or_cognito_auth`` and partner lifecycle routes use ``require_cognito_auth``.
    Kept, with tests, as the primitive for a future partner-only endpoint.

    Args:
        func: Handler function with signature (headers, request_data, event, **kwargs)

    Returns:
        Decorated function that validates partner authentication
    """

    @wraps(func)
    def wrapper(
        headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        try:
            partner = resolve_partner_from_event(event)
        except PartnerResolutionError as exc:
            return _partner_denied(exc)
        if not partner:
            from ..utils.response_helpers import create_error_response

            # A request without a validated native key never reaches a key-gated operation on the
            # partner API; this guards the internal plane, where no key is validated at the edge.
            return create_error_response(403, "Partner API key required", error_code="invalid_api_key")

        # Add partner info to kwargs for the handler to use
        kwargs["authenticated_user"] = partner
        kwargs["auth_type"] = "partner_key"

        # Log partner access for billing purposes
        logger.info(f"Partner API access: {partner.get('partner_id')} - {event.get('path', 'unknown')}")

        return func(headers, request_data, event, **kwargs)

    return wrapper


def require_cognito_auth(func: Callable) -> Callable:
    """
    Decorator that requires Cognito JWT authentication only.
    Used for UI Backend endpoints that are exclusively for authenticated publishers.

    Args:
        func: Handler function with signature (headers, request_data, event, **kwargs)

    Returns:
        Decorated function that validates Cognito authentication
    """

    @wraps(func)
    def wrapper(
        headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        # First check if API Gateway Cognito authorizer already validated the request
        cognito_user = extract_cognito_user_context(event)
        if cognito_user:
            kwargs["authenticated_user"] = cognito_user
            kwargs["auth_type"] = "cognito"

            # Log UI access for analytics (not billing)
            logger.info(f"UI Backend access: {cognito_user.get('user_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        # Fallback to JWT token authentication
        jwt_auth = authenticate_jwt(headers)
        if jwt_auth:
            kwargs["authenticated_user"] = jwt_auth
            kwargs["auth_type"] = "jwt"

            # Log JWT access for analytics
            logger.info(f"JWT access: {jwt_auth.get('user_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        from ..utils.response_helpers import create_error_response

        return create_error_response(401, "Invalid or missing Cognito token")

    return wrapper


def require_partner_or_cognito_auth(func: Callable) -> Callable:
    """
    Decorator that requires EITHER a resolved partner (native API key) OR Cognito JWT authentication.
    Used for hybrid endpoints that can be accessed from both Partner API and UI Backend.

    This replaces the confusingly named @require_full_auth decorator.

    Args:
        func: Handler function with signature (headers, request_data, event, **kwargs)

    Returns:
        Decorated function that validates either authentication type
    """

    @wraps(func)
    def wrapper(
        headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        # Try Cognito authentication first (for UI access)
        cognito_user = extract_cognito_user_context(event)
        if cognito_user:
            kwargs["authenticated_user"] = cognito_user
            kwargs["auth_type"] = "cognito"

            # Log UI testing/preview access (not for billing)
            logger.info(f"Hybrid endpoint UI access: {cognito_user.get('user_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        # Try JWT token authentication
        jwt_auth = authenticate_jwt(headers)
        if jwt_auth:
            kwargs["authenticated_user"] = jwt_auth
            kwargs["auth_type"] = "jwt"

            # Log JWT access
            logger.info(f"Hybrid endpoint JWT access: {jwt_auth.get('user_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        # Try native API key resolution (for external partner access)
        try:
            partner = resolve_partner_from_event(event)
        except PartnerResolutionError as exc:
            return _partner_denied(exc)
        if partner:
            kwargs["authenticated_user"] = partner
            kwargs["auth_type"] = "partner_key"

            # Log partner access for billing purposes
            logger.info(f"Hybrid endpoint partner access: {partner.get('partner_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        from ..utils.response_helpers import create_error_response

        return create_error_response(401, "Authentication required: API key or Cognito token")

    return wrapper


def optional_auth(func: Callable) -> Callable:
    """
    Decorator for endpoints where authentication is optional but provides enhanced features.
    Used for public endpoints that offer additional functionality for authenticated users.

    Args:
        func: Handler function with signature (headers, request_data, event, **kwargs)

    Returns:
        Decorated function that optionally validates authentication
    """

    @wraps(func)
    def wrapper(
        headers: Dict[str, str], request_data: Dict[str, Any], event: Dict[str, Any], **kwargs
    ) -> Dict[str, Any]:
        # Try authentication but don't fail if none provided

        # Try Cognito authentication first
        cognito_user = extract_cognito_user_context(event)
        if cognito_user:
            kwargs["authenticated_user"] = cognito_user
            kwargs["auth_type"] = "cognito"

            logger.info(f"Optional auth - Cognito user: {cognito_user.get('user_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        # Try JWT token authentication
        jwt_auth = authenticate_jwt(headers)
        if jwt_auth:
            kwargs["authenticated_user"] = jwt_auth
            kwargs["auth_type"] = "jwt"

            logger.info(f"Optional auth - JWT user: {jwt_auth.get('user_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        # Try native API key resolution. Public operations are never key-gated, so a partner that
        # presents a key here is identified but never denied for its allow-list.
        try:
            partner = resolve_partner_from_event(event, enforce_endpoint=False)
        except PartnerResolutionError as exc:
            return _partner_denied(exc)
        if partner:
            kwargs["authenticated_user"] = partner
            kwargs["auth_type"] = "partner_key"

            logger.info(f"Optional auth - Partner: {partner.get('partner_id')} - {event.get('path', 'unknown')}")

            return func(headers, request_data, event, **kwargs)

        # No authentication - public access
        kwargs["authenticated_user"] = None
        kwargs["auth_type"] = "public"

        logger.info(f"Optional auth - Public access: {event.get('path', 'unknown')}")

        return func(headers, request_data, event, **kwargs)

    return wrapper


# Legacy decorator for backward compatibility - will be removed in future versions
def require_full_auth(func: Callable) -> Callable:
    """
    DEPRECATED: Use require_partner_or_cognito_auth instead.

    This decorator is deprecated and will be removed in a future version.
    It has been renamed to require_partner_or_cognito_auth for clarity.
    """
    import warnings

    warnings.warn(
        "require_full_auth is deprecated. Use require_partner_or_cognito_auth instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return require_partner_or_cognito_auth(func)
