"""
Data validation module for mock data generator.

This module provides schema validation and referential integrity checking
for generated mock data to ensure it meets DynamoDB table requirements.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple


def safe_parse_datetime(date_str):
    """Parse an ISO-8601 datetime string, raising ValueError on malformed input.

    Self-contained on purpose. ``mock_data_generator`` is a standalone dev tool whose CLI
    (``generate_mock_data.py``) puts only ``source/`` on ``sys.path`` — NOT ``source/lambda`` — so it
    cannot import the Lambda-side ``shared.utils.safe_datetime_parser`` at runtime. GitLab #11 (Req
    7.4 / task 6.1) requires removing the former non-canonical ``from utils.safe_datetime_parser``
    import and its ``try/except ImportError`` mask; inlining the implementation here is the
    runtime-correct, mask-free fix. It intentionally *raises* ``ValueError`` on malformed input (the
    contract ``_validate_format`` relies on), unlike the Lambda helper which returns ``None``.
    """
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """Represents a validation error with details."""

    field: str
    message: str
    value: Any = None
    record_id: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of validation operation."""

    is_valid: bool
    errors: List[ValidationError]
    warnings: List[ValidationError]

    def add_error(self, field: str, message: str, value: Any = None, record_id: Optional[str] = None):
        """Add a validation error."""
        self.errors.append(ValidationError(field, message, value, record_id))
        self.is_valid = False

    def add_warning(self, field: str, message: str, value: Any = None, record_id: Optional[str] = None):
        """Add a validation warning."""
        self.warnings.append(ValidationError(field, message, value, record_id))

    def merge(self, other: "ValidationResult"):
        """Merge another validation result into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        if not other.is_valid:
            self.is_valid = False


class SchemaValidator:
    """Validates generated data against DynamoDB table schemas."""

    # DynamoDB attribute type mappings
    VALID_ATTRIBUTE_TYPES = {"S", "N", "B", "SS", "NS", "BS", "M", "L", "NULL", "BOOL"}

    # Content table schema definition
    CONTENT_SCHEMA = {
        "content_id": {"type": "S", "required": True, "pattern": r"^(art|rpt|vid|img|doc)_\d{3}$"},
        "title": {"type": "S", "required": True, "min_length": 1, "max_length": 500},
        "content_type": {"type": "S", "required": True, "values": ["article", "report", "video", "image", "document"]},
        "description": {"type": "S", "required": True, "min_length": 1, "max_length": 2000},
        "content": {"type": "S", "required": True, "min_length": 1},
        "author": {"type": "S", "required": True, "min_length": 1, "max_length": 200},
        "publisher": {"type": "S", "required": True, "min_length": 1, "max_length": 200},
        "created_at": {"type": "S", "required": True, "format": "iso_datetime"},
        "updated_at": {"type": "S", "required": True, "format": "iso_datetime"},
        "iab_categories": {"type": "L", "required": True, "min_items": 1, "max_items": 5},
        "tags": {"type": "SS", "required": True, "min_items": 1, "max_items": 10},
        "url": {"type": "S", "required": True, "format": "url"},
        "access_level": {"type": "S", "required": True, "values": ["public", "premium", "restricted"]},
        "pricing_tier": {"type": "S", "required": True, "values": ["free", "basic", "premium"]},
        "language": {"type": "S", "required": True, "pattern": r"^[a-z]{2}$"},
        "word_count": {"type": "N", "required": True, "min_value": 0},
        "metadata": {"type": "M", "required": True},
    }

    # Partner table schema definition (matches actual generator output): META items only.
    PARTNER_SCHEMA = {
        "PK": {"type": "S", "required": True, "pattern": r"^PARTNER#partner_\d{3}$"},
        "SK": {"type": "S", "required": True, "values": ["META"]},
        "partner_id": {"type": "S", "required": True, "pattern": r"^partner_\d{3}$"},
        "partner_name": {"type": "S", "required": True, "min_length": 1, "max_length": 200},
        "partner_type": {"type": "S", "required": True, "values": ["publisher", "ai_operator", "analytics_provider"]},
        "contact_email": {"type": "S", "required": True, "format": "email"},
        "contact_name": {"type": "S", "required": True, "min_length": 1, "max_length": 200},
        "pricing_tier": {"type": "S", "required": True, "values": ["basic", "standard", "premium", "enterprise"]},
        "status": {"type": "S", "required": True, "values": ["active", "suspended"]},
        "identity_source": {"type": "S", "required": True, "values": ["direct", "marketplace"]},
        "allowed_endpoints": {"type": "L", "required": True},
        "created_at": {"type": "S", "required": True, "format": "iso_datetime"},
        "updated_at": {"type": "S", "required": True, "format": "iso_datetime"},
    }

    # Jobs table schema definition
    JOBS_SCHEMA = {
        "job_id": {"type": "S", "required": True, "pattern": r"^job_\d{3}$"},
        "content_id": {"type": "S", "required": True, "pattern": r"^(art|rpt|vid|img|doc)_\d{3}$"},
        "partner_id": {"type": "S", "required": True, "pattern": r"^partner_\d{3}$"},
        "job_type": {"type": "S", "required": True, "values": ["embedding", "processing", "analysis"]},
        "status": {"type": "S", "required": True, "values": ["pending", "processing", "completed", "failed"]},
        "created_at": {"type": "S", "required": True, "format": "iso_datetime"},
        "started_at": {"type": "S", "required": False, "format": "iso_datetime"},
        "completed_at": {"type": "S", "required": False, "format": "iso_datetime"},
        "processing_time_ms": {"type": "N", "required": False, "min_value": 0},
        "error_message": {"type": "S", "required": False},
        "input_params": {"type": "M", "required": True},
        "output_data": {"type": "M", "required": False},
        "retry_count": {"type": "N", "required": True, "min_value": 0, "max_value": 10},
        "priority": {"type": "N", "required": True, "min_value": 1, "max_value": 10},
        "metadata": {"type": "M", "required": True},
    }

    # Access logs table schema definition
    ACCESS_LOGS_SCHEMA = {
        "log_id": {"type": "S", "required": True, "format": "uuid"},
        "partner_id": {"type": "S", "required": True, "pattern": r"^partner_\d{3}$"},
        "content_id": {"type": "S", "required": False, "pattern": r"^(art|rpt|vid|img|doc)_\d{3}$"},
        "timestamp": {"type": "S", "required": True, "format": "iso_datetime"},
        "endpoint": {"type": "S", "required": True, "min_length": 1},
        "method": {"type": "S", "required": True, "values": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
        "response_code": {"type": "N", "required": True, "min_value": 100, "max_value": 599},
        "response_time_ms": {"type": "N", "required": True, "min_value": 0},
        "request_size": {"type": "N", "required": True, "min_value": 0},
        "response_size": {"type": "N", "required": True, "min_value": 0},
        "ip_address": {"type": "S", "required": True, "format": "ip"},
        "user_agent": {"type": "S", "required": True, "min_length": 1},
        "query_params": {"type": "M", "required": False},
        "error_details": {"type": "S", "required": False},
        "billing_units": {"type": "N", "required": True, "min_value": 0},
        "metadata": {"type": "M", "required": True},
    }

    # Analytics table schema definition
    ANALYTICS_SCHEMA = {
        "metric_date": {"type": "S", "required": True, "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "metric_type_timestamp": {"type": "S", "required": True, "min_length": 1},
        "metric_type": {
            "type": "S",
            "required": True,
            "values": [
                "api_requests",
                "content_access",
                "bot_access",
                "mcp_tool_usage",
                "revenue_daily",
                "search_query",
            ],
        },
        "timestamp": {"type": "S", "required": True, "format": "iso_datetime"},
        "value": {"type": "N", "required": True, "min_value": 0},
        "metadata": {"type": "M", "required": True},
    }

    # Bot rules table schema definition
    BOT_RULES_SCHEMA = {
        "rule_id": {"type": "S", "required": True, "pattern": r"^rule_\d{3}$"},
        "rule_name": {"type": "S", "required": True, "min_length": 1, "max_length": 200},
        "description": {"type": "S", "required": True, "min_length": 1, "max_length": 1000},
        "action": {"type": "S", "required": True, "values": ["block", "allow", "count"]},
        "priority": {"type": "N", "required": True, "min_value": 1, "max_value": 1000},
        "conditions": {"type": "M", "required": True},
        "status": {"type": "S", "required": True, "values": ["active", "inactive"]},
        "created_at": {"type": "S", "required": True, "format": "iso_datetime"},
        "updated_at": {"type": "S", "required": True, "format": "iso_datetime"},
        "waf_rule_id": {"type": "S", "required": False, "min_length": 1},
        "last_synced": {"type": "S", "required": False, "format": "iso_datetime"},
        "sync_status": {"type": "S", "required": True, "values": ["synced", "pending", "error"]},
        "metadata": {"type": "M", "required": True},
    }

    # Pricing rules table schema definition
    PRICING_RULES_SCHEMA = {
        "rule_set_id": {"type": "S", "required": True, "min_length": 1, "max_length": 100},
        "version_timestamp": {"type": "S", "required": True, "format": "iso_datetime"},
        "effective_date": {"type": "S", "required": True, "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "status": {"type": "S", "required": True, "values": ["active", "inactive", "draft"]},
        "created_by": {"type": "S", "required": True, "format": "email"},
        "created_at": {"type": "S", "required": True, "format": "iso_datetime"},
        "pricing_rules": {"type": "M", "required": True},
        "audit_trail": {"type": "M", "required": True},
        "activated_at": {"type": "S", "required": False, "format": "iso_datetime"},
    }

    # Pricing transactions table schema definition
    PRICING_TRANSACTIONS_SCHEMA = {
        "transaction_date": {"type": "S", "required": True, "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "transaction_id": {"type": "S", "required": True, "pattern": r"^txn_\d{8}_\d{6}$"},
        "transaction_timestamp": {"type": "S", "required": True, "format": "iso_datetime"},
        "content_id": {"type": "S", "required": True, "pattern": r"^(art|rpt|vid|img|doc)_\d{3}$"},
        "partner_id": {"type": "S", "required": True, "pattern": r"^partner_\d{3}$"},
        "access_type": {"type": "S", "required": True, "values": ["preview", "full", "download"]},
        "content_type": {"type": "S", "required": True, "values": ["article", "report", "video", "image", "document"]},
        "pricing_rule_version": {"type": "S", "required": True, "min_length": 1},
        "cost_calculation": {"type": "M", "required": True},
        "metadata": {"type": "M", "required": True},
    }

    def __init__(self):
        """Initialize the schema validator."""
        self.schemas = {
            "content": self.CONTENT_SCHEMA,
            "partners": self.PARTNER_SCHEMA,
            "jobs": self.JOBS_SCHEMA,
            "access_logs": self.ACCESS_LOGS_SCHEMA,
            "analytics": self.ANALYTICS_SCHEMA,
            "bot_rules": self.BOT_RULES_SCHEMA,
            "pricing_rules": self.PRICING_RULES_SCHEMA,
            "pricing_transactions": self.PRICING_TRANSACTIONS_SCHEMA,
        }

    def validate_record(self, record: Dict[str, Any], table_type: str) -> ValidationResult:
        """
        Validate a single record against its table schema.

        Args:
            record: The record to validate (DynamoDB format)
            table_type: Type of table ('content', 'partners', 'jobs', 'access_logs', 'analytics', 'bot_rules')

        Returns:
            ValidationResult with any errors or warnings
        """
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        if table_type not in self.schemas:
            result.add_error("table_type", f"Unknown table type: {table_type}")
            return result

        schema = self.schemas[table_type]
        record_id = self._extract_record_id(record, table_type)

        # Check for required fields
        for field_name, field_schema in schema.items():
            if field_schema.get("required", False) and field_name not in record:
                result.add_error(field_name, "Required field missing", record_id=record_id)
                continue

            if field_name in record:
                field_result = self._validate_field(field_name, record[field_name], field_schema, record_id)
                result.merge(field_result)

        # Check for unexpected fields
        for field_name in record:
            if field_name not in schema:
                result.add_warning(field_name, f"Unexpected field in {table_type} record", record_id=record_id)

        return result

    def validate_records(self, records: List[Dict[str, Any]], table_type: str) -> ValidationResult:
        """
        Validate multiple records against their table schema.

        Args:
            records: List of records to validate
            table_type: Type of table

        Returns:
            Combined ValidationResult for all records
        """
        combined_result = ValidationResult(is_valid=True, errors=[], warnings=[])

        for i, record in enumerate(records):
            record_result = self.validate_record(record, table_type)
            combined_result.merge(record_result)

            if i % 100 == 0 and i > 0:
                logger.debug(f"Validated {i}/{len(records)} {table_type} records")

        logger.info(
            f"Validated {len(records)} {table_type} records - "
            f"Errors: {len(combined_result.errors)}, Warnings: {len(combined_result.warnings)}"
        )

        return combined_result

    def _validate_field(
        self, field_name: str, field_value: Dict[str, Any], field_schema: Dict[str, Any], record_id: Optional[str]
    ) -> ValidationResult:
        """
        Validate a single field against its schema definition.

        Args:
            field_name: Name of the field
            field_value: DynamoDB-formatted field value
            field_schema: Schema definition for the field
            record_id: ID of the record being validated

        Returns:
            ValidationResult for the field
        """
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        # Check DynamoDB attribute type
        expected_type = field_schema["type"]
        if expected_type not in field_value:
            result.add_error(field_name, f"Missing DynamoDB type {expected_type}", field_value, record_id)
            return result

        actual_value = field_value[expected_type]

        # Validate based on DynamoDB type
        if expected_type == "S":
            field_result = self._validate_string_field(field_name, actual_value, field_schema, record_id)
        elif expected_type == "N":
            field_result = self._validate_number_field(field_name, actual_value, field_schema, record_id)
        elif expected_type == "SS":
            field_result = self._validate_string_set_field(field_name, actual_value, field_schema, record_id)
        elif expected_type == "M":
            field_result = self._validate_map_field(field_name, actual_value, field_schema, record_id)
        elif expected_type == "BOOL":
            field_result = self._validate_boolean_field(field_name, actual_value, field_schema, record_id)
        else:
            # For other types (B, NS, BS, L, NULL), just check they exist
            field_result = ValidationResult(is_valid=True, errors=[], warnings=[])

        result.merge(field_result)
        return result

    def _validate_string_field(
        self, field_name: str, value: str, field_schema: Dict[str, Any], record_id: Optional[str]
    ) -> ValidationResult:
        """Validate a string field."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        if not isinstance(value, str):
            result.add_error(field_name, f"Expected string, got {type(value).__name__}", value, record_id)
            return result

        # Check length constraints
        if "min_length" in field_schema and len(value) < field_schema["min_length"]:
            result.add_error(field_name, f"String too short (min: {field_schema['min_length']})", value, record_id)

        if "max_length" in field_schema and len(value) > field_schema["max_length"]:
            result.add_error(field_name, f"String too long (max: {field_schema['max_length']})", value, record_id)

        # Check allowed values
        if "values" in field_schema and value not in field_schema["values"]:
            result.add_error(field_name, f"Invalid value. Allowed: {field_schema['values']}", value, record_id)

        # Check pattern matching
        if "pattern" in field_schema:
            if not re.match(field_schema["pattern"], value):
                result.add_error(field_name, f"Does not match pattern: {field_schema['pattern']}", value, record_id)

        # Check format validation
        if "format" in field_schema:
            format_result = self._validate_format(field_name, value, field_schema["format"], record_id)
            result.merge(format_result)

        return result

    def _validate_number_field(
        self, field_name: str, value: str, field_schema: Dict[str, Any], record_id: Optional[str]
    ) -> ValidationResult:
        """Validate a number field (DynamoDB stores numbers as strings)."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        try:
            numeric_value = float(value)
        except (ValueError, TypeError):
            result.add_error(field_name, "Invalid number format", value, record_id)
            return result

        # Check range constraints
        if "min_value" in field_schema and numeric_value < field_schema["min_value"]:
            result.add_error(field_name, f"Number too small (min: {field_schema['min_value']})", value, record_id)

        if "max_value" in field_schema and numeric_value > field_schema["max_value"]:
            result.add_error(field_name, f"Number too large (max: {field_schema['max_value']})", value, record_id)

        return result

    def _validate_string_set_field(
        self, field_name: str, value: List[str], field_schema: Dict[str, Any], record_id: Optional[str]
    ) -> ValidationResult:
        """Validate a string set field."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        if not isinstance(value, list):
            result.add_error(field_name, f"Expected list for string set, got {type(value).__name__}", value, record_id)
            return result

        # Check item count constraints
        if "min_items" in field_schema and len(value) < field_schema["min_items"]:
            result.add_error(field_name, f"Too few items (min: {field_schema['min_items']})", value, record_id)

        if "max_items" in field_schema and len(value) > field_schema["max_items"]:
            result.add_error(field_name, f"Too many items (max: {field_schema['max_items']})", value, record_id)

        # Check for duplicates
        if len(value) != len(set(value)):
            result.add_warning(field_name, "String set contains duplicates", value, record_id)

        # Validate each string in the set
        for item in value:
            if not isinstance(item, str):
                result.add_error(field_name, f"String set contains non-string: {type(item).__name__}", item, record_id)

        return result

    def _validate_map_field(
        self, field_name: str, value: Dict[str, Any], field_schema: Dict[str, Any], record_id: Optional[str]
    ) -> ValidationResult:
        """Validate a map field."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        if not isinstance(value, dict):
            result.add_error(field_name, f"Expected dict for map, got {type(value).__name__}", value, record_id)
            return result

        # Basic validation - ensure it's a valid DynamoDB map structure
        for key, val in value.items():
            if not isinstance(key, str):
                result.add_error(field_name, f"Map key must be string, got {type(key).__name__}", key, record_id)

            if not isinstance(val, dict) or not any(attr_type in val for attr_type in self.VALID_ATTRIBUTE_TYPES):
                result.add_warning(field_name, "Map value may not be valid DynamoDB format", val, record_id)

        return result

    def _validate_boolean_field(
        self, field_name: str, value: bool, field_schema: Dict[str, Any], record_id: Optional[str]
    ) -> ValidationResult:
        """Validate a boolean field."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        if not isinstance(value, bool):
            result.add_error(field_name, f"Expected boolean, got {type(value).__name__}", value, record_id)

        return result

    def _validate_format(
        self, field_name: str, value: str, format_type: str, record_id: Optional[str]
    ) -> ValidationResult:
        """Validate string format constraints."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        if format_type == "iso_datetime":
            try:
                safe_parse_datetime(value)
            except ValueError:
                result.add_error(field_name, "Invalid ISO datetime format", value, record_id)

        elif format_type == "email":
            email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            if not re.match(email_pattern, value):
                result.add_error(field_name, "Invalid email format", value, record_id)

        elif format_type == "url":
            url_pattern = r"^https?://[^\s/$.?#].[^\s]*$"
            if not re.match(url_pattern, value):
                result.add_error(field_name, "Invalid URL format", value, record_id)

        elif format_type == "uuid":
            uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            if not re.match(uuid_pattern, value.lower()):
                result.add_error(field_name, "Invalid UUID format", value, record_id)

        elif format_type == "ip":
            # Simple IP validation (IPv4)
            ip_pattern = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
            if not re.match(ip_pattern, value):
                result.add_error(field_name, "Invalid IP address format", value, record_id)

        return result

    def _extract_record_id(self, record: Dict[str, Any], table_type: str) -> Optional[str]:
        """Extract the record ID for error reporting."""
        id_fields = {
            "content": "content_id",
            "partners": "partner_id",
            "jobs": "job_id",
            "access_logs": "log_id",
            "analytics": "metric_type_timestamp",
            "bot_rules": "rule_id",
        }

        id_field = id_fields.get(table_type)
        if id_field and id_field in record:
            field_value = record[id_field]
            if isinstance(field_value, dict) and "S" in field_value:
                return field_value["S"]

        return None


class ReferentialIntegrityValidator:
    """Validates referential integrity between related tables."""

    def __init__(self):
        """Initialize the referential integrity validator."""
        pass

    def validate_referential_integrity(
        self,
        content_records: List[Dict[str, Any]],
        partner_records: List[Dict[str, Any]],
        job_records: List[Dict[str, Any]],
        access_log_records: List[Dict[str, Any]],
        analytics_records: List[Dict[str, Any]] = None,
        bot_rules_records: List[Dict[str, Any]] = None,
        pricing_rules_records: List[Dict[str, Any]] = None,
        pricing_transactions_records: List[Dict[str, Any]] = None,
    ) -> ValidationResult:
        """
        Validate referential integrity across all tables.

        Args:
            content_records: List of content records
            partner_records: List of partner records
            job_records: List of job records
            access_log_records: List of access log records
            analytics_records: List of analytics records (optional)
            bot_rules_records: List of bot rules records (optional)
            pricing_rules_records: List of pricing rules records (optional)
            pricing_transactions_records: List of pricing transactions records (optional)

        Returns:
            ValidationResult with referential integrity errors
        """
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        # Extract IDs from each table
        content_ids = self._extract_ids(content_records, "content_id")
        partner_ids = self._extract_ids(partner_records, "partner_id")

        logger.info(
            f"Validating referential integrity - Content IDs: {len(content_ids)}, Partner IDs: {len(partner_ids)}"
        )

        # Validate job records reference existing content and partners
        job_integrity = self._validate_job_references(job_records, content_ids, partner_ids)
        result.merge(job_integrity)

        # Validate access log records reference existing partners and content
        log_integrity = self._validate_access_log_references(access_log_records, content_ids, partner_ids)
        result.merge(log_integrity)

        # Validate analytics records reference existing content and partners
        if analytics_records:
            analytics_integrity = self._validate_analytics_references(analytics_records, content_ids, partner_ids)
            result.merge(analytics_integrity)

        # Validate bot rules records (self-contained, no external references required)
        if bot_rules_records:
            bot_rules_integrity = self._validate_bot_rules_references(bot_rules_records)
            result.merge(bot_rules_integrity)

        # Validate pricing rules records (self-contained, no external references required)
        if pricing_rules_records:
            pricing_rules_integrity = self._validate_pricing_rules_references(pricing_rules_records)
            result.merge(pricing_rules_integrity)

        # Validate pricing transactions records reference existing content and partners
        if pricing_transactions_records:
            pricing_transactions_integrity = self._validate_pricing_transactions_references(
                pricing_transactions_records, content_ids, partner_ids
            )
            result.merge(pricing_transactions_integrity)

        # Check for orphaned records
        orphan_check = self._check_for_orphaned_records(
            content_ids,
            partner_ids,
            job_records,
            access_log_records,
            analytics_records,
            bot_rules_records,
            pricing_rules_records,
            pricing_transactions_records,
        )
        result.merge(orphan_check)

        logger.info(
            f"Referential integrity validation complete - "
            f"Errors: {len(result.errors)}, Warnings: {len(result.warnings)}"
        )

        return result

    def _extract_ids(self, records: List[Dict[str, Any]], id_field: str) -> Set[str]:
        """Extract IDs from a list of records."""
        ids = set()
        for record in records:
            if id_field in record and "S" in record[id_field]:
                ids.add(record[id_field]["S"])
        return ids

    def _validate_job_references(
        self, job_records: List[Dict[str, Any]], content_ids: Set[str], partner_ids: Set[str]
    ) -> ValidationResult:
        """Validate that job records reference existing content and partners."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        for job_record in job_records:
            job_id = job_record.get("job_id", {}).get("S", "unknown")

            # Check content_id reference
            if "content_id" in job_record:
                content_id = job_record["content_id"].get("S")
                if content_id and content_id not in content_ids:
                    result.add_error(
                        "content_id", f"Job references non-existent content: {content_id}", content_id, job_id
                    )

            # Check partner_id reference
            if "partner_id" in job_record:
                partner_id = job_record["partner_id"].get("S")
                if partner_id and partner_id not in partner_ids:
                    result.add_error(
                        "partner_id", f"Job references non-existent partner: {partner_id}", partner_id, job_id
                    )

        return result

    def _validate_access_log_references(
        self, access_log_records: List[Dict[str, Any]], content_ids: Set[str], partner_ids: Set[str]
    ) -> ValidationResult:
        """Validate that access log records reference existing partners and content."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        for log_record in access_log_records:
            log_id = log_record.get("log_id", {}).get("S", "unknown")

            # Check partner_id reference (required)
            if "partner_id" in log_record:
                partner_id = log_record["partner_id"].get("S")
                if partner_id and partner_id not in partner_ids:
                    result.add_error(
                        "partner_id", f"Access log references non-existent partner: {partner_id}", partner_id, log_id
                    )

            # Check content_id reference (optional)
            if "content_id" in log_record:
                content_id = log_record["content_id"].get("S")
                if content_id and content_id not in content_ids:
                    result.add_error(
                        "content_id", f"Access log references non-existent content: {content_id}", content_id, log_id
                    )

        return result

    def _check_for_orphaned_records(
        self,
        content_ids: Set[str],
        partner_ids: Set[str],
        job_records: List[Dict[str, Any]],
        access_log_records: List[Dict[str, Any]],
        analytics_records: List[Dict[str, Any]] = None,
        bot_rules_records: List[Dict[str, Any]] = None,
        pricing_rules_records: List[Dict[str, Any]] = None,
        pricing_transactions_records: List[Dict[str, Any]] = None,
    ) -> ValidationResult:
        """Check for records that are not referenced by any other records."""
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        # Find content IDs referenced by jobs and access logs
        referenced_content_ids = set()
        for job_record in job_records:
            if "content_id" in job_record:
                content_id = job_record["content_id"].get("S")
                if content_id:
                    referenced_content_ids.add(content_id)

        for log_record in access_log_records:
            if "content_id" in log_record:
                content_id = log_record["content_id"].get("S")
                if content_id:
                    referenced_content_ids.add(content_id)

        # Find partner IDs referenced by jobs and access logs
        referenced_partner_ids = set()
        for job_record in job_records:
            if "partner_id" in job_record:
                partner_id = job_record["partner_id"].get("S")
                if partner_id:
                    referenced_partner_ids.add(partner_id)

        for log_record in access_log_records:
            if "partner_id" in log_record:
                partner_id = log_record["partner_id"].get("S")
                if partner_id:
                    referenced_partner_ids.add(partner_id)

        # Include analytics references if provided
        if analytics_records:
            for analytics_record in analytics_records:
                if "metadata" in analytics_record and "M" in analytics_record["metadata"]:
                    metadata = analytics_record["metadata"]["M"]

                    # Check content references
                    if "content_id" in metadata and "S" in metadata["content_id"]:
                        content_id = metadata["content_id"]["S"]
                        referenced_content_ids.add(content_id)

                    if "content_accessed" in metadata and "SS" in metadata["content_accessed"]:
                        for content_id in metadata["content_accessed"]["SS"]:
                            referenced_content_ids.add(content_id)

                    # Check partner references
                    if "partner_id" in metadata and "S" in metadata["partner_id"]:
                        partner_id = metadata["partner_id"]["S"]
                        referenced_partner_ids.add(partner_id)

        # Include pricing transactions references if provided
        if pricing_transactions_records:
            for transaction_record in pricing_transactions_records:
                # Check content references
                if "content_id" in transaction_record and "S" in transaction_record["content_id"]:
                    content_id = transaction_record["content_id"]["S"]
                    referenced_content_ids.add(content_id)

                # Check partner references
                if "partner_id" in transaction_record and "S" in transaction_record["partner_id"]:
                    partner_id = transaction_record["partner_id"]["S"]
                    referenced_partner_ids.add(partner_id)

        # Check for orphaned content
        orphaned_content = content_ids - referenced_content_ids
        if orphaned_content:
            result.add_warning(
                "referential_integrity",
                f"Found {len(orphaned_content)} content records not referenced by any jobs or logs",
                list(orphaned_content),
            )

        # Check for orphaned partners
        orphaned_partners = partner_ids - referenced_partner_ids
        if orphaned_partners:
            result.add_warning(
                "referential_integrity",
                f"Found {len(orphaned_partners)} partner records not referenced by any jobs or logs",
                list(orphaned_partners),
            )

        # Note: Bot rules are self-contained and don't reference other tables,
        # so they don't contribute to orphaned record analysis

        return result

    def _validate_analytics_references(
        self, analytics_records: List[Dict[str, Any]], content_ids: Set[str], partner_ids: Set[str]
    ) -> ValidationResult:
        """
        Validate that analytics records reference existing content and partners.

        Args:
            analytics_records: List of analytics records to validate
            content_ids: Set of valid content IDs
            partner_ids: Set of valid partner IDs

        Returns:
            ValidationResult with analytics referential integrity errors
        """
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        for record in analytics_records:
            record_id = record.get("metric_type_timestamp", {}).get("S", "unknown")

            # Check metadata for content and partner references
            if "metadata" in record and "M" in record["metadata"]:
                metadata = record["metadata"]["M"]

                # Validate content_id references
                if "content_id" in metadata and "S" in metadata["content_id"]:
                    content_id = metadata["content_id"]["S"]
                    if content_id not in content_ids:
                        result.add_error(
                            "content_id",
                            f"Analytics record references non-existent content ID: {content_id}",
                            content_id,
                            record_id,
                        )

                # Validate partner_id references
                if "partner_id" in metadata and "S" in metadata["partner_id"]:
                    partner_id = metadata["partner_id"]["S"]
                    if partner_id not in partner_ids:
                        result.add_error(
                            "partner_id",
                            f"Analytics record references non-existent partner ID: {partner_id}",
                            partner_id,
                            record_id,
                        )

                # Validate content_accessed references (for bot access metrics)
                if "content_accessed" in metadata and "SS" in metadata["content_accessed"]:
                    content_accessed = metadata["content_accessed"]["SS"]
                    for content_id in content_accessed:
                        if content_id not in content_ids:
                            result.add_error(
                                "content_accessed",
                                f"Analytics record references non-existent content ID in content_accessed: {content_id}",
                                content_id,
                                record_id,
                            )

        return result

    def _validate_bot_rules_references(self, bot_rules_records: List[Dict[str, Any]]) -> ValidationResult:
        """
        Validate bot rules records for internal consistency.

        Bot rules are self-contained and don't reference other tables,
        but we validate internal consistency like priority uniqueness.

        Args:
            bot_rules_records: List of bot rules records to validate

        Returns:
            ValidationResult with bot rules validation errors
        """
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        # Track priorities to check for duplicates
        priorities = {}
        rule_names = set()

        for record in bot_rules_records:
            rule_id = record.get("rule_id", {}).get("S", "unknown")

            # Check for duplicate rule names
            if "rule_name" in record and "S" in record["rule_name"]:
                rule_name = record["rule_name"]["S"]
                if rule_name in rule_names:
                    result.add_warning("rule_name", f"Duplicate rule name found: {rule_name}", rule_name, rule_id)
                else:
                    rule_names.add(rule_name)

            # Check for duplicate priorities (should be unique for active rules)
            if "priority" in record and "N" in record["priority"]:
                priority = int(record["priority"]["N"])
                status = record.get("status", {}).get("S", "active")

                if status == "active":
                    if priority in priorities:
                        result.add_error(
                            "priority",
                            f"Duplicate priority {priority} found for active rules. "
                            f"Conflicts with rule: {priorities[priority]}",
                            priority,
                            rule_id,
                        )
                    else:
                        priorities[priority] = rule_id

            # Validate WAF rule ID format if present
            if "waf_rule_id" in record and "S" in record["waf_rule_id"]:
                waf_rule_id = record["waf_rule_id"]["S"]
                # WAF rule IDs should be UUIDs or AWS resource identifiers
                if not (len(waf_rule_id) >= 8 and ("-" in waf_rule_id or waf_rule_id.isalnum())):
                    result.add_warning(
                        "waf_rule_id", f"WAF rule ID format may be invalid: {waf_rule_id}", waf_rule_id, rule_id
                    )

            # Validate sync status consistency
            if "sync_status" in record and "S" in record["sync_status"]:
                sync_status = record["sync_status"]["S"]
                has_waf_rule_id = "waf_rule_id" in record and record["waf_rule_id"].get("S")
                has_last_synced = "last_synced" in record and record["last_synced"].get("S")

                if sync_status == "synced" and not has_waf_rule_id:
                    result.add_error(
                        "sync_status", "Rule marked as synced but missing WAF rule ID", sync_status, rule_id
                    )

                if sync_status == "synced" and not has_last_synced:
                    result.add_warning(
                        "sync_status", "Rule marked as synced but missing last_synced timestamp", sync_status, rule_id
                    )

        return result

    def _validate_pricing_rules_references(self, pricing_rules_records: List[Dict[str, Any]]) -> ValidationResult:
        """
        Validate pricing rules records for internal consistency.

        Args:
            pricing_rules_records: List of pricing rules records to validate

        Returns:
            ValidationResult with pricing rules validation errors
        """
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        # Track rule sets and versions
        rule_sets = {}
        active_rules = {}

        for record in pricing_rules_records:
            rule_set_id = record.get("rule_set_id", {}).get("S", "unknown")
            version_timestamp = record.get("version_timestamp", {}).get("S", "unknown")
            status = record.get("status", {}).get("S", "unknown")

            # Track rule set versions
            if rule_set_id not in rule_sets:
                rule_sets[rule_set_id] = []
            rule_sets[rule_set_id].append({"version": version_timestamp, "status": status, "record": record})

            # Check for multiple active rules per rule set
            if status == "active":
                if rule_set_id in active_rules:
                    result.add_error(
                        "status",
                        f"Multiple active pricing rules found for rule set {rule_set_id}. "
                        f"Conflicts with version: {active_rules[rule_set_id]}",
                        status,
                        f"{rule_set_id}#{version_timestamp}",
                    )
                else:
                    active_rules[rule_set_id] = version_timestamp

            # Validate effective date format and logic
            if "effective_date" in record and "S" in record["effective_date"]:
                effective_date = record["effective_date"]["S"]
                try:
                    from datetime import datetime

                    datetime.strptime(effective_date, "%Y-%m-%d")
                except ValueError:
                    result.add_error(
                        "effective_date",
                        f"Invalid effective date format: {effective_date}",
                        effective_date,
                        f"{rule_set_id}#{version_timestamp}",
                    )

            # Validate pricing rules structure
            if "pricing_rules" in record and "M" in record["pricing_rules"]:
                pricing_rules = record["pricing_rules"]["M"]

                # Check for required pricing categories
                if "per_access" not in pricing_rules:
                    result.add_warning(
                        "pricing_rules", "Missing per_access pricing rules", None, f"{rule_set_id}#{version_timestamp}"
                    )

                # Validate pricing values are numeric
                for category, rules in pricing_rules.items():
                    if "M" in rules:
                        for content_type, price in rules["M"].items():
                            if "S" in price:
                                try:
                                    float(price["S"])
                                except ValueError:
                                    result.add_error(
                                        "pricing_rules",
                                        f"Invalid price value for {category}.{content_type}: {price['S']}",
                                        price["S"],
                                        f"{rule_set_id}#{version_timestamp}",
                                    )

        return result

    def _validate_pricing_transactions_references(
        self, pricing_transactions_records: List[Dict[str, Any]], content_ids: Set[str], partner_ids: Set[str]
    ) -> ValidationResult:
        """
        Validate pricing transactions records reference existing content and partners.

        Args:
            pricing_transactions_records: List of pricing transactions records to validate
            content_ids: Set of valid content IDs
            partner_ids: Set of valid partner IDs

        Returns:
            ValidationResult with pricing transactions validation errors
        """
        result = ValidationResult(is_valid=True, errors=[], warnings=[])

        for record in pricing_transactions_records:
            transaction_id = record.get("transaction_id", {}).get("S", "unknown")

            # Check content_id reference
            if "content_id" in record and "S" in record["content_id"]:
                content_id = record["content_id"]["S"]
                if content_id not in content_ids:
                    result.add_error(
                        "content_id",
                        f"Pricing transaction references non-existent content: {content_id}",
                        content_id,
                        transaction_id,
                    )

            # Check partner_id reference
            if "partner_id" in record and "S" in record["partner_id"]:
                partner_id = record["partner_id"]["S"]
                if partner_id not in partner_ids:
                    result.add_error(
                        "partner_id",
                        f"Pricing transaction references non-existent partner: {partner_id}",
                        partner_id,
                        transaction_id,
                    )

            # Validate cost calculation structure
            if "cost_calculation" in record and "M" in record["cost_calculation"]:
                cost_calc = record["cost_calculation"]["M"]

                required_fields = ["base_cost", "multiplier", "final_cost", "currency"]
                for field in required_fields:
                    if field not in cost_calc:
                        result.add_error(
                            "cost_calculation",
                            f"Missing required cost calculation field: {field}",
                            None,
                            transaction_id,
                        )

                # Validate numeric fields
                numeric_fields = ["base_cost", "multiplier", "final_cost"]
                for field in numeric_fields:
                    if field in cost_calc and "N" in cost_calc[field]:
                        try:
                            float(cost_calc[field]["N"])
                        except ValueError:
                            result.add_error(
                                "cost_calculation",
                                f"Invalid numeric value for {field}: {cost_calc[field]['N']}",
                                cost_calc[field]["N"],
                                transaction_id,
                            )

            # Validate transaction date consistency
            if (
                "transaction_date" in record
                and "S" in record["transaction_date"]
                and "transaction_timestamp" in record
                and "S" in record["transaction_timestamp"]
            ):
                transaction_date = record["transaction_date"]["S"]
                transaction_timestamp = record["transaction_timestamp"]["S"]

                try:
                    from datetime import datetime

                    parsed_timestamp = datetime.fromisoformat(transaction_timestamp.replace("Z", "+00:00"))
                    expected_date = parsed_timestamp.strftime("%Y-%m-%d")

                    if transaction_date != expected_date:
                        result.add_error(
                            "transaction_date",
                            f"Transaction date {transaction_date} does not match timestamp date {expected_date}",
                            transaction_date,
                            transaction_id,
                        )
                except ValueError:
                    result.add_error(
                        "transaction_timestamp",
                        f"Invalid transaction timestamp format: {transaction_timestamp}",
                        transaction_timestamp,
                        transaction_id,
                    )

        return result


class ValidationReporter:
    """Generates validation error reports."""

    def __init__(self):
        """Initialize the validation reporter."""
        pass

    def generate_report(self, validation_results: Dict[str, ValidationResult]) -> str:
        """
        Generate a comprehensive validation report.

        Args:
            validation_results: Dictionary mapping table names to validation results

        Returns:
            Formatted validation report string
        """
        report_lines = []
        report_lines.append("=" * 80)
        report_lines.append("MOCK DATA VALIDATION REPORT")
        report_lines.append("=" * 80)
        report_lines.append("")

        total_errors = 0
        total_warnings = 0

        # Summary section
        for table_name, result in validation_results.items():
            total_errors += len(result.errors)
            total_warnings += len(result.warnings)

            status = "✓ PASS" if result.is_valid else "✗ FAIL"
            report_lines.append(f"{table_name.upper()} TABLE: {status}")
            report_lines.append(f"  Errors: {len(result.errors)}")
            report_lines.append(f"  Warnings: {len(result.warnings)}")
            report_lines.append("")

        report_lines.append(f"TOTAL ERRORS: {total_errors}")
        report_lines.append(f"TOTAL WARNINGS: {total_warnings}")
        report_lines.append("")

        # Detailed error section
        if total_errors > 0:
            report_lines.append("DETAILED ERRORS:")
            report_lines.append("-" * 40)

            for table_name, result in validation_results.items():
                if result.errors:
                    report_lines.append(f"\n{table_name.upper()} TABLE ERRORS:")
                    for error in result.errors:
                        error_line = f"  • {error.field}: {error.message}"
                        if error.record_id:
                            error_line += f" (Record: {error.record_id})"
                        if error.value is not None:
                            error_line += f" (Value: {error.value})"
                        report_lines.append(error_line)

        # Detailed warning section
        if total_warnings > 0:
            report_lines.append("\nDETAILED WARNINGS:")
            report_lines.append("-" * 40)

            for table_name, result in validation_results.items():
                if result.warnings:
                    report_lines.append(f"\n{table_name.upper()} TABLE WARNINGS:")
                    for warning in result.warnings:
                        warning_line = f"  • {warning.field}: {warning.message}"
                        if warning.record_id:
                            warning_line += f" (Record: {warning.record_id})"
                        if warning.value is not None and len(str(warning.value)) < 100:
                            warning_line += f" (Value: {warning.value})"
                        report_lines.append(warning_line)

        report_lines.append("")
        report_lines.append("=" * 80)

        return "\n".join(report_lines)

    def log_validation_summary(self, validation_results: Dict[str, ValidationResult]):
        """Log a summary of validation results."""
        total_errors = sum(len(result.errors) for result in validation_results.values())
        total_warnings = sum(len(result.warnings) for result in validation_results.values())

        if total_errors == 0:
            logger.info(f"✓ All validation checks passed! ({total_warnings} warnings)")
        else:
            logger.error(f"✗ Validation failed with {total_errors} errors and {total_warnings} warnings")

        for table_name, result in validation_results.items():
            if result.errors:
                logger.error(f"{table_name}: {len(result.errors)} errors, {len(result.warnings)} warnings")
            elif result.warnings:
                logger.warning(f"{table_name}: {len(result.warnings)} warnings")
            else:
                logger.info(f"{table_name}: ✓ passed validation")
