"""
Unit tests for shared/handlers/internal_log_handler.py (wave0 Phase 6, Req 19-21).

``tests/internal/test_log_endpoint.py`` already covers the happy paths and the
partner-usage validation/timestamp/500 branches. This suite ADDS the branches
that suite does not exercise, so no assertion is duplicated:

- ``handle_internal_log_ui_test_usage``: validation error, invalid-timestamp,
  timestamp-parsed pass-through, and the service-exception 500 path.
- ``handle_get_usage_logs``: the service-exception 500 path.
- ``handle_log_api_access``: invalid-timestamp path and the service-exception
  500 path.

Handlers are decorated with ``@require_cognito_auth``; following the established
pattern we call ``.__wrapped__`` with an injected ``authenticated_user`` to
bypass the auth decorator under unit test.
"""

import json
from datetime import datetime
from unittest.mock import Mock, patch

from shared.handlers.internal_log_handler import (
    handle_get_usage_logs,
    handle_internal_log_ui_test_usage,
    handle_log_api_access,
)

HEADERS = {"Authorization": "Bearer cognito-token"}
EVENT = {"path": "/internal/log"}
USER = {"user_id": "user123", "publisher_id": "pub123", "groups": ["publishers"]}


# --------------------------------------------------------------------------- #
# handle_internal_log_ui_test_usage — uncovered branches
# --------------------------------------------------------------------------- #
def test_ui_test_usage_validation_error_missing_field():
    result = handle_internal_log_ui_test_usage.__wrapped__(
        HEADERS, {}, EVENT, authenticated_user=USER, auth_type="cognito"
    )
    assert result["statusCode"] == 400
    body = json.loads(result["body"])
    assert body["error"] == "Validation failed"
    assert "test_usage_data" in body["details"]


def test_ui_test_usage_invalid_timestamp():
    request_data = {"test_usage_data": {"q": "ai"}, "timestamp": "not-a-timestamp"}
    result = handle_internal_log_ui_test_usage.__wrapped__(
        HEADERS, request_data, EVENT, authenticated_user=USER, auth_type="cognito"
    )
    assert result["statusCode"] == 400
    assert "Invalid timestamp format" in json.loads(result["body"])["error"]


@patch("shared.handlers.internal_log_handler.InternalLoggingService")
def test_ui_test_usage_parses_timestamp(mock_service_cls):
    mock_service = Mock()
    mock_service_cls.return_value = mock_service
    mock_service.log_ui_test_usage.return_value = {
        "log_id": "ui_test_log_abc",
        "timestamp": "2025-01-15T10:30:00Z",
        "note": "UI test usage - not billable",
    }
    request_data = {"test_usage_data": {"q": "ai"}, "timestamp": "2025-01-15T10:30:00Z"}

    result = handle_internal_log_ui_test_usage.__wrapped__(
        HEADERS, request_data, EVENT, authenticated_user=USER, auth_type="cognito"
    )
    assert result["statusCode"] == 200
    call_kwargs = mock_service.log_ui_test_usage.call_args[1]
    assert isinstance(call_kwargs["timestamp"], datetime)


@patch("shared.handlers.internal_log_handler.InternalLoggingService")
def test_ui_test_usage_service_exception_returns_500(mock_service_cls):
    mock_service = Mock()
    mock_service_cls.return_value = mock_service
    mock_service.log_ui_test_usage.side_effect = Exception("db down")

    result = handle_internal_log_ui_test_usage.__wrapped__(
        HEADERS, {"test_usage_data": {"q": "ai"}}, EVENT, authenticated_user=USER, auth_type="cognito"
    )
    assert result["statusCode"] == 500
    assert "Failed to log UI test usage" in json.loads(result["body"])["error"]


# --------------------------------------------------------------------------- #
# handle_get_usage_logs — uncovered 500 branch
# --------------------------------------------------------------------------- #
@patch("shared.handlers.internal_log_handler.InternalLoggingService")
def test_get_usage_logs_service_exception_returns_500(mock_service_cls):
    mock_service = Mock()
    mock_service_cls.return_value = mock_service
    mock_service.get_usage_logs.side_effect = Exception("scan failed")

    result = handle_get_usage_logs.__wrapped__(
        HEADERS, {"filters": {}}, EVENT, authenticated_user=USER, auth_type="cognito"
    )
    assert result["statusCode"] == 500
    assert "Failed to retrieve usage logs" in json.loads(result["body"])["error"]


# --------------------------------------------------------------------------- #
# handle_log_api_access — uncovered branches
# --------------------------------------------------------------------------- #
def test_log_api_access_invalid_timestamp():
    request_data = {
        "endpoint": "/query",
        "user_id": "partner123",
        "auth_type": "partner_key",
        "metadata": {},
        "timestamp": "bad",
    }
    result = handle_log_api_access.__wrapped__(
        HEADERS, request_data, EVENT, authenticated_user=USER, auth_type="cognito"
    )
    assert result["statusCode"] == 400
    assert "Invalid timestamp format" in json.loads(result["body"])["error"]


@patch("shared.handlers.internal_log_handler.InternalLoggingService")
def test_log_api_access_service_exception_returns_500(mock_service_cls):
    mock_service = Mock()
    mock_service_cls.return_value = mock_service
    mock_service.log_api_access.side_effect = Exception("cw down")

    request_data = {
        "endpoint": "/query",
        "user_id": "partner123",
        "auth_type": "partner_key",
        "metadata": {"ip": "1.2.3.4"},
    }
    result = handle_log_api_access.__wrapped__(
        HEADERS, request_data, EVENT, authenticated_user=USER, auth_type="cognito"
    )
    assert result["statusCode"] == 500
    assert "Failed to log API access" in json.loads(result["body"])["error"]
