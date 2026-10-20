"""
IAB Access Rules Engine Lambda Function

This Lambda function handles:
1. Enforcing access policies based on configurable rules
2. Geographic restriction functionality
3. Content-specific access control
4. Partner-specific access permissions
5. Time-based access restrictions

Requirements covered:
- 2.5: Provide mechanism to update access rules without code changes
- 2.6: Enforce geographic restrictions based on request origin

MIGRATION NOTE: This file has been updated to use the new shared modules architecture.
Response helpers and utilities are now available in shared/ for reuse across Lambda functions.
"""

import ipaddress
import json
import logging
import os
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Tuple

import boto3
import requests

# Import shared utilities
from shared.utils.response_helpers import create_error_response, create_success_response
from shared.utils.safe_datetime_parser import safe_parse_datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

# Environment variables
PARTNERS_TABLE_NAME = os.environ.get("PARTNERS_TABLE_NAME", "cm-partners")
CONTENT_TABLE_NAME = os.environ.get("CONTENT_TABLE_NAME", "cm-content")
ACCESS_RULES_BUCKET = os.environ.get("ACCESS_RULES_BUCKET", "iab-access-rules")

# Geographic IP service (using a free service for demo)
GEO_IP_SERVICE_URL = "http://ip-api.com/json/"

# Cache for geographic lookups
_geo_cache = {}
_geo_cache_ttl = 3600  # 1 hour


def get_country_from_ip(ip_address: str) -> Optional[str]:
    """
    Get country code from IP address using geolocation service

    Args:
        ip_address: IP address to lookup

    Returns:
        Country code (ISO 3166-1 alpha-2) or None if lookup fails
    """
    if not ip_address or ip_address == "127.0.0.1":
        return None

    # Check cache first
    if ip_address in _geo_cache:
        cache_entry = _geo_cache[ip_address]
        if (datetime.utcnow().timestamp() - cache_entry["timestamp"]) < _geo_cache_ttl:
            return cache_entry["country"]

    try:
        # Make request to geolocation service
        response = requests.get(f"{GEO_IP_SERVICE_URL}{ip_address}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                country_code = data.get("countryCode")

                # Cache the result
                _geo_cache[ip_address] = {"country": country_code, "timestamp": datetime.utcnow().timestamp()}

                return country_code

    except Exception as e:
        logger.warning(f"Failed to get country for IP {ip_address}: {str(e)}")

    return None


def is_ip_in_range(ip_address: str, ip_ranges: List[str]) -> bool:
    """
    Check if IP address is in any of the specified ranges

    Args:
        ip_address: IP address to check
        ip_ranges: List of IP ranges in CIDR notation

    Returns:
        True if IP is in any range, False otherwise
    """
    try:
        ip = ipaddress.ip_address(ip_address)

        for ip_range in ip_ranges:
            try:
                network = ipaddress.ip_network(ip_range, strict=False)
                if ip in network:
                    return True
            except ValueError:
                logger.warning(f"Invalid IP range format: {ip_range}")
                continue

        return False

    except ValueError:
        logger.warning(f"Invalid IP address format: {ip_address}")
        return False


def is_time_in_range(current_time: time, start_time: time, end_time: time) -> bool:
    """
    Check if current time is within allowed time range

    Args:
        current_time: Current time
        start_time: Start of allowed time range
        end_time: End of allowed time range

    Returns:
        True if current time is in range, False otherwise
    """
    if start_time <= end_time:
        # Same day range
        return start_time <= current_time <= end_time
    else:
        # Overnight range (e.g., 22:00 to 06:00)
        return current_time >= start_time or current_time <= end_time


def get_partner_access_rules(partner_id: str) -> Dict[str, Any]:
    """
    Get access rules for a specific partner

    Args:
        partner_id: Partner identifier

    Returns:
        Dictionary containing partner access rules
    """
    logger.info("=== GET_PARTNER_ACCESS_RULES START ===")
    logger.info(f"Partner ID: {partner_id}")

    try:
        partners_table = dynamodb.Table(PARTNERS_TABLE_NAME)

        # Partners are keyed on their identity: PARTNER#<id> / META.
        response = partners_table.get_item(Key={"PK": f"PARTNER#{partner_id}", "SK": "META"})

        partner = response.get("Item")
        if not partner:
            return {}

        # Extract access rules from partner record
        access_rules = {
            "allowed_endpoints": partner.get("allowed_endpoints", []),
            "geographic_restrictions": partner.get("geographic_restrictions", []),
            "ip_whitelist": partner.get("ip_whitelist", []),
            "ip_blacklist": partner.get("ip_blacklist", []),
            "time_restrictions": partner.get("time_restrictions", {}),
            "rate_limits": partner.get("rate_limits", {}),
            "content_restrictions": partner.get("content_restrictions", []),
            "status": partner.get("status", "inactive"),
        }

        logger.info(
            f"=== GET_PARTNER_ACCESS_RULES END === Retrieved rules for {partner_id}, status: {access_rules.get('status', 'unknown')}"
        )
        return access_rules

    except Exception as e:
        logger.error(f"=== GET_PARTNER_ACCESS_RULES ERROR === {str(e)}")
        return {}


def get_content_access_rules(content_id: str) -> Dict[str, Any]:
    """
    Get access rules for specific content

    Args:
        content_id: Content identifier

    Returns:
        Dictionary containing content access rules
    """
    try:
        content_table = dynamodb.Table(CONTENT_TABLE_NAME)

        response = content_table.get_item(Key={"content_id": content_id})

        if "Item" not in response:
            return {}

        content = response["Item"]

        # Extract access rules from content metadata
        access_rules = {
            "geographic_restrictions": content.get("geographic_restrictions", []),
            "partner_restrictions": content.get("partner_restrictions", []),
            "access_level": content.get("access_level", "public"),
            "embargo_until": content.get("embargo_until"),
            "expires_at": content.get("expires_at"),
            "pricing_tier_required": content.get("pricing_tier_required"),
            "status": content.get("status", "published"),
        }

        return access_rules

    except Exception as e:
        logger.error(f"Error getting content access rules: {str(e)}")
        return {}


def check_geographic_restrictions(
    ip_address: str, allowed_countries: List[str], blocked_countries: List[str]
) -> Tuple[bool, str]:
    """
    Check if access is allowed based on geographic restrictions

    Args:
        ip_address: Client IP address
        allowed_countries: List of allowed country codes
        blocked_countries: List of blocked country codes

    Returns:
        Tuple of (is_allowed, reason)
    """
    if not allowed_countries and not blocked_countries:
        return True, "No geographic restrictions"

    country = get_country_from_ip(ip_address)

    if not country:
        # If we can't determine country, apply default policy
        if allowed_countries:
            return False, "Unable to determine country, access restricted"
        else:
            return True, "Unable to determine country, no restrictions"

    # Check blocked countries first
    if blocked_countries and country in blocked_countries:
        return False, f"Access blocked from country: {country}"

    # Check allowed countries
    if allowed_countries and country not in allowed_countries:
        return False, f"Access not allowed from country: {country}"

    return True, f"Access allowed from country: {country}"


def check_time_restrictions(time_restrictions: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Check if access is allowed based on time restrictions

    Args:
        time_restrictions: Dictionary containing time restriction rules

    Returns:
        Tuple of (is_allowed, reason)
    """
    if not time_restrictions:
        return True, "No time restrictions"

    current_time = datetime.utcnow().time()
    current_day = datetime.utcnow().strftime("%A").lower()

    # Check daily restrictions
    daily_restrictions = time_restrictions.get("daily", {})
    if daily_restrictions:
        start_time_str = daily_restrictions.get("start_time")
        end_time_str = daily_restrictions.get("end_time")

        if start_time_str and end_time_str:
            try:
                start_time = datetime.strptime(start_time_str, "%H:%M").time()
                end_time = datetime.strptime(end_time_str, "%H:%M").time()

                if not is_time_in_range(current_time, start_time, end_time):
                    return False, f"Access not allowed at current time: {current_time.strftime('%H:%M')}"
            except ValueError:
                logger.warning(f"Invalid time format in daily restrictions: {start_time_str}, {end_time_str}")

    # Check day-specific restrictions
    day_restrictions = time_restrictions.get(current_day, {})
    if day_restrictions:
        if not day_restrictions.get("allowed", True):
            return False, f"Access not allowed on {current_day}"

        start_time_str = day_restrictions.get("start_time")
        end_time_str = day_restrictions.get("end_time")

        if start_time_str and end_time_str:
            try:
                start_time = datetime.strptime(start_time_str, "%H:%M").time()
                end_time = datetime.strptime(end_time_str, "%H:%M").time()

                if not is_time_in_range(current_time, start_time, end_time):
                    return (
                        False,
                        f"Access not allowed at current time on {current_day}: {current_time.strftime('%H:%M')}",
                    )
            except ValueError:
                logger.warning(f"Invalid time format in {current_day} restrictions: {start_time_str}, {end_time_str}")

    return True, "Time restrictions passed"


def check_ip_restrictions(ip_address: str, ip_whitelist: List[str], ip_blacklist: List[str]) -> Tuple[bool, str]:
    """
    Check if access is allowed based on IP restrictions

    Args:
        ip_address: Client IP address
        ip_whitelist: List of allowed IP ranges
        ip_blacklist: List of blocked IP ranges

    Returns:
        Tuple of (is_allowed, reason)
    """
    # Check blacklist first
    if ip_blacklist and is_ip_in_range(ip_address, ip_blacklist):
        return False, f"IP address {ip_address} is blacklisted"

    # Check whitelist
    if ip_whitelist:
        if not is_ip_in_range(ip_address, ip_whitelist):
            return False, f"IP address {ip_address} is not whitelisted"

    return True, "IP restrictions passed"


def check_content_embargo(content_rules: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Check if content is under embargo or expired

    Args:
        content_rules: Content access rules

    Returns:
        Tuple of (is_allowed, reason)
    """
    current_time = datetime.utcnow()

    # Check embargo
    embargo_until = content_rules.get("embargo_until")
    if embargo_until:
        try:
            embargo_time = safe_parse_datetime(embargo_until)
            if current_time < embargo_time:
                return False, f"Content under embargo until {embargo_until}"
        except ValueError:
            logger.warning(f"Invalid embargo date format: {embargo_until}")

    # Check expiration
    expires_at = content_rules.get("expires_at")
    if expires_at:
        try:
            expiry_time = safe_parse_datetime(expires_at)
            if current_time > expiry_time:
                return False, f"Content expired at {expires_at}"
        except ValueError:
            logger.warning(f"Invalid expiry date format: {expires_at}")

    return True, "Content timing restrictions passed"


def evaluate_access_request(
    partner_id: str, content_id: Optional[str], endpoint: str, ip_address: str, user_agent: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluate an access request against all applicable rules

    Args:
        partner_id: Partner identifier
        content_id: Content identifier (optional)
        endpoint: API endpoint being accessed
        ip_address: Client IP address
        user_agent: User agent string (optional)

    Returns:
        Dictionary with access decision and details
    """
    logger.info("=== EVALUATE_ACCESS_REQUEST START ===")
    logger.info(f"Partner: {partner_id}, Content: {content_id}, Endpoint: {endpoint}, IP: {ip_address}")

    result = {
        "allowed": False,
        "partner_id": partner_id,
        "content_id": content_id,
        "endpoint": endpoint,
        "ip_address": ip_address,
        "checks": [],
        "reason": "",
        "timestamp": datetime.utcnow().isoformat(),
    }

    try:
        # Get partner access rules
        partner_rules = get_partner_access_rules(partner_id)

        if not partner_rules:
            result["reason"] = "Partner not found"
            result["checks"].append({"check": "partner_exists", "passed": False, "reason": "Partner not found"})
            return result

        # Check partner status
        if partner_rules.get("status") != "active":
            result["reason"] = "Partner account inactive"
            result["checks"].append({"check": "partner_status", "passed": False, "reason": "Partner account inactive"})
            return result

        result["checks"].append({"check": "partner_status", "passed": True, "reason": "Partner is active"})

        # Check endpoint access
        allowed_endpoints = partner_rules.get("allowed_endpoints", [])
        if allowed_endpoints and endpoint not in allowed_endpoints:
            result["reason"] = f"Endpoint {endpoint} not allowed for partner"
            result["checks"].append({"check": "endpoint_access", "passed": False, "reason": result["reason"]})
            return result

        result["checks"].append({"check": "endpoint_access", "passed": True, "reason": "Endpoint access allowed"})

        # Check IP restrictions
        ip_allowed, ip_reason = check_ip_restrictions(
            ip_address, partner_rules.get("ip_whitelist", []), partner_rules.get("ip_blacklist", [])
        )

        result["checks"].append({"check": "ip_restrictions", "passed": ip_allowed, "reason": ip_reason})

        if not ip_allowed:
            result["reason"] = ip_reason
            return result

        # Check geographic restrictions
        geo_allowed, geo_reason = check_geographic_restrictions(
            ip_address,
            partner_rules.get("geographic_restrictions", []),
            [],  # No blocked countries at partner level for now
        )

        result["checks"].append({"check": "geographic_restrictions", "passed": geo_allowed, "reason": geo_reason})

        if not geo_allowed:
            result["reason"] = geo_reason
            return result

        # Check time restrictions
        time_allowed, time_reason = check_time_restrictions(partner_rules.get("time_restrictions", {}))

        result["checks"].append({"check": "time_restrictions", "passed": time_allowed, "reason": time_reason})

        if not time_allowed:
            result["reason"] = time_reason
            return result

        # Check content-specific rules if content_id is provided
        if content_id:
            content_rules = get_content_access_rules(content_id)

            if content_rules:
                # Check content status
                if content_rules.get("status") != "published":
                    result["reason"] = "Content not published"
                    result["checks"].append(
                        {"check": "content_status", "passed": False, "reason": "Content not published"}
                    )
                    return result

                result["checks"].append({"check": "content_status", "passed": True, "reason": "Content is published"})

                # Check content embargo/expiration
                embargo_allowed, embargo_reason = check_content_embargo(content_rules)
                result["checks"].append(
                    {"check": "content_embargo", "passed": embargo_allowed, "reason": embargo_reason}
                )

                if not embargo_allowed:
                    result["reason"] = embargo_reason
                    return result

                # Check content geographic restrictions
                content_geo_restrictions = content_rules.get("geographic_restrictions", [])
                if content_geo_restrictions:
                    content_geo_allowed, content_geo_reason = check_geographic_restrictions(
                        ip_address, content_geo_restrictions, []
                    )
                    result["checks"].append(
                        {"check": "content_geographic", "passed": content_geo_allowed, "reason": content_geo_reason}
                    )

                    if not content_geo_allowed:
                        result["reason"] = content_geo_reason
                        return result

                # Check partner restrictions for content
                partner_restrictions = content_rules.get("partner_restrictions", [])
                if partner_restrictions and partner_id not in partner_restrictions:
                    result["reason"] = "Partner not authorized for this content"
                    result["checks"].append(
                        {"check": "content_partner_access", "passed": False, "reason": result["reason"]}
                    )
                    return result

                result["checks"].append(
                    {"check": "content_partner_access", "passed": True, "reason": "Partner authorized for content"}
                )

        # All checks passed
        result["allowed"] = True
        result["reason"] = "Access granted - all checks passed"

        logger.info(f"=== EVALUATE_ACCESS_REQUEST END === Access granted for {partner_id}")
        return result

    except Exception as e:
        logger.error(f"=== EVALUATE_ACCESS_REQUEST ERROR === {str(e)}")
        result["reason"] = f"Internal error: {str(e)}"
        result["checks"].append({"check": "system_error", "passed": False, "reason": str(e)})
        return result


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for access rules engine operations

    Supported operations:
    - evaluate_access: Evaluate an access request
    - get_partner_rules: Get access rules for a partner
    - get_content_rules: Get access rules for content
    """
    request_id = getattr(context, "aws_request_id", "unknown")
    logger.info(f"=== ACCESS_RULES_ENGINE_LAMBDA START === Request ID: {request_id}")
    logger.info(f"Event: {json.dumps(event, default=str)}")

    try:
        # Parse request
        operation = event.get("operation")

        if not operation:
            return create_error_response(
                400,
                "Missing operation parameter",
                details={"supported_operations": ["evaluate_access", "get_partner_rules", "get_content_rules"]},
            )

        if operation == "evaluate_access":
            return handle_evaluate_access(event)
        elif operation == "get_partner_rules":
            return handle_get_partner_rules(event)
        elif operation == "get_content_rules":
            return handle_get_content_rules(event)
        else:
            return create_error_response(
                400,
                f"Unsupported operation: {operation}",
                details={"supported_operations": ["evaluate_access", "get_partner_rules", "get_content_rules"]},
            )

    except Exception as e:
        logger.error(f"=== ACCESS_RULES_ENGINE_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}")
        return create_error_response(500, "Internal server error", str(e))


def handle_evaluate_access(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle access evaluation request"""
    required_fields = ["partner_id", "endpoint", "ip_address"]

    for field in required_fields:
        if field not in event:
            return create_error_response(400, f"Missing required field: {field}")

    try:
        result = evaluate_access_request(
            partner_id=event["partner_id"],
            content_id=event.get("content_id"),
            endpoint=event["endpoint"],
            ip_address=event["ip_address"],
            user_agent=event.get("user_agent"),
        )

        status_code = 200 if result["allowed"] else 403

        if result["allowed"]:
            return create_success_response(result)
        else:
            return create_error_response(status_code, result["reason"], details=result)

    except Exception as e:
        return create_error_response(500, "Failed to evaluate access", str(e))


def handle_get_partner_rules(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle partner rules request"""
    partner_id = event.get("partner_id")

    if not partner_id:
        return create_error_response(400, "Missing partner_id parameter")

    try:
        rules = get_partner_access_rules(partner_id)

        return create_success_response({"partner_id": partner_id, "rules": rules})

    except Exception as e:
        return create_error_response(500, "Failed to get partner rules", str(e))


def handle_get_content_rules(event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle content rules request"""
    content_id = event.get("content_id")

    if not content_id:
        return create_error_response(400, "Missing content_id parameter")

    try:
        rules = get_content_access_rules(content_id)

        return create_success_response({"content_id": content_id, "rules": rules})

    except Exception as e:
        return create_error_response(500, "Failed to get content rules", str(e))
