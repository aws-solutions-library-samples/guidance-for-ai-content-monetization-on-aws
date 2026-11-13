"""
Unit tests for response helper functions

Tests the consolidated response helper functions for consistent CORS headers
and response formats across all endpoints.
"""

import pytest
import json
from shared.utils.response_helpers import (
    create_success_response,
    create_error_response,
    create_text_response,
    create_redirect_response,
    create_options_response,
    create_validation_error_response,
    create_authentication_error_response,
    create_authorization_error_response,
    create_not_found_response,
    create_rate_limit_response,
)


class TestResponseHelpers:
    """Test cases for response helper functions"""

    def test_create_success_response_basic(self):
        """Test basic success response creation"""
        data = {"message": "Success", "id": 123}

        response = create_success_response(data)

        assert response["statusCode"] == 200
        assert response["headers"]["Content-Type"] == "application/json"
        # TM-02: with no allow-list configured (ALLOWED_ORIGINS unset, no SSM), the
        # credentialed CORS origin header is OMITTED (fail safe) — never a literal "*".
        assert "Access-Control-Allow-Origin" not in response["headers"]
        assert response["headers"]["Access-Control-Allow-Methods"] == "GET,POST,PUT,DELETE,OPTIONS"

        body = json.loads(response["body"])
        assert body["message"] == "Success"
        assert body["id"] == 123

    def test_create_success_response_custom_status(self):
        """Test success response with custom status code"""
        data = {"created": True}

        response = create_success_response(data, status_code=201)

        assert response["statusCode"] == 201
        body = json.loads(response["body"])
        assert body["created"] is True

    def test_create_success_response_additional_headers(self):
        """Test success response with additional headers"""
        data = {"data": "test"}
        additional_headers = {"X-Custom-Header": "custom-value"}

        response = create_success_response(data, additional_headers=additional_headers)

        assert response["headers"]["X-Custom-Header"] == "custom-value"
        assert response["headers"]["Content-Type"] == "application/json"  # Should still have default headers

    def test_create_error_response_basic(self):
        """Test basic error response creation"""
        response = create_error_response(400, "Bad request")

        assert response["statusCode"] == 400
        assert response["headers"]["Content-Type"] == "application/json"
        # TM-02: no allow-list configured => origin header omitted, never "*".
        assert "Access-Control-Allow-Origin" not in response["headers"]

        body = json.loads(response["body"])
        assert body["error"] == "Bad request"
        assert body["status_code"] == 400
        assert "request_id" in body
        assert body["request_id"].startswith("req_")

    def test_create_error_response_with_details(self):
        """Test error response with details"""
        details = {"field": "username", "issue": "required"}

        response = create_error_response(400, "Validation failed", details=details)

        body = json.loads(response["body"])
        assert body["error"] == "Validation failed"
        assert body["details"] == details

    def test_create_error_response_with_error_code(self):
        """Test error response with error code"""
        response = create_error_response(401, "Unauthorized", error_code="AUTH_FAILED")

        body = json.loads(response["body"])
        assert body["error"] == "Unauthorized"
        assert body["error_code"] == "AUTH_FAILED"

    def test_create_text_response_basic(self):
        """Test basic text response creation"""
        content = "This is plain text content"

        response = create_text_response(content)

        assert response["statusCode"] == 200
        assert response["headers"]["Content-Type"] == "text/plain"
        # TM-02: no allow-list configured => origin header omitted, never "*".
        assert "Access-Control-Allow-Origin" not in response["headers"]
        assert response["body"] == content

    def test_create_text_response_custom_content_type(self):
        """Test text response with custom content type"""
        content = "<html><body>Hello</body></html>"

        response = create_text_response(content, content_type="text/html")

        assert response["headers"]["Content-Type"] == "text/html"
        assert response["body"] == content

    def test_create_redirect_response_basic(self):
        """Test basic redirect response creation"""
        location = "https://example.com/redirect"

        response = create_redirect_response(location)

        assert response["statusCode"] == 302
        assert response["headers"]["Location"] == location
        # TM-02: no allow-list configured => origin header omitted, never "*".
        assert "Access-Control-Allow-Origin" not in response["headers"]
        assert response["body"] == ""

    def test_create_redirect_response_custom_status(self):
        """Test redirect response with custom status code"""
        location = "https://example.com/permanent"

        response = create_redirect_response(location, status_code=301)

        assert response["statusCode"] == 301
        assert response["headers"]["Location"] == location

    def test_create_options_response_basic(self):
        """Test basic OPTIONS response creation"""
        response = create_options_response()

        assert response["statusCode"] == 200
        # TM-02: no allow-list configured => origin header omitted, never "*".
        assert "Access-Control-Allow-Origin" not in response["headers"]
        assert response["headers"]["Access-Control-Allow-Methods"] == "GET,POST,PUT,DELETE,OPTIONS"
        assert response["headers"]["Access-Control-Max-Age"] == "86400"
        assert response["body"] == ""

    def test_create_options_response_custom_methods(self):
        """Test OPTIONS response with custom allowed methods"""
        response = create_options_response(allowed_methods="GET,POST")

        assert response["headers"]["Access-Control-Allow-Methods"] == "GET,POST"

    def test_create_validation_error_response(self):
        """Test validation error response creation"""
        validation_errors = {"username": "Username is required", "email": "Invalid email format"}

        response = create_validation_error_response(validation_errors)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["error"] == "Validation failed"
        assert body["details"] == validation_errors
        assert body["error_code"] == "VALIDATION_ERROR"

    def test_create_authentication_error_response_default(self):
        """Test authentication error response with default message"""
        response = create_authentication_error_response()

        assert response["statusCode"] == 401
        body = json.loads(response["body"])
        assert body["error"] == "Authentication required"
        assert body["error_code"] == "AUTHENTICATION_ERROR"

    def test_create_authentication_error_response_custom(self):
        """Test authentication error response with custom message"""
        response = create_authentication_error_response("Invalid API key")

        body = json.loads(response["body"])
        assert body["error"] == "Invalid API key"
        assert body["error_code"] == "AUTHENTICATION_ERROR"

    def test_create_authorization_error_response_default(self):
        """Test authorization error response with default message"""
        response = create_authorization_error_response()

        assert response["statusCode"] == 403
        body = json.loads(response["body"])
        assert body["error"] == "Insufficient permissions"
        assert body["error_code"] == "AUTHORIZATION_ERROR"

    def test_create_authorization_error_response_custom(self):
        """Test authorization error response with custom message"""
        response = create_authorization_error_response("Access denied to this resource")

        body = json.loads(response["body"])
        assert body["error"] == "Access denied to this resource"
        assert body["error_code"] == "AUTHORIZATION_ERROR"

    def test_create_not_found_response_default(self):
        """Test not found error response with default resource name"""
        response = create_not_found_response()

        assert response["statusCode"] == 404
        body = json.loads(response["body"])
        assert body["error"] == "Resource not found"
        assert body["error_code"] == "NOT_FOUND"

    def test_create_not_found_response_custom(self):
        """Test not found error response with custom resource name"""
        response = create_not_found_response("User")

        body = json.loads(response["body"])
        assert body["error"] == "User not found"
        assert body["error_code"] == "NOT_FOUND"

    def test_create_rate_limit_response_basic(self):
        """Test rate limit response without retry-after"""
        response = create_rate_limit_response()

        assert response["statusCode"] == 429
        body = json.loads(response["body"])
        assert body["error"] == "Rate limit exceeded"
        assert body["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert "Retry-After" not in response["headers"]

    def test_create_rate_limit_response_with_retry_after(self):
        """Test rate limit response with retry-after header"""
        response = create_rate_limit_response(retry_after=60)

        assert response["statusCode"] == 429
        assert response["headers"]["Retry-After"] == "60"
        body = json.loads(response["body"])
        assert body["error"] == "Rate limit exceeded"

    def test_cors_headers_consistency(self):
        """TM-02: CORS static headers are consistent and NO response emits a '*' origin.

        With no allow-list configured, the credentialed Access-Control-Allow-Origin
        header is omitted everywhere (fail safe); the static Allow-Headers /
        Allow-Methods are always present and unchanged.
        """
        responses = [
            create_success_response({"test": "data"}),
            create_error_response(400, "Error"),
            create_text_response("text"),
            create_redirect_response("https://example.com"),
            create_options_response(),
            create_validation_error_response({"field": "error"}),
            create_authentication_error_response(),
            create_authorization_error_response(),
            create_not_found_response(),
            create_rate_limit_response(),
        ]

        for response in responses:
            # Never a wildcard origin on any response path.
            assert response["headers"].get("Access-Control-Allow-Origin") != "*"
            assert "Access-Control-Allow-Headers" in response["headers"]
            assert (
                "Content-Type,X-Amz-Date,Authorization,X-Api-Key"
                in response["headers"]["Access-Control-Allow-Headers"]
            )

    def test_json_serialization_with_datetime(self):
        """Test JSON serialization with datetime objects"""
        from datetime import datetime

        data = {"timestamp": datetime(2025, 1, 15, 10, 30, 0), "message": "Test with datetime"}

        response = create_success_response(data)

        # Should not raise an exception
        body = json.loads(response["body"])
        assert "timestamp" in body
        assert body["message"] == "Test with datetime"

    def test_response_structure_consistency(self):
        """Test that all JSON responses have consistent structure"""
        # Success response
        success_response = create_success_response({"data": "test"})
        success_body = json.loads(success_response["body"])
        assert "data" in success_body

        # Error responses should all have 'error' field
        error_responses = [
            create_error_response(400, "Bad request"),
            create_validation_error_response({"field": "error"}),
            create_authentication_error_response(),
            create_authorization_error_response(),
            create_not_found_response(),
            create_rate_limit_response(),
        ]

        for response in error_responses:
            body = json.loads(response["body"])
            assert "error" in body
            assert "status_code" in body or "error_code" in body

    def test_header_override_behavior(self):
        """Test that additional headers can override default headers"""
        additional_headers = {
            "Content-Type": "application/custom+json",
            "Access-Control-Allow-Origin": "https://specific-domain.com",
        }

        response = create_success_response({"test": "data"}, additional_headers=additional_headers)

        assert response["headers"]["Content-Type"] == "application/custom+json"
        assert response["headers"]["Access-Control-Allow-Origin"] == "https://specific-domain.com"
        # Other CORS headers should still be present
        assert "Access-Control-Allow-Headers" in response["headers"]

    def test_empty_data_handling(self):
        """Test handling of empty or None data"""
        # Empty dict
        response1 = create_success_response({})
        body1 = json.loads(response1["body"])
        assert body1 == {}

        # None data
        response2 = create_success_response(None)
        body2 = json.loads(response2["body"])
        assert body2 is None

        # Empty list
        response3 = create_success_response([])
        body3 = json.loads(response3["body"])
        assert body3 == []


class TestCorsOriginResolver:
    """TM-02 (security-hardening-phase2): the _cors_origin / cors_headers policy.

    Covers the Requirement-2 acceptance criteria at the unit tier:
      * 2.2  ALLOWED_ORIGINS env precedence, then /cm/ui/origin SSM fallback.
      * 2.3  echo a matching request Origin; otherwise the configured default; never '*'.
      * 2a   neither source resolves => omit the credentialed CORS origin header.
      * 3.4a demo path: with ALLOWED_ORIGINS unset, resolve the stubbed SSM value and
             echo a matching CloudFront Origin.
    """

    @pytest.fixture(autouse=True)
    def _reset_cors_state(self, monkeypatch):
        """Clear the per-container SSM cache and ALLOWED_ORIGINS before each test."""
        from shared.utils import response_helpers as rh

        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        rh._ssm_origin_cache["value"] = None
        rh._ssm_origin_cache["fetched"] = False
        rh._ssm_origin_cache["fetched_at"] = 0.0
        # Block any real SSM call unless a test opts in via its own patch.
        monkeypatch.setattr(rh, "_origin_from_ssm", lambda: None)
        yield

    def _event(self, origin):
        return {"headers": {"Origin": origin}}

    def test_env_allowlist_echoes_matching_origin(self, monkeypatch):
        from shared.utils.response_helpers import create_success_response

        monkeypatch.setenv("ALLOWED_ORIGINS", "https://portal.publisher.com,https://admin.publisher.com")
        resp = create_success_response({"ok": True}, event=self._event("https://admin.publisher.com"))
        assert resp["headers"]["Access-Control-Allow-Origin"] == "https://admin.publisher.com"

    def test_env_allowlist_non_match_returns_configured_default_not_wildcard(self, monkeypatch):
        from shared.utils.response_helpers import create_success_response

        monkeypatch.setenv("ALLOWED_ORIGINS", "https://portal.publisher.com,https://admin.publisher.com")
        resp = create_success_response({"ok": True}, event=self._event("https://evil.example.com"))
        origin = resp["headers"]["Access-Control-Allow-Origin"]
        assert origin == "https://portal.publisher.com"  # first entry = configured default
        assert origin != "*"

    def test_no_source_resolved_omits_cors_origin(self):
        from shared.utils.response_helpers import create_success_response

        resp = create_success_response({"ok": True}, event=self._event("https://portal.publisher.com"))
        assert "Access-Control-Allow-Origin" not in resp["headers"]

    def test_ssm_demo_resolution_echoes_cloudfront_origin(self, monkeypatch):
        """3.4a: ALLOWED_ORIGINS unset -> resolver reads the stubbed /cm/ui/origin SSM
        value and echoes a matching CloudFront Origin."""
        from shared.utils import response_helpers as rh
        from shared.utils.response_helpers import create_success_response

        cf_origin = "https://d1234abcd.cloudfront.net"
        monkeypatch.setattr(rh, "_origin_from_ssm", lambda: cf_origin)
        resp = create_success_response({"ok": True}, event=self._event(cf_origin))
        assert resp["headers"]["Access-Control-Allow-Origin"] == cf_origin

    def test_ssm_demo_resolution_non_match_returns_ssm_default(self, monkeypatch):
        from shared.utils import response_helpers as rh
        from shared.utils.response_helpers import create_success_response

        cf_origin = "https://d1234abcd.cloudfront.net"
        monkeypatch.setattr(rh, "_origin_from_ssm", lambda: cf_origin)
        resp = create_success_response({"ok": True}, event=self._event("https://somewhere-else.example"))
        assert resp["headers"]["Access-Control-Allow-Origin"] == cf_origin
        assert resp["headers"]["Access-Control-Allow-Origin"] != "*"

    def test_env_precedence_over_ssm(self, monkeypatch):
        from shared.utils import response_helpers as rh
        from shared.utils.response_helpers import create_success_response

        monkeypatch.setenv("ALLOWED_ORIGINS", "https://portal.publisher.com")
        monkeypatch.setattr(rh, "_origin_from_ssm", lambda: "https://d1234abcd.cloudfront.net")
        resp = create_success_response({"ok": True}, event=self._event("https://portal.publisher.com"))
        assert resp["headers"]["Access-Control-Allow-Origin"] == "https://portal.publisher.com"

    def test_static_cors_values_unchanged(self, monkeypatch):
        """Requirement 2.6: Allow-Headers / Allow-Methods / Max-Age are unchanged."""
        from shared.utils.response_helpers import create_success_response

        monkeypatch.setenv("ALLOWED_ORIGINS", "https://portal.publisher.com")
        headers = create_success_response({"ok": True}, event=self._event("https://portal.publisher.com"))["headers"]
        assert headers["Access-Control-Allow-Headers"] == "Content-Type,X-Amz-Date,Authorization,X-Api-Key,If-Match"
        assert headers["Access-Control-Allow-Methods"] == "GET,POST,PUT,DELETE,OPTIONS"
        assert headers["Access-Control-Max-Age"] == "86400"
