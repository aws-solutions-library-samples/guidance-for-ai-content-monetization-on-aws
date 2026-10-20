"""
Unit tests for IAB Access Logger

Tests cover:
- Access logging functionality
- Partner usage statistics
- Content access statistics
- Error handling and edge cases
"""

import unittest
import json
import time
import uuid
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timedelta
import sys
import os

# Add lambda directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "source", "lambda"))

from access_logger import (
    log_access_attempt,
    get_partner_usage_stats,
    get_content_access_stats,
    lambda_handler,
    handle_log_access,
    handle_get_partner_stats,
    handle_get_content_stats,
    generate_log_id,
    calculate_ttl,
)


class TestAccessLogger(unittest.TestCase):
    """Test cases for access logger functions"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_partner_id = "partner_123"
        self.test_content_id = "content_456"
        self.test_log_entry = {
            "partner_id": self.test_partner_id,
            "request_type": "query",
            "endpoint": "/api/query",
            "response_code": 200,
            "content_id": self.test_content_id,
            "price_charged": 0.05,
            "currency": "USD",
            "user_agent": "TestBot/1.0",
            "ip_address": "192.168.1.1",
            "country_code": "US",
        }

    def test_generate_log_id(self):
        """Test log ID generation"""
        log_id = generate_log_id()

        # Check format
        self.assertTrue(log_id.startswith("log_"))
        self.assertIn("_", log_id)

        # Check uniqueness
        log_id2 = generate_log_id()
        self.assertNotEqual(log_id, log_id2)

    def test_calculate_ttl(self):
        """Test TTL calculation"""
        ttl = calculate_ttl()

        # Should be a timestamp 90 days in the future
        current_time = int(datetime.utcnow().timestamp())
        expected_min = current_time + (89 * 24 * 3600)  # 89 days
        expected_max = current_time + (91 * 24 * 3600)  # 91 days

        self.assertGreaterEqual(ttl, expected_min)
        self.assertLessEqual(ttl, expected_max)

    @patch("access_logger.dynamodb")
    def test_log_access_attempt_success(self, mock_dynamodb):
        """Test successful access logging"""
        # Mock DynamoDB table
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        # Test logging
        log_id = log_access_attempt(
            partner_id=self.test_partner_id,
            request_type="query",
            endpoint="/api/query",
            response_code=200,
            content_id=self.test_content_id,
            price_charged=0.05,
            currency="USD",
            user_agent="TestBot/1.0",
            ip_address="192.168.1.1",
            country_code="US",
        )

        # Verify log ID format
        self.assertTrue(log_id.startswith("log_"))

        # Verify DynamoDB call
        mock_table.put_item.assert_called_once()
        call_args = mock_table.put_item.call_args[1]["Item"]

        self.assertEqual(call_args["partner_id"], self.test_partner_id)
        self.assertEqual(call_args["request_type"], "query")
        self.assertEqual(call_args["endpoint"], "/api/query")
        self.assertEqual(call_args["response_code"], 200)
        self.assertEqual(call_args["content_id"], self.test_content_id)
        self.assertEqual(call_args["price_charged"], 0.05)

    @patch("access_logger.dynamodb")
    def test_log_access_attempt_minimal(self, mock_dynamodb):
        """Test access logging with minimal required fields"""
        # Mock DynamoDB table
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.put_item.return_value = {}

        # Test logging with minimal fields
        log_id = log_access_attempt(
            partner_id=self.test_partner_id, request_type="info", endpoint="/api/info", response_code=200
        )

        # Verify log ID
        self.assertTrue(log_id.startswith("log_"))

        # Verify DynamoDB call
        mock_table.put_item.assert_called_once()
        call_args = mock_table.put_item.call_args[1]["Item"]

        self.assertEqual(call_args["partner_id"], self.test_partner_id)
        self.assertEqual(call_args["request_type"], "info")
        self.assertEqual(call_args["endpoint"], "/api/info")
        self.assertEqual(call_args["response_code"], 200)
        self.assertNotIn("content_id", call_args)
        self.assertNotIn("price_charged", call_args)

    @patch("access_logger.dynamodb")
    def test_get_partner_usage_stats_success(self, mock_dynamodb):
        """Test successful partner usage statistics retrieval"""
        # Mock DynamoDB table and response
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table

        # Mock query response with sample logs
        mock_logs = [
            {
                "log_id": "log_1",
                "timestamp": "2024-01-01T10:00:00",
                "partner_id": self.test_partner_id,
                "request_type": "query",
                "endpoint": "/api/query",
                "response_code": 200,
                "price_charged": 0.05,
                "currency": "USD",
                "content_id": "content_1",
                "country_code": "US",
            },
            {
                "log_id": "log_2",
                "timestamp": "2024-01-01T11:00:00",
                "partner_id": self.test_partner_id,
                "request_type": "search",
                "endpoint": "/api/search",
                "response_code": 200,
                "price_charged": 0.03,
                "currency": "USD",
                "content_id": "content_2",
                "country_code": "CA",
            },
            {
                "log_id": "log_3",
                "timestamp": "2024-01-01T12:00:00",
                "partner_id": self.test_partner_id,
                "request_type": "query",
                "endpoint": "/api/query",
                "response_code": 404,
                "price_charged": 0.0,
                "currency": "USD",
            },
        ]

        mock_table.query.return_value = {"Items": mock_logs}

        # Test statistics retrieval
        stats = get_partner_usage_stats(self.test_partner_id)

        # Verify statistics
        self.assertEqual(stats["partner_id"], self.test_partner_id)
        self.assertEqual(stats["total_requests"], 3)
        self.assertEqual(stats["successful_requests"], 2)
        self.assertEqual(stats["failed_requests"], 1)
        self.assertEqual(stats["total_cost"], 0.08)
        self.assertEqual(stats["currency"], "USD")
        self.assertEqual(stats["unique_content_count"], 2)
        self.assertEqual(stats["unique_countries_count"], 2)

        # Check request types breakdown
        self.assertEqual(stats["request_types"]["query"], 2)
        self.assertEqual(stats["request_types"]["search"], 1)

        # Check endpoints breakdown
        self.assertEqual(stats["endpoints"]["/api/query"], 2)
        self.assertEqual(stats["endpoints"]["/api/search"], 1)

    @patch("access_logger.dynamodb")
    def test_get_partner_usage_stats_no_data(self, mock_dynamodb):
        """Test partner usage statistics with no data"""
        # Mock DynamoDB table with empty response
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.query.return_value = {"Items": []}

        # Test statistics retrieval
        stats = get_partner_usage_stats(self.test_partner_id)

        # Verify empty statistics
        self.assertEqual(stats["partner_id"], self.test_partner_id)
        self.assertEqual(stats["total_requests"], 0)
        self.assertEqual(stats["successful_requests"], 0)
        self.assertEqual(stats["failed_requests"], 0)
        self.assertEqual(stats["total_cost"], 0)
        self.assertEqual(stats["unique_content_count"], 0)
        self.assertEqual(stats["unique_countries_count"], 0)

    @patch("access_logger.dynamodb")
    def test_get_content_access_stats_success(self, mock_dynamodb):
        """Test successful content access statistics retrieval"""
        # Mock DynamoDB table and response
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table

        # Mock query response with sample logs
        mock_logs = [
            {
                "log_id": "log_1",
                "timestamp": "2024-01-01T10:00:00",
                "partner_id": "partner_1",
                "content_id": self.test_content_id,
                "request_type": "query",
                "response_code": 200,
                "price_charged": 0.05,
                "currency": "USD",
                "country_code": "US",
            },
            {
                "log_id": "log_2",
                "timestamp": "2024-01-01T11:00:00",
                "partner_id": "partner_2",
                "content_id": self.test_content_id,
                "request_type": "bid",
                "response_code": 200,
                "price_charged": 0.10,
                "currency": "USD",
                "country_code": "UK",
            },
        ]

        mock_table.query.return_value = {"Items": mock_logs}

        # Test statistics retrieval
        stats = get_content_access_stats(self.test_content_id)

        # Verify statistics
        self.assertEqual(stats["content_id"], self.test_content_id)
        self.assertEqual(stats["total_accesses"], 2)
        self.assertEqual(stats["successful_accesses"], 2)
        self.assertAlmostEqual(stats["total_revenue"], 0.15, places=2)
        self.assertEqual(stats["currency"], "USD")
        self.assertEqual(stats["unique_partners"], 2)
        self.assertIn("partner_1", stats["partners"])
        self.assertIn("partner_2", stats["partners"])
        self.assertIn("US", stats["countries"])
        self.assertIn("UK", stats["countries"])

    @patch("access_logger.dynamodb")
    def test_get_content_access_stats_no_data(self, mock_dynamodb):
        """Test content access statistics with no data"""
        # Mock DynamoDB table with empty response
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.query.return_value = {"Items": []}

        # Test statistics retrieval
        stats = get_content_access_stats(self.test_content_id)

        # Verify empty statistics
        self.assertEqual(stats["content_id"], self.test_content_id)
        self.assertEqual(stats["total_accesses"], 0)
        self.assertEqual(stats["successful_accesses"], 0)
        self.assertEqual(stats["total_revenue"], 0)
        self.assertEqual(stats["unique_partners"], 0)


class TestLambdaHandlers(unittest.TestCase):
    """Test cases for Lambda handler functions"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_partner_id = "partner_123"
        self.test_content_id = "content_456"

    def test_lambda_handler_missing_operation(self):
        """Test lambda handler with missing operation"""
        event = {}
        context = Mock()

        response = lambda_handler(event, context)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing operation parameter", body["error"])

    def test_lambda_handler_invalid_operation(self):
        """Test lambda handler with invalid operation"""
        event = {"operation": "invalid_operation"}
        context = Mock()

        response = lambda_handler(event, context)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Unsupported operation", body["error"])

    @patch("access_logger.log_access_attempt")
    def test_handle_log_access_success(self, mock_log_access):
        """Test successful access logging handler"""
        # Mock log_access_attempt
        mock_log_access.return_value = "log_12345"

        event = {
            "partner_id": self.test_partner_id,
            "request_type": "query",
            "endpoint": "/api/query",
            "response_code": 200,
            "content_id": self.test_content_id,
            "price_charged": 0.05,
        }

        response = handle_log_access(event)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["log_id"], "log_12345")
        self.assertIn("Access logged successfully", body["message"])

        # Verify function was called with correct parameters
        mock_log_access.assert_called_once_with(
            partner_id=self.test_partner_id,
            request_type="query",
            endpoint="/api/query",
            response_code=200,
            content_id=self.test_content_id,
            price_charged=0.05,
            currency="USD",
            user_agent=None,
            ip_address=None,
            country_code=None,
            additional_metadata=None,
        )

    def test_handle_log_access_missing_fields(self):
        """Test access logging handler with missing required fields"""
        event = {"partner_id": self.test_partner_id}  # Missing required fields

        response = handle_log_access(event)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing required field", body["error"])

    @patch("access_logger.get_partner_usage_stats")
    def test_handle_get_partner_stats_success(self, mock_get_stats):
        """Test successful partner statistics handler"""
        # Mock statistics response
        mock_stats = {
            "partner_id": self.test_partner_id,
            "total_requests": 100,
            "successful_requests": 95,
            "total_cost": 5.0,
        }
        mock_get_stats.return_value = mock_stats

        event = {"partner_id": self.test_partner_id}
        response = handle_get_partner_stats(event)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["partner_id"], self.test_partner_id)
        self.assertEqual(body["total_requests"], 100)

    def test_handle_get_partner_stats_missing_partner_id(self):
        """Test partner statistics handler with missing partner ID"""
        event = {}
        response = handle_get_partner_stats(event)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing partner_id parameter", body["error"])

    @patch("access_logger.get_content_access_stats")
    def test_handle_get_content_stats_success(self, mock_get_stats):
        """Test successful content statistics handler"""
        # Mock statistics response
        mock_stats = {
            "content_id": self.test_content_id,
            "total_accesses": 50,
            "total_revenue": 2.5,
            "unique_partners": 10,
        }
        mock_get_stats.return_value = mock_stats

        event = {"content_id": self.test_content_id}
        response = handle_get_content_stats(event)

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["content_id"], self.test_content_id)
        self.assertEqual(body["total_accesses"], 50)

    def test_handle_get_content_stats_missing_content_id(self):
        """Test content statistics handler with missing content ID"""
        event = {}
        response = handle_get_content_stats(event)

        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertIn("Missing content_id parameter", body["error"])

    @patch("access_logger.log_access_attempt")
    def test_handle_log_access_with_metadata(self, mock_log_access):
        """Test access logging with additional metadata"""
        mock_log_access.return_value = "log_12345"

        event = {
            "partner_id": self.test_partner_id,
            "request_type": "query",
            "endpoint": "/api/query",
            "response_code": 200,
            "user_agent": "TestBot/1.0",
            "ip_address": "192.168.1.1",
            "country_code": "US",
            "metadata": {"query_terms": ["AI", "technology"]},
        }

        response = handle_log_access(event)

        self.assertEqual(response["statusCode"], 200)

        # Verify all parameters were passed
        mock_log_access.assert_called_once_with(
            partner_id=self.test_partner_id,
            request_type="query",
            endpoint="/api/query",
            response_code=200,
            content_id=None,
            price_charged=None,
            currency="USD",
            user_agent="TestBot/1.0",
            ip_address="192.168.1.1",
            country_code="US",
            additional_metadata={"query_terms": ["AI", "technology"]},
        )

    @patch("access_logger.get_partner_usage_stats")
    def test_handle_get_partner_stats_with_date_range(self, mock_get_stats):
        """Test partner statistics with date range"""
        mock_stats = {"partner_id": self.test_partner_id, "total_requests": 50}
        mock_get_stats.return_value = mock_stats

        event = {
            "partner_id": self.test_partner_id,
            "start_date": "2024-01-01T00:00:00",
            "end_date": "2024-01-31T23:59:59",
        }

        response = handle_get_partner_stats(event)

        self.assertEqual(response["statusCode"], 200)

        # Verify date range was passed
        mock_get_stats.assert_called_once_with(
            partner_id=self.test_partner_id, start_date="2024-01-01T00:00:00", end_date="2024-01-31T23:59:59"
        )


class TestErrorHandling(unittest.TestCase):
    """Test cases for error handling scenarios"""

    def setUp(self):
        """Set up test fixtures"""
        self.test_partner_id = "partner_123"

    @patch("access_logger.dynamodb")
    def test_log_access_attempt_dynamodb_error(self, mock_dynamodb):
        """Test access logging with DynamoDB error"""
        # Mock DynamoDB error
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.put_item.side_effect = Exception("DynamoDB error")

        # Test that exception is raised
        with self.assertRaises(Exception):
            log_access_attempt(
                partner_id=self.test_partner_id, request_type="query", endpoint="/api/query", response_code=200
            )

    @patch("access_logger.dynamodb")
    def test_get_partner_usage_stats_dynamodb_error(self, mock_dynamodb):
        """Test partner statistics with DynamoDB error"""
        # Mock DynamoDB error
        mock_table = Mock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.query.side_effect = Exception("DynamoDB error")

        # Test that exception is raised
        with self.assertRaises(Exception):
            get_partner_usage_stats(self.test_partner_id)

    @patch("access_logger.log_access_attempt")
    def test_handle_log_access_exception(self, mock_log_access):
        """Test access logging handler with exception"""
        # Mock exception
        mock_log_access.side_effect = Exception("Logging failed")

        event = {
            "partner_id": self.test_partner_id,
            "request_type": "query",
            "endpoint": "/api/query",
            "response_code": 200,
        }

        response = handle_log_access(event)

        self.assertEqual(response["statusCode"], 500)
        body = json.loads(response["body"])
        self.assertIn("Failed to log access", body["error"])

    @patch("access_logger.get_partner_usage_stats")
    def test_handle_get_partner_stats_exception(self, mock_get_stats):
        """Test partner statistics handler with exception"""
        # Mock exception
        mock_get_stats.side_effect = Exception("Stats failed")

        event = {"partner_id": self.test_partner_id}
        response = handle_get_partner_stats(event)

        self.assertEqual(response["statusCode"], 500)
        body = json.loads(response["body"])
        self.assertIn("Failed to get partner statistics", body["error"])


if __name__ == "__main__":
    # Create tests directory if it doesn't exist
    os.makedirs("tests", exist_ok=True)

    # Run tests
    unittest.main(verbosity=2)
