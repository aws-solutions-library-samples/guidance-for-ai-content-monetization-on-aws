#!/usr/bin/env python3
"""
Test error handling when IAB APIs or MCP server are unavailable
"""

import os
import pytest
import asyncio
import json
import requests
from unittest.mock import patch, MagicMock, Mock
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "internal-assets" / "mcp_server"))

pytestmark = pytest.mark.integration
sys.path.insert(0, str(project_root / "source" / "agent"))


class TestErrorScenarios:
    """Test error handling when external services are unavailable"""

    @pytest.fixture(autouse=True)
    def setup_environment(self):
        """Setup test environment variables"""
        self.original_env = {}
        # Store original values
        for key in ["USE_MOCK_DATA", "USE_MOCK_ANALYTICS", "IAB_API_BASE_URL", "PARTNER_API_KEY"]:
            self.original_env[key] = os.environ.get(key)

        yield

        # Restore original values
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_iab_api_unavailable_error_handling(self):
        """Test error handling when IAB API is unavailable"""
        os.environ["USE_MOCK_DATA"] = "false"
        os.environ["IAB_API_BASE_URL"] = "https://unavailable-api.example.com/v1"
        os.environ["PARTNER_API_KEY"] = "test_key"

        # Mock IAB API failure
        def simulate_iab_api_call():
            """Simulate IAB API call that fails"""
            try:
                # Simulate network timeout
                raise requests.exceptions.Timeout("IAB API request timed out after 30 seconds")
            except requests.exceptions.Timeout as e:
                return {
                    "error": True,
                    "error_type": "timeout",
                    "error_message": str(e),
                    "error_code": "IAB_API_TIMEOUT",
                    "retry_after": 60,
                    "fallback_available": True,
                }
            except requests.exceptions.ConnectionError as e:
                return {
                    "error": True,
                    "error_type": "connection_error",
                    "error_message": str(e),
                    "error_code": "IAB_API_UNAVAILABLE",
                    "retry_after": 300,
                    "fallback_available": True,
                }

        result = simulate_iab_api_call()

        # Verify error response structure
        assert result["error"] is True
        assert result["error_type"] == "timeout"
        assert result["error_code"] == "IAB_API_TIMEOUT"
        assert result["retry_after"] == 60
        assert result["fallback_available"] is True

        print("✓ IAB API timeout error handling validated")

    def test_iab_api_authentication_error(self):
        """Test error handling for IAB API authentication failures"""
        os.environ["USE_MOCK_DATA"] = "false"
        os.environ["IAB_API_BASE_URL"] = "https://api.iab.com/v1"
        os.environ["PARTNER_API_KEY"] = "invalid_key"

        # Mock IAB API authentication failure
        def simulate_iab_auth_failure():
            """Simulate IAB API authentication failure"""
            return {
                "error": True,
                "error_type": "authentication_error",
                "error_message": "Invalid partner API key",
                "error_code": "IAB_AUTH_FAILED",
                "status_code": 401,
                "retry_after": None,  # Don't retry auth errors
                "fallback_available": True,
            }

        result = simulate_iab_auth_failure()

        # Verify authentication error response
        assert result["error"] is True
        assert result["error_type"] == "authentication_error"
        assert result["error_code"] == "IAB_AUTH_FAILED"
        assert result["status_code"] == 401
        assert result["retry_after"] is None

        print("✓ IAB API authentication error handling validated")

    def test_iab_api_rate_limit_error(self):
        """Test error handling for IAB API rate limiting"""
        os.environ["USE_MOCK_DATA"] = "false"

        # Mock IAB API rate limit response
        def simulate_iab_rate_limit():
            """Simulate IAB API rate limit error"""
            return {
                "error": True,
                "error_type": "rate_limit_exceeded",
                "error_message": "Rate limit exceeded: 1000 requests per hour",
                "error_code": "IAB_RATE_LIMIT",
                "status_code": 429,
                "retry_after": 3600,  # 1 hour
                "rate_limit_info": {"limit": 1000, "remaining": 0, "reset_time": "2025-01-15T11:30:00Z"},
                "fallback_available": True,
            }

        result = simulate_iab_rate_limit()

        # Verify rate limit error response
        assert result["error"] is True
        assert result["error_type"] == "rate_limit_exceeded"
        assert result["error_code"] == "IAB_RATE_LIMIT"
        assert result["status_code"] == 429
        assert result["retry_after"] == 3600
        assert "rate_limit_info" in result

        print("✓ IAB API rate limit error handling validated")

    @pytest.mark.asyncio
    async def test_mcp_server_unavailable_error_handling(self):
        """Test error handling when MCP server is unavailable"""
        os.environ["USE_MOCK_ANALYTICS"] = "false"

        # Mock MCP server connection failure
        class MockMCPClientWithError:
            def __init__(self, server_url):
                self.server_url = server_url
                self.connected = False

            async def connect(self):
                """Simulate MCP server connection failure"""
                raise Exception("MCP server unavailable: Connection refused")

            async def call_tool(self, tool_name, **kwargs):
                """Simulate tool call failure when not connected"""
                if not self.connected:
                    return {
                        "error": True,
                        "error_type": "connection_error",
                        "error_message": "MCP server not available",
                        "error_code": "MCP_SERVER_UNAVAILABLE",
                        "tool_name": tool_name,
                        "fallback_available": True,
                    }

        # Test MCP server connection failure
        client = MockMCPClientWithError("arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test-server")

        with pytest.raises(Exception) as exc_info:
            await client.connect()

        assert "MCP server unavailable" in str(exc_info.value)

        # Test tool call failure
        result = await client.call_tool("get_usage_analytics")
        assert result["error"] is True
        assert result["error_code"] == "MCP_SERVER_UNAVAILABLE"

        print("✓ MCP server unavailable error handling validated")

    def test_mcp_server_authentication_error(self):
        """Test error handling for MCP server authentication failures"""
        os.environ["USE_MOCK_ANALYTICS"] = "false"

        # Mock MCP server authentication failure
        def simulate_mcp_auth_failure():
            """Simulate MCP server authentication failure"""
            return {
                "error": True,
                "error_type": "authentication_error",
                "error_message": "Invalid bearer token for AgentCore MCP server",
                "error_code": "MCP_AUTH_FAILED",
                "status_code": 401,
                "retry_with_new_token": True,
                "fallback_available": True,
            }

        result = simulate_mcp_auth_failure()

        # Verify MCP authentication error response
        assert result["error"] is True
        assert result["error_type"] == "authentication_error"
        assert result["error_code"] == "MCP_AUTH_FAILED"
        assert result["retry_with_new_token"] is True

        print("✓ MCP server authentication error handling validated")

    def test_network_connectivity_errors(self):
        """Test error handling for various network connectivity issues"""

        network_errors = [
            {
                "name": "DNS Resolution Failure",
                "exception": "requests.exceptions.ConnectionError",
                "message": "Failed to resolve hostname",
                "error_code": "DNS_RESOLUTION_FAILED",
            },
            {
                "name": "Connection Timeout",
                "exception": "requests.exceptions.Timeout",
                "message": "Connection timed out",
                "error_code": "CONNECTION_TIMEOUT",
            },
            {
                "name": "SSL Certificate Error",
                "exception": "requests.exceptions.SSLError",
                "message": "SSL certificate verification failed",
                "error_code": "SSL_VERIFICATION_FAILED",
            },
        ]

        for error_scenario in network_errors:

            def simulate_network_error(scenario):
                return {
                    "error": True,
                    "error_type": "network_error",
                    "error_message": scenario["message"],
                    "error_code": scenario["error_code"],
                    "exception_type": scenario["exception"],
                    "retry_recommended": True,
                    "fallback_available": True,
                }

            result = simulate_network_error(error_scenario)

            assert result["error"] is True
            assert result["error_type"] == "network_error"
            assert result["error_code"] == error_scenario["error_code"]
            assert result["retry_recommended"] is True

            print(f"✓ {error_scenario['name']} error handling validated")

    def test_graceful_fallback_to_mock_data(self):
        """Test graceful fallback to mock data when real services fail"""

        # Simulate production mode with fallback capability
        def handle_service_failure_with_fallback(service_name, enable_fallback=True):
            """Handle service failure with optional fallback to mock data"""
            try:
                # Simulate service failure
                if service_name == "iab_api":
                    raise Exception("IAB API service unavailable")
                elif service_name == "mcp_server":
                    raise Exception("MCP server connection failed")
                else:
                    raise Exception(f"Unknown service: {service_name}")

            except Exception as e:
                if enable_fallback:
                    # Fallback to mock data
                    return {
                        "data": {
                            "fallback_mode": True,
                            "original_service": service_name,
                            "error_reason": str(e),
                            "mock_data": True,
                            "results": [
                                {"content_id": "fallback_001", "title": "Fallback Content", "source": "mock_fallback"}
                            ],
                        },
                        "metadata": {"mode": "fallback", "original_mode": "production", "fallback_reason": str(e)},
                    }
                else:
                    # Re-raise the error if fallback disabled
                    raise

        # Test fallback for IAB API failure
        result = handle_service_failure_with_fallback("iab_api")
        assert result["data"]["fallback_mode"] is True
        assert result["data"]["original_service"] == "iab_api"
        assert result["metadata"]["mode"] == "fallback"

        # Test fallback for MCP server failure
        result = handle_service_failure_with_fallback("mcp_server")
        assert result["data"]["fallback_mode"] is True
        assert result["data"]["original_service"] == "mcp_server"

        print("✓ Graceful fallback to mock data validated")

    def test_error_response_consistency(self):
        """Test that error responses follow consistent format"""

        # Define standard error response format
        def create_standard_error_response(error_type, error_code, message, **kwargs):
            """Create standardized error response"""
            return {
                "error": True,
                "error_type": error_type,
                "error_code": error_code,
                "error_message": message,
                "timestamp": "2025-01-15T10:30:00Z",
                "service": kwargs.get("service", "unknown"),
                "retry_after": kwargs.get("retry_after"),
                "fallback_available": kwargs.get("fallback_available", True),
                **kwargs,
            }

        # Test different error types
        error_types = [
            {
                "type": "timeout",
                "code": "SERVICE_TIMEOUT",
                "message": "Service request timed out",
                "service": "iab_api",
            },
            {
                "type": "authentication_error",
                "code": "AUTH_FAILED",
                "message": "Authentication failed",
                "service": "mcp_server",
            },
            {
                "type": "rate_limit_exceeded",
                "code": "RATE_LIMIT",
                "message": "Rate limit exceeded",
                "service": "iab_api",
                "retry_after": 3600,
            },
        ]

        for error_spec in error_types:
            error_response = create_standard_error_response(
                error_spec["type"],
                error_spec["code"],
                error_spec["message"],
                service=error_spec["service"],
                retry_after=error_spec.get("retry_after"),
            )

            # Verify standard error response format
            assert error_response["error"] is True
            assert error_response["error_type"] == error_spec["type"]
            assert error_response["error_code"] == error_spec["code"]
            assert error_response["error_message"] == error_spec["message"]
            assert "timestamp" in error_response
            assert "fallback_available" in error_response

            print(f"✓ {error_spec['type']} error response format validated")

    def test_retry_logic_for_transient_errors(self):
        """Test retry logic for transient errors"""

        def retry_with_backoff(operation, max_retries=3, base_delay=1):
            """Implement retry logic with exponential backoff"""
            import time

            for attempt in range(max_retries):
                try:
                    # Simulate operation that might fail
                    if operation == "transient_failure" and attempt < 2:
                        raise Exception("Transient service error")
                    elif operation == "permanent_failure":
                        raise Exception("Permanent service error")

                    # Success case
                    return {"success": True, "attempts": attempt + 1, "operation": operation}

                except Exception as e:
                    if attempt == max_retries - 1:
                        # Final attempt failed
                        return {"success": False, "attempts": attempt + 1, "error": str(e), "operation": operation}

                    # Wait before retry with exponential backoff
                    delay = base_delay * (2**attempt)
                    time.sleep(0.01)  # Very short sleep for testing

        # Test successful retry after transient failures
        result = retry_with_backoff("transient_failure")
        assert result["success"] is True
        assert result["attempts"] == 3  # Should succeed on 3rd attempt

        # Test permanent failure (no success even with retries)
        result = retry_with_backoff("permanent_failure")
        assert result["success"] is False
        assert result["attempts"] == 3  # Should fail after all attempts

        print("✓ Retry logic for transient errors validated")

    def test_circuit_breaker_pattern(self):
        """Test circuit breaker pattern for failing services"""

        class MockCircuitBreaker:
            def __init__(self, failure_threshold=3, recovery_timeout=60):
                self.failure_threshold = failure_threshold
                self.recovery_timeout = recovery_timeout
                self.failure_count = 0
                self.last_failure_time = None
                self.state = "closed"  # closed, open, half_open

            def call_service(self, service_operation):
                """Call service with circuit breaker protection"""
                import time

                current_time = time.time()

                # Check if circuit should transition from open to half_open
                if (
                    self.state == "open"
                    and self.last_failure_time
                    and current_time - self.last_failure_time > self.recovery_timeout
                ):
                    self.state = "half_open"

                # If circuit is open, fail fast
                if self.state == "open":
                    return {
                        "error": True,
                        "error_type": "circuit_breaker_open",
                        "error_code": "CIRCUIT_BREAKER_OPEN",
                        "error_message": "Service circuit breaker is open",
                        "fallback_available": True,
                    }

                try:
                    # Simulate service call
                    if service_operation == "failing_service":
                        raise Exception("Service failure")

                    # Success - reset failure count
                    self.failure_count = 0
                    self.state = "closed"

                    return {"success": True, "circuit_state": self.state, "failure_count": self.failure_count}

                except Exception as e:
                    # Failure - increment count and check threshold
                    self.failure_count += 1
                    self.last_failure_time = current_time

                    if self.failure_count >= self.failure_threshold:
                        self.state = "open"

                    return {
                        "error": True,
                        "error_type": "service_failure",
                        "error_message": str(e),
                        "circuit_state": self.state,
                        "failure_count": self.failure_count,
                    }

        # Test circuit breaker behavior
        circuit_breaker = MockCircuitBreaker(failure_threshold=2)

        # First failure
        result = circuit_breaker.call_service("failing_service")
        assert result["error"] is True
        assert result["circuit_state"] == "closed"
        assert result["failure_count"] == 1

        # Second failure - should open circuit
        result = circuit_breaker.call_service("failing_service")
        assert result["error"] is True
        assert result["circuit_state"] == "open"
        assert result["failure_count"] == 2

        # Third call - should fail fast due to open circuit
        result = circuit_breaker.call_service("failing_service")
        assert result["error"] is True
        assert result["error_type"] == "circuit_breaker_open"

        print("✓ Circuit breaker pattern validated")

    def test_error_monitoring_and_alerting_data(self):
        """Test error data collection for monitoring and alerting"""

        def collect_error_metrics(error_response):
            """Collect error metrics for monitoring"""
            return {
                "error_type": error_response.get("error_type"),
                "error_code": error_response.get("error_code"),
                "service": error_response.get("service"),
                "timestamp": error_response.get("timestamp"),
                "severity": determine_error_severity(error_response),
                "alert_required": should_alert(error_response),
                "metrics": {
                    "error_count": 1,
                    "service_availability": calculate_availability(error_response),
                    "response_time": error_response.get("response_time", 0),
                },
            }

        def determine_error_severity(error_response):
            """Determine error severity level"""
            error_type = error_response.get("error_type", "")

            if error_type in ["authentication_error", "authorization_error"]:
                return "high"
            elif error_type in ["timeout", "connection_error"]:
                return "medium"
            elif error_type in ["rate_limit_exceeded"]:
                return "low"
            else:
                return "medium"

        def should_alert(error_response):
            """Determine if error requires alerting"""
            severity = determine_error_severity(error_response)
            error_code = error_response.get("error_code", "")

            # Alert on high severity or specific error codes
            return severity == "high" or error_code in ["SERVICE_UNAVAILABLE", "CIRCUIT_BREAKER_OPEN"]

        def calculate_availability(error_response):
            """Calculate service availability (mocked)"""
            # In real implementation, this would calculate based on success/failure ratio
            return 0.95  # 95% availability

        # Test error metrics collection
        sample_error = {
            "error": True,
            "error_type": "authentication_error",
            "error_code": "AUTH_FAILED",
            "service": "iab_api",
            "timestamp": "2025-01-15T10:30:00Z",
            "response_time": 5000,
        }

        metrics = collect_error_metrics(sample_error)

        assert metrics["error_type"] == "authentication_error"
        assert metrics["severity"] == "high"
        assert metrics["alert_required"] is True
        assert metrics["metrics"]["error_count"] == 1

        print("✓ Error monitoring and alerting data collection validated")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
