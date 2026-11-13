"""
Authentication module for Lambda functions

Provides standardized authentication decorators and functions.
"""

from .authentication_decorators import (
    optional_auth,
    require_cognito_auth,
    require_full_auth,  # Deprecated, use require_partner_or_cognito_auth
    require_partner_auth,
    require_partner_or_cognito_auth,
)
from .authentication_functions import (
    PartnerResolutionError,
    authenticate_jwt,
    authenticate_request,
    extract_cognito_user_context,
    resolve_partner_from_event,
)

__all__ = [
    "require_partner_auth",
    "require_cognito_auth",
    "require_partner_or_cognito_auth",
    "optional_auth",
    "require_full_auth",  # Deprecated
    "resolve_partner_from_event",
    "PartnerResolutionError",
    "authenticate_jwt",
    "extract_cognito_user_context",
    "authenticate_request",
]
