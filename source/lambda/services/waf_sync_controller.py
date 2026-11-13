"""
WAF Synchronization Controller

This module provides synchronization between DynamoDB bot rules and AWS WAF configurations.
It implements the hybrid infrastructure-application approach where CDK provisions initial
WAF resources and the application manages runtime rule configurations through AWS APIs.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


@dataclass
class BotRule:
    """Data model for bot control rules stored in DynamoDB"""

    rule_id: str
    rule_name: str
    description: str
    action: str  # block, allow, count
    priority: int
    conditions: Dict[str, Any]  # WAF rule conditions
    status: str  # active, inactive
    created_at: str
    updated_at: str
    # WAF synchronization fields
    waf_rule_id: Optional[str] = None
    last_synced: Optional[str] = None
    sync_status: str = "pending"  # synced, pending, error


@dataclass
class WAFSyncResult:
    """Result of WAF synchronization operation"""

    success: bool
    rule_id: str
    waf_rule_id: Optional[str] = None
    error_message: Optional[str] = None
    sync_timestamp: Optional[str] = None


class WAFSyncController:
    """
    Controller for synchronizing bot control rules between DynamoDB and AWS WAF.

    This service implements the hybrid approach where:
    - CDK provisions the initial WAF WebACL and basic rules
    - Application manages runtime rule configurations through AWS APIs
    - DynamoDB serves as the source of truth for application-managed rules
    """

    def __init__(self, bot_rules_table_name: str = None, web_acl_name: str = None, web_acl_scope: str = "REGIONAL"):
        """
        Initialize the WAF sync controller.

        Args:
            bot_rules_table_name: DynamoDB table name for bot rules
            web_acl_name: WAF WebACL name
            web_acl_scope: WAF scope (REGIONAL or CLOUDFRONT)
        """
        self.dynamodb = boto3.resource("dynamodb")
        self.wafv2_client = boto3.client("wafv2")
        self.cloudwatch = boto3.client("cloudwatch")

        self.bot_rules_table_name = bot_rules_table_name or self._get_bot_rules_table_name()
        self.web_acl_name = web_acl_name or self._get_web_acl_name()
        self.web_acl_scope = web_acl_scope

        self.bot_rules_table = self.dynamodb.Table(self.bot_rules_table_name)

        # Retry configuration
        self.retry_config = {"max_attempts": 3, "backoff_factor": 2, "exceptions": (ClientError, BotoCoreError)}

        # Cache for WebACL info
        self._web_acl_cache = None
        self._cache_timestamp = None
        self._cache_ttl = 300  # 5 minutes

    def _get_bot_rules_table_name(self) -> str:
        """Get bot rules table name from environment or use default"""
        import os

        return os.environ.get("BOT_RULES_TABLE_NAME", "iab-bot-rules-table")

    def _get_web_acl_name(self) -> str:
        """Get WAF WebACL name from environment or use default"""
        import os

        return os.environ.get("WAF_WEB_ACL_NAME", "iab-bot-control-webacl")

    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        return datetime.now(timezone.utc).isoformat()

    def _get_web_acl_info(self) -> Optional[Dict[str, Any]]:
        """
        Get WebACL information with caching.

        Returns:
            dict: WebACL information including ID and ARN
        """
        try:
            current_time = time.time()

            # Check cache
            if self._web_acl_cache and self._cache_timestamp and current_time - self._cache_timestamp < self._cache_ttl:
                return self._web_acl_cache

            # List WebACLs to find our target - handle pagination
            next_marker = None
            all_webacls = []

            while True:
                list_params = {
                    "Scope": self.web_acl_scope,
                    "Limit": 100,  # Get more results per page
                }
                if next_marker:
                    list_params["NextMarker"] = next_marker

                response = self.wafv2_client.list_web_acls(**list_params)
                webacls_in_response = response.get("WebACLs", [])
                all_webacls.extend(webacls_in_response)

                # Check if there are more pages
                next_marker = response.get("NextMarker")
                if not next_marker:
                    break

            # Now search through all WebACLs
            for web_acl in all_webacls:
                if web_acl["Name"] == self.web_acl_name:
                    self._web_acl_cache = web_acl
                    self._cache_timestamp = current_time
                    logger.info(f"Found WebACL: {web_acl['Name']} (ID: {web_acl['Id']})")
                    return web_acl

            logger.error(f"WebACL not found: {self.web_acl_name}")
            return None

        except Exception as e:
            logger.error(f"Failed to get WebACL info: {str(e)}")
            return None

    def _retry_operation(self, operation_func, *args, **kwargs):
        """
        Retry operation with exponential backoff.

        Args:
            operation_func: Function to retry
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Operation result or raises exception
        """
        last_exception = None

        for attempt in range(self.retry_config["max_attempts"]):
            try:
                return operation_func(*args, **kwargs)
            except self.retry_config["exceptions"] as e:
                last_exception = e
                if attempt < self.retry_config["max_attempts"] - 1:
                    wait_time = self.retry_config["backoff_factor"] ** attempt
                    logger.warning(f"Operation failed (attempt {attempt + 1}), retrying in {wait_time}s: {str(e)}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Operation failed after {self.retry_config['max_attempts']} attempts: {str(e)}")

        raise last_exception

    def create_waf_rule(self, bot_rule: BotRule) -> WAFSyncResult:
        """
        Create a new WAF rule based on bot rule configuration.

        Args:
            bot_rule: Bot rule configuration

        Returns:
            WAFSyncResult: Result of the operation
        """
        try:
            web_acl_info = self._get_web_acl_info()
            if not web_acl_info:
                return WAFSyncResult(success=False, rule_id=bot_rule.rule_id, error_message="WebACL not found")

            # Get current WebACL configuration
            web_acl_response = self._retry_operation(
                self.wafv2_client.get_web_acl,
                Name=web_acl_info["Name"],
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
            )

            web_acl = web_acl_response["WebACL"]
            current_rules = web_acl.get("Rules", [])

            # Create new WAF rule from bot rule configuration
            waf_rule = self._convert_bot_rule_to_waf_rule(bot_rule)

            # Add the new rule to existing rules
            updated_rules = current_rules + [waf_rule]

            # Update WebACL with new rule
            self._retry_operation(
                self.wafv2_client.update_web_acl,
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
                Name=web_acl_info["Name"],
                DefaultAction=web_acl["DefaultAction"],
                Rules=updated_rules,
                VisibilityConfig=web_acl["VisibilityConfig"],
                LockToken=web_acl["LockToken"],
            )

            # Update bot rule with WAF sync info
            sync_timestamp = self._get_current_timestamp()
            self._update_bot_rule_sync_status(
                bot_rule.rule_id,
                waf_rule_id=bot_rule.rule_name,  # Use rule name as WAF rule ID
                sync_status="synced",
                last_synced=sync_timestamp,
            )

            logger.info(f"Successfully created WAF rule: {bot_rule.rule_name}")
            return WAFSyncResult(
                success=True, rule_id=bot_rule.rule_id, waf_rule_id=bot_rule.rule_name, sync_timestamp=sync_timestamp
            )

        except Exception as e:
            error_msg = f"Failed to create WAF rule: {str(e)}"
            logger.error(error_msg)

            # Update bot rule with error status
            self._update_bot_rule_sync_status(
                bot_rule.rule_id, sync_status="error", last_synced=self._get_current_timestamp()
            )

            return WAFSyncResult(success=False, rule_id=bot_rule.rule_id, error_message=error_msg)

    def update_waf_rule(self, bot_rule: BotRule) -> WAFSyncResult:
        """
        Update an existing WAF rule based on bot rule configuration.

        Args:
            bot_rule: Updated bot rule configuration

        Returns:
            WAFSyncResult: Result of the operation
        """
        try:
            web_acl_info = self._get_web_acl_info()
            if not web_acl_info:
                return WAFSyncResult(success=False, rule_id=bot_rule.rule_id, error_message="WebACL not found")

            # Get current WebACL configuration
            web_acl_response = self._retry_operation(
                self.wafv2_client.get_web_acl,
                Name=web_acl_info["Name"],
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
            )

            web_acl = web_acl_response["WebACL"]
            current_rules = web_acl.get("Rules", [])

            # Find and update the existing rule
            updated_rules = []
            rule_found = False

            for rule in current_rules:
                if rule["Name"] == bot_rule.waf_rule_id or rule["Name"] == bot_rule.rule_name:
                    # Replace with updated rule
                    updated_rule = self._convert_bot_rule_to_waf_rule(bot_rule)
                    updated_rules.append(updated_rule)
                    rule_found = True
                else:
                    updated_rules.append(rule)

            if not rule_found:
                # Rule doesn't exist in WAF, create it
                return self.create_waf_rule(bot_rule)

            # Update WebACL with modified rules
            self._retry_operation(
                self.wafv2_client.update_web_acl,
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
                Name=web_acl_info["Name"],
                DefaultAction=web_acl["DefaultAction"],
                Rules=updated_rules,
                VisibilityConfig=web_acl["VisibilityConfig"],
                LockToken=web_acl["LockToken"],
            )

            # Update bot rule with WAF sync info
            sync_timestamp = self._get_current_timestamp()
            self._update_bot_rule_sync_status(
                bot_rule.rule_id, waf_rule_id=bot_rule.rule_name, sync_status="synced", last_synced=sync_timestamp
            )

            logger.info(f"Successfully updated WAF rule: {bot_rule.rule_name}")
            return WAFSyncResult(
                success=True, rule_id=bot_rule.rule_id, waf_rule_id=bot_rule.rule_name, sync_timestamp=sync_timestamp
            )

        except Exception as e:
            error_msg = f"Failed to update WAF rule: {str(e)}"
            logger.error(error_msg)

            # Update bot rule with error status
            self._update_bot_rule_sync_status(
                bot_rule.rule_id, sync_status="error", last_synced=self._get_current_timestamp()
            )

            return WAFSyncResult(success=False, rule_id=bot_rule.rule_id, error_message=error_msg)

    def delete_waf_rule(self, bot_rule: BotRule) -> WAFSyncResult:
        """
        Delete a WAF rule based on bot rule configuration.

        Args:
            bot_rule: Bot rule to delete

        Returns:
            WAFSyncResult: Result of the operation
        """
        try:
            web_acl_info = self._get_web_acl_info()
            if not web_acl_info:
                return WAFSyncResult(success=False, rule_id=bot_rule.rule_id, error_message="WebACL not found")

            # Get current WebACL configuration
            web_acl_response = self._retry_operation(
                self.wafv2_client.get_web_acl,
                Name=web_acl_info["Name"],
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
            )

            web_acl = web_acl_response["WebACL"]
            current_rules = web_acl.get("Rules", [])

            # Remove the rule from existing rules
            updated_rules = []
            rule_found = False

            for rule in current_rules:
                if rule["Name"] == bot_rule.waf_rule_id or rule["Name"] == bot_rule.rule_name:
                    rule_found = True
                    # Skip this rule (delete it)
                else:
                    updated_rules.append(rule)

            if not rule_found:
                logger.warning(f"WAF rule not found for deletion: {bot_rule.rule_name}")
                # Still mark as synced since the rule doesn't exist
                sync_timestamp = self._get_current_timestamp()
                self._update_bot_rule_sync_status(bot_rule.rule_id, sync_status="synced", last_synced=sync_timestamp)
                return WAFSyncResult(success=True, rule_id=bot_rule.rule_id, sync_timestamp=sync_timestamp)

            # Update WebACL without the deleted rule
            self._retry_operation(
                self.wafv2_client.update_web_acl,
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
                Name=web_acl_info["Name"],
                DefaultAction=web_acl["DefaultAction"],
                Rules=updated_rules,
                VisibilityConfig=web_acl["VisibilityConfig"],
                LockToken=web_acl["LockToken"],
            )

            logger.info(f"Successfully deleted WAF rule: {bot_rule.rule_name}")
            return WAFSyncResult(success=True, rule_id=bot_rule.rule_id, sync_timestamp=self._get_current_timestamp())

        except Exception as e:
            error_msg = f"Failed to delete WAF rule: {str(e)}"
            logger.error(error_msg)

            # Update bot rule with error status
            self._update_bot_rule_sync_status(
                bot_rule.rule_id, sync_status="error", last_synced=self._get_current_timestamp()
            )

            return WAFSyncResult(success=False, rule_id=bot_rule.rule_id, error_message=error_msg)

    def get_waf_metrics(self, rule_name: str = None) -> Dict[str, Any]:
        """
        Retrieve WAF metrics from CloudWatch.

        Args:
            rule_name: Specific rule name to get metrics for (optional)

        Returns:
            dict: WAF metrics data
        """
        try:
            web_acl_info = self._get_web_acl_info()
            if not web_acl_info:
                return {"error": "WebACL not found"}

            # Get metrics for the WebACL
            end_time = datetime.now(timezone.utc)
            start_time = end_time.replace(hour=0, minute=0, second=0, microsecond=0)  # Start of today
            if start_time >= end_time:
                # If we're at the start of the day, go back to yesterday
                start_time = start_time.replace(day=start_time.day - 1)

            metrics = {}

            # Get blocked requests metric
            blocked_response = self.cloudwatch.get_metric_statistics(
                Namespace="AWS/WAFV2",
                MetricName="BlockedRequests",
                Dimensions=[
                    {"Name": "WebACL", "Value": web_acl_info["Name"]},
                    {"Name": "Region", "Value": boto3.Session().region_name},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour periods
                Statistics=["Sum"],
            )

            metrics["blocked_requests"] = {
                "datapoints": blocked_response.get("Datapoints", []),
                "total": sum(dp["Sum"] for dp in blocked_response.get("Datapoints", [])),
            }

            # Get allowed requests metric
            allowed_response = self.cloudwatch.get_metric_statistics(
                Namespace="AWS/WAFV2",
                MetricName="AllowedRequests",
                Dimensions=[
                    {"Name": "WebACL", "Value": web_acl_info["Name"]},
                    {"Name": "Region", "Value": boto3.Session().region_name},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=["Sum"],
            )

            metrics["allowed_requests"] = {
                "datapoints": allowed_response.get("Datapoints", []),
                "total": sum(dp["Sum"] for dp in allowed_response.get("Datapoints", [])),
            }

            # If specific rule requested, get rule-specific metrics
            if rule_name:
                rule_blocked_response = self.cloudwatch.get_metric_statistics(
                    Namespace="AWS/WAFV2",
                    MetricName="BlockedRequests",
                    Dimensions=[
                        {"Name": "WebACL", "Value": web_acl_info["Name"]},
                        {"Name": "Rule", "Value": rule_name},
                        {"Name": "Region", "Value": boto3.Session().region_name},
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600,
                    Statistics=["Sum"],
                )

                metrics[f"rule_{rule_name}_blocked"] = {
                    "datapoints": rule_blocked_response.get("Datapoints", []),
                    "total": sum(dp["Sum"] for dp in rule_blocked_response.get("Datapoints", [])),
                }

            return metrics

        except Exception as e:
            logger.error(f"Failed to get WAF metrics: {str(e)}")
            return {"error": str(e)}

    def sync_waf_state(self) -> Dict[str, Any]:
        """
        Reconcile DynamoDB bot rules with WAF configuration.

        Returns:
            dict: Synchronization results
        """
        try:
            # Get all bot rules from DynamoDB
            response = self.bot_rules_table.scan()
            bot_rules = response.get("Items", [])

            # Get current WAF rules
            web_acl_info = self._get_web_acl_info()
            if not web_acl_info:
                return {"error": "WebACL not found"}

            web_acl_response = self._retry_operation(
                self.wafv2_client.get_web_acl,
                Name=web_acl_info["Name"],
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
            )

            waf_rules = web_acl_response["WebACL"].get("Rules", [])
            waf_rule_names = {rule["Name"] for rule in waf_rules}

            sync_results = {"synced_rules": [], "missing_in_waf": [], "missing_in_dynamodb": [], "sync_errors": []}

            # Check each bot rule against WAF
            for bot_rule_item in bot_rules:
                bot_rule = BotRule(**bot_rule_item)

                if bot_rule.status == "active":
                    if bot_rule.rule_name not in waf_rule_names:
                        # Rule missing in WAF, create it
                        result = self.create_waf_rule(bot_rule)
                        if result.success:
                            sync_results["synced_rules"].append(bot_rule.rule_name)
                        else:
                            sync_results["sync_errors"].append(
                                {"rule_name": bot_rule.rule_name, "error": result.error_message}
                            )
                    else:
                        sync_results["synced_rules"].append(bot_rule.rule_name)
                elif bot_rule.status == "inactive" and bot_rule.rule_name in waf_rule_names:
                    # Inactive rule exists in WAF, remove it
                    result = self.delete_waf_rule(bot_rule)
                    if result.success:
                        sync_results["synced_rules"].append(f"{bot_rule.rule_name} (deleted)")
                    else:
                        sync_results["sync_errors"].append(
                            {"rule_name": bot_rule.rule_name, "error": result.error_message}
                        )

            # Check for WAF rules not in DynamoDB (CDK-managed rules are expected)
            bot_rule_names = {rule["rule_name"] for rule in bot_rules}
            for waf_rule_name in waf_rule_names:
                if waf_rule_name not in bot_rule_names:
                    # This could be a CDK-managed rule, which is expected
                    sync_results["missing_in_dynamodb"].append(waf_rule_name)

            logger.info(
                f"WAF sync completed: {len(sync_results['synced_rules'])} synced, "
                f"{len(sync_results['sync_errors'])} errors"
            )

            return sync_results

        except Exception as e:
            error_msg = f"Failed to sync WAF state: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    def _convert_bot_rule_to_waf_rule(self, bot_rule: BotRule) -> Dict[str, Any]:
        """
        Convert a bot rule to WAF rule format.

        Args:
            bot_rule: Bot rule configuration

        Returns:
            dict: WAF rule configuration
        """
        # Convert action
        if bot_rule.action == "block":
            action = {"Block": {}}
        elif bot_rule.action == "allow":
            action = {"Allow": {}}
        else:  # count
            action = {"Count": {}}

        # Build WAF rule based on conditions
        conditions = bot_rule.conditions

        # Default to user-agent matching if no specific conditions
        if not conditions or "user_agent_patterns" not in conditions:
            statement = {
                "ByteMatchStatement": {
                    "SearchString": conditions.get("search_string", "bot"),
                    "FieldToMatch": {"SingleHeader": {"Name": "user-agent"}},
                    "TextTransformations": [{"Priority": 0, "Type": "LOWERCASE"}],
                    "PositionalConstraint": "CONTAINS",
                }
            }
        else:
            # Handle multiple user agent patterns
            patterns = conditions["user_agent_patterns"]
            if len(patterns) == 1:
                statement = {
                    "ByteMatchStatement": {
                        "SearchString": patterns[0],
                        "FieldToMatch": {"SingleHeader": {"Name": "user-agent"}},
                        "TextTransformations": [{"Priority": 0, "Type": "LOWERCASE"}],
                        "PositionalConstraint": "CONTAINS",
                    }
                }
            else:
                # Multiple patterns - use OR statement
                or_statements = []
                for pattern in patterns:
                    or_statements.append(
                        {
                            "ByteMatchStatement": {
                                "SearchString": pattern,
                                "FieldToMatch": {"SingleHeader": {"Name": "user-agent"}},
                                "TextTransformations": [{"Priority": 0, "Type": "LOWERCASE"}],
                                "PositionalConstraint": "CONTAINS",
                            }
                        }
                    )

                statement = {"OrStatement": {"Statements": or_statements}}

        return {
            "Name": bot_rule.rule_name,
            "Priority": bot_rule.priority,
            "Statement": statement,
            "Action": action,
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": bot_rule.rule_name.replace("-", "_"),
            },
        }

    def _update_bot_rule_sync_status(
        self, rule_id: str, waf_rule_id: str = None, sync_status: str = None, last_synced: str = None
    ):
        """
        Update bot rule sync status in DynamoDB.

        Args:
            rule_id: Bot rule ID
            waf_rule_id: WAF rule ID (optional)
            sync_status: Sync status (optional)
            last_synced: Last sync timestamp (optional)
        """
        try:
            update_expression = "SET updated_at = :updated_at"
            expression_values = {":updated_at": self._get_current_timestamp()}

            if waf_rule_id is not None:
                update_expression += ", waf_rule_id = :waf_rule_id"
                expression_values[":waf_rule_id"] = waf_rule_id

            if sync_status is not None:
                update_expression += ", sync_status = :sync_status"
                expression_values[":sync_status"] = sync_status

            if last_synced is not None:
                update_expression += ", last_synced = :last_synced"
                expression_values[":last_synced"] = last_synced

            self.bot_rules_table.update_item(
                Key={"rule_id": rule_id},
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values,
            )

        except Exception as e:
            logger.error(f"Failed to update bot rule sync status: {str(e)}")
