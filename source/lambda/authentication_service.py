"""
IAB Authentication Service Lambda Function

This Lambda function handles:
1. JWT token generation for content access
2. JWT token verification

Partner API keys are native API Gateway keys: validation, quota and throttling happen at the
edge and partner resolution happens in the serving Lambda
(``shared.auth.authentication_functions.resolve_partner_from_event``). The ``validate_key``
operation and the API-key branch of the custom authorizer are retired; the authorizer Lambda
itself is removed with the API Gateway definition that referenced it.

Requirements covered:
- 2.1: Validate authentication credentials
- 7.3: Implement industry-standard authentication mechanisms

MIGRATION NOTE: This file has been updated to use the new shared modules architecture.
Authentication functions are now available in shared/auth/ for reuse across Lambda functions.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import boto3
import jwt
from shared.auth.authentication_functions import (
    generate_jwt_token,
    get_jwt_secret,
    publish_auth_failure_metric,
    verify_jwt_token,
)

# Import shared utilities
from shared.utils.response_helpers import create_error_response, create_success_response

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables for backward compatibility
PARTNERS_TABLE_NAME = os.environ.get("PARTNERS_TABLE_NAME", "cm-partners")
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for authentication operations

    Supported operations:
    - validate_key: Validate API key
    - generate_token: Generate JWT token
    - verify_token: Verify JWT token
    """
    request_id = getattr(context, "aws_request_id", "unknown")
    logger.info(f"=== AUTHENTICATION_SERVICE_LAMBDA START === Request ID: {request_id}")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    try:
        # Parse request
        operation = event.get("operation")

        if not operation:
            return create_error_response(
                400,
                "Missing operation parameter",
                details={"supported_operations": ["validate_key", "generate_token", "verify_token"]},
            )

        if operation == "validate_key":
            return handle_validate_key(event)
        elif operation == "generate_token":
            return handle_generate_token(event)
        elif operation == "verify_token":
            return handle_verify_token(event)
        else:
            return create_error_response(
                400,
                f"Unsupported operation: {operation}",
                details={"supported_operations": ["validate_key", "generate_token", "verify_token"]},
            )

    except Exception as e:
        logger.error(f"=== AUTHENTICATION_SERVICE_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}")
        return create_error_response(500, "Internal server error", str(e))


def handle_validate_key(event: Dict[str, Any]) -> Dict[str, Any]:
    """Retired: partner keys are validated by API Gateway, not by this service."""
    return create_error_response(
        410,
        "Partner key validation is performed by API Gateway",
        "Send the key in the x-api-key header of a partner API request",
        error_code="operation_retired",
    )


def handle_generate_token(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle JWT token generation request"""
    partner_info = event.get("partner_info")
    content_id = event.get("content_id")

    if not partner_info:
        return create_error_response(400, "Missing partner_info parameter")

    try:
        token = generate_jwt_token(partner_info, content_id)

        return create_success_response({"token": token, "expires_in": JWT_EXPIRY_HOURS * 3600, "token_type": "Bearer"})

    except Exception as e:
        return create_error_response(500, "Failed to generate token", str(e))


def handle_verify_token(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle JWT token verification request"""
    token = event.get("token")

    if not token:
        return create_error_response(400, "Missing token parameter")

    # Remove 'Bearer ' prefix if present
    if token.startswith("Bearer "):
        token = token[7:]

    is_valid, payload = verify_jwt_token(token)

    if is_valid:
        return create_success_response({"valid": True, "payload": payload})
    else:
        return create_error_response(401, "Invalid or expired token")


# Utility function for API Gateway authorizer
def create_policy(effect: str, resource: str, principal_id: str) -> Dict[str, Any]:
    """Create IAM policy for API Gateway authorizer"""
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{"Action": "execute-api:Invoke", "Effect": effect, "Resource": resource}],
        },
    }


def authorizer_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    API Gateway Lambda Authorizer handler

    This function can be used as a Lambda authorizer for API Gateway
    to automatically validate API keys or JWT tokens
    """
    try:
        # Get token from Authorization header
        token = event.get("authorizationToken", "")
        method_arn = event.get("methodArn", "")

        if not token:
            raise Exception("Unauthorized")

        # Check if it's an API key or JWT token
        if token.startswith("Bearer "):
            # JWT token
            jwt_token = token[7:]
            is_valid, payload = verify_jwt_token(jwt_token)

            if is_valid:
                principal_id = payload.get("partner_id", "unknown")
                policy = create_policy("Allow", method_arn, principal_id)
                policy["context"] = {
                    "partner_id": payload.get("partner_id"),
                    "partner_name": payload.get("partner_name"),
                    "pricing_tier": payload.get("pricing_tier"),
                }
                return policy
            else:
                raise Exception("Unauthorized")

        else:
            # Raw partner keys are no longer accepted here: API Gateway validates native keys.
            publish_auth_failure_metric("legacy_partner_key_token")
            raise Exception("Unauthorized")

    except Exception as e:
        logger.error(f"Authorization failed: {str(e)}")
        raise Exception("Unauthorized")
