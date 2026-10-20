"""
Unit tests for IAB Access Rules Engine

Tests cover:
- Access policy enforcement
- Geographic restriction functionality
- IP address filtering
- Time-based restrictions
- Content-specific access control
- Partner-specific permissions
- Error handling and edge cases
"""

import unittest
import json
from unittest.mock import Mock, patch
from datetime import datetime, time, timedelta
import sys
import os

# Add lambda directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "lambda"))

from access_rules_engine import (
    get_country_from_ip,
    is_ip_in_range,
    is_time_in_range,
    check_geographic_restrictions,
    check_ip_restrictions,
    check_content_embargo,
    evaluate_access_request,
    lambda_handler,
    handle_evaluate_access,
    handle_get_partner_rules,
    handle_get_content_rules,
)


class TestGeographicFunctions(unittest.TestCase):
    """Test cases for geographic restriction functions"""

    @patch("access_rules_engine.requests.get")
    def test_get_country_from_ip_success(self, mock_get):
        """Test successful country lookup from IP"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "success", "countryCode": "US"}
        mock_get.return_value = mock_response

        country = get_country_from_ip("192.168.1.1")
        self.assertEqual(country, "US")

    @patch("access_rules_engine.requests.get")
    def test_get_country_from_ip_failure(self, mock_get):
        """Test country lookup failure"""
        mock_get.side_effect = Exception("Network error")
        country = get_country_from_ip("192.168.1.1")
        self.assertIsNone(country)

    def test_get_country_from_ip_localhost(self):
        """Test country lookup for localhost"""
        country = get_country_from_ip("127.0.0.1")
        self.assertIsNone(country)


class TestIPFunctions(unittest.TestCase):
    """Test cases for IP address functions"""

    def test_is_ip_in_range_single_ip(self):
        """Test IP range check with single IP"""
        self.assertTrue(is_ip_in_range("192.168.1.1", ["192.168.1.1/32"]))
        self.assertFalse(is_ip_in_range("192.168.1.2", ["192.168.1.1/32"]))

    def test_is_ip_in_range_subnet(self):
        """Test IP range check with subnet"""
        self.assertTrue(is_ip_in_range("192.168.1.100", ["192.168.1.0/24"]))
        self.assertFalse(is_ip_in_range("192.168.2.1", ["192.168.1.0/24"]))

    def test_is_ip_in_range_invalid_ip(self):
        """Test IP range check with invalid IP"""
        self.assertFalse(is_ip_in_range("invalid-ip", ["192.168.1.0/24"]))


class TestTimeFunctions(unittest.TestCase):
    """Test cases for time restriction functions"""

    def test_is_time_in_range_same_day(self):
        """Test time range check within same day"""
        start_time = time(9, 0)
        end_time = time(17, 0)

        self.assertTrue(is_time_in_range(time(12, 0), start_time, end_time))
        self.assertFalse(is_time_in_range(time(8, 59), start_time, end_time))

    def test_is_time_in_range_overnight(self):
        """Test time range check overnight"""
        start_time = time(22, 0)
        end_time = time(6, 0)

        self.assertTrue(is_time_in_range(time(23, 0), start_time, end_time))
        self.assertFalse(is_time_in_range(time(12, 0), start_time, end_time))


class TestRestrictionChecks(unittest.TestCase):
    """Test cases for restriction checking functions"""

    @patch("access_rules_engine.get_country_from_ip")
    def test_check_geographic_restrictions_allowed(self, mock_get_country):
        """Test geographic restrictions with allowed country"""
        mock_get_country.return_value = "US"

        allowed, reason = check_geographic_restrictions("192.168.1.1", ["US"], [])

        self.assertTrue(allowed)
        self.assertIn("Access allowed from country: US", reason)

    def test_check_ip_restrictions_whitelist_allowed(self):
        """Test IP restrictions with whitelisted IP"""
        allowed, reason = check_ip_restrictions("192.168.1.100", ["192.168.1.0/24"], [])

        self.assertTrue(allowed)
        self.assertIn("IP restrictions passed", reason)

    def test_check_content_embargo_valid(self):
        """Test content embargo check with valid content"""
        content_rules = {}

        allowed, reason = check_content_embargo(content_rules)

        self.assertTrue(allowed)
        self.assertIn("Content timing restrictions passed", reason)


class TestAccessEvaluation(unittest.TestCase):
    """Test cases for access request evaluation"""

    @patch("access_rules_engine.get_partner_access_rules")
    def test_evaluate_access_request_partner_not_found(self, mock_partner_rules):
        """Test access request evaluation when partner not found"""
        mock_partner_rules.return_value = {}

        result = evaluate_access_request("partner_123", None, "/api/query", "192.168.1.1")

        self.assertFalse(result["allowed"])
        self.assertIn("Partner not found", result["reason"])


class TestLambdaHandlers(unittest.TestCase):
    """Test cases for Lambda handler functions"""

    def test_lambda_handler_missing_operation(self):
        """Test lambda handler with missing operation"""
        event = {}
        context = Mock()

        response = lambda_handler(event, context)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing operation parameter", body["error"])

    def test_handle_evaluate_access_missing_fields(self):
        """Test access evaluation handler with missing required fields"""
        event = {"partner_id": "partner_123"}

        response = handle_evaluate_access(event)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing required field", body["error"])

    def test_handle_get_partner_rules_missing_partner_id(self):
        """Test partner rules handler with missing partner ID"""
        event = {}
        response = handle_get_partner_rules(event)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing partner_id parameter", body["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
