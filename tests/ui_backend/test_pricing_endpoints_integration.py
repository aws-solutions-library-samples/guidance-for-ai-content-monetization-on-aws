#!/usr/bin/env python3
"""
Integration tests for UI Backend Pricing Endpoints

Tests the actual pricing endpoints with real PricingVersionManager integration
to catch method signature mismatches and data structure issues.
"""

import json
import pytest
import os
import sys
from unittest.mock import Mock, patch
from moto import mock_aws
import boto3

# Add the lambda directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda", "ui_backend"))

# Task 9.4: the pricing handlers were moved out of ``ui_backend.handler`` into the per-domain route
# module ``ui_backend/routes/pricing.py`` (delegating to ``services.pricing_manager``, the pricing
# single home) when the ``/pricing/*`` dispatcher entry was flipped. These integration tests exercise
# the (behavior-preserving) handlers at their new home; the route module calls the real service
# directly against the moto-backed tables below.
from ui_backend.routes.pricing import handle_create_pricing_rules, handle_update_pricing_rules
from services.pricing_manager import PricingVersionManager


@mock_aws
class TestPricingEndpointsIntegration:
    """Integration tests for pricing endpoints with real PricingVersionManager"""

    def setup_method(self, method):
        """Set up test fixtures with real DynamoDB tables"""
        # Wire the pricing manager to this test's own moto tables regardless of any co-run test
        # that set a placeholder PRICING_*_TABLE_NAME at collection time (wave0 Req 16/18).
        os.environ["PRICING_RULES_TABLE_NAME"] = "cm-pricing-rules"
        os.environ["PRICING_TRANSACTIONS_TABLE_NAME"] = "cm-pricing-transactions"
        self.mock_context = Mock()
        self.mock_context.aws_request_id = "test-request-id"

        # Mock Cognito user context
        self.mock_user = {"user_id": "test-user-123", "email": "test@example.com", "publisher_id": "test-publisher"}

        # Set up DynamoDB tables
        self.dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        # Create pricing rules table
        self.pricing_rules_table = self.dynamodb.create_table(
            TableName="cm-pricing-rules",
            KeySchema=[
                {"AttributeName": "rule_set_id", "KeyType": "HASH"},
                {"AttributeName": "version_timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "rule_set_id", "AttributeType": "S"},
                {"AttributeName": "version_timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create pricing transactions table
        self.pricing_transactions_table = self.dynamodb.create_table(
            TableName="cm-pricing-transactions",
            KeySchema=[
                {"AttributeName": "transaction_date", "KeyType": "HASH"},
                {"AttributeName": "transaction_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "transaction_date", "AttributeType": "S"},
                {"AttributeName": "transaction_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Base event structure
        self.base_event = {
            "httpMethod": "POST",
            "path": "/pricing/rules",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer test-token"},
            "queryStringParameters": None,
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:username": "test_user",
                        "email": "test@example.com",
                        "custom:publisher_id": "test-publisher",
                        "sub": "test-user-123",
                    }
                }
            },
        }

    def test_create_pricing_rules_with_real_structure(self):
        """Test create pricing rules with the actual data structure from frontend"""
        headers = {"Content-Type": "application/json"}

        # This is the ACTUAL structure sent by the frontend
        request_data = {
            "pricing_rules": {
                "per_access": {
                    "article": "0.05",
                    "video": "0.10",
                    "image": "0.02",
                    "document": "0.08",
                    "report": "0.25",
                },
                "multipliers": {
                    "iab_category": {
                        "111": "1.2",  # Medical Health
                        "126": "1.1",  # Technology & Computing
                        "52": "1.3",  # Business and Finance
                        "1": "2.0",  # Automotive (updated)
                    },
                    "recency": {"last_24h": "1.5", "last_week": "1.2", "last_month": "1.0", "older": "0.8"},
                    "demand": {"high": "1.3", "medium": "1.0", "low": "0.8"},
                },
            },
            "effective_date": "2025-10-07",
            "change_reason": "automotive update",
            "activate": True,
        }

        event = self.base_event.copy()

        # Test that this doesn't raise an exception about missing methods
        response = handle_create_pricing_rules(headers, request_data, event, authenticated_user=self.mock_user)

        # Should succeed (either with real implementation or mock fallback)
        assert response["statusCode"] in [200, 201]

        body = json.loads(response["body"])

        # Should return the correct structure
        if "pricing_rule" in body:
            # Real implementation response
            pricing_rule = body["pricing_rule"]
            assert "rule_set_id" in pricing_rule
            assert "pricing_rules" in pricing_rule
            assert "effective_date" in pricing_rule
            assert pricing_rule["effective_date"] == "2025-10-07"
            assert pricing_rule["pricing_rules"]["per_access"]["article"] == "0.05"
        else:
            # Mock implementation response (should still have correct structure)
            assert "rule_id" in body or "pricing_rule" in body

    def test_update_pricing_rules_with_real_structure(self):
        """Test update pricing rules with the actual data structure from frontend"""
        headers = {"Content-Type": "application/json"}

        # First create a pricing rule to update
        create_request = {
            "pricing_rules": {"per_access": {"article": "0.05", "video": "0.10"}},
            "effective_date": "2025-10-01",
            "change_reason": "initial setup",
            "activate": True,
        }

        create_response = handle_create_pricing_rules(
            headers, create_request, self.base_event.copy(), authenticated_user=self.mock_user
        )
        assert create_response["statusCode"] in [200, 201]

        # Now test update with the ACTUAL structure sent by the frontend
        update_request = {
            "pricing_rules": {
                "per_access": {
                    "article": "0.06",  # Updated price
                    "video": "0.12",  # Updated price
                    "document": "0.08",  # New content type
                },
                "multipliers": {
                    "iab_category": {
                        "1": "2.0"  # Automotive multiplier
                    }
                },
            },
            "effective_date": "2025-10-07",
            "change_reason": "price adjustment",
        }

        event = self.base_event.copy()
        event["httpMethod"] = "PUT"

        # Test that this doesn't raise an exception about missing methods
        response = handle_update_pricing_rules(headers, update_request, event, authenticated_user=self.mock_user)

        # Should succeed (either with real implementation or mock fallback)
        assert response["statusCode"] == 200

        body = json.loads(response["body"])

        # Should return the correct structure
        if "pricing_rule" in body:
            # Real implementation response
            pricing_rule = body["pricing_rule"]
            assert "rule_set_id" in pricing_rule
            assert "pricing_rules" in pricing_rule
            assert "effective_date" in pricing_rule
            assert pricing_rule["effective_date"] == "2025-10-07"
        else:
            # Mock implementation response (should still have correct structure)
            assert "rule_id" in body or "pricing_rule" in body

    def test_create_pricing_rules_missing_required_fields(self):
        """Test create pricing rules with missing required fields"""
        headers = {"Content-Type": "application/json"}

        # Missing pricing_rules
        request_data = {"effective_date": "2025-10-07", "change_reason": "test"}

        response = handle_create_pricing_rules(
            headers, request_data, self.base_event.copy(), authenticated_user=self.mock_user
        )

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "pricing_rules is required" in body["error"]

        # Missing effective_date
        request_data = {"pricing_rules": {"per_access": {"article": "0.05"}}, "change_reason": "test"}

        response = handle_create_pricing_rules(
            headers, request_data, self.base_event.copy(), authenticated_user=self.mock_user
        )

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "effective_date is required" in body["error"]

        # Missing change_reason
        request_data = {"pricing_rules": {"per_access": {"article": "0.05"}}, "effective_date": "2025-10-07"}

        response = handle_create_pricing_rules(
            headers, request_data, self.base_event.copy(), authenticated_user=self.mock_user
        )

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert "change_reason is required" in body["error"]

    def test_pricing_version_manager_method_availability(self):
        """Test that PricingVersionManager has the expected methods"""
        pricing_manager = PricingVersionManager(region_name="us-west-2")

        # These methods should exist
        assert hasattr(pricing_manager, "create_pricing_rule_version")
        assert hasattr(pricing_manager, "activate_pricing_rule_version")
        assert hasattr(pricing_manager, "get_active_pricing_rules")
        assert hasattr(pricing_manager, "get_pricing_history")

        # These methods should NOT exist (this test would have caught the original bug)
        assert not hasattr(pricing_manager, "create_pricing_rules")
        assert not hasattr(pricing_manager, "update_pricing_rules")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
