#!/usr/bin/env python3
"""
Import existing WAF rules into DynamoDB bot rules table.

This script reads existing WAF rules from the WebACL and creates corresponding
records in the DynamoDB bot rules table, ensuring the UI can display existing rules.
"""

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import boto3

# Add lambda source modules to path so we can import the WAF sync controller
# (services/ lives in source/lambda/)
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "source", "lambda"))

from services.waf_sync_controller import BotRule, WAFSyncController

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WAFRulesImporter:
    """Import existing WAF rules into DynamoDB bot rules table."""

    def __init__(
        self,
        bot_rules_table_name: str = None,
        web_acl_name: str = None,
        web_acl_scope: str = "REGIONAL",
        aws_profile: str = None,
    ):
        """
        Initialize the WAF rules importer.

        Args:
            bot_rules_table_name: DynamoDB table name for bot rules
            web_acl_name: WAF WebACL name
            web_acl_scope: WAF scope (REGIONAL or CLOUDFRONT)
            aws_profile: AWS profile to use
        """
        # Set up AWS session with profile if specified
        if aws_profile:
            session = boto3.Session(profile_name=aws_profile)
            self.dynamodb = session.resource("dynamodb")
            self.wafv2_client = session.client("wafv2")
        else:
            self.dynamodb = boto3.resource("dynamodb")
            self.wafv2_client = boto3.client("wafv2")

        self.bot_rules_table_name = bot_rules_table_name or self._get_bot_rules_table_name()
        self.web_acl_name = web_acl_name or self._get_web_acl_name()
        self.web_acl_scope = web_acl_scope

        self.bot_rules_table = self.dynamodb.Table(self.bot_rules_table_name)

        # Initialize WAF sync controller for utility methods
        self.waf_sync = WAFSyncController(
            bot_rules_table_name=self.bot_rules_table_name,
            web_acl_name=self.web_acl_name,
            web_acl_scope=self.web_acl_scope,
        )

    def _get_bot_rules_table_name(self) -> str:
        """Get bot rules table name from environment or use default"""
        return os.environ.get("BOT_RULES_TABLE_NAME", "cm-bot-rules")

    def _get_web_acl_name(self) -> str:
        """Get WAF WebACL name from environment or use default"""
        return os.environ.get("WAF_WEB_ACL_NAME", "iab-bot-control-webacl")

    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        return datetime.now(timezone.utc).isoformat()

    def get_existing_waf_rules(self) -> List[Dict[str, Any]]:
        """
        Get all existing WAF rules from the WebACL.

        Returns:
            List of WAF rule configurations
        """
        try:
            # Try to get WebACL info using the sync controller
            web_acl_info = self.waf_sync._get_web_acl_info()
            if not web_acl_info:
                logger.warning(f"WebACL not found via search: {self.web_acl_name}")
                # Try direct access with known ID if available
                if self.web_acl_name == "iab-bot-control-webacl":
                    logger.info("Attempting direct access with known WebACL ID")
                    try:
                        web_acl_response = self.wafv2_client.get_web_acl(
                            Name="iab-bot-control-webacl",
                            Scope=self.web_acl_scope,
                            Id="0437ab41-3fc2-4eff-933f-c76555a86f2d",
                        )
                        waf_rules = web_acl_response["WebACL"].get("Rules", [])
                        logger.info(f"Found {len(waf_rules)} existing WAF rules via direct access")
                        return waf_rules
                    except Exception as direct_e:
                        logger.error(f"Direct access also failed: {str(direct_e)}")
                return []

            # Get current WebACL configuration
            web_acl_response = self.wafv2_client.get_web_acl(
                Name=web_acl_info["Name"], Scope=self.web_acl_scope, Id=web_acl_info["Id"]
            )

            waf_rules = web_acl_response["WebACL"].get("Rules", [])
            logger.info(f"Found {len(waf_rules)} existing WAF rules")

            return waf_rules

        except Exception as e:
            logger.error(f"Failed to get existing WAF rules: {str(e)}")
            return []

    def get_existing_bot_rules(self) -> List[Dict[str, Any]]:
        """
        Get all existing bot rules from DynamoDB.

        Returns:
            List of bot rule records
        """
        try:
            response = self.bot_rules_table.scan()
            bot_rules = response.get("Items", [])
            logger.info(f"Found {len(bot_rules)} existing bot rules in DynamoDB")
            return bot_rules

        except Exception as e:
            logger.error(f"Failed to get existing bot rules: {str(e)}")
            return []

    def convert_waf_rule_to_bot_rule(self, waf_rule: Dict[str, Any]) -> BotRule:
        """
        Convert a WAF rule to a bot rule format.

        Args:
            waf_rule: WAF rule configuration

        Returns:
            BotRule object
        """
        # Extract basic information
        rule_name = waf_rule["Name"]
        priority = waf_rule["Priority"]

        # Convert action
        action_key = list(waf_rule["Action"].keys())[0].lower()
        if action_key == "block":
            action = "block"
        elif action_key == "allow":
            action = "allow"
        else:
            action = "count"

        # Extract conditions from statement
        conditions = self._extract_conditions_from_statement(waf_rule.get("Statement", {}))

        # Generate unique rule ID
        rule_id = str(uuid.uuid4())

        # Create timestamps
        current_time = self._get_current_timestamp()

        return BotRule(
            rule_id=rule_id,
            rule_name=rule_name,
            description=f"Imported from existing WAF rule: {rule_name}",
            action=action,
            priority=priority,
            conditions=conditions,
            status="active",
            created_at=current_time,
            updated_at=current_time,
            waf_rule_id=rule_name,
            last_synced=current_time,
            sync_status="synced",
        )

    def _extract_conditions_from_statement(self, statement: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract conditions from WAF rule statement.

        Args:
            statement: WAF rule statement

        Returns:
            Conditions dictionary
        """
        conditions = {}

        if "ByteMatchStatement" in statement:
            byte_match = statement["ByteMatchStatement"]
            search_string = byte_match.get("SearchString", "")
            # Ensure search_string is a string, not bytes
            if isinstance(search_string, bytes):
                search_string = search_string.decode("utf-8")
            conditions["search_string"] = search_string

            # Check if it's matching user-agent
            field_to_match = byte_match.get("FieldToMatch", {})
            if "SingleHeader" in field_to_match and field_to_match["SingleHeader"].get("Name") == "user-agent":
                conditions["user_agent_patterns"] = [conditions["search_string"]]

        elif "OrStatement" in statement:
            # Handle multiple patterns
            patterns = []
            for sub_statement in statement["OrStatement"].get("Statements", []):
                if "ByteMatchStatement" in sub_statement:
                    search_string = sub_statement["ByteMatchStatement"].get("SearchString", "")
                    # Ensure search_string is a string, not bytes
                    if isinstance(search_string, bytes):
                        search_string = search_string.decode("utf-8")
                    patterns.append(search_string)
            conditions["user_agent_patterns"] = patterns

        elif "AndStatement" in statement:
            # Handle AND conditions - simplified extraction
            conditions["complex_statement"] = True
            conditions["statement_type"] = "and"

        elif "NotStatement" in statement:
            # Handle NOT conditions - simplified extraction
            conditions["complex_statement"] = True
            conditions["statement_type"] = "not"

        else:
            # Unknown statement type
            conditions["complex_statement"] = True
            conditions["statement_type"] = "unknown"
            conditions["raw_statement"] = statement

        return conditions

    def import_waf_rules(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Import existing WAF rules into DynamoDB bot rules table.

        Args:
            dry_run: If True, only show what would be imported without making changes

        Returns:
            Import results summary
        """
        try:
            # Get existing WAF rules
            waf_rules = self.get_existing_waf_rules()
            if not waf_rules:
                return {"error": "No WAF rules found to import"}

            # Get existing bot rules to avoid duplicates
            existing_bot_rules = self.get_existing_bot_rules()
            existing_rule_names = {rule.get("rule_name") for rule in existing_bot_rules}

            import_results = {
                "total_waf_rules": len(waf_rules),
                "imported_rules": [],
                "skipped_rules": [],
                "errors": [],
            }

            for waf_rule in waf_rules:
                rule_name = waf_rule["Name"]

                # Skip if already exists in DynamoDB
                if rule_name in existing_rule_names:
                    import_results["skipped_rules"].append(
                        {"rule_name": rule_name, "reason": "Already exists in DynamoDB"}
                    )
                    continue

                try:
                    # Convert WAF rule to bot rule
                    bot_rule = self.convert_waf_rule_to_bot_rule(waf_rule)

                    if dry_run:
                        import_results["imported_rules"].append(
                            {
                                "rule_name": rule_name,
                                "action": bot_rule.action,
                                "priority": bot_rule.priority,
                                "conditions": bot_rule.conditions,
                                "status": "DRY_RUN",
                            }
                        )
                    else:
                        # Store in DynamoDB
                        self.bot_rules_table.put_item(
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
                                "waf_rule_id": bot_rule.waf_rule_id,
                                "last_synced": bot_rule.last_synced,
                                "sync_status": bot_rule.sync_status,
                            }
                        )

                        import_results["imported_rules"].append(
                            {
                                "rule_name": rule_name,
                                "rule_id": bot_rule.rule_id,
                                "action": bot_rule.action,
                                "priority": bot_rule.priority,
                                "status": "IMPORTED",
                            }
                        )

                        logger.info(f"Imported WAF rule: {rule_name}")

                except Exception as e:
                    error_msg = f"Failed to import rule {rule_name}: {str(e)}"
                    logger.error(error_msg)
                    import_results["errors"].append({"rule_name": rule_name, "error": error_msg})

            # Summary
            imported_count = len(import_results["imported_rules"])
            skipped_count = len(import_results["skipped_rules"])
            error_count = len(import_results["errors"])

            logger.info(f"Import completed: {imported_count} imported, {skipped_count} skipped, {error_count} errors")

            return import_results

        except Exception as e:
            error_msg = f"Failed to import WAF rules: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}

    def list_all_webacls(self) -> None:
        """List all WebACLs for inspection."""
        try:
            next_marker = None
            webacl_count = 0

            print(f"\nListing all WebACLs in scope: {self.web_acl_scope}")
            print("-" * 80)

            while True:
                list_params = {"Scope": self.web_acl_scope}
                if next_marker:
                    list_params["NextMarker"] = next_marker

                response = self.wafv2_client.list_web_acls(**list_params)

                for web_acl in response.get("WebACLs", []):
                    webacl_count += 1
                    print(f"WebACL {webacl_count}:")
                    print(f"  Name: {web_acl['Name']}")
                    print(f"  ID: {web_acl['Id']}")
                    print(f"  Description: {web_acl.get('Description', 'N/A')}")
                    print(f"  ARN: {web_acl.get('ARN', 'N/A')}")
                    print("-" * 40)

                # Check if there are more pages
                next_marker = response.get("NextMarker")
                if not next_marker:
                    break

            if webacl_count == 0:
                print("No WebACLs found.")

        except Exception as e:
            print(f"Error listing WebACLs: {str(e)}")

    def list_waf_rules(self) -> None:
        """List all existing WAF rules for inspection."""
        waf_rules = self.get_existing_waf_rules()

        if not waf_rules:
            print("No WAF rules found.")
            return

        print(f"\nFound {len(waf_rules)} WAF rules:")
        print("-" * 80)

        for rule in waf_rules:
            try:
                action_key = list(rule["Action"].keys())[0] if "Action" in rule and rule["Action"] else "Unknown"
                print(f"Name: {rule.get('Name', 'Unknown')}")
                print(f"Priority: {rule.get('Priority', 'Unknown')}")
                print(f"Action: {action_key}")

                # Show statement summary
                statement = rule.get("Statement", {})
                if "ByteMatchStatement" in statement:
                    byte_match = statement["ByteMatchStatement"]
                    print(f"Condition: ByteMatch - '{byte_match.get('SearchString', '')}'")
                elif "OrStatement" in statement:
                    patterns = []
                    for sub_stmt in statement["OrStatement"].get("Statements", []):
                        if "ByteMatchStatement" in sub_stmt:
                            patterns.append(sub_stmt["ByteMatchStatement"].get("SearchString", ""))
                    print(f"Condition: OR - {patterns}")
                else:
                    print(f"Condition: {list(statement.keys())[0] if statement else 'None'}")

                print("-" * 40)
            except Exception as e:
                print(f"Error processing rule: {e}")
                print(f"Rule data: {rule}")
                print("-" * 40)

    def list_bot_rules(self) -> None:
        """List all existing bot rules in DynamoDB for inspection."""
        bot_rules = self.get_existing_bot_rules()

        if not bot_rules:
            print("No bot rules found in DynamoDB.")
            return

        print(f"\nFound {len(bot_rules)} bot rules in DynamoDB:")
        print("-" * 80)

        for rule in bot_rules:
            print(f"Name: {rule.get('rule_name', 'N/A')}")
            print(f"ID: {rule.get('rule_id', 'N/A')}")
            print(f"Action: {rule.get('action', 'N/A')}")
            print(f"Priority: {rule.get('priority', 'N/A')}")
            print(f"Status: {rule.get('status', 'N/A')}")
            print(f"Sync Status: {rule.get('sync_status', 'N/A')}")
            print(f"WAF Rule ID: {rule.get('waf_rule_id', 'N/A')}")
            print("-" * 40)


def main():
    """Main function to run the WAF rules importer."""
    parser = argparse.ArgumentParser(description="Import existing WAF rules into DynamoDB bot rules table")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported without making changes")
    parser.add_argument("--list-waf", action="store_true", help="List existing WAF rules")
    parser.add_argument("--list-bot", action="store_true", help="List existing bot rules in DynamoDB")
    parser.add_argument("--list-webacls", action="store_true", help="List all WebACLs")
    parser.add_argument("--profile", help="AWS profile to use")
    parser.add_argument("--table-name", help="DynamoDB bot rules table name")
    parser.add_argument("--web-acl-name", help="WAF WebACL name")
    parser.add_argument("--web-acl-scope", default="REGIONAL", choices=["REGIONAL", "CLOUDFRONT"], help="WAF scope")

    args = parser.parse_args()

    # Initialize importer
    importer = WAFRulesImporter(
        bot_rules_table_name=args.table_name,
        web_acl_name=args.web_acl_name,
        web_acl_scope=args.web_acl_scope,
        aws_profile=args.profile,
    )

    try:
        if args.list_webacls:
            importer.list_all_webacls()
        elif args.list_waf:
            importer.list_waf_rules()
        elif args.list_bot:
            importer.list_bot_rules()
        else:
            # Import WAF rules
            results = importer.import_waf_rules(dry_run=args.dry_run)

            if "error" in results:
                print(f"Error: {results['error']}")
                sys.exit(1)

            # Print results
            print("\nImport Results:")
            print(f"Total WAF rules: {results['total_waf_rules']}")
            print(f"Imported: {len(results['imported_rules'])}")
            print(f"Skipped: {len(results['skipped_rules'])}")
            print(f"Errors: {len(results['errors'])}")

            if results["imported_rules"]:
                print("\nImported rules:")
                for rule in results["imported_rules"]:
                    status = rule.get("status", "UNKNOWN")
                    print(f"  - {rule['rule_name']} ({rule['action']}, priority: {rule['priority']}) [{status}]")

            if results["skipped_rules"]:
                print("\nSkipped rules:")
                for rule in results["skipped_rules"]:
                    print(f"  - {rule['rule_name']}: {rule['reason']}")

            if results["errors"]:
                print("\nErrors:")
                for error in results["errors"]:
                    print(f"  - {error['rule_name']}: {error['error']}")

    except Exception as e:
        logger.error(f"Failed to run importer: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
