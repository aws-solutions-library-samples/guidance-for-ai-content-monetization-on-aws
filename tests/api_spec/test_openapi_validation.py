#!/usr/bin/env python3
"""
OpenAPI Specification Validation Tests

This module validates the OpenAPI specification against the current implementation
to ensure consistency between documented and actual API behavior.
"""

import json
import os
import sys
import yaml
import pytest
import requests
from typing import Dict, List, Any, Optional
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from openapi_spec_validator import validate_spec
    from openapi_spec_validator.readers import read_from_filename

    OPENAPI_VALIDATOR_AVAILABLE = True
except ImportError:
    OPENAPI_VALIDATOR_AVAILABLE = False
    print("Warning: openapi-spec-validator not available. Install with: pip install openapi-spec-validator")


class TestOpenAPIValidation:
    """Test suite for OpenAPI specification validation."""

    @classmethod
    def setup_class(cls):
        """Set up test class with OpenAPI spec and configuration."""
        cls.spec_path = project_root / "openapi-spec.yaml"
        cls.load_openapi_spec()
        cls.api_base_url = os.getenv("IAB_API_BASE_URL", "https://api.example.com")
        cls.partner_key = os.getenv("PARTNER_API_KEY", "test_key")
        cls.publisher_key = os.getenv("PUBLISHER_API_KEY", "test_key")

    @classmethod
    def load_openapi_spec(cls):
        """Load and parse the OpenAPI specification."""
        try:
            with open(cls.spec_path, "r") as f:
                cls.openapi_spec = yaml.safe_load(f)
        except Exception as e:
            pytest.fail(f"Failed to load OpenAPI spec: {e}")

    def test_openapi_spec_exists(self):
        """Test that the OpenAPI specification file exists."""
        assert self.spec_path.exists(), f"OpenAPI spec file not found at {self.spec_path}"

    def test_openapi_spec_valid_yaml(self):
        """Test that the OpenAPI specification is valid YAML."""
        assert self.openapi_spec is not None, "OpenAPI spec could not be parsed as YAML"
        assert isinstance(self.openapi_spec, dict), "OpenAPI spec should be a dictionary"

    def test_openapi_spec_has_required_fields(self):
        """Test that the OpenAPI specification has all required fields."""
        required_fields = ["openapi", "info", "paths", "components"]
        for field in required_fields:
            assert field in self.openapi_spec, f"Required field '{field}' missing from OpenAPI spec"

    @pytest.mark.skipif(not OPENAPI_VALIDATOR_AVAILABLE, reason="openapi-spec-validator not available")
    def test_openapi_spec_validation(self):
        """Test that the OpenAPI specification is valid according to OpenAPI 3.0 standards."""
        try:
            spec_dict, spec_url = read_from_filename(str(self.spec_path))
            validate_spec(spec_dict)
        except Exception as e:
            pytest.fail(f"OpenAPI specification validation failed: {e}")

    def test_security_schemes_defined(self):
        """Test that the standardized security schemes are defined (SSOT #26: two schemes)."""
        security_schemes = self.openapi_spec.get("components", {}).get("securitySchemes", {})

        required_schemes = ["PartnerKeyAuth", "CognitoAuth"]
        for scheme in required_schemes:
            assert scheme in security_schemes, f"Security scheme '{scheme}' not defined"
        # HybridAuth was removed — hybrid endpoints use an OR-list of the two schemes instead.
        assert "HybridAuth" not in security_schemes, "HybridAuth should be removed (use OR-list)"

    def test_hybrid_authentication_documented(self):
        """Standardized schemes + hybrid endpoints expressed as an OR-list (SSOT #26)."""
        security_schemes = self.openapi_spec.get("components", {}).get("securitySchemes", {})

        # PartnerKeyAuth is the native API Gateway key (partner plane); the old custom header is gone.
        partner_key = security_schemes.get("PartnerKeyAuth", {})
        assert partner_key.get("type") == "apiKey", "PartnerKeyAuth should be apiKey type"
        assert partner_key.get("in") == "header"
        assert partner_key.get("name") == "x-api-key", "PartnerKeyAuth must be the native x-api-key header"
        assert "X-Partner-Key" not in self.spec_path.read_text(), "legacy X-Partner-Key header must not appear anywhere"
        assert "external api access" in partner_key.get("description", "").lower(), (
            "PartnerKeyAuth description should mention external API access"
        )

        # CognitoAuth (UI / revenue-agent)
        cognito = security_schemes.get("CognitoAuth", {})
        assert cognito.get("type") == "http", "CognitoAuth should be http type"
        assert cognito.get("scheme") == "bearer", "CognitoAuth should use bearer scheme"
        assert "publisher dashboard" in cognito.get("description", "").lower(), (
            "CognitoAuth description should mention publisher dashboard"
        )

        # The hybrid /query expresses "either credential" as an OR-list.
        paths = self.openapi_spec.get("paths", {})
        for hybrid_path in ("/query",):
            op = paths.get(hybrid_path, {}).get("post", {})
            security = op.get("security", [])
            scheme_names = {list(s.keys())[0] for s in security if s}
            assert scheme_names == {"PartnerKeyAuth", "CognitoAuth"}, (
                f"{hybrid_path} should allow either PartnerKeyAuth or CognitoAuth (OR-list), got {scheme_names}"
            )

    def test_missing_endpoints_added(self):
        """Test that all missing endpoints have been added to the specification."""
        paths = self.openapi_spec.get("paths", {})

        # Analytics endpoints
        analytics_endpoints = [
            "/analytics/metrics",
            "/analytics/usage",
            "/analytics/revenue",
            "/analytics/revenue/categories",
            "/analytics/partners",
        ]

        for endpoint in analytics_endpoints:
            assert endpoint in paths, f"Analytics endpoint '{endpoint}' missing from OpenAPI spec"

        # Bot control endpoints
        bot_control_endpoints = ["/bot-control/rules", "/bot-control/activity", "/bot-control/stats"]

        for endpoint in bot_control_endpoints:
            assert endpoint in paths, f"Bot control endpoint '{endpoint}' missing from OpenAPI spec"

        # Vector store endpoints were PRUNED (SSOT #26 — unwired, not intended for this release).
        pruned_endpoints = [
            "/vector-store/info",
            "/vector-store/create",
            "/vector-store/migrate",
            "/vector-store/test",
            "/generate-llms-txt",  # replaced by the llms.txt management capability (#30)
        ]

        for endpoint in pruned_endpoints:
            assert endpoint not in paths, f"Pruned endpoint '{endpoint}' should no longer be in the OpenAPI spec"

        # Retained endpoints
        other_endpoints = ["/processing/status", "/llms.txt", "/analytics-agent/chat"]

        for endpoint in other_endpoints:
            assert endpoint in paths, f"Endpoint '{endpoint}' missing from OpenAPI spec"

        # Pricing endpoints
        pricing_endpoints = ["/pricing/rules", "/pricing/history", "/pricing/calculate"]

        for endpoint in pricing_endpoints:
            assert endpoint in paths, f"Pricing endpoint '{endpoint}' missing from OpenAPI spec"

    def test_pagination_model_updated(self):
        """Test that pagination model has been updated from offset-based to token-based."""
        paths = self.openapi_spec.get("paths", {})

        # Check content listing endpoint
        content_list = paths.get("/content", {}).get("get", {})
        parameters = content_list.get("parameters", [])

        # Should have next_token parameter instead of offset
        param_names = [p.get("name") for p in parameters]
        assert "next_token" in param_names, "Content listing should use next_token for pagination"
        assert "offset" not in param_names, "Content listing should not use offset-based pagination"

        # Check next_token parameter details
        next_token_param = next((p for p in parameters if p.get("name") == "next_token"), None)
        assert next_token_param is not None, "next_token parameter should be defined"
        assert "DynamoDB-compatible" in next_token_param.get("description", ""), (
            "next_token should mention DynamoDB compatibility"
        )

    def test_cors_configuration_documented(self):
        """Test that CORS configuration is properly documented."""
        # Read the raw YAML file to check for CORS documentation
        with open(self.spec_path, "r") as f:
            spec_content = f.read()

        # Check for CORS documentation in the spec
        assert "CORS" in spec_content, "CORS configuration should be documented"
        assert "Access-Control-Allow-Origin" in spec_content, "CORS headers should be documented"
        assert "OPTIONS" in spec_content, "CORS preflight should be documented"

    def test_error_response_schema_defined(self):
        """Test that standardized error response schema is defined."""
        schemas = self.openapi_spec.get("components", {}).get("schemas", {})

        assert "Error" in schemas, "Error schema should be defined"

        error_schema = schemas["Error"]
        required_fields = error_schema.get("required", [])
        properties = error_schema.get("properties", {})

        assert "error" in required_fields, "Error schema should require 'error' field"
        assert "timestamp" in properties, "Error schema should include 'timestamp' field"

    def test_iab_categories_schema_consistent(self):
        """Test that IAB categories schema is consistent across endpoints."""
        schemas = self.openapi_spec.get("components", {}).get("schemas", {})

        # Check IABContentCategory schema exists
        assert "IABContentCategory" in schemas, "IABContentCategory schema should be defined"

        iab_schema = schemas["IABContentCategory"]
        properties = iab_schema.get("properties", {})

        # Should have consistent structure
        assert "id" in properties, "IABContentCategory should have 'id' field"
        assert "name" in properties, "IABContentCategory should have 'name' field"
        assert "tier_1" in properties, "IABContentCategory should have 'tier_1' field"

        # Check that content schemas use consistent IAB category format
        content_schemas = ["ContentItem", "ContentDetails", "ContentIngestionRequest"]
        for schema_name in content_schemas:
            if schema_name in schemas:
                schema = schemas[schema_name]
                properties = schema.get("properties", {})
                if "iab_categories" in properties:
                    iab_prop = properties["iab_categories"]
                    # Should reference IABContentCategory or use integer array (standardized)
                    items = iab_prop.get("items", {})
                    assert (
                        items.get("$ref") == "#/components/schemas/IABContentCategory" or items.get("type") == "integer"
                    ), f"{schema_name} should use standardized IAB category format"

    def test_field_name_consistency(self):
        """Test that field names are consistent between schemas."""
        schemas = self.openapi_spec.get("components", {}).get("schemas", {})

        # Check for embedding_info vs embeddings consistency
        content_schemas = ["ContentItem", "ContentDetails"]
        for schema_name in content_schemas:
            if schema_name in schemas:
                schema = schemas[schema_name]
                properties = schema.get("properties", {})

                # Should use 'embedding_info' not 'embeddings'
                assert "embedding_info" in properties or "embeddings" not in properties, (
                    f"{schema_name} should use 'embedding_info' field name consistently"
                )

    def test_authentication_flows_documented(self):
        """Test that authentication flows are properly documented."""
        security_schemes = self.openapi_spec.get("components", {}).get("securitySchemes", {})

        # Check that each security scheme has proper documentation
        for scheme_name, scheme in security_schemes.items():
            description = scheme.get("description", "")
            assert len(description) > 50, f"Security scheme '{scheme_name}' should have detailed description"
            assert "**Usage**:" in description, f"Security scheme '{scheme_name}' should document usage"
            assert "**Header**:" in description, f"Security scheme '{scheme_name}' should document header format"

    def test_endpoint_security_configuration(self):
        """Test that endpoints have appropriate security configurations."""
        paths = self.openapi_spec.get("paths", {})

        # Analytics endpoints should require CognitoAuth only (UI-Only)
        analytics_paths = [p for p in paths.keys() if p.startswith("/analytics")]
        for path in analytics_paths:
            for method, operation in paths[path].items():
                if method in ["get", "post", "put", "delete"]:
                    security = operation.get("security", [])
                    assert len(security) > 0, (
                        f"Analytics endpoint {method.upper()} {path} should have security requirements"
                    )

                    # Should allow CognitoAuth only (UI-Only)
                    security_schemes = [list(s.keys())[0] for s in security if s]
                    assert "CognitoAuth" in security_schemes, (
                        f"Analytics endpoint {method.upper()} {path} should require CognitoAuth (UI-Only)"
                    )

        # Bot control endpoints should require CognitoAuth only (UI-Only)
        bot_control_paths = [p for p in paths.keys() if p.startswith("/bot-control")]
        for path in bot_control_paths:
            for method, operation in paths[path].items():
                if method in ["get", "post", "put", "delete"]:
                    security = operation.get("security", [])
                    assert len(security) > 0, (
                        f"Bot control endpoint {method.upper()} {path} should have security requirements"
                    )
                    security_schemes = [list(s.keys())[0] for s in security if s]
                    assert "CognitoAuth" in security_schemes, (
                        f"Bot control endpoint {method.upper()} {path} should require CognitoAuth (UI-Only)"
                    )

        # Pricing endpoints should require CognitoAuth only (UI-Only)
        pricing_paths = [p for p in paths.keys() if p.startswith("/pricing")]
        for path in pricing_paths:
            for method, operation in paths[path].items():
                if method in ["get", "post", "put", "delete"]:
                    security = operation.get("security", [])
                    assert len(security) > 0, (
                        f"Pricing endpoint {method.upper()} {path} should have security requirements"
                    )
                    security_schemes = [list(s.keys())[0] for s in security if s]
                    assert "CognitoAuth" in security_schemes, (
                        f"Pricing endpoint {method.upper()} {path} should require CognitoAuth (UI-Only)"
                    )

        # Content endpoints should require CognitoAuth only (UI-Only)
        content_paths = [p for p in paths.keys() if p.startswith("/content")]
        for path in content_paths:
            for method, operation in paths[path].items():
                if method in ["get", "post", "put", "delete"]:
                    security = operation.get("security", [])
                    assert len(security) > 0, (
                        f"Content endpoint {method.upper()} {path} should have security requirements"
                    )
                    security_schemes = [list(s.keys())[0] for s in security if s]
                    assert "CognitoAuth" in security_schemes, (
                        f"Content endpoint {method.upper()} {path} should require CognitoAuth (UI-Only)"
                    )

        # Analytics agent chat should require CognitoAuth only (UI-Only)
        if "/analytics-agent/chat" in paths:
            chat_operation = paths["/analytics-agent/chat"].get("post", {})
            security = chat_operation.get("security", [])
            assert len(security) > 0, "Analytics agent chat should have security requirements"
            security_schemes = [list(s.keys())[0] for s in security if s]
            assert "CognitoAuth" in security_schemes, "Analytics agent chat should require CognitoAuth (UI-Only)"

        # Processing status should require CognitoAuth only (UI-Only)
        if "/processing/status" in paths:
            status_operation = paths["/processing/status"].get("get", {})
            security = status_operation.get("security", [])
            assert len(security) > 0, "Processing status should have security requirements"
            security_schemes = [list(s.keys())[0] for s in security if s]
            assert "CognitoAuth" in security_schemes, "Processing status should require CognitoAuth (UI-Only)"

    def test_hybrid_authentication_endpoints(self):
        """Test that hybrid authentication endpoints support both Partner Key and Cognito JWT."""
        paths = self.openapi_spec.get("paths", {})

        # Hybrid endpoints should support both PartnerKeyAuth and CognitoAuth
        hybrid_endpoints = ["/query"]

        for endpoint in hybrid_endpoints:
            if endpoint in paths:
                for method, operation in paths[endpoint].items():
                    if method in ["get", "post", "put", "delete"]:
                        security = operation.get("security", [])
                        assert len(security) > 0, (
                            f"Hybrid endpoint {method.upper()} {endpoint} should have security requirements"
                        )

                        # Should support both PartnerKeyAuth and CognitoAuth
                        security_schemes = [list(s.keys())[0] for s in security if s]
                        assert "PartnerKeyAuth" in security_schemes, (
                            f"Hybrid endpoint {method.upper()} {endpoint} should support PartnerKeyAuth"
                        )
                        assert "CognitoAuth" in security_schemes, (
                            f"Hybrid endpoint {method.upper()} {endpoint} should support CognitoAuth"
                        )

                        # Description should mention hybrid authentication
                        description = operation.get("description", "").lower()
                        assert "hybrid" in description, (
                            f"Hybrid endpoint {method.upper()} {endpoint} should mention hybrid authentication in description"
                        )

    def test_partner_only_endpoints(self):
        """Test that partner-only endpoints have appropriate authentication."""
        paths = self.openapi_spec.get("paths", {})

        # Partner-only endpoints (public with optional auth or partner-only)
        partner_endpoints = ["/info", "/taxonomy", "/llms.txt", "/log"]

        for endpoint in partner_endpoints:
            if endpoint in paths:
                for method, operation in paths[endpoint].items():
                    if method in ["get", "post", "put", "delete"]:
                        security = operation.get("security", [])

                        if endpoint == "/log":
                            # /log should require PartnerKeyAuth only
                            assert len(security) > 0, (
                                f"Partner-only endpoint {method.upper()} {endpoint} should have security requirements"
                            )
                            security_schemes = [list(s.keys())[0] for s in security if s]
                            assert "PartnerKeyAuth" in security_schemes, (
                                f"Partner-only endpoint {method.upper()} {endpoint} should require PartnerKeyAuth"
                            )
                        else:
                            # /info, /taxonomy, /llms.txt should allow optional auth (empty security option)
                            has_optional_auth = any(not s for s in security)  # Empty dict means optional auth
                            has_partner_auth = any("PartnerKeyAuth" in s for s in security if s)
                            assert has_optional_auth or has_partner_auth, (
                                f"Public endpoint {method.upper()} {endpoint} should allow optional or partner authentication"
                            )

    def test_response_schemas_defined(self):
        """Test that response schemas are properly defined when they exist."""
        schemas = self.openapi_spec.get("components", {}).get("schemas", {})

        # Check that existing response schemas are properly structured
        # Note: Not all schemas may be defined yet - this test validates structure when they exist
        potential_response_schemas = [
            "AnalyticsMetrics",
            "UsageAnalytics",
            "RevenueMetrics",
            "BotControlRulesResponse",
            "BotActivityResponse",
            "AnalyticsAgentChatResponse",
            "PricingRulesResponse",
            "PricingHistoryResponse",
            "PricingCalculationResponse",
        ]

        for schema_name in potential_response_schemas:
            if schema_name in schemas:
                schema = schemas[schema_name]
                assert "type" in schema, f"Schema '{schema_name}' should have type defined"
                # Properties are optional for some schema types
                if schema.get("type") == "object":
                    assert "properties" in schema or "allOf" in schema or "oneOf" in schema, (
                        f"Object schema '{schema_name}' should have properties, allOf, or oneOf defined"
                    )


class TestAPIEndpointConsistency:
    """Test suite for API endpoint consistency with OpenAPI specification."""

    @classmethod
    def setup_class(cls):
        """Set up test class with API configuration."""
        cls.api_base_url = os.getenv("IAB_API_BASE_URL", "https://api.example.com")
        cls.partner_key = os.getenv("PARTNER_API_KEY", "test_key")
        cls.publisher_key = os.getenv("PUBLISHER_API_KEY", "test_key")
        cls.cognito_token = os.getenv("COGNITO_JWT_TOKEN", "test_token")

        # Load OpenAPI spec for reference
        spec_path = project_root / "openapi-spec.yaml"
        with open(spec_path, "r") as f:
            cls.openapi_spec = yaml.safe_load(f)

    def get_headers(self, auth_type: str = "partner") -> Dict[str, str]:
        """Get appropriate headers for API requests."""
        headers = {"Content-Type": "application/json"}

        if auth_type == "partner":
            headers["X-Partner-Key"] = self.partner_key
        elif auth_type == "publisher":
            headers["X-Publisher-Key"] = self.publisher_key
        elif auth_type == "cognito":
            headers["Authorization"] = f"Bearer {self.cognito_token}"

        return headers

    @pytest.mark.integration
    def test_analytics_endpoints_exist(self):
        """Test that analytics endpoints exist and return appropriate responses."""
        analytics_endpoints = [
            ("/analytics/metrics", "GET", "publisher"),
            ("/analytics/usage", "GET", "publisher"),
            ("/analytics/revenue", "GET", "publisher"),
            ("/analytics/revenue/categories", "GET", "publisher"),
            ("/analytics/partners", "GET", "publisher"),
        ]

        for endpoint, method, auth_type in analytics_endpoints:
            url = f"{self.api_base_url}{endpoint}"
            headers = self.get_headers(auth_type)

            try:
                if method == "GET":
                    response = requests.get(url, headers=headers, timeout=10)
                else:
                    response = requests.request(method, url, headers=headers, timeout=10)

                # Should not return 404 (endpoint exists)
                assert response.status_code != 404, f"Endpoint {method} {endpoint} should exist"

                # Should return JSON content type for successful responses
                if response.status_code == 200:
                    content_type = response.headers.get("Content-Type", "")
                    assert "application/json" in content_type, f"Endpoint {method} {endpoint} should return JSON"

            except requests.exceptions.RequestException as e:
                pytest.skip(f"Could not test endpoint {endpoint}: {e}")

    @pytest.mark.integration
    def test_bot_control_endpoints_exist(self):
        """Test that bot control endpoints exist and return appropriate responses."""
        bot_control_endpoints = [
            ("/bot-control/rules", "GET", "publisher"),
            ("/bot-control/activity", "GET", "publisher"),
            ("/bot-control/stats", "GET", "publisher"),
        ]

        for endpoint, method, auth_type in bot_control_endpoints:
            url = f"{self.api_base_url}{endpoint}"
            headers = self.get_headers(auth_type)

            try:
                response = requests.get(url, headers=headers, timeout=10)

                # Should not return 404 (endpoint exists)
                assert response.status_code != 404, f"Endpoint {method} {endpoint} should exist"

            except requests.exceptions.RequestException as e:
                pytest.skip(f"Could not test endpoint {endpoint}: {e}")

    @pytest.mark.integration
    def test_processing_status_endpoint_exists(self):
        """Test that processing status endpoint exists."""
        url = f"{self.api_base_url}/processing/status"
        headers = self.get_headers("publisher")

        try:
            response = requests.get(url, headers=headers, timeout=10)

            # Should not return 404 (endpoint exists)
            assert response.status_code != 404, "Processing status endpoint should exist"

        except requests.exceptions.RequestException as e:
            pytest.skip(f"Could not test processing status endpoint: {e}")

    @pytest.mark.integration
    def test_llms_txt_endpoints_exist(self):
        """Test that LLMs.txt endpoints exist."""
        endpoints = [
            ("/llms.txt", "GET", None),  # Should allow unauthenticated access
        ]

        for endpoint, method, auth_type in endpoints:
            url = f"{self.api_base_url}{endpoint}"
            headers = self.get_headers(auth_type) if auth_type else {}

            try:
                response = requests.get(url, headers=headers, timeout=10)

                # Should not return 404 (endpoint exists)
                assert response.status_code != 404, f"Endpoint {method} {endpoint} should exist"

            except requests.exceptions.RequestException as e:
                pytest.skip(f"Could not test endpoint {endpoint}: {e}")

    @pytest.mark.integration
    def test_cors_headers_present(self):
        """Test that CORS headers are present in API responses."""
        test_endpoints = ["/info", "/analytics/metrics", "/bot-control/rules"]

        for endpoint in test_endpoints:
            url = f"{self.api_base_url}{endpoint}"

            try:
                # Test OPTIONS request (CORS preflight)
                response = requests.options(url, timeout=10)

                # Should have CORS headers
                cors_headers = [
                    "Access-Control-Allow-Origin",
                    "Access-Control-Allow-Methods",
                    "Access-Control-Allow-Headers",
                ]

                for header in cors_headers:
                    assert header in response.headers, f"CORS header '{header}' should be present for {endpoint}"

            except requests.exceptions.RequestException as e:
                pytest.skip(f"Could not test CORS for endpoint {endpoint}: {e}")

    @pytest.mark.integration
    def test_pricing_endpoints_exist(self):
        """Test that pricing endpoints exist and return appropriate responses."""
        pricing_endpoints = [
            ("/pricing/rules", "GET", "cognito"),
            ("/pricing/history", "GET", "cognito"),
            ("/pricing/calculate", "POST", "cognito"),
        ]

        for endpoint, method, auth_type in pricing_endpoints:
            url = f"{self.api_base_url}{endpoint}"
            headers = self.get_headers(auth_type)

            try:
                if method == "GET":
                    response = requests.get(url, headers=headers, timeout=10)
                elif method == "POST":
                    response = requests.post(url, headers=headers, json={}, timeout=10)
                else:
                    response = requests.request(method, url, headers=headers, timeout=10)

                # Should not return 404 (endpoint exists)
                assert response.status_code != 404, f"Endpoint {method} {endpoint} should exist"

                # Should return JSON content type for successful responses
                if response.status_code == 200:
                    content_type = response.headers.get("Content-Type", "")
                    assert "application/json" in content_type, f"Endpoint {method} {endpoint} should return JSON"

            except requests.exceptions.RequestException as e:
                pytest.skip(f"Could not test endpoint {endpoint}: {e}")

    @pytest.mark.integration
    def test_hybrid_endpoints_support_both_auth_types(self):
        """Test that hybrid endpoints support both Partner Key and Cognito JWT authentication."""
        hybrid_endpoints = [("/query", "POST")]

        for endpoint, method in hybrid_endpoints:
            url = f"{self.api_base_url}{endpoint}"

            # Test with Partner Key
            try:
                partner_headers = self.get_headers("partner")
                partner_response = requests.post(url, headers=partner_headers, json={}, timeout=10)

                # Should not return 404 (endpoint exists)
                assert partner_response.status_code != 404, (
                    f"Hybrid endpoint {method} {endpoint} should exist for Partner Key auth"
                )

                # Should not return 401 for valid partner key (assuming test key is valid)
                # Note: May return 400 for invalid request body, but not 401 for auth

            except requests.exceptions.RequestException as e:
                pytest.skip(f"Could not test Partner Key auth for {endpoint}: {e}")

            # Test with Cognito JWT
            try:
                cognito_headers = self.get_headers("cognito")
                cognito_response = requests.post(url, headers=cognito_headers, json={}, timeout=10)

                # Should not return 404 (endpoint exists)
                assert cognito_response.status_code != 404, (
                    f"Hybrid endpoint {method} {endpoint} should exist for Cognito JWT auth"
                )

            except requests.exceptions.RequestException as e:
                pytest.skip(f"Could not test Cognito JWT auth for {endpoint}: {e}")


# HTTP methods considered "operations" for tag/x-audience assertions.
_OP_METHODS = ("get", "post", "put", "delete", "patch")

# The partner-facing surface (x-audience == "partner"), per #31 Requirement 6.2.
# Fail-closed: any contract path that is neither here nor in the internal set fails the gate,
# forcing a deliberate classification when new paths are added.
_PARTNER_PATHS = {
    "/info",
    "/query",
    "/taxonomy",
    "/llms.txt",
    "/.well-known/comp.json",
    "/log",
    "/licenses",
    "/licenses/{license_id}",
    "/licenses/{license_id}/validate",
    "/licenses/{license_id}/versions",
}

# The internal surface (x-audience == "internal"), per #31 Requirement 6.3 (incl. all existing /content*).
_INTERNAL_PATHS = {
    "/content/llms-txt",
    "/content/llms-txt/runs",
    "/content/llms-txt/runs/{run_id}",
    "/content/llms-txt/drafts/{run_id}",
    "/content/llms-txt/drafts/{run_id}/publish",
    "/analytics/metrics",
    "/analytics/usage",
    "/analytics/revenue",
    "/analytics/revenue/categories",
    "/analytics/trends",
    "/analytics/partners",
    "/analytics-agent/chat",
    "/bot-control/rules",
    "/bot-control/rules/{rule_id}",
    "/bot-control/activity",
    "/bot-control/stats",
    "/pricing/rules",
    "/pricing/history",
    "/pricing/calculate",
    "/partners",
    "/partners/{partner_id}",
    "/partners/{partner_id}/keys",
    "/partners/{partner_id}/keys/{key_id}",
    "/processing/status",
    "/taxonomy/categories",
    "/content",
    "/content/bulk",
    "/content/{content_id}",
    "/content/{content_id}/reprocess",
    "/content/{content_id}/metadata",
    # Connections API (integration-adapters #35) — internal (Cognito) only, never partner-facing.
    "/connections/adapters",
    "/connections",
    "/connections/{id}",
    "/connections/{id}/test",
    "/connections/{id}/status",
    "/connections/mirrors/{domain}",
}

_WORKFLOW_TAGS = [
    "Ingest",
    "Enrich",
    "Price",
    "License",
    "Onboard & Manage",
    "Connections",
    "Control",
    "Measure",
]
_PARTNER_TAGS = ["Discovery", "Content Access", "Usage Reporting"]


class TestContractCategorization:
    """#31: tags, x-tagGroups, and the x-audience audience partition on the SSOT."""

    @classmethod
    def setup_class(cls):
        with open(project_root / "openapi-spec.yaml", "r") as f:
            cls.spec = yaml.safe_load(f)
        cls.paths = cls.spec.get("paths", {}) or {}

    def _operations(self):
        """Yield (path, method, operation) for every real operation."""
        for path, ops in self.paths.items():
            for method, op in ops.items():
                if method in _OP_METHODS and isinstance(op, dict):
                    yield path, method, op

    # --- Requirement 1: tags + x-tagGroups ---

    def test_every_operation_has_exactly_one_tag(self):
        offenders = [
            f"{m.upper()} {p}"
            for p, m, op in self._operations()
            if not (isinstance(op.get("tags"), list) and len(op["tags"]) == 1)
        ]
        assert not offenders, f"operations without exactly one tag: {offenders}"

    def test_tag_groups_present_and_correct(self):
        groups = {g["name"]: g["tags"] for g in self.spec.get("x-tagGroups", [])}
        assert set(groups) == {"Publisher Workflow", "Partner API"}, (
            f"unexpected x-tagGroups: {list(groups)}"
        )
        assert groups["Publisher Workflow"] == _WORKFLOW_TAGS
        assert groups["Partner API"] == _PARTNER_TAGS

    def test_every_used_tag_belongs_to_a_group(self):
        grouped = {t for g in self.spec.get("x-tagGroups", []) for t in g["tags"]}
        used = {op["tags"][0] for _, _, op in self._operations() if op.get("tags")}
        assert used <= grouped, f"tags used but not in any group: {used - grouped}"

    def test_tag_overrides(self):
        """Requirement 1.3 exact-path overrides."""
        # /licenses/{id}/validate is Content Access; the rest of /licenses/* is License.
        assert self.paths["/licenses/{license_id}/validate"]["post"]["tags"] == ["Content Access"]
        for m in ("get", "post"):
            if m in self.paths["/licenses"]:
                assert self.paths["/licenses"][m]["tags"] == ["License"]
        # /taxonomy/categories -> Ingest (provisional); /partners* -> Onboard & Manage;
        # /analytics/partners stays Measure.
        assert self.paths["/taxonomy/categories"]["get"]["tags"] == ["Ingest"]
        assert self.paths["/partners"]["get"]["tags"] == ["Onboard & Manage"]
        assert self.paths["/analytics/partners"]["get"]["tags"] == ["Measure"]
        # existing /content/{id}/metadata is internal content-management, NOT partner Content Access
        assert self.paths["/content/{content_id}/metadata"]["get"]["tags"] == ["Ingest"]

    # --- Requirement 2: status-noise removal ---

    def test_no_status_markers_in_summaries_or_descriptions(self):
        markers = ("DEPLOYED", "NOT PART OF COMP", "(UI-Only)", "CoMP Endpoint", "Deployment Status")
        offenders = []
        for p, m, op in self._operations():
            text = f"{op.get('summary', '')}\n{op.get('description', '')}"
            for mk in markers:
                if mk in text:
                    offenders.append(f"{m.upper()} {p}: {mk}")
        assert not offenders, f"status markers remain: {offenders}"

    def test_info_cleaned(self):
        info = self.spec["info"]
        assert "contact" not in info, "info.contact should be removed"
        assert "license" not in info, "info.license should be removed"
        assert info["description"].startswith("Complete API specification for the AI Content Monetization.")
        # single-sentence: no leftover status narrative
        assert "DEPLOYED" not in info["description"]
        assert "Implementation Status" not in info["description"]

    # --- Requirement 7: x-audience partition (fail-closed) ---
    #
    # Audience lives under `x-audience: internal|partner`, NOT the Scalar-reserved `x-internal`
    # (which recent @scalar/api-reference hides from the sidebar). This test also fails if the
    # reserved key reappears anywhere in the contract.

    _VALID_AUDIENCES = {"internal", "partner"}

    def test_no_reserved_x_internal_key_anywhere(self):
        """The Scalar-reserved x-internal must not appear on any operation (it hides ops)."""
        offenders = [f"{m.upper()} {p}" for p, m, op in self._operations() if "x-internal" in op]
        assert not offenders, (
            f"x-internal is Scalar-reserved (hides operations); use x-audience instead: {offenders}"
        )

    def test_every_operation_has_explicit_x_audience(self):
        offenders = [
            f"{m.upper()} {p}"
            for p, m, op in self._operations()
            if op.get("x-audience") not in self._VALID_AUDIENCES
        ]
        assert not offenders, f"operations without explicit x-audience (internal|partner): {offenders}"

    def test_x_audience_partition_matches_exactly(self):
        """Fail-closed: every path must be classified, and match the declared partition."""
        contract_paths = set(self.paths)
        classified = _PARTNER_PATHS | _INTERNAL_PATHS
        unclassified = contract_paths - classified
        assert not unclassified, (
            f"paths neither partner nor internal (classify them in #31 + this test): {unclassified}"
        )
        stale = classified - contract_paths
        assert not stale, f"partition lists a path not in the contract: {stale}"

        for path, m, op in self._operations():
            expected = "internal" if path in _INTERNAL_PATHS else "partner"
            assert op["x-audience"] == expected, (
                f"{m.upper()} {path}: x-audience={op['x-audience']} but expected {expected}"
            )

    def test_partner_paths_are_not_internal(self):
        for path in _PARTNER_PATHS:
            for m, op in self.paths[path].items():
                if m in _OP_METHODS:
                    assert op["x-audience"] == "partner", f"{m.upper()} {path} should be partner-facing"


def test_markdown_schema_version_is_exposed_by_content_metadata_schemas():
    with open(project_root / "openapi-spec.yaml") as spec_file:
        schemas = yaml.safe_load(spec_file)["components"]["schemas"]

    version_schema = schemas["MarkdownSchemaVersion"]
    assert version_schema["enum"] == ["1.0", "legacy"]

    for schema_name in (
        "ContentResult",
        "ContentSummary",
        "CompTextAsset",
        "ContentMetadataResponse",
        "ContentItem",
    ):
        field = schemas[schema_name]["properties"]["markdown_schema_version"]
        assert field["$ref"] == "#/components/schemas/MarkdownSchemaVersion"

    assert schemas["ContentDetails"]["allOf"][0]["$ref"] == "#/components/schemas/ContentItem"


def test_native_edge_metadata_is_exposed_by_content_read_schemas():
    with open(project_root / "openapi-spec.yaml") as spec_file:
        schemas = yaml.safe_load(spec_file)["components"]["schemas"]

    expected = {
        "canonical_url",
        "url_hash",
        "edge_eligible",
        "edge_ineligible_reason",
        "md_s3_key",
    }
    for schema_name in ("ContentSummary", "ContentItem"):
        assert expected <= schemas[schema_name]["properties"].keys()
        assert schemas[schema_name]["properties"]["url_hash"]["pattern"] == "^[0-9a-f]{64}$"
        assert schemas[schema_name]["properties"]["md_s3_key"]["pattern"] == "^content/"


def test_discovery_contract_documents_opensearch_availability_and_no_s3_vector_path():
    with open(project_root / "openapi-spec.yaml") as spec_file:
        spec = yaml.safe_load(spec_file)

    schemas = spec["components"]["schemas"]
    for endpoint in ("/query",):
        response = spec["paths"][endpoint]["post"]["responses"]["503"]
        assert response["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/Error"

    error_properties = schemas["Error"]["properties"]
    assert {"error_code", "status_code"} <= error_properties.keys()

    assert "/search" not in spec["paths"]
    assert "StandardSearchRequest" not in schemas
    assert "use_s3_vector" not in schemas["StandardQueryRequest"]["properties"]

    comp_extension = schemas["CompAISystemUse"]["properties"]["ext"]["properties"]["discovery"]
    assert comp_extension["$ref"] == "#/components/schemas/CompDiscoveryFilters"
    assert not any(name.startswith("VectorStore") for name in schemas)
    assert "MigrateEmbeddingsRequest" not in schemas
    assert "TestVectorStoreRequest" not in schemas


if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v"])
