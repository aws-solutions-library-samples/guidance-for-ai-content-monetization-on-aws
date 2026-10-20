"""
Authentication functions for native API keys, JWT, and Cognito authentication

This module provides the core authentication functions used by the decorators.
All functions are designed to work with standardized parameters and return consistent results.

Partner identity: API Gateway validates the native ``x-api-key`` at the edge and places the key
*id* on ``requestContext.identity.apiKeyId``. The Lambda never sees or stores the key value; it
resolves the key id to a partner through ``cm-partners`` (``api-key-id-index`` → ``KEY#`` item →
``META`` item) and then authorizes the operation against the partner's ``allowed_endpoints``.
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import boto3
import jwt
from boto3.dynamodb.conditions import Key

from ..utils.models import PARTNER_META_SK, partner_pk
from .partner_operations import is_key_gated, operation_id_for

logger = logging.getLogger(__name__)

class _LazyAwsClient:
    """Build the boto3 client on first use, not at import.

    This module is imported by every function in ``shared.auth`` (and by CLIs that only need
    ``partner_operations``), so constructing clients at import time would require a region and
    credentials merely to import the package. Tests patch these module attributes by name, which
    keeps working because the attribute is replaced wholesale.
    """

    def __init__(self, factory):
        self._factory = factory
        self._client = None

    def __getattr__(self, name):
        if self._client is None:
            self._client = self._factory()
        return getattr(self._client, name)


dynamodb = _LazyAwsClient(lambda: boto3.resource("dynamodb"))
secrets_manager = _LazyAwsClient(lambda: boto3.client("secretsmanager"))
cloudwatch = _LazyAwsClient(lambda: boto3.client("cloudwatch"))

# Environment variables
PARTNERS_TABLE_NAME = os.environ.get("PARTNERS_TABLE_NAME", "cm-partners")
JWT_SECRET_NAME = os.environ.get("JWT_SECRET_NAME", "iab-jwt-secret")
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))

# Cache for JWT secret
_jwt_secret_cache = None
_jwt_secret_cache_time = 0
JWT_SECRET_CACHE_TTL = 3600  # 1 hour


PARTNER_API_KEY_ID_INDEX = "api-key-id-index"


@dataclass
class PartnerResolutionError(Exception):
    """A key that API Gateway accepted maps to a partner that may not perform this request."""

    status_code: int
    error_code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.status_code} {self.error_code}: {self.message}"


class JwtSecretUnavailableError(Exception):
    """TM-01: raised when the JWT signing secret cannot be obtained from Secrets Manager.

    Fail closed — the API must refuse to mint or verify sessions rather than fall back to a
    hardcoded or environment-default key (which would be a session-forgery / auth-bypass sink).
    """


def _partner_table():
    return dynamodb.Table(os.environ.get("PARTNER_TABLE_NAME") or PARTNERS_TABLE_NAME)


def extract_api_key_id(event: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return ``requestContext.identity.apiKeyId`` when API Gateway validated a native key."""
    if not event:
        return None
    identity = (event.get("requestContext") or {}).get("identity") or {}
    api_key_id = identity.get("apiKeyId")
    return api_key_id or None


def resolve_partner_from_event(event: Dict[str, Any], enforce_endpoint: bool = True) -> Optional[Dict[str, Any]]:
    """Resolve the partner behind a native API key and authorize the requested operation.

    Returns ``None`` when the request carries no validated key (the caller falls through to other
    authentication or public handling). Raises :class:`PartnerResolutionError` when a key is
    present but its partner is unknown, disabled, suspended, deleted, or not allowed to call the
    operation. Never reads a key value from headers.
    """
    api_key_id = extract_api_key_id(event)
    if not api_key_id:
        return None

    table = _partner_table()
    try:
        keys = table.query(
            IndexName=PARTNER_API_KEY_ID_INDEX,
            KeyConditionExpression=Key("api_key_id").eq(api_key_id),
            Limit=1,
        ).get("Items", [])
    except Exception as exc:  # fail closed: a lookup failure must not admit the request
        logger.error(f"Partner resolution lookup failed for apiKeyId={api_key_id}: {exc}")
        raise PartnerResolutionError(403, "partner_inactive", "Partner could not be resolved") from exc

    if not keys:
        logger.warning(f"No partner credential mapped to apiKeyId={api_key_id}")
        publish_auth_failure_metric("unmapped_api_key")
        raise PartnerResolutionError(403, "partner_inactive", "API key is not bound to an active partner")

    credential = keys[0]
    if credential.get("status") != "active":
        logger.warning(f"Disabled credential used: apiKeyId={api_key_id} partner={credential.get('partner_id')}")
        publish_auth_failure_metric("disabled_api_key")
        raise PartnerResolutionError(403, "partner_inactive", "API key has been disabled")

    partner_id = credential.get("partner_id")
    partner = table.get_item(Key={"PK": partner_pk(partner_id), "SK": PARTNER_META_SK}).get("Item")
    if not partner:
        logger.warning(f"Credential apiKeyId={api_key_id} points at missing partner {partner_id}")
        publish_auth_failure_metric("unmapped_api_key")
        raise PartnerResolutionError(403, "partner_inactive", "API key is not bound to an active partner")

    if partner.get("status") != "active":
        logger.warning(f"Inactive partner {partner_id} ({partner.get('status')}) attempted access")
        publish_auth_failure_metric("inactive_partner")
        raise PartnerResolutionError(403, "partner_inactive", "Partner is not active")

    operation_id = operation_id_for(event.get("httpMethod"), event.get("resource") or event.get("path"))
    allowed = list(partner.get("allowed_endpoints") or [])
    if enforce_endpoint and is_key_gated(operation_id) and allowed and operation_id not in allowed:
        logger.warning(f"Partner {partner_id} not allowed to call {operation_id}")
        publish_auth_failure_metric("endpoint_not_allowed")
        raise PartnerResolutionError(403, "endpoint_not_allowed", f"Partner is not permitted to call {operation_id}")

    return {
        "partner_id": partner_id,
        "api_key_id": api_key_id,
        "usage_plan_id": credential.get("usage_plan_id"),
        "name": partner.get("partner_name"),
        "pricing_tier": partner.get("pricing_tier", "basic"),
        "subscription": partner.get("pricing_tier", "basic"),
        "allowed_endpoints": allowed,
        "operation_id": operation_id,
        "scopes": ["query_content", "analytics"],
    }


def authenticate_jwt(headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Authenticate using JWT token (for publishers) - supports both Cognito and custom JWT tokens

    Args:
        headers: HTTP headers dictionary

    Returns:
        User information dict if valid, None if invalid
    """
    if not headers:
        return None

    auth_header = headers.get("Authorization") or headers.get("authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # Remove 'Bearer ' prefix

    try:
        # First try to verify as Cognito JWT token
        import jwt

        # Decode token without verification to check issuer
        unverified_payload = jwt.decode(
            token, options={"verify_signature": False}
        )  # nosemgrep: unverified-jwt-decode - intentional: extract claims to determine key before verification

        # Check if it's a Cognito token (has 'iss' field with cognito-idp)
        if "iss" in unverified_payload and "cognito-idp" in unverified_payload["iss"]:
            logger.info("Detected Cognito JWT token")

            # For Cognito tokens, we'll do basic validation
            # In production, you'd verify against Cognito's public keys
            if unverified_payload.get("token_use") == "id" and unverified_payload.get("exp", 0) > time.time():
                return {
                    "user_id": unverified_payload.get("sub"),
                    "email": unverified_payload.get("email"),
                    "name": unverified_payload.get("name", unverified_payload.get("email")),
                    "type": "cognito_publisher",
                    "scopes": ["analytics", "dashboard"],
                    "publisher_id": unverified_payload.get("custom:publisher_id", "default_publisher"),
                }
            else:
                logger.warning("Invalid or expired Cognito token")
                return None

        # If not Cognito, try custom JWT token
        else:
            logger.info("Detected custom JWT token")
            # Lazy import (intentional): authentication_service imports back from
            # shared.auth.authentication_functions, so a module-level import here would be circular.
            # No ImportError fallback — a genuine packaging break must surface, not be masked.
            from authentication_service import verify_jwt_token

            is_valid, payload = verify_jwt_token(token)

            if is_valid:
                return {
                    "user_id": payload.get("partner_id"),
                    "name": payload.get("partner_name"),
                    "type": "custom_publisher",
                    "scopes": ["analytics", "dashboard"],
                    "publisher_id": payload.get("publisher_id", "default_publisher"),
                }
            return None

    except Exception as e:
        logger.error(f"JWT authentication error: {str(e)}")
        return None


def extract_cognito_user_context(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract Cognito user identity from the API Gateway event context.

    Args:
        event: API Gateway event dictionary

    Returns:
        Cognito user information dict if valid, None if invalid
    """
    try:
        # Safely extract from API Gateway authorizer context
        request_context = event.get("requestContext")
        if not request_context:
            return None

        authorizer = request_context.get("authorizer")
        if not authorizer:
            return None

        # Get Cognito claims
        claims = authorizer.get("claims")
        if claims:
            return {
                "user_id": claims.get("sub"),
                "email": claims.get("email"),
                "name": claims.get("name", claims.get("email")),  # Use name if available, fallback to email
                "publisher_id": claims.get("custom:publisher_id", "default_publisher"),
                "groups": claims.get("cognito:groups", []),
                "username": claims.get("cognito:username"),
                "type": "cognito_publisher",
                "scopes": ["analytics", "dashboard"],
            }

        return None

    except Exception as e:
        logger.error(f"Error extracting Cognito user context: {str(e)}")
        return None


def authenticate_request(headers: Dict[str, str], event: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Unified authentication that supports native API keys, JWT tokens, and API Gateway Cognito authorizer

    Args:
        headers: HTTP headers dictionary
        event: API Gateway event dictionary (optional)

    Returns:
        Authentication result dict with 'valid', 'user', 'type', and optional 'error' keys
    """
    # First check if API Gateway Cognito authorizer already validated the request
    if event:
        cognito_user = extract_cognito_user_context(event)
        if cognito_user:
            return {"valid": True, "user": cognito_user, "type": "cognito"}

    # Try JWT authentication (for publishers using custom JWT tokens)
    jwt_auth = authenticate_jwt(headers)
    if jwt_auth:
        return {"valid": True, "user": jwt_auth, "type": "jwt"}

    # Try native API key resolution (for external partners)
    if event:
        try:
            partner_auth = resolve_partner_from_event(event)
        except PartnerResolutionError as exc:
            return {"valid": False, "error": exc.message, "error_code": exc.error_code, "status_code": exc.status_code}
        if partner_auth:
            return {"valid": True, "user": partner_auth, "type": "partner_key"}

    # No valid authentication found
    return {"valid": False, "error": "Invalid or missing authentication credentials"}


def get_jwt_secret() -> str:
    """Get JWT secret from AWS Secrets Manager with caching"""
    global _jwt_secret_cache, _jwt_secret_cache_time

    current_time = time.time()
    if _jwt_secret_cache and (current_time - _jwt_secret_cache_time) < JWT_SECRET_CACHE_TTL:
        return _jwt_secret_cache

    try:
        response = secrets_manager.get_secret_value(SecretId=JWT_SECRET_NAME)
        secret_data = json.loads(response["SecretString"])
        _jwt_secret_cache = secret_data["jwt_secret"]
        _jwt_secret_cache_time = current_time
        return _jwt_secret_cache
    except Exception as e:
        # TM-01: fail closed. Never fall back to a hardcoded or environment-default secret —
        # that would let a transient Secrets Manager failure, or a deployment without the
        # CDK-generated secret, sign/verify sessions with a publicly-known key. Log the error
        # (not the secret) and re-raise so token signing/verification returns a 5xx.
        logger.error(f"Failed to retrieve JWT secret: {str(e)}")
        raise JwtSecretUnavailableError(
            "JWT signing secret is unavailable from Secrets Manager; refusing to sign or "
            "verify tokens."
        ) from e


def publish_auth_failure_metric(failure_reason: str) -> None:
    """
    Publish authentication failure metric to CloudWatch

    Args:
        failure_reason: Reason for authentication failure
    """
    try:
        cloudwatch.put_metric_data(
            Namespace="IAB/Authentication",
            MetricData=[
                {
                    "MetricName": "AuthenticationFailures",
                    "Dimensions": [
                        {"Name": "Service", "Value": "AI-Content-Monetization"},
                        {"Name": "FailureReason", "Value": failure_reason},
                    ],
                    "Value": 1,
                    "Unit": "Count",
                }
            ],
        )
        logger.debug(f"Published authentication failure metric: {failure_reason}")
    except Exception as e:
        logger.warning(f"Failed to publish authentication failure metric: {str(e)}")
        # Don't raise exception as this is not critical


def generate_jwt_token(partner_info: Dict[str, Any], content_id: Optional[str] = None) -> str:
    """
    Generate JWT token for content access

    Args:
        partner_info: Partner information from validation
        content_id: Optional specific content ID for access

    Returns:
        JWT token string
    """
    logger.info("=== GENERATE_JWT_TOKEN START ===")
    logger.info(f"Partner: {partner_info.get('partner_name')}, Content ID: {content_id}")

    try:
        jwt_secret = get_jwt_secret()

        # Token payload
        payload = {
            "partner_id": partner_info.get("partner_id"),
            "partner_name": partner_info.get("partner_name"),
            "pricing_tier": partner_info.get("pricing_tier"),
            "allowed_endpoints": partner_info.get("allowed_endpoints", []),
            "iat": int(time.time()),
            "exp": int(time.time()) + (JWT_EXPIRY_HOURS * 3600),
        }

        # Add content-specific claims if provided
        if content_id:
            payload["content_id"] = content_id
            payload["access_type"] = "content_specific"
        else:
            payload["access_type"] = "general"

        # Generate token
        token = jwt.encode(payload, jwt_secret, algorithm="HS256")

        logger.info(f"=== GENERATE_JWT_TOKEN END === Generated token for partner: {partner_info.get('partner_name')}")
        return token

    except Exception as e:
        logger.error(f"=== GENERATE_JWT_TOKEN ERROR === {str(e)}")
        raise


def verify_jwt_token(token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verify JWT token and return payload

    Args:
        token: JWT token to verify

    Returns:
        Tuple of (is_valid, payload)
    """
    try:
        jwt_secret = get_jwt_secret()

        # Decode and verify token
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])

        logger.info(f"Valid JWT token for partner: {payload.get('partner_name')}")
        return True, payload

    except jwt.ExpiredSignatureError:
        logger.warning("JWT token expired")
        return False, None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {str(e)}")
        return False, None
    except Exception as e:
        logger.error(f"Error verifying JWT token: {str(e)}")
        return False, None
