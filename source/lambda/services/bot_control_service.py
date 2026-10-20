"""
Bot-control domain service (Service_Layer single home for the bot-control domain).

This module is the designated ``services/*`` home for the ui_backend bot-control domain
(Requirement 1.1 / 7.1): bot-rule CRUD, WAF-sync orchestration, WAF metrics retrieval, and
all ``iab-bot-rules-table`` DynamoDB access now live here as the single source of truth. The
business logic and data-store access were bodily extracted from the former top-level
bot-control API root module (now deleted); ``BotControlService.handle_request`` is the public
entry and no longer delegates back to a root handler module.

Imports are absolute, rooted at the Canonical_Import_Root ``source/lambda/`` (Requirement
15.1), declared at module top level with no ``try/except ImportError`` shim (Requirement 5.1
/ 5.2) and no ``sys.path`` manipulation (Requirement 15.2).
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError
from shared.utils.response_helpers import cors_headers
from shared.utils.safe_datetime_parser import safe_parse_datetime

# Import the WAF sync controller
from services.waf_sync_controller import BotRule, WAFSyncController

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
cloudwatch = boto3.client("cloudwatch")

# Environment variables
BOT_RULES_TABLE_NAME = os.environ.get("BOT_RULES_TABLE_NAME", "iab-bot-rules-table")
WAF_WEB_ACL_NAME = os.environ.get("WAF_WEB_ACL_NAME", "iab-bot-control-webacl")

# Initialize WAF sync controller
waf_sync_controller = WAFSyncController(bot_rules_table_name=BOT_RULES_TABLE_NAME, web_acl_name=WAF_WEB_ACL_NAME)

# DynamoDB table
bot_rules_table = dynamodb.Table(BOT_RULES_TABLE_NAME)


def clean_binary_data(obj):
    """
    Recursively clean binary data from objects for JSON serialization.

    Args:
        obj: Object to clean

    Returns:
        Object with binary data converted to strings
    """
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            # If UTF-8 decoding fails, use base64 encoding
            import base64

            return base64.b64encode(obj).decode("utf-8")
    elif isinstance(obj, dict):
        return {clean_binary_data(key): clean_binary_data(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [clean_binary_data(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(clean_binary_data(item) for item in obj)
    elif isinstance(obj, set):
        return {clean_binary_data(item) for item in obj}
    elif isinstance(obj, Decimal):
        return float(obj)
    elif hasattr(obj, "__dict__"):
        # Handle custom objects with attributes
        return {key: clean_binary_data(value) for key, value in obj.__dict__.items()}
    else:
        return obj


def _debug_data_types(obj, path="root"):
    """
    Debug helper to identify data types in nested structures.

    Args:
        obj: Object to analyze
        path: Current path in the object hierarchy

    Returns:
        String describing the data types found
    """
    if isinstance(obj, dict):
        types = []
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                types.append(_debug_data_types(value, f"{path}.{key}"))
            else:
                types.append(f"{path}.{key}: {type(value).__name__}")
        return "; ".join(types)
    elif isinstance(obj, list):
        if obj:
            return f"{path}[]: {type(obj[0]).__name__} (and {len(obj) - 1} more)"
        else:
            return f"{path}[]: empty list"
    else:
        return f"{path}: {type(obj).__name__}"


def _convert_all_to_strings(obj):
    """
    Fallback function to convert all non-serializable objects to strings.

    Args:
        obj: Object to convert

    Returns:
        Object with all values converted to JSON-serializable types
    """
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    elif isinstance(obj, dict):
        return {str(key): _convert_all_to_strings(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [_convert_all_to_strings(item) for item in obj]
    else:
        return str(obj)


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder for DynamoDB Decimal and Binary types"""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        elif isinstance(obj, bytes):
            # Convert bytes to string for JSON serialization
            return obj.decode("utf-8")
        return super(DecimalEncoder, self).default(obj)


def handle_get_bot_rules(headers: Dict[str, str], query_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle GET /bot-control/rules - Retrieve bot control rules.

    Args:
        headers: HTTP headers
        query_params: Query parameters for filtering

    Returns:
        dict: HTTP response with bot rules data
    """
    try:
        logger.info("=== HANDLE_GET_BOT_RULES START ===")
        logger.info(f"Query params: {query_params}")

        # Authentication handled by main lambda decorator

        # Extract filter parameters
        status_filter = query_params.get("status")
        rule_type_filter = query_params.get("rule_type")

        logger.info(f"Filtering rules: status={status_filter}, rule_type={rule_type_filter}")

        # Query DynamoDB for bot rules
        scan_kwargs = {}
        filter_expressions = []
        expression_values = {}

        if status_filter:
            filter_expressions.append("#status = :status")
            expression_values[":status"] = status_filter
            scan_kwargs["ExpressionAttributeNames"] = {"#status": "status"}

        if rule_type_filter:
            filter_expressions.append("contains(#action, :rule_type)")
            expression_values[":rule_type"] = rule_type_filter
            if "ExpressionAttributeNames" not in scan_kwargs:
                scan_kwargs["ExpressionAttributeNames"] = {}
            scan_kwargs["ExpressionAttributeNames"]["#action"] = "action"

        if filter_expressions:
            scan_kwargs["FilterExpression"] = " AND ".join(filter_expressions)
            scan_kwargs["ExpressionAttributeValues"] = expression_values

        response = bot_rules_table.scan(**scan_kwargs)
        rules = response.get("Items", [])

        # Clean binary data immediately after retrieving from DynamoDB
        rules = clean_binary_data(rules)

        # Convert to API format
        formatted_rules = []
        for rule in rules:
            formatted_rule = {
                "rule_id": rule["rule_id"],
                "rule_name": rule["rule_name"],
                "description": rule["description"],
                "action": rule["action"],
                "priority": int(rule["priority"]),
                "conditions": rule.get("conditions", {}),
                "status": rule["status"],
                "created_at": rule["created_at"],
                "updated_at": rule["updated_at"],
                "waf_rule_id": rule.get("waf_rule_id"),
                "last_synced": rule.get("last_synced"),
                "sync_status": rule.get("sync_status", "pending"),
            }
            formatted_rules.append(formatted_rule)

        # Sort by priority
        formatted_rules.sort(key=lambda x: x["priority"])

        logger.info(f"=== HANDLE_GET_BOT_RULES SUCCESS === Retrieved {len(formatted_rules)} bot rules")
        return create_success_response({"rules": formatted_rules, "total_count": len(formatted_rules)})

    except Exception as e:
        logger.error(f"=== HANDLE_GET_BOT_RULES ERROR === {str(e)}", exc_info=True)
        return create_error_response(500, "Failed to retrieve bot rules")


def handle_create_bot_rule(headers: Dict[str, str], request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle POST /bot-control/rules - Create a new bot control rule.

    Args:
        headers: HTTP headers
        request_data: Request body data

    Returns:
        dict: HTTP response with created rule data
    """
    try:
        logger.info("=== HANDLE_CREATE_BOT_RULE START ===")
        logger.info(f"Request data: {json.dumps(request_data, default=str)}")

        # Authentication handled by main lambda decorator

        # Validate required fields
        required_fields = ["rule_name", "description", "action", "priority", "conditions"]
        for field in required_fields:
            if field not in request_data:
                return create_error_response(400, f"Missing required field: {field}")

        # Validate action type
        valid_actions = ["block", "allow", "count", "rate_limit"]
        if request_data["action"] not in valid_actions:
            return create_error_response(400, f"Invalid action. Must be one of: {valid_actions}")

        # Generate rule ID
        rule_id = f"rule_{int(datetime.now().timestamp())}"
        current_time = datetime.now(timezone.utc).isoformat()

        # Create bot rule object
        bot_rule = BotRule(
            rule_id=rule_id,
            rule_name=request_data["rule_name"],
            description=request_data["description"],
            action=request_data["action"],
            priority=int(request_data["priority"]),
            conditions=request_data["conditions"],
            status=request_data.get("status", "active"),
            created_at=current_time,
            updated_at=current_time,
        )

        # Store in DynamoDB
        bot_rules_table.put_item(
            Item={
                "rule_id": bot_rule.rule_id,
                "rule_name": bot_rule.rule_name,
                "description": bot_rule.description,
                "action": bot_rule.action,
                "priority": bot_rule.priority,
                "conditions": bot_rule.conditions,
                "status": bot_rule.status,
                "created_at": bot_rule.created_at,
                "updated_at": bot_rule.updated_at,
                "sync_status": "pending",
            }
        )

        # Sync to WAF if rule is active
        if bot_rule.status == "active":
            sync_result = waf_sync_controller.create_waf_rule(bot_rule)
            if not sync_result.success:
                logger.warning(f"WAF sync failed for rule {rule_id}: {sync_result.error_message}")

        logger.info(f"Created bot rule: {rule_id}")
        return create_success_response(
            {
                "rule_id": bot_rule.rule_id,
                "rule_name": bot_rule.rule_name,
                "description": bot_rule.description,
                "action": bot_rule.action,
                "priority": bot_rule.priority,
                "conditions": bot_rule.conditions,
                "status": bot_rule.status,
                "created_at": bot_rule.created_at,
                "updated_at": bot_rule.updated_at,
                "sync_status": "pending",
            },
            status_code=201,
        )

        logger.info(f"=== HANDLE_CREATE_BOT_RULE SUCCESS === Created rule: {bot_rule.rule_id}")

    except Exception as e:
        logger.error(f"=== HANDLE_CREATE_BOT_RULE ERROR === {str(e)}", exc_info=True)
        return create_error_response(500, "Failed to create bot rule")


def handle_update_bot_rule(headers: Dict[str, str], rule_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle PUT /bot-control/rules/{rule_id} - Update an existing bot control rule.

    Args:
        headers: HTTP headers
        rule_id: Rule ID to update
        request_data: Request body data

    Returns:
        dict: HTTP response with updated rule data
    """
    logger.info("=== HANDLE_UPDATE_BOT_RULE START ===")
    logger.info(f"Rule ID: {rule_id}, Request data: {json.dumps(request_data, default=str)}")

    try:
        # Authentication handled by main lambda decorator

        # Get existing rule
        try:
            response = bot_rules_table.get_item(Key={"rule_id": rule_id})
            if "Item" not in response:
                return create_error_response(404, "Bot rule not found")

            # Clean binary data from retrieved item
            response["Item"] = clean_binary_data(response["Item"])

            existing_rule = response["Item"]
        except ClientError as e:
            logger.error(f"Error retrieving rule {rule_id}: {str(e)}")
            return create_error_response(500, "Failed to retrieve rule")

        # Update fields
        current_time = datetime.now(timezone.utc).isoformat()
        update_expression = "SET updated_at = :updated_at"
        expression_values = {":updated_at": current_time}
        expression_names = {}

        # Build update expression for provided fields
        updatable_fields = ["rule_name", "description", "action", "priority", "conditions", "status"]
        for field in updatable_fields:
            if field in request_data:
                # Handle reserved keywords
                if field == "status":
                    update_expression += ", #status = :status"
                    expression_names["#status"] = "status"
                    expression_values[":status"] = request_data[field]
                else:
                    update_expression += f", {field} = :{field}"
                    expression_values[f":{field}"] = request_data[field]

        # Validate action if provided
        if "action" in request_data:
            valid_actions = ["block", "allow", "count", "rate_limit"]
            if request_data["action"] not in valid_actions:
                return create_error_response(400, f"Invalid action. Must be one of: {valid_actions}")

        # Update in DynamoDB
        update_kwargs = {
            "Key": {"rule_id": rule_id},
            "UpdateExpression": update_expression,
            "ExpressionAttributeValues": expression_values,
        }

        if expression_names:
            update_kwargs["ExpressionAttributeNames"] = expression_names

        bot_rules_table.update_item(**update_kwargs)

        # Get updated rule for WAF sync
        response = bot_rules_table.get_item(Key={"rule_id": rule_id})
        updated_rule_data = clean_binary_data(response["Item"])

        # Create BotRule object for WAF sync
        updated_bot_rule = BotRule(
            rule_id=updated_rule_data["rule_id"],
            rule_name=updated_rule_data["rule_name"],
            description=updated_rule_data["description"],
            action=updated_rule_data["action"],
            priority=int(updated_rule_data["priority"]),
            conditions=updated_rule_data["conditions"],
            status=updated_rule_data["status"],
            created_at=updated_rule_data["created_at"],
            updated_at=updated_rule_data["updated_at"],
            waf_rule_id=updated_rule_data.get("waf_rule_id"),
            last_synced=updated_rule_data.get("last_synced"),
            sync_status=updated_rule_data.get("sync_status", "pending"),
        )

        # Sync to WAF
        if updated_bot_rule.status == "active":
            sync_result = waf_sync_controller.update_waf_rule(updated_bot_rule)
            if not sync_result.success:
                logger.warning(f"WAF sync failed for rule {rule_id}: {sync_result.error_message}")
        elif updated_bot_rule.status == "inactive" and existing_rule.get("status") == "active":
            # Rule was deactivated, remove from WAF
            sync_result = waf_sync_controller.delete_waf_rule(updated_bot_rule)
            if not sync_result.success:
                logger.warning(f"WAF rule deletion failed for rule {rule_id}: {sync_result.error_message}")

        logger.info(f"=== HANDLE_UPDATE_BOT_RULE END === Updated rule: {rule_id}")
        return create_success_response(
            {
                "rule_id": updated_bot_rule.rule_id,
                "rule_name": updated_bot_rule.rule_name,
                "description": updated_bot_rule.description,
                "action": updated_bot_rule.action,
                "priority": updated_bot_rule.priority,
                "conditions": updated_bot_rule.conditions,
                "status": updated_bot_rule.status,
                "created_at": updated_bot_rule.created_at,
                "updated_at": updated_bot_rule.updated_at,
                "waf_rule_id": updated_bot_rule.waf_rule_id,
                "last_synced": updated_bot_rule.last_synced,
                "sync_status": updated_bot_rule.sync_status,
            }
        )

    except Exception as e:
        logger.error(f"=== HANDLE_UPDATE_BOT_RULE ERROR === Rule ID: {rule_id}, Error: {str(e)}")
        return create_error_response(500, "Failed to update bot rule")


def handle_delete_bot_rule(headers: Dict[str, str], rule_id: str) -> Dict[str, Any]:
    """
    Handle DELETE /bot-control/rules/{rule_id} - Delete a bot control rule.

    Args:
        headers: HTTP headers
        rule_id: Rule ID to delete

    Returns:
        dict: HTTP response confirming deletion
    """
    logger.info("=== HANDLE_DELETE_BOT_RULE START ===")
    logger.info(f"Rule ID: {rule_id}")

    try:
        # Authentication handled by main lambda decorator

        # Get existing rule for WAF sync
        try:
            response = bot_rules_table.get_item(Key={"rule_id": rule_id})
            if "Item" not in response:
                return create_error_response(404, "Bot rule not found")

            # Clean binary data from retrieved item
            response["Item"] = clean_binary_data(response["Item"])

            rule_data = response["Item"]
        except ClientError as e:
            logger.error(f"Error retrieving rule {rule_id}: {str(e)}")
            return create_error_response(500, "Failed to retrieve rule")

        # Create BotRule object for WAF sync
        bot_rule = BotRule(
            rule_id=rule_data["rule_id"],
            rule_name=rule_data["rule_name"],
            description=rule_data["description"],
            action=rule_data["action"],
            priority=int(rule_data["priority"]),
            conditions=rule_data["conditions"],
            status=rule_data["status"],
            created_at=rule_data["created_at"],
            updated_at=rule_data["updated_at"],
            waf_rule_id=rule_data.get("waf_rule_id"),
            last_synced=rule_data.get("last_synced"),
            sync_status=rule_data.get("sync_status", "pending"),
        )

        # Remove from WAF if active
        if bot_rule.status == "active":
            sync_result = waf_sync_controller.delete_waf_rule(bot_rule)
            if not sync_result.success:
                logger.warning(f"WAF rule deletion failed for rule {rule_id}: {sync_result.error_message}")

        # Delete from DynamoDB
        bot_rules_table.delete_item(Key={"rule_id": rule_id})

        logger.info(f"=== HANDLE_DELETE_BOT_RULE END === Deleted rule: {rule_id}")
        return create_success_response({}, status_code=204)

    except Exception as e:
        logger.error(f"=== HANDLE_DELETE_BOT_RULE ERROR === Rule ID: {rule_id}, Error: {str(e)}")
        return create_error_response(500, "Failed to delete bot rule")


def handle_get_bot_activity(headers: Dict[str, str], query_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle GET /bot-control/activity - Retrieve bot activity and blocking statistics.

    Args:
        headers: HTTP headers
        query_params: Query parameters for filtering

    Returns:
        dict: HTTP response with bot activity data
    """
    logger.info("=== HANDLE_GET_BOT_ACTIVITY START ===")
    logger.info(f"Query params: {query_params}")

    try:
        # Authentication handled by main lambda decorator

        # Extract parameters
        time_period = query_params.get("time_period", "last_24_hours")
        bot_id = query_params.get("bot_id")

        # Get WAF metrics
        waf_metrics = waf_sync_controller.get_waf_metrics()

        if "error" in waf_metrics:
            logger.error(f"Failed to get WAF metrics: {waf_metrics['error']}")
            # Return mock data as fallback
            return create_success_response(
                {
                    "activity": {
                        "blocked_requests": 0,
                        "allowed_requests": 0,
                        "total_requests": 0,
                        "block_rate": 0.0,
                        "time_period": time_period,
                        "bot_id": bot_id,
                        "datapoints": [],
                        "error": "WAF metrics unavailable",
                    }
                }
            )

        # Process metrics data
        blocked_total = waf_metrics.get("blocked_requests", {}).get("total", 0)
        allowed_total = waf_metrics.get("allowed_requests", {}).get("total", 0)
        total_requests = blocked_total + allowed_total

        block_rate = (blocked_total / total_requests * 100) if total_requests > 0 else 0.0

        # Get datapoints for charts
        blocked_datapoints = waf_metrics.get("blocked_requests", {}).get("datapoints", [])
        allowed_datapoints = waf_metrics.get("allowed_requests", {}).get("datapoints", [])

        # Combine datapoints by timestamp
        combined_datapoints = []
        timestamp_map = {}

        for dp in blocked_datapoints:
            timestamp = dp["Timestamp"].isoformat()
            timestamp_map[timestamp] = {"timestamp": timestamp, "blocked": dp["Sum"], "allowed": 0}

        for dp in allowed_datapoints:
            timestamp = dp["Timestamp"].isoformat()
            if timestamp in timestamp_map:
                timestamp_map[timestamp]["allowed"] = dp["Sum"]
            else:
                timestamp_map[timestamp] = {"timestamp": timestamp, "blocked": 0, "allowed": dp["Sum"]}

        combined_datapoints = list(timestamp_map.values())
        combined_datapoints.sort(key=lambda x: x["timestamp"])

        activity_data = {
            "blocked_requests": int(blocked_total),
            "allowed_requests": int(allowed_total),
            "total_requests": int(total_requests),
            "block_rate": round(block_rate, 2),
            "time_period": time_period,
            "bot_id": bot_id,
            "datapoints": combined_datapoints,
        }

        # Add bot-specific metrics if requested
        if bot_id:
            bot_metrics = waf_sync_controller.get_waf_metrics(rule_name=bot_id)
            if f"rule_{bot_id}_blocked" in bot_metrics:
                bot_blocked = bot_metrics[f"rule_{bot_id}_blocked"].get("total", 0)
                activity_data["bot_specific"] = {
                    "bot_id": bot_id,
                    "blocked_requests": int(bot_blocked),
                    "datapoints": bot_metrics[f"rule_{bot_id}_blocked"].get("datapoints", []),
                }

        logger.info(f"=== HANDLE_GET_BOT_ACTIVITY END === Retrieved activity data for period: {time_period}")
        return create_success_response({"activity": activity_data})

    except Exception as e:
        logger.error(f"=== HANDLE_GET_BOT_ACTIVITY ERROR === {str(e)}")
        return create_error_response(500, "Failed to retrieve bot activity")


def handle_get_bot_stats(headers: Dict[str, str], query_params: Dict[str, str]) -> Dict[str, Any]:
    """
    Handle GET /bot-control/stats - Retrieve overall bot control statistics.

    Args:
        headers: HTTP headers
        query_params: Query parameters for filtering

    Returns:
        dict: HTTP response with bot control statistics
    """
    logger.info("=== HANDLE_GET_BOT_STATS START ===")
    logger.info(f"Query params: {query_params}")

    try:
        # Authentication handled by main lambda decorator

        # Extract parameters
        time_period = query_params.get("time_period", "last_24_hours")

        # Get rule counts from DynamoDB
        response = bot_rules_table.scan()
        rules = clean_binary_data(response.get("Items", []))

        active_rules = len([r for r in rules if r.get("status") == "active"])
        inactive_rules = len([r for r in rules if r.get("status") == "inactive"])
        total_rules = len(rules)

        # Get sync status counts
        synced_rules = len([r for r in rules if r.get("sync_status") == "synced"])
        pending_rules = len([r for r in rules if r.get("sync_status") == "pending"])
        error_rules = len([r for r in rules if r.get("sync_status") == "error"])

        # Get WAF metrics
        waf_metrics = waf_sync_controller.get_waf_metrics()

        blocked_total = 0
        allowed_total = 0

        if "error" not in waf_metrics:
            blocked_total = waf_metrics.get("blocked_requests", {}).get("total", 0)
            allowed_total = waf_metrics.get("allowed_requests", {}).get("total", 0)

        total_requests = blocked_total + allowed_total
        block_rate = (blocked_total / total_requests * 100) if total_requests > 0 else 0.0

        # Real WAF metrics only (wave0 Req 10.1): no fabricated demonstration values.
        # When there is no WAF traffic the totals remain zero (a truthful empty result).

        # Calculate rule effectiveness
        rule_effectiveness = []
        for rule in rules:
            if rule.get("status") == "active" and rule.get("waf_rule_id"):
                rule_metrics = waf_sync_controller.get_waf_metrics(rule_name=rule["rule_name"])
                rule_blocked = 0
                if f"rule_{rule['rule_name']}_blocked" in rule_metrics:
                    rule_blocked = rule_metrics[f"rule_{rule['rule_name']}_blocked"].get("total", 0)

                rule_effectiveness.append(
                    {
                        "rule_name": rule["rule_name"],
                        "rule_id": rule["rule_id"],
                        "action": rule["action"],
                        "blocked_requests": int(rule_blocked),
                        "effectiveness_score": min(100, (rule_blocked / max(1, blocked_total)) * 100),
                    }
                )

        # Sort by effectiveness
        rule_effectiveness.sort(key=lambda x: x["blocked_requests"], reverse=True)

        stats = {
            "time_period": time_period,
            "rule_summary": {
                "total_rules": total_rules,
                "active_rules": active_rules,
                "inactive_rules": inactive_rules,
            },
            "sync_status": {
                "synced_rules": synced_rules,
                "pending_rules": pending_rules,
                "error_rules": error_rules,
                "sync_health": round((synced_rules / max(1, total_rules)) * 100, 1),
            },
            "traffic_summary": {
                "total_requests": int(total_requests),
                "blocked_requests": int(blocked_total),
                "allowed_requests": int(allowed_total),
                "block_rate": round(block_rate, 2),
                "data_source": "waf_metrics",
            },
            "rule_effectiveness": rule_effectiveness[:10],  # Top 10 most effective rules
            "waf_health": "healthy" if "error" not in waf_metrics else "degraded",
        }

        logger.info(f"=== HANDLE_GET_BOT_STATS END === Retrieved statistics for period: {time_period}")
        return create_success_response(stats)

    except Exception as e:
        logger.error(f"=== HANDLE_GET_BOT_STATS ERROR === {str(e)}")
        return create_error_response(500, "Failed to retrieve bot control statistics")


def validate_authentication(headers: Dict[str, str]) -> Dict[str, Any]:
    """
    Validate authentication for bot control endpoints.

    Args:
        headers: HTTP headers

    Returns:
        dict: Validation result with 'valid' boolean and optional 'error' message
    """
    logger.info("=== VALIDATE_AUTHENTICATION START ===")
    logger.info(f"Headers present: {list(headers.keys()) if headers else 'none'}")

    # Check for Cognito JWT (UI access)
    auth_header = headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        # For now, accept any Bearer token (Cognito validation would be implemented here)
        logger.info("=== VALIDATE_AUTHENTICATION END === Bearer token accepted")
        return {"valid": True}

    # Check for Publisher Key (API access)
    publisher_key = headers.get("X-Publisher-Key", "")
    if publisher_key:
        # For now, accept any publisher key (validation would be implemented here)
        logger.info("=== VALIDATE_AUTHENTICATION END === Publisher key accepted")
        return {"valid": True}

    logger.info("=== VALIDATE_AUTHENTICATION END === Authentication failed")
    return {"valid": False, "error": "Missing or invalid authentication"}


def create_success_response(data: Dict[str, Any], status_code: int = 200) -> Dict[str, Any]:
    """
    Create a successful HTTP response.

    Args:
        data: Response data
        status_code: HTTP status code

    Returns:
        dict: HTTP response
    """
    # Clean binary data before JSON serialization
    cleaned_data = clean_binary_data(data)

    try:
        body = json.dumps(cleaned_data, cls=DecimalEncoder)
    except TypeError as e:
        # If JSON serialization still fails, log the error and try a more aggressive approach
        logger.error(f"JSON serialization failed even after cleaning: {str(e)}")
        logger.error(f"Data types in cleaned_data: {_debug_data_types(cleaned_data)}")

        # Try converting everything to strings as a last resort
        try:
            body = json.dumps(_convert_all_to_strings(cleaned_data))
        except Exception as fallback_e:
            logger.error(f"Fallback serialization also failed: {str(fallback_e)}")
            body = json.dumps({"error": "Data serialization failed", "message": str(e)})

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            # Custom Allow-Headers (X-Publisher-Key) / Methods preserved after the spread (Requirement 2.6).
            **cors_headers(),
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,X-Publisher-Key",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": body,
    }


def create_error_response(status_code: int, message: str) -> Dict[str, Any]:
    """
    Create an error HTTP response.

    Args:
        status_code: HTTP status code
        message: Error message

    Returns:
        dict: HTTP response
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # TM-02: origin resolved by the shared helper (ALLOWED_ORIGINS / SSM), never "*".
            # Custom Allow-Headers (X-Publisher-Key) / Methods preserved after the spread (Requirement 2.6).
            **cors_headers(),
            "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token,X-Publisher-Key",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
        },
        "body": json.dumps({"error": {"code": status_code, "message": message}}),
    }


class BotControlService:
    """Single home for the bot-control domain's request handling (Requirement 1.1 / 7.1).

    The bot-control domain's business logic and ``iab-bot-rules-table`` DynamoDB access (plus
    WAF-sync orchestration and WAF metrics retrieval) now live in this module directly
    (module-level helpers above); this service no longer delegates back to a root handler
    module. The public entry is :meth:`handle_request`, which accepts the raw API Gateway
    proxy event and returns an API Gateway proxy response object. Path disambiguation across
    ``/bot-control/rules``, ``/bot-control/rules/{rule_id}``, ``/bot-control/activity``, and
    ``/bot-control/stats`` plus the module's own request validation are performed here,
    exactly as the monolith did (Requirement 2).
    """

    def handle_request(self, event: Dict[str, Any], context: Optional[Any] = None) -> Dict[str, Any]:
        """
        Main handler for bot control API endpoints.

        Args:
            event: Lambda event containing HTTP request data
            context: Lambda context object

        Returns:
            dict: HTTP response with status code and body
        """
        request_id = getattr(context, "aws_request_id", "unknown")
        logger.info(f"=== BOT_CONTROL_API_LAMBDA START === Request ID: {request_id}")

        try:
            # Extract request information
            http_method = event.get("httpMethod", "")
            path = event.get("path", "")
            headers = event.get("headers", {})
            query_params = event.get("queryStringParameters") or {}
            body = event.get("body", "{}")
            path_parameters = event.get("pathParameters") or {}

            logger.info(f"Bot Control API request: {http_method} {path}")
            logger.info(f"Query params: {query_params}, Path params: {path_parameters}")

            # Parse request body if present
            request_data = {}
            if body and body != "{}":
                try:
                    request_data = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON in request body: {str(e)}")
                    return create_error_response(400, "Invalid JSON in request body")

            # Route requests to appropriate handlers
            if path == "/bot-control/rules":
                if http_method == "GET":
                    return handle_get_bot_rules(headers, query_params)
                elif http_method == "POST":
                    return handle_create_bot_rule(headers, request_data)
                else:
                    return create_error_response(405, "Method not allowed")

            elif path.startswith("/bot-control/rules/"):
                rule_id = path_parameters.get("rule_id")
                if not rule_id:
                    return create_error_response(400, "Missing rule_id parameter")

                if http_method == "PUT":
                    return handle_update_bot_rule(headers, rule_id, request_data)
                elif http_method == "DELETE":
                    return handle_delete_bot_rule(headers, rule_id)
                else:
                    return create_error_response(405, "Method not allowed")

            elif path == "/bot-control/activity":
                if http_method == "GET":
                    return handle_get_bot_activity(headers, query_params)
                else:
                    return create_error_response(405, "Method not allowed")

            elif path == "/bot-control/stats":
                if http_method == "GET":
                    return handle_get_bot_stats(headers, query_params)
                else:
                    return create_error_response(405, "Method not allowed")

            else:
                return create_error_response(404, "Endpoint not found")

        except Exception as e:
            logger.error(
                f"=== BOT_CONTROL_API_LAMBDA ERROR === Request ID: {request_id}, Error: {str(e)}", exc_info=True
            )
            return create_error_response(500, "Internal server error")
