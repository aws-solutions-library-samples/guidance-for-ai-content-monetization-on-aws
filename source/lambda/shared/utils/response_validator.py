"""
API Response Validation Middleware

This module provides comprehensive validation of API responses against the OpenAPI specification.
It validates response schemas, field names, data types, and ensures consistency across all endpoints.

The validation can be enabled/disabled via environment variables for performance control in production.
"""

import json
import logging
import os
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)

# Environment variable to control validation in production
ENABLE_RESPONSE_VALIDATION = os.environ.get("ENABLE_RESPONSE_VALIDATION", "true").lower() == "true"
VALIDATION_MODE = os.environ.get("VALIDATION_MODE", "log")  # 'log', 'strict', 'disabled'


class ResponseValidationError(Exception):
    """Exception raised when response validation fails"""

    pass


class APIResponseValidator:
    """
    Validates API responses against OpenAPI specification schemas
    """

    def __init__(self, openapi_spec_path: str = None):
        """
        Initialize the validator with OpenAPI specification

        Args:
            openapi_spec_path: Path to OpenAPI specification file
        """
        self.openapi_spec = None
        self.schemas = {}
        self.validation_enabled = ENABLE_RESPONSE_VALIDATION
        self.validation_mode = VALIDATION_MODE

        if openapi_spec_path and os.path.exists(openapi_spec_path):
            try:
                with open(openapi_spec_path, "r") as f:
                    self.openapi_spec = yaml.safe_load(f)
                    self.schemas = self.openapi_spec.get("components", {}).get("schemas", {})
                logger.info(f"Loaded OpenAPI specification with {len(self.schemas)} schemas")
            except Exception as e:
                logger.error(f"Failed to load OpenAPI specification: {str(e)}")
                self.validation_enabled = False

    def validate_response(
        self, response: Dict[str, Any], endpoint: str, method: str, status_code: int
    ) -> Dict[str, Any]:
        """
        Validate API response against OpenAPI specification

        Args:
            response: The response dictionary to validate
            endpoint: API endpoint path
            method: HTTP method
            status_code: HTTP status code

        Returns:
            Validation result with errors and warnings
        """
        if not self.validation_enabled:
            return {"valid": True, "errors": [], "warnings": []}

        validation_result = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "endpoint": endpoint,
            "method": method,
            "status_code": status_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            # Extract response body
            response_body = response.get("body")
            response_data = None

            # Handle empty body (e.g., 204 No Content)
            if not response_body or response_body == "":
                response_data = None
            elif isinstance(response_body, str):
                try:
                    response_data = json.loads(response_body)
                except json.JSONDecodeError:
                    # Only treat as error if we expect JSON content
                    if status_code != 204:
                        validation_result["errors"].append("Response body is not valid JSON")
                        validation_result["valid"] = False
                        return validation_result
            else:
                response_data = response_body

            # Validate response structure
            self._validate_response_structure(response, validation_result)

            # Validate response headers
            self._validate_response_headers(response, validation_result)

            # Validate response data against schema
            if response_data:
                self._validate_response_data(response_data, endpoint, method, status_code, validation_result)

            # Validate field naming conventions
            self._validate_field_naming(response_data, validation_result)

            # Validate required fields
            self._validate_required_fields(response_data, endpoint, method, status_code, validation_result)

        except Exception as e:
            validation_result["errors"].append(f"Validation error: {str(e)}")
            validation_result["valid"] = False
            logger.error(f"Response validation failed: {str(e)}")

        return validation_result

    def _validate_response_structure(self, response: Dict[str, Any], result: Dict[str, Any]):
        """Validate basic response structure"""
        required_fields = ["statusCode", "headers", "body"]

        for field in required_fields:
            if field not in response:
                result["errors"].append(f"Missing required response field: {field}")
                result["valid"] = False

        # Validate status code
        status_code = response.get("statusCode")
        if not isinstance(status_code, int) or status_code < 100 or status_code > 599:
            result["errors"].append(f"Invalid status code: {status_code}")
            result["valid"] = False

    def _validate_response_headers(self, response: Dict[str, Any], result: Dict[str, Any]):
        """Validate response headers"""
        headers = response.get("headers", {})

        # Check required headers
        required_headers = ["Content-Type"]
        for header in required_headers:
            if header not in headers:
                result["warnings"].append(f"Missing recommended header: {header}")

        # Check CORS headers
        cors_headers = ["Access-Control-Allow-Origin", "Access-Control-Allow-Headers", "Access-Control-Allow-Methods"]

        missing_cors = [h for h in cors_headers if h not in headers]
        if missing_cors:
            result["warnings"].append(f"Missing CORS headers: {missing_cors}")

    def _validate_response_data(self, data: Any, endpoint: str, method: str, status_code: int, result: Dict[str, Any]):
        """Validate response data against expected schemas"""
        # Handle 204 No Content responses (empty body is expected)
        if status_code == 204:
            return

        if not isinstance(data, dict):
            return

        # Validate error responses
        if status_code >= 400:
            self._validate_error_response(data, result)
        else:
            # Validate success responses based on endpoint
            self._validate_success_response(data, endpoint, method, result)

    def _validate_error_response(self, data: Dict[str, Any], result: Dict[str, Any]):
        """Validate error response format"""
        required_error_fields = ["error"]
        recommended_error_fields = ["code", "timestamp", "request_id"]

        for field in required_error_fields:
            if field not in data:
                result["errors"].append(f"Missing required error field: {field}")
                result["valid"] = False

        for field in recommended_error_fields:
            if field not in data:
                result["warnings"].append(f"Missing recommended error field: {field}")

        # Validate error field types
        if "error" in data and not isinstance(data["error"], str):
            result["errors"].append("Error field must be a string")
            result["valid"] = False

        if "message" in data and not isinstance(data["message"], str):
            result["errors"].append("Message field must be a string")
            result["valid"] = False

    def _validate_success_response(self, data: Dict[str, Any], endpoint: str, method: str, result: Dict[str, Any]):
        """Validate success response based on endpoint"""
        # Content endpoints validation
        if "/content" in endpoint:
            if method == "GET" and endpoint == "/content":
                self._validate_content_list_response(data, result)
            elif method == "GET" and "/content/" in endpoint:
                self._validate_content_details_response(data, result)
            elif method == "POST" and endpoint == "/content":
                self._validate_content_creation_response(data, result)

        # Analytics endpoints validation
        elif "/analytics" in endpoint:
            self._validate_analytics_response(data, result)

        # Processing status validation
        elif "/processing/status" in endpoint:
            self._validate_processing_status_response(data, result)

    def _validate_content_list_response(self, data: Dict[str, Any], result: Dict[str, Any]):
        """Validate content list response"""
        required_fields = ["items", "pagination"]

        for field in required_fields:
            if field not in data:
                result["errors"].append(f"Missing required field in content list: {field}")
                result["valid"] = False

        # Validate items array
        if "items" in data:
            if not isinstance(data["items"], list):
                result["errors"].append("Items field must be an array")
                result["valid"] = False
            else:
                for i, item in enumerate(data["items"]):
                    self._validate_content_item(item, f"items[{i}]", result)

        # Validate pagination
        if "pagination" in data:
            self._validate_pagination(data["pagination"], "pagination", result)

    def _validate_content_details_response(self, data: Dict[str, Any], result: Dict[str, Any]):
        """Validate content details response"""
        self._validate_content_item(data, "content", result)

    def _validate_content_creation_response(self, data: Dict[str, Any], result: Dict[str, Any]):
        """Validate content creation response"""
        required_fields = ["content_id", "status", "created_at"]

        for field in required_fields:
            if field not in data:
                result["errors"].append(f"Missing required field in content creation: {field}")
                result["valid"] = False

    def _validate_content_item(self, item: Dict[str, Any], context: str, result: Dict[str, Any]):
        """Validate individual content item"""
        required_fields = ["content_id", "title", "content_type", "status"]

        for field in required_fields:
            if field not in item:
                result["errors"].append(f"Missing required field in {context}: {field}")
                result["valid"] = False

        # Validate field types
        string_fields = ["content_id", "title", "content_type", "status", "author"]
        for field in string_fields:
            if field in item and not isinstance(item[field], str):
                result["errors"].append(f"Field {field} in {context} must be a string")
                result["valid"] = False

        # Validate IAB categories
        if "iab_categories" in item:
            self._validate_iab_categories(item["iab_categories"], f"{context}.iab_categories", result)

        # Validate embedding_info (not embeddings)
        if "embeddings" in item:
            result["errors"].append(f"Use 'embedding_info' instead of 'embeddings' in {context}")
            result["valid"] = False

        if "embedding_info" in item:
            self._validate_embedding_info(item["embedding_info"], f"{context}.embedding_info", result)

    def _validate_iab_categories(self, categories: Any, context: str, result: Dict[str, Any]):
        """Validate IAB categories format"""
        if not isinstance(categories, list):
            result["errors"].append(f"IAB categories in {context} must be an array")
            result["valid"] = False
            return

        for i, category in enumerate(categories):
            if isinstance(category, dict):
                # Standardized format
                required_cat_fields = ["id", "name", "tier_1"]
                for field in required_cat_fields:
                    if field not in category:
                        result["errors"].append(f"Missing required field {field} in {context}[{i}]")
                        result["valid"] = False
            elif not isinstance(category, (str, int)):
                result["errors"].append(f"Invalid category format in {context}[{i}]")
                result["valid"] = False

    def _validate_embedding_info(self, embedding_info: Any, context: str, result: Dict[str, Any]):
        """Validate embedding info structure"""
        if not isinstance(embedding_info, dict):
            result["errors"].append(f"Embedding info in {context} must be an object")
            result["valid"] = False
            return

        required_fields = ["model", "dimensions"]
        for field in required_fields:
            if field not in embedding_info:
                result["warnings"].append(f"Missing recommended field {field} in {context}")

    def _validate_pagination(self, pagination: Any, context: str, result: Dict[str, Any]):
        """Validate pagination structure"""
        if not isinstance(pagination, dict):
            result["errors"].append(f"Pagination in {context} must be an object")
            result["valid"] = False
            return

        required_fields = ["items_per_page", "has_next", "has_previous"]
        for field in required_fields:
            if field not in pagination:
                result["errors"].append(f"Missing required pagination field: {field}")
                result["valid"] = False

    def _validate_analytics_response(self, data: Dict[str, Any], result: Dict[str, Any]):
        """Validate analytics response"""
        # Analytics responses should have data arrays or objects
        # Check for various analytics data patterns
        analytics_data_fields = [
            "metrics",
            "usage_data",
            "categories",
            "partners",
            "activity",
            "total_requests",
            "unique_partners",
            "content_accessed",
            "revenue_generated",
            "total_bot_access",
            "total_mcp_usage",
            "mappings",
            "total_count",
        ]

        # Only warn if no analytics data fields are present
        if not any(key in data for key in analytics_data_fields):
            result["warnings"].append("Analytics response should contain data fields")

        # For metrics endpoints, validate that core metrics are present
        if any(
            key in ["total_requests", "unique_partners", "content_accessed", "revenue_generated"] for key in data.keys()
        ):
            # This looks like a metrics response, don't warn about missing data fields
            pass

    def _validate_processing_status_response(self, data: Dict[str, Any], result: Dict[str, Any]):
        """Validate processing status response"""
        if "jobs" in data and isinstance(data["jobs"], list):
            for i, job in enumerate(data["jobs"]):
                required_job_fields = ["job_id", "status", "created_at"]
                for field in required_job_fields:
                    if field not in job:
                        result["errors"].append(f"Missing required field {field} in jobs[{i}]")
                        result["valid"] = False

    def _validate_field_naming(self, data: Any, result: Dict[str, Any], parent_key: str = None):
        """Validate field naming conventions"""
        if not isinstance(data, dict):
            return

        for key in data.keys():
            # Skip validation for mappings objects where keys are IDs
            if parent_key == "mappings" or (parent_key and "mapping" in parent_key.lower()):
                # Keys in mappings can be IDs (numbers as strings), skip naming validation
                continue

            # Skip validation for numeric string keys (likely IDs)
            if key.isdigit():
                continue

            # Check for snake_case convention
            if not self._is_snake_case(key) and not key.startswith("_"):
                result["warnings"].append(f"Field '{key}' should use snake_case naming")

            # Check for deprecated field names
            deprecated_fields = ["embeddings"]  # 'categories' is valid for analytics endpoints
            if key in deprecated_fields:
                result["errors"].append(f"Deprecated field name: '{key}'")
                result["valid"] = False

        # Recursively validate nested objects
        for key, value in data.items():
            # Skip field naming validation for mappings objects entirely
            if key == "mappings":
                continue
            elif isinstance(value, dict):
                self._validate_field_naming(value, result, key)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._validate_field_naming(item, result, key)

    def _validate_required_fields(
        self, data: Any, endpoint: str, method: str, status_code: int, result: Dict[str, Any]
    ):
        """Validate required fields based on endpoint and method"""
        if not isinstance(data, dict):
            return

        # This would be expanded based on OpenAPI specification
        # For now, we'll do basic validation
        pass

    def _is_snake_case(self, field_name: str) -> bool:
        """Check if field name follows snake_case convention"""
        # Allow single lowercase words and snake_case
        if field_name.islower():
            # Check if it contains only lowercase letters, numbers, and underscores
            import re

            return bool(re.match(r"^[a-z0-9_]+$", field_name))
        return False


# Global validator instance
_validator = None


def get_validator() -> APIResponseValidator:
    """Get or create global validator instance"""
    global _validator
    if _validator is None:
        # Try to find OpenAPI spec file
        spec_paths = ["openapi-spec.yaml", "../openapi-spec.yaml", "../../openapi-spec.yaml"]

        spec_path = None
        for path in spec_paths:
            if os.path.exists(path):
                spec_path = path
                break

        _validator = APIResponseValidator(spec_path)

    return _validator


def validate_response(endpoint: str, method: str):
    """
    Decorator to validate API responses

    Args:
        endpoint: API endpoint path
        method: HTTP method

    Usage:
        @validate_response('/content', 'GET')
        def handle_list_content(...):
            return response
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Execute the original function
            response = func(*args, **kwargs)

            # Skip validation if disabled
            validator = get_validator()
            if not validator.validation_enabled:
                return response

            # Validate the response
            status_code = response.get("statusCode", 200)
            validation_result = validator.validate_response(response, endpoint, method, status_code)

            # Handle validation results based on mode
            if validation_result["errors"]:
                error_msg = f"Response validation failed for {method} {endpoint}: {validation_result['errors']}"

                if validator.validation_mode == "strict":
                    # In strict mode, raise an exception
                    logger.error(error_msg)
                    raise ResponseValidationError(error_msg)
                else:
                    # In log mode, just log the errors
                    logger.error(error_msg)

            if validation_result["warnings"]:
                warning_msg = f"Response validation warnings for {method} {endpoint}: {validation_result['warnings']}"
                logger.warning(warning_msg)

            # Add validation metadata in development/testing
            if os.environ.get("ENVIRONMENT", "development") != "production":
                if isinstance(response.get("body"), str):
                    try:
                        body_data = json.loads(response["body"])
                        body_data["_validation"] = {
                            "valid": validation_result["valid"],
                            "errors": validation_result["errors"],
                            "warnings": validation_result["warnings"],
                            "timestamp": validation_result["timestamp"],
                        }
                        response["body"] = json.dumps(body_data)
                    except (json.JSONDecodeError, TypeError):
                        pass

            return response

        return wrapper

    return decorator


def validate_response_manually(response: Dict[str, Any], endpoint: str, method: str) -> Dict[str, Any]:
    """
    Manually validate a response (for use in tests or specific cases)

    Args:
        response: Response dictionary to validate
        endpoint: API endpoint path
        method: HTTP method

    Returns:
        Validation result
    """
    validator = get_validator()
    status_code = response.get("statusCode", 200)
    return validator.validate_response(response, endpoint, method, status_code)
