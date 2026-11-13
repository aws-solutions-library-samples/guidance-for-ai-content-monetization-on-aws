"""
Unit tests for partner endpoints in UI Backend Lambda

Tests the PUT /partners/{partner_id} and GET /analytics/access-logs endpoints
"""

import pytest
import json
import boto3
from moto import mock_aws
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from decimal import Decimal
import sys
import os

# Add lambda directory to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "ui_backend"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "shared"))

# NOTE (task 8.5): the inline ``handle_analytics_access_logs`` handler was deleted from
# ``ui_backend.handler`` when the ``/analytics/*`` dispatcher entry was flipped to
# ``ui_backend/routes/analytics.py`` (Requirement 16.3). Its import and the ``/analytics/access-logs``
# tests that exercised it were removed here; analytics access-logs route equivalence + auth are now
# enforced by ``tests/ui_backend/test_analytics_characterization.py``.
#
# NOTE (task 10.4): the inline ``handle_update_partner`` was deleted from ``ui_backend.handler`` when
# both partners dispatcher entries were flipped to ``ui_backend/routes/partners.py`` (Requirement
# 16.3). Golden-master form: this suite now exercises the extracted handler in its new home,
# ``ui_backend/routes/partners.py``, which delegates the DynamoDB write to the Service_Layer single
# home ``services.partners_service.PartnersService`` (Requirement 7.2). The handler keeps the same
# ``(headers, request_data, event, **kwargs)`` signature and behavior, so these tests gate the
# migrated route module directly.
from ui_backend.routes.partners import handle_update_partner


@mock_aws
class TestPartnerEndpoints:
    """Test class for partner management endpoints"""

    def setup_method(self, method):
        """Set up test environment before each test"""
        # Create mock DynamoDB tables
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create partners table
        self.partners_table = self.dynamodb.create_table(
            TableName="cm-partners",
            KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "api_key_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "api-key-id-index",
                    "KeySchema": [{"AttributeName": "api_key_id", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "INCLUDE", "NonKeyAttributes": ["partner_id", "usage_plan_id", "status"]},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create access logs table
        self.access_logs_table = self.dynamodb.create_table(
            TableName="iab-access-logs",
            KeySchema=[{"AttributeName": "log_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "log_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Set environment variables
        os.environ["PARTNER_TABLE_NAME"] = "cm-partners"
        os.environ["ACCESS_LOG_TABLE_NAME"] = "iab-access-logs"

        # Create test partner data
        self.test_partner = {
            "PK": "PARTNER#partner_001",
            "SK": "META",
            "partner_id": "partner_001",
            "partner_name": "Test Partner Corp",
            "contact_email": "test@partner.com",
            "pricing_tier": "standard",
            "allowed_endpoints": ["queryContent", "submitLog"],
            "status": "active",
            "identity_source": "direct",
            "created_at": "2024-01-01T00:00:00Z",
        }

        # Insert test partner
        self.partners_table.put_item(Item=self.test_partner)

        # Create test access logs
        self.test_access_logs = [
            {
                "log_id": "log_001",
                "partner_id": "partner_001",
                "partner_name": "Test Partner Corp",
                "timestamp": "2025-01-15T14:30:15Z",
                "endpoint": "/query",
                "request_type": "POST",
                "response_code": 200,
                "ip_address": "192.168.1.100",
                "user_agent": "TestBot/1.0",
                "content_accessed": "article_12345",
                "price_charged": Decimal("0.05"),
                "processing_time_ms": 245,
            },
            {
                "log_id": "log_002",
                "partner_id": "partner_002",
                "partner_name": "Another Partner",
                "timestamp": "2025-01-15T14:29:45Z",
                "endpoint": "/search",
                "request_type": "POST",
                "response_code": 200,
                "ip_address": "10.0.1.50",
                "user_agent": "AnotherBot/2.1",
                "content_accessed": "report_67890",
                "price_charged": Decimal("0.25"),
                "processing_time_ms": 180,
            },
            {
                "log_id": "log_003",
                "partner_id": "partner_001",
                "partner_name": "Test Partner Corp",
                "timestamp": "2025-01-15T13:28:30Z",
                "endpoint": "/bid",
                "request_type": "POST",
                "response_code": 200,
                "ip_address": "192.168.1.100",
                "user_agent": "TestBot/1.0",
                "content_accessed": "research_paper_11111",
                "price_charged": Decimal("0.50"),
                "processing_time_ms": 320,
            },
        ]

        # Insert test access logs
        for log_entry in self.test_access_logs:
            self.access_logs_table.put_item(Item=log_entry)

    def teardown_method(self, method):
        """Clean up after each test"""
        # Clean up environment variables
        if "PARTNER_TABLE_NAME" in os.environ:
            del os.environ["PARTNER_TABLE_NAME"]
        if "ACCESS_LOG_TABLE_NAME" in os.environ:
            del os.environ["ACCESS_LOG_TABLE_NAME"]

    def create_mock_event(self, path: str, method: str = "GET", query_params: dict = None, body: dict = None):
        """Create a mock API Gateway event"""
        event = {
            "httpMethod": method,
            "path": path,
            "headers": {"Authorization": "Bearer mock_token", "Content-Type": "application/json"},
            "queryStringParameters": query_params,
            "body": json.dumps(body) if body else None,
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "sub": "test_user_123",
                        "email": "test@publisher.com",
                        "name": "Test Publisher",
                        "custom:publisher_id": "test_publisher",
                    }
                }
            },
        }
        return event

    def test_update_partner_success(self):
        """Test successful partner update"""
        # Prepare update data
        update_data = {
            "partner_name": "Updated Partner Corp",
            "contact_email": "updated@partner.com",
            "pricing_tier": "premium",
            "api_quota": 20000,  # retired field: ignored, never persisted
            "status": "active",
        }

        # Create event
        event = self.create_mock_event("/partners/partner_001", "PUT", body=update_data)

        # Call the handler
        response = handle_update_partner(
            {}, update_data, event, authenticated_user={"user_id": "test_user_123", "publisher_id": "test_publisher"}
        )

        # Verify response
        assert response["statusCode"] == 200
        response_body = json.loads(response["body"])

        assert response_body["partner_id"] == "partner_001"
        assert response_body["partner_name"] == "Updated Partner Corp"
        assert response_body["contact_email"] == "updated@partner.com"
        assert response_body["pricing_tier"] == "premium"
        assert "api_quota" not in response_body
        assert response_body["status"] == "active"
        assert response_body["active_key_count"] == 0
        assert "updated_at" in response_body
        stored = self.partners_table.get_item(Key={"PK": "PARTNER#partner_001", "SK": "META"})["Item"]
        assert "api_quota" not in stored

    def test_update_partner_not_found(self):
        """Test partner update when partner doesn't exist"""
        update_data = {"partner_name": "Non-existent Partner"}

        event = self.create_mock_event("/partners/nonexistent_partner", "PUT", body=update_data)

        response = handle_update_partner(
            {}, update_data, event, authenticated_user={"user_id": "test_user_123", "publisher_id": "test_publisher"}
        )

        assert response["statusCode"] == 404
        response_body = json.loads(response["body"])
        assert "not found" in response_body["error"].lower()

    def test_update_partner_invalid_path(self):
        """Test partner update with invalid path"""
        update_data = {"partner_name": "Test Partner"}

        event = self.create_mock_event("/invalid/path", "PUT", body=update_data)

        response = handle_update_partner(
            {}, update_data, event, authenticated_user={"user_id": "test_user_123", "publisher_id": "test_publisher"}
        )

        assert response["statusCode"] == 400
        response_body = json.loads(response["body"])
        assert "invalid partner id" in response_body["error"].lower()

    def test_update_partner_no_fields(self):
        """Test partner update with no valid fields"""
        update_data = {}

        event = self.create_mock_event("/partners/partner_001", "PUT", body=update_data)

        response = handle_update_partner(
            {}, update_data, event, authenticated_user={"user_id": "test_user_123", "publisher_id": "test_publisher"}
        )

        assert response["statusCode"] == 400
        response_body = json.loads(response["body"])
        assert "no valid fields" in response_body["error"].lower()

    # NOTE (task 8.5): ``test_get_access_logs_success``, ``test_get_access_logs_with_endpoint_filter``,
    # ``test_get_access_logs_no_filters``, ``test_get_access_logs_with_time_period``, and
    # ``test_get_access_logs_database_error`` were removed here. They exercised the inline
    # ``handle_analytics_access_logs`` handler that was deleted when the ``/analytics/*`` dispatcher
    # entry was flipped to ``ui_backend/routes/analytics.py`` (Requirement 16.3). Analytics
    # access-logs route equivalence + auth (including the DynamoDB-failure mock fallback) are now
    # enforced by ``tests/ui_backend/test_analytics_characterization.py``.

    @patch(
        "services.partners_service.PartnersService.update_partner",
        side_effect=Exception("Database connection failed"),
    )
    def test_update_partner_database_error(self, mock_update):
        """Test partner update when the partners service (DynamoDB write) fails.

        The migrated handler delegates the write to ``PartnersService.update_partner``; a generic
        service failure propagates past the typed 404/400 mappings to the handler's outer
        exception guard, which returns the same 500 ``Failed to update partner: ...`` response as
        before.
        """
        update_data = {"partner_name": "Updated Partner"}
        event = self.create_mock_event("/partners/partner_001", "PUT", body=update_data)

        response = handle_update_partner(
            {}, update_data, event, authenticated_user={"user_id": "test_user_123", "publisher_id": "test_publisher"}
        )

        assert response["statusCode"] == 500
        response_body = json.loads(response["body"])
        assert "failed to update partner" in response_body["error"].lower()


if __name__ == "__main__":
    pytest.main([__file__])
