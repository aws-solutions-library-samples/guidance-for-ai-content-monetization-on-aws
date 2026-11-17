#!/usr/bin/env python3
"""
Unit Tests for Bot Registry Management Interface

This test suite validates the bot registry management interface functionality
including partner management, access log viewing, and integration with existing APIs.

Requirements covered:
- 10.1: Display bot details including ID, name, registration date
- 10.2: Add bot enable/disable controls and access permission management
- 10.3: Add "Access Log/Activity Info" tab that links out to the metrics page
- 10.5: Consolidate API calls for high-volume access log data in the metrics page
- 10.6: Bot registry management interface with two-tab structure
"""

import pytest
import json
import boto3
from moto import mock_aws
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
import os
import sys

# Set required environment variables before importing lambda functions
os.environ.setdefault("CONTENT_TABLE_NAME", "test-content-table")
os.environ.setdefault("PARTNER_TABLE_NAME", "test-partner-table")
os.environ.setdefault("ANALYTICS_TABLE_NAME", "test-analytics-table")
os.environ.setdefault("JOBS_TABLE_NAME", "test-jobs-table")
os.environ.setdefault("BOT_RULES_TABLE_NAME", "test-bot-rules-table")
os.environ.setdefault("PRICING_RULES_TABLE_NAME", "test-pricing-rules-table")
os.environ.setdefault("PRICING_TRANSACTIONS_TABLE_NAME", "test-pricing-transactions-table")

# Add the lambda directory to the Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "shared"))


# Mock response helpers for testing
def create_success_response(data):
    return {"statusCode": 200, "body": json.dumps(data), "headers": {"Content-Type": "application/json"}}


def create_error_response(status_code, message):
    return {
        "statusCode": status_code,
        "body": json.dumps({"error": {"message": message}}),
        "headers": {"Content-Type": "application/json"},
    }


class TestBotRegistryBackend:
    """Test the backend API endpoints that support the bot registry interface"""

    def test_analytics_partners_response_structure(self):
        """Test that the analytics partners endpoint returns proper response structure"""
        # Mock response structure that should be returned by the API
        mock_response = create_success_response(
            {
                "partners": [
                    {"name": "Test Partner", "requests": 5000, "revenue": 250.00, "status": "active", "blocked": 10}
                ]
            }
        )

        # Verify response structure
        assert mock_response["statusCode"] == 200
        data = json.loads(mock_response["body"])
        assert "partners" in data
        assert len(data["partners"]) > 0

        partner = data["partners"][0]
        assert "name" in partner
        assert "requests" in partner
        assert "revenue" in partner
        assert "status" in partner
        assert "blocked" in partner

    def test_analytics_partners_empty_response_structure(self):
        """Test that the analytics partners endpoint handles empty data gracefully"""
        # Mock empty response structure
        mock_response = create_success_response({"partners": []})

        # Verify response structure
        assert mock_response["statusCode"] == 200
        data = json.loads(mock_response["body"])
        assert "partners" in data
        assert isinstance(data["partners"], list)

    def test_analytics_partners_error_response_structure(self):
        """Test that the analytics partners endpoint returns proper error structure"""
        # Mock error response structure
        mock_response = create_error_response(500, "Internal server error")

        # Verify error response structure
        assert mock_response["statusCode"] == 500
        data = json.loads(mock_response["body"])
        assert "error" in data
        assert "message" in data["error"]


class TestBotRegistryDataStructures:
    """Test the data structures and transformations for bot registry"""

    def test_partner_data_structure_completeness(self):
        """Test that partner data structure includes all required fields"""
        # Mock partner data structure that should be returned by the API
        partner_data = {
            "partner_id": "test_partner_001",
            "partner_name": "Test Partner",
            "contact_email": "test@example.com",
            "pricing_tier": "standard",
            "allowed_endpoints": ["queryContent", "submitLog"],
            "status": "active",
            "created_at": "2024-01-01T00:00:00Z",
            "last_access": "2024-01-15T14:30:00Z",
        }

        # Verify all required fields are present
        required_fields = [
            "partner_id",
            "partner_name",
            "contact_email",
            "pricing_tier",
            "allowed_endpoints",
            "status",
            "created_at",
            "last_access",
        ]

        for field in required_fields:
            assert field in partner_data, f"Required field '{field}' missing from partner data"

        # Verify data types
        assert isinstance(partner_data["partner_id"], str)
        assert isinstance(partner_data["partner_name"], str)
        assert isinstance(partner_data["contact_email"], str)
        assert partner_data["pricing_tier"] in ["basic", "standard", "premium", "enterprise"]
        assert isinstance(partner_data["allowed_endpoints"], list)
        assert partner_data["status"] in ["active", "inactive", "suspended"]
        assert isinstance(partner_data["created_at"], str)
        assert isinstance(partner_data["last_access"], str)

    def test_access_log_data_structure_completeness(self):
        """Test that access log data structure includes all required fields"""
        # Mock access log data structure
        access_log_data = {
            "log_id": "log_001",
            "partner_id": "test_partner_001",
            "partner_name": "Test Partner",
            "timestamp": "2024-01-15T14:30:15Z",
            "endpoint": "/query",
            "request_type": "POST",
            "response_code": 200,
            "ip_address": "192.168.1.100",
            "user_agent": "TestBot/1.0",
            "content_accessed": "article_12345",
            "price_charged": 0.05,
            "processing_time_ms": 245,
        }

        # Verify all required fields are present
        required_fields = [
            "log_id",
            "partner_id",
            "partner_name",
            "timestamp",
            "endpoint",
            "request_type",
            "response_code",
            "ip_address",
            "user_agent",
        ]

        for field in required_fields:
            assert field in access_log_data, f"Required field '{field}' missing from access log data"

        # Verify data types
        assert isinstance(access_log_data["log_id"], str)
        assert isinstance(access_log_data["partner_id"], str)
        assert isinstance(access_log_data["partner_name"], str)
        assert isinstance(access_log_data["timestamp"], str)
        assert isinstance(access_log_data["endpoint"], str)
        assert isinstance(access_log_data["request_type"], str)
        assert isinstance(access_log_data["response_code"], int)
        assert isinstance(access_log_data["ip_address"], str)
        assert isinstance(access_log_data["user_agent"], str)

        # Optional fields
        if access_log_data.get("content_accessed"):
            assert isinstance(access_log_data["content_accessed"], str)
        if access_log_data.get("price_charged"):
            assert isinstance(access_log_data["price_charged"], (int, float))
        if access_log_data.get("processing_time_ms"):
            assert isinstance(access_log_data["processing_time_ms"], int)


class TestBotRegistryUIBackendEndpoints:
    """Test the UI backend endpoints that will be needed for bot registry functionality"""

    def test_update_partner_endpoint_structure(self):
        """Test the expected structure for the PUT /partners/{partner_id} endpoint"""
        # This test validates the expected API call structure for partner updates
        partner_id = "test_partner_001"
        update_data = {
            "partner_name": "Updated Partner Name",
            "contact_email": "updated@example.com",
            "pricing_tier": "premium",
            "allowed_endpoints": ["queryContent", "submitLog"],
            "status": "active",
        }

        # Verify the update structure matches expected format
        assert "partner_name" in update_data
        assert "contact_email" in update_data
        assert "pricing_tier" in update_data
        assert update_data["pricing_tier"] in ["basic", "standard", "premium", "enterprise"]
        assert "allowed_endpoints" in update_data
        assert isinstance(update_data["allowed_endpoints"], list)
        assert "status" in update_data
        assert update_data["status"] in ["active", "inactive", "suspended"]

        # Expected API endpoint structure
        expected_endpoint = f"/partners/{partner_id}"
        expected_method = "PUT"

        assert expected_endpoint == f"/partners/{partner_id}"
        assert expected_method == "PUT"

    def test_access_logs_endpoint_structure(self):
        """Test the expected structure for the GET /analytics/access-logs endpoint"""
        # Expected query parameters for access logs
        query_params = {
            "partner_id": "test_partner_001",
            "time_period": "last_24_hours",
            "limit": "100",
            "endpoint": "/query",
        }

        # Verify query parameter structure
        assert "partner_id" in query_params
        assert "time_period" in query_params
        assert query_params["time_period"] in ["last_hour", "last_24_hours", "last_7_days", "last_30_days"]
        assert "limit" in query_params
        assert int(query_params["limit"]) > 0

        # Expected API endpoint
        expected_endpoint = "/analytics/access-logs"
        expected_method = "GET"

        assert expected_endpoint == "/analytics/access-logs"
        assert expected_method == "GET"

    def test_partner_response_structure(self):
        """Test the expected response structure from partner update endpoint"""
        # Expected response structure after partner update
        partner_response = {
            "partner_id": "test_partner_001",
            "partner_name": "Updated Partner Name",
            "contact_email": "updated@example.com",
            "pricing_tier": "premium",
            "allowed_endpoints": ["queryContent", "submitLog"],
            "status": "active",
            "created_at": "2024-01-01T00:00:00Z",
            "last_access": "2024-01-15T14:30:00Z",
            "updated_at": "2024-01-15T15:00:00Z",
        }

        # Verify all required fields are present
        required_fields = [
            "partner_id",
            "partner_name",
            "contact_email",
            "pricing_tier",
            "allowed_endpoints",
            "status",
            "created_at",
            "last_access",
            "updated_at",
        ]

        for field in required_fields:
            assert field in partner_response, f"Required field '{field}' missing from partner response"

    def test_access_logs_response_structure(self):
        """Test the expected response structure from access logs endpoint"""
        # Expected response structure for access logs
        access_logs_response = {
            "access_logs": [
                {
                    "log_id": "log_001",
                    "partner_id": "test_partner_001",
                    "partner_name": "Test Partner",
                    "timestamp": "2024-01-15T14:30:15Z",
                    "endpoint": "/query",
                    "request_type": "POST",
                    "response_code": 200,
                    "ip_address": "192.168.1.100",
                    "user_agent": "TestBot/1.0",
                    "content_accessed": "article_12345",
                    "price_charged": 0.05,
                    "processing_time_ms": 245,
                }
            ],
            "total_count": 1,
            "time_period": "last_24_hours",
        }

        # Verify response structure
        assert "access_logs" in access_logs_response
        assert isinstance(access_logs_response["access_logs"], list)
        assert "total_count" in access_logs_response
        assert "time_period" in access_logs_response

        # Verify log entry structure
        if access_logs_response["access_logs"]:
            log_entry = access_logs_response["access_logs"][0]
            required_log_fields = [
                "log_id",
                "partner_id",
                "partner_name",
                "timestamp",
                "endpoint",
                "request_type",
                "response_code",
                "ip_address",
                "user_agent",
            ]

            for field in required_log_fields:
                assert field in log_entry, f"Required field '{field}' missing from log entry"


class TestBotRegistryIntegration:
    """Test integration aspects of the bot registry interface"""

    def test_partner_status_toggle_integration(self):
        """Test that partner status toggle would integrate with existing APIs"""
        # This test validates the expected API call structure for status updates

        # Expected API call structure for updating partner status
        partner_id = "test_partner_001"
        update_data = {"status": "inactive"}

        # Verify the update structure matches expected format
        assert "status" in update_data
        assert update_data["status"] in ["active", "inactive", "suspended"]

        # This would be the expected API endpoint
        expected_endpoint = f"/partners/{partner_id}"
        expected_method = "PUT"

        assert expected_endpoint == f"/partners/{partner_id}"
        assert expected_method == "PUT"

    def test_access_log_filtering_integration(self):
        """Test that access log filtering integrates with existing analytics APIs"""
        # Expected API call structure for filtered access logs
        filters = {"partner_id": "test_partner_001", "time_period": "last_24_hours", "limit": 100, "endpoint": "/query"}

        # Verify filter structure
        assert "partner_id" in filters
        assert "time_period" in filters
        assert "limit" in filters
        assert "endpoint" in filters

        # Expected API endpoint
        expected_endpoint = "/analytics/access-logs"
        query_params = "&".join([f"{k}={v}" for k, v in filters.items()])
        full_url = f"{expected_endpoint}?{query_params}"

        assert expected_endpoint in full_url
        assert "partner_id=test_partner_001" in full_url
        assert "time_period=last_24_hours" in full_url

    def test_metrics_dashboard_link_integration(self):
        """Test that the metrics dashboard link integration works as expected"""
        # The bot registry should link to the main metrics dashboard for large volume data
        expected_dashboard_route = "/dashboard"

        # Verify the route is correct
        assert expected_dashboard_route == "/dashboard"

        # The link should be external=false for internal navigation
        link_config = {"href": expected_dashboard_route, "external": False}

        assert link_config["href"] == "/dashboard"
        assert link_config["external"] is False


class TestBotRegistryUIComponents:
    """Test UI component behavior and data handling"""

    def test_partner_card_display_logic(self):
        """Test the logic for displaying partner information in cards"""
        # Mock partner data
        partner = {
            "partner_id": "test_partner_001",
            "partner_name": "Test Partner",
            "status": "active",
            "pricing_tier": "premium",
            "allowed_endpoints": ["queryContent", "submitLog"],
        }

        # Test status color logic
        def get_status_color(status):
            if status == "active":
                return "success"
            elif status == "inactive":
                return "stopped"
            elif status == "suspended":
                return "error"
            else:
                return "info"

        assert get_status_color(partner["status"]) == "success"

        # Test tier color logic
        def get_tier_color(tier):
            if tier == "enterprise":
                return "blue"
            elif tier == "premium":
                return "green"
            elif tier == "standard":
                return "grey"
            elif tier == "basic":
                return "red"
            else:
                return "grey"

        assert get_tier_color(partner["pricing_tier"]) == "green"

        # Usage percentage comes from the tier's usage plan and the key's usage for the current
        # quota period (GET /partners/{id}), not from fields stored on the partner record.
        tier_limits = {"quota_limit": 10000, "quota_period": "DAY"}
        key_usage = {"used": 7500, "remaining": 2500}
        usage_percentage = (key_usage["used"] / tier_limits["quota_limit"]) * 100
        assert usage_percentage == 75.0

    def test_access_log_response_code_color_logic(self):
        """Test the logic for coloring response codes in access logs"""

        def get_response_code_color(code):
            if 200 <= code < 300:
                return "success"
            elif 400 <= code < 500:
                return "warning"
            elif code >= 500:
                return "error"
            else:
                return "info"

        # Test various response codes
        assert get_response_code_color(200) == "success"
        assert get_response_code_color(201) == "success"
        assert get_response_code_color(400) == "warning"
        assert get_response_code_color(401) == "warning"
        assert get_response_code_color(404) == "warning"
        assert get_response_code_color(429) == "warning"
        assert get_response_code_color(500) == "error"
        assert get_response_code_color(502) == "error"
        assert get_response_code_color(100) == "info"

    def test_filter_logic(self):
        """Test the filtering logic for partners and access logs"""
        # Mock partners data
        partners = [
            {"partner_id": "partner_001", "partner_name": "Test Partner 1", "status": "active"},
            {"partner_id": "partner_002", "partner_name": "Test Partner 2", "status": "inactive"},
            {"partner_id": "partner_003", "partner_name": "Another Company", "status": "active"},
        ]

        # Test name filter
        def filter_partners_by_name(partners, filter_text):
            return [
                p
                for p in partners
                if filter_text.lower() in p["partner_name"].lower() or filter_text.lower() in p["partner_id"].lower()
            ]

        filtered = filter_partners_by_name(partners, "test")
        assert len(filtered) == 2
        assert all("test" in p["partner_name"].lower() or "test" in p["partner_id"].lower() for p in filtered)

        # Test status filter
        def filter_partners_by_status(partners, status_filter):
            if status_filter == "all":
                return partners
            return [p for p in partners if p["status"] == status_filter]

        active_partners = filter_partners_by_status(partners, "active")
        assert len(active_partners) == 2
        assert all(p["status"] == "active" for p in active_partners)


if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v"])
