"""
Pricing Version Manager Service

Handles versioned pricing rules with effective dates, historical tracking,
and transaction cost calculation using appropriate historical pricing.
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from services.taxonomy_manager import TaxonomyManager

logger = logging.getLogger(__name__)


class PricingVersionManager:
    """Manages versioned pricing rules and transaction cost calculations."""

    def __init__(
        self,
        region_name: str = "us-west-2",
        pricing_rules_table_name: str = None,
        pricing_transactions_table_name: str = None,
    ):
        """Initialize the pricing version manager.

        Args:
            region_name: AWS region name
            pricing_rules_table_name: Name of the pricing rules table
            pricing_transactions_table_name: Name of the pricing transactions table
        """
        self.dynamodb = boto3.resource("dynamodb", region_name=region_name)

        # Use provided table names or environment variables or defaults
        rules_table_name = pricing_rules_table_name or os.environ.get(
            "PRICING_RULES_TABLE_NAME", "cm-pricing-rules"
        )
        transactions_table_name = pricing_transactions_table_name or os.environ.get(
            "PRICING_TRANSACTIONS_TABLE_NAME", "cm-pricing-transactions"
        )

        self.pricing_rules_table = self.dynamodb.Table(rules_table_name)
        self.pricing_transactions_table = self.dynamodb.Table(transactions_table_name)

    def create_pricing_rule_version(
        self,
        rule_set_id: str,
        pricing_rules: Dict[str, Any],
        effective_date: str,
        created_by: str,
        change_reason: str,
        previous_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new version of pricing rules.

        Args:
            rule_set_id: Identifier for the rule set
            pricing_rules: The pricing configuration
            effective_date: Date when rules become effective (YYYY-MM-DD)
            created_by: User who created the version
            change_reason: Reason for the change
            previous_version: Previous version timestamp for audit trail

        Returns:
            Dict containing the created pricing rule version
        """
        try:
            now = datetime.now(timezone.utc)
            version_timestamp = now.isoformat()

            # Get current version number
            current_version = self._get_latest_version_number(rule_set_id)
            new_version = current_version + 1

            pricing_rule_item = {
                "rule_set_id": rule_set_id,
                "version_timestamp": version_timestamp,
                "effective_date": effective_date,
                "status": "draft",  # New versions start as draft
                "created_by": created_by,
                "created_at": version_timestamp,
                "pricing_rules": pricing_rules,
                "audit_trail": {
                    "version": new_version,
                    "change_reason": change_reason,
                    "previous_version": previous_version,
                },
            }

            # Store the new version
            self.pricing_rules_table.put_item(Item=pricing_rule_item)

            logger.info(f"Created pricing rule version {new_version} for rule set {rule_set_id}")
            return pricing_rule_item

        except ClientError as e:
            logger.error(f"Failed to create pricing rule version: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating pricing rule version: {e}")
            raise

    def activate_pricing_rule_version(self, rule_set_id: str, version_timestamp: str) -> Dict[str, Any]:
        """Activate a pricing rule version.

        Args:
            rule_set_id: Identifier for the rule set
            version_timestamp: Timestamp of the version to activate

        Returns:
            Dict containing the activated pricing rule version
        """
        try:
            # First, deactivate any currently active versions
            self._deactivate_current_versions(rule_set_id)

            # Activate the specified version
            response = self.pricing_rules_table.update_item(
                Key={"rule_set_id": rule_set_id, "version_timestamp": version_timestamp},
                UpdateExpression="SET #status = :status, activated_at = :activated_at",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "active",
                    ":activated_at": datetime.now(timezone.utc).isoformat(),
                },
                ReturnValues="ALL_NEW",
            )

            activated_item = response["Attributes"]
            logger.info(f"Activated pricing rule version {version_timestamp} for rule set {rule_set_id}")
            return activated_item

        except ClientError as e:
            logger.error(f"Failed to activate pricing rule version: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error activating pricing rule version: {e}")
            raise

    def get_active_pricing_rules(
        self, rule_set_id: str = "default-pricing", effective_date: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Get the active pricing rules for a given date.

        Args:
            rule_set_id: Identifier for the rule set
            effective_date: Date to get rules for (YYYY-MM-DD), defaults to today

        Returns:
            Dict containing active pricing rules or None if not found
        """
        try:
            if effective_date is None:
                effective_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            logger.info(f"Looking for active pricing rules: rule_set_id={rule_set_id}, effective_date={effective_date}")

            # First, try to get active rules for the specific rule set
            response = self.pricing_rules_table.query(
                KeyConditionExpression="rule_set_id = :rule_set_id",
                FilterExpression="#status = :status AND effective_date <= :date",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":rule_set_id": rule_set_id, ":status": "active", ":date": effective_date},
                ScanIndexForward=False,  # Get most recent first
                Limit=1,
            )

            logger.info(f"Query with date filter returned {len(response['Items'])} items")

            if response["Items"]:
                logger.info(
                    f"Found active pricing rule: {response['Items'][0]['rule_set_id']} - {response['Items'][0]['status']}"
                )
                return response["Items"][0]

            # Fallback: get any active rules for the rule set (ignore effective date)
            response = self.pricing_rules_table.query(
                KeyConditionExpression="rule_set_id = :rule_set_id",
                FilterExpression="#status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":rule_set_id": rule_set_id, ":status": "active"},
                ScanIndexForward=False,
                Limit=1,
            )

            logger.info(f"Fallback query returned {len(response['Items'])} items")

            if response["Items"]:
                logger.info(
                    f"Found active pricing rule (fallback): {response['Items'][0]['rule_set_id']} - {response['Items'][0]['status']}"
                )
                return response["Items"][0]

            # Final fallback: scan the table for any active rules (this is expensive but works)
            logger.warning("Using table scan as final fallback for active pricing rules")
            response = self.pricing_rules_table.scan(
                FilterExpression="rule_set_id = :rule_set_id AND #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":rule_set_id": rule_set_id, ":status": "active"},
                Limit=1,
            )

            logger.info(f"Table scan returned {len(response['Items'])} items")

            if response["Items"]:
                logger.info(
                    f"Found active pricing rule (scan): {response['Items'][0]['rule_set_id']} - {response['Items'][0]['status']}"
                )
                return response["Items"][0]

            logger.warning(f"No active pricing rules found for rule_set_id: {rule_set_id}")
            return None

        except ClientError as e:
            logger.error(f"Failed to get active pricing rules: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting active pricing rules: {e}")
            return None

    def get_historical_pricing_rules(
        self, transaction_timestamp: str, rule_set_id: str = "default-pricing"
    ) -> Optional[Dict[str, Any]]:
        """Get pricing rules that were active at a specific timestamp.

        Args:
            transaction_timestamp: ISO timestamp of the transaction
            rule_set_id: Identifier for the rule set

        Returns:
            Dict containing historical pricing rules or None if not found
        """
        try:
            # Convert timestamp to date for comparison
            transaction_date = datetime.fromisoformat(transaction_timestamp.replace("Z", "+00:00")).strftime("%Y-%m-%d")

            # Get all versions for the rule set
            response = self.pricing_rules_table.query(
                KeyConditionExpression="rule_set_id = :rule_set_id",
                ExpressionAttributeValues={":rule_set_id": rule_set_id},
                ScanIndexForward=True,  # Oldest first
            )

            # Find the version that was active at the transaction time
            active_version = None
            for version in response["Items"]:
                if version["effective_date"] <= transaction_date and version.get("status") == "active":
                    active_version = version

            return active_version

        except ClientError as e:
            logger.error(f"Failed to get historical pricing rules: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting historical pricing rules: {e}")
            return None

    def calculate_transaction_cost(
        self,
        content_type: str,
        access_type: str,
        transaction_timestamp: str,
        partner_id: str,
        content_id: str,
        rule_set_id: str = "default-pricing",
    ) -> Dict[str, Any]:
        """Calculate cost for a transaction using historical pricing.

        Args:
            content_type: Type of content (article, video, image, etc.)
            access_type: Type of access (preview, full, download)
            transaction_timestamp: ISO timestamp of the transaction
            partner_id: Partner making the request
            content_id: Content being accessed
            rule_set_id: Identifier for the rule set

        Returns:
            Dict containing cost calculation details
        """
        try:
            # Get historical pricing rules
            pricing_rules = self.get_historical_pricing_rules(transaction_timestamp, rule_set_id)

            if not pricing_rules:
                # Use default fallback pricing
                base_cost = self._get_default_cost(content_type, access_type)
                pricing_version = "default-fallback"
            else:
                base_cost = self._extract_cost_from_rules(pricing_rules, content_type, access_type)
                pricing_version = f"{rule_set_id}#{pricing_rules['version_timestamp']}"

            # Apply any multipliers or adjustments
            multiplier = self._get_cost_multiplier(partner_id, content_type)
            final_cost = base_cost * multiplier

            cost_calculation = {
                "base_cost": base_cost,
                "multiplier": multiplier,
                "final_cost": final_cost,
                "currency": "USD",
                "pricing_version": pricing_version,
                "calculation_timestamp": datetime.now(timezone.utc).isoformat(),
            }

            return cost_calculation

        except Exception as e:
            logger.error(f"Failed to calculate transaction cost: {e}")
            # Return default cost on error
            return {
                "base_cost": Decimal("0.01"),
                "multiplier": Decimal("1.0"),
                "final_cost": Decimal("0.01"),
                "currency": "USD",
                "pricing_version": "error-fallback",
                "calculation_timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(e),
            }

    def record_pricing_transaction(
        self,
        transaction_id: str,
        content_id: str,
        partner_id: str,
        access_type: str,
        content_type: str,
        cost_calculation: Dict[str, Any],
        transaction_timestamp: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record a pricing transaction for historical tracking.

        Args:
            transaction_id: Unique transaction identifier
            content_id: Content being accessed
            partner_id: Partner making the request
            access_type: Type of access
            content_type: Type of content
            cost_calculation: Cost calculation details
            transaction_timestamp: ISO timestamp of the transaction
            metadata: Additional transaction metadata

        Returns:
            Dict containing the recorded transaction
        """
        try:
            transaction_date = datetime.fromisoformat(transaction_timestamp.replace("Z", "+00:00")).strftime("%Y-%m-%d")

            transaction_item = {
                "transaction_date": transaction_date,
                "transaction_id": transaction_id,
                "transaction_timestamp": transaction_timestamp,
                "content_id": content_id,
                "partner_id": partner_id,
                "access_type": access_type,
                "content_type": content_type,
                "pricing_rule_version": cost_calculation.get("pricing_version"),
                "cost_calculation": cost_calculation,
                "metadata": metadata or {},
            }

            self.pricing_transactions_table.put_item(Item=transaction_item)

            logger.info(f"Recorded pricing transaction {transaction_id}")
            return transaction_item

        except ClientError as e:
            logger.error(f"Failed to record pricing transaction: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error recording pricing transaction: {e}")
            raise

    def get_pricing_history(self, rule_set_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pricing rule version history.

        Args:
            rule_set_id: Identifier for the rule set
            limit: Maximum number of versions to return

        Returns:
            List of pricing rule versions ordered by creation time
        """
        try:
            response = self.pricing_rules_table.query(
                KeyConditionExpression="rule_set_id = :rule_set_id",
                ExpressionAttributeValues={":rule_set_id": rule_set_id},
                ScanIndexForward=False,  # Most recent first
                Limit=limit,
            )

            return response["Items"]

        except ClientError as e:
            logger.error(f"Failed to get pricing history: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error getting pricing history: {e}")
            return []

    # ------------------------------------------------------------------
    # UI view helpers (single home for the mock/seed behavior + the
    # real/mock feature-flag decisions that previously lived inline in the
    # ui_backend handler). Relocated here so handlers/route modules stay thin
    # and contain no inline mock generators (Requirements 7.5, 7.6).
    # ------------------------------------------------------------------

    def get_pricing_history_view(self, rule_set_id: str, limit: Any, publisher_id: str) -> Dict[str, Any]:
        """Return the pricing-history view used by ``GET /pricing/history``.

        Real-data-only (wave0 Req 9): reads pricing history from DynamoDB and formats it
        for the UI. On failure the error propagates; the caller wraps the result in a
        success response. The mock seed/fallback was removed.
        """
        # Real-data-only: read pricing history from DynamoDB (wave0 Req 9 — mock fallback removed)
        limit = int(limit)
        history_data = self.get_pricing_history(rule_set_id, limit)

        formatted_history = []
        for item in history_data:
            formatted_item = {
                "rule_set_id": item.get("rule_set_id"),
                "version_timestamp": item.get("version_timestamp"),
                "effective_date": item.get("effective_date"),
                "status": item.get("status"),
                "created_by": item.get("created_by"),
                "created_at": item.get("created_at"),
                "pricing_rules": item.get("pricing_rules", {}),
                "audit_trail": {
                    "version": item.get("audit_trail", {}).get("version", 1),
                    "change_reason": item.get("audit_trail", {}).get("change_reason", "No reason provided"),
                    "previous_version": item.get("audit_trail", {}).get("previous_version"),
                },
            }
            formatted_history.append(formatted_item)

        pricing_history = {
            "history": formatted_history,
            "publisher_id": publisher_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"Real pricing history retrieved: {len(formatted_history)} items")
        return pricing_history

    def get_pricing_rules_view(
        self, rule_set_id: str, effective_date: Optional[str], created_by: str
    ) -> Optional[Dict[str, Any]]:
        """Return the pricing-rules view used by ``GET /pricing/rules``.

        Loads the active pricing rules and ALWAYS resolves IAB category multipliers to
        human-readable names via :class:`TaxonomyManager`, returning the required
        ``multipliers.iab_category_resolved`` block (SSOT #26): the raw ``iab_category`` map is
        preserved (additive) and every consumer (UI, agent tools) gets self-describing category
        names without a second call. Category-name resolution is a read-time join against
        ``cm-taxonomy``; the ``cm-pricing-rules`` table is unchanged. On no active rules -> ``None``
        (caller maps to 404). A taxonomy lookup miss degrades to ``"Category {id}"`` and never 5xxs.
        """
        try:
            active_rules = self.get_active_pricing_rules(rule_set_id=rule_set_id, effective_date=effective_date)
            if active_rules:
                resolved = self._resolve_category_names(active_rules)
                return {
                    "status": "success",
                    "pricing_rule": resolved,
                    "rule_set_id": rule_set_id,
                    "resolved_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as e:
            logger.error(f"Real pricing data failed: {str(e)}")

        # Real-data-only (wave0 Req 9): no active rules -> signal "not found" (caller maps to 404)
        return None

    def _resolve_category_names(self, active_rules: Dict[str, Any]) -> Dict[str, Any]:
        """Add ``multipliers.iab_category_resolved`` (name/full_path/tier_level) to a rules record.

        Read-time join against :class:`TaxonomyManager`; unknown IDs degrade to ``"Category {id}"``.
        The raw ``iab_category`` map is left intact (additive). Absorbs the former
        ``get_resolved_pricing_rules_view`` logic (SSOT #26 — single home, one endpoint).
        """
        resolved_rules = active_rules.copy()
        try:
            all_categories = TaxonomyManager().get_all_categories()
        except Exception as e:
            logger.error(f"Taxonomy resolution unavailable, emitting placeholder names: {str(e)}")
            all_categories = {}

        pricing_rules = resolved_rules.get("pricing_rules", {})
        multipliers = pricing_rules.get("multipliers", {}) if isinstance(pricing_rules, dict) else {}
        iab_category = multipliers.get("iab_category", {}) if isinstance(multipliers, dict) else {}

        resolved_category_multipliers = {}
        for category_id, multiplier in iab_category.items():
            category_info = all_categories.get(str(category_id))
            if category_info:
                resolved_category_multipliers[category_id] = {
                    "multiplier": multiplier,
                    "category_name": category_info["name"],
                    "full_path": category_info.get("full_path", category_info["name"]),
                    "tier_level": category_info.get("tier_level", 1),
                }
            else:
                resolved_category_multipliers[category_id] = {
                    "multiplier": multiplier,
                    "category_name": f"Category {category_id}",
                    "full_path": f"Category {category_id}",
                    "tier_level": 1,
                }

        # Always present the resolved block (required by the contract), even if empty.
        if isinstance(pricing_rules, dict):
            pricing_rules.setdefault("multipliers", {})
            resolved_rules["pricing_rules"]["multipliers"]["iab_category_resolved"] = resolved_category_multipliers
        return resolved_rules

    @staticmethod
    def calculate_pricing_estimate(content_items: List[Dict[str, Any]], publisher_id: str) -> Dict[str, Any]:
        """Compute the pricing estimate used by ``POST /pricing/calculate``.

        Relocated from the ui_backend handler's inline calculation so the
        domain logic lives in the Service_Layer (Requirement 7.5).
        """
        total_cost = 0.0
        breakdown = []

        for item in content_items:
            content_type = item.get("content_type", "article")
            quantity = item.get("quantity", 1)

            # Pricing logic
            unit_price = 0.05 if content_type == "article" else 0.25
            item_cost = unit_price * quantity
            total_cost += item_cost

            breakdown.append(
                {"content_type": content_type, "quantity": quantity, "unit_price": unit_price, "total_cost": item_cost}
            )

        return {
            "total_cost": total_cost,
            "currency": "USD",
            "breakdown": breakdown,
            "publisher_id": publisher_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _get_latest_version_number(self, rule_set_id: str) -> int:
        """Get the latest version number for a rule set."""
        try:
            response = self.pricing_rules_table.query(
                KeyConditionExpression="rule_set_id = :rule_set_id",
                ExpressionAttributeValues={":rule_set_id": rule_set_id},
                ScanIndexForward=False,
                Limit=1,
            )

            if response["Items"]:
                item = response["Items"][0]
                # Safely extract version number with fallback
                audit_trail = item.get("audit_trail", {})
                if isinstance(audit_trail, dict) and "version" in audit_trail:
                    return int(audit_trail["version"])
                else:
                    # Fallback: count all existing versions for this rule set
                    logger.warning(f"No version in audit_trail for {rule_set_id}, counting existing versions")
                    count_response = self.pricing_rules_table.query(
                        KeyConditionExpression="rule_set_id = :rule_set_id",
                        ExpressionAttributeValues={":rule_set_id": rule_set_id},
                        Select="COUNT",
                    )
                    return count_response.get("Count", 0)
            return 0

        except Exception as e:
            logger.error(f"Error getting latest version number for {rule_set_id}: {e}")
            # Final fallback: count all versions
            try:
                count_response = self.pricing_rules_table.query(
                    KeyConditionExpression="rule_set_id = :rule_set_id",
                    ExpressionAttributeValues={":rule_set_id": rule_set_id},
                    Select="COUNT",
                )
                return count_response.get("Count", 0)
            except Exception:
                return 0

    def _deactivate_current_versions(self, rule_set_id: str) -> None:
        """Deactivate all currently active versions for a rule set."""
        try:
            # Get all active versions
            response = self.pricing_rules_table.query(
                KeyConditionExpression="rule_set_id = :rule_set_id",
                FilterExpression="#status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":rule_set_id": rule_set_id, ":status": "active"},
            )

            # Deactivate each active version
            for item in response["Items"]:
                self.pricing_rules_table.update_item(
                    Key={"rule_set_id": item["rule_set_id"], "version_timestamp": item["version_timestamp"]},
                    UpdateExpression="SET #status = :status, deactivated_at = :deactivated_at",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":status": "inactive",
                        ":deactivated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

        except Exception as e:
            logger.error(f"Failed to deactivate current versions: {e}")
            raise

    def _extract_cost_from_rules(self, pricing_rules: Dict[str, Any], content_type: str, access_type: str) -> Decimal:
        """Extract cost from pricing rules based on content and access type."""
        try:
            rules = pricing_rules.get("pricing_rules", {})

            # Try per-access pricing first
            if access_type in ["full", "download"]:
                per_access = rules.get("per_access", {})
                if content_type in per_access:
                    return Decimal(str(per_access[content_type]))

            # Fallback to default pricing
            return self._get_default_cost(content_type, access_type)

        except Exception:
            return self._get_default_cost(content_type, access_type)

    def _get_default_cost(self, content_type: str, access_type: str) -> Decimal:
        """Get default cost for content and access type."""
        default_costs = {
            "article": Decimal("0.01"),
            "video": Decimal("0.05"),
            "image": Decimal("0.002"),
            "audio": Decimal("0.03"),
            "document": Decimal("0.015"),
        }

        base_cost = default_costs.get(content_type, Decimal("0.01"))

        # Adjust for access type
        if access_type == "preview":
            return base_cost * Decimal("0.1")  # 10% of full cost
        elif access_type == "download":
            return base_cost * Decimal("1.5")  # 150% of full cost

        return base_cost

    def _get_cost_multiplier(self, partner_id: str, content_type: str) -> Decimal:
        """Get cost multiplier based on partner and content type."""
        # This could be enhanced to look up partner-specific pricing
        # For now, return standard multiplier
        return Decimal("1.0")

    def fix_version_numbering(self, rule_set_id: str) -> Dict[str, Any]:
        """Fix version numbering for a rule set by reassigning sequential version numbers.

        Unrouted ops utility (SSOT #26): the former ``POST /pricing/fix-versions`` route was
        removed — this is a one-time data-repair for the ``audit_trail.version`` counter (which is
        display-only and self-heals via ``_get_latest_version_number``), not a runtime endpoint.
        Retained for manual/operational use only.

        Args:
            rule_set_id: Identifier for the rule set to fix

        Returns:
            Dict containing the fix results
        """
        try:
            logger.info(f"Starting version numbering fix for rule_set_id: {rule_set_id}")

            # Get all versions for this rule set, ordered by creation time
            response = self.pricing_rules_table.query(
                KeyConditionExpression="rule_set_id = :rule_set_id",
                ExpressionAttributeValues={":rule_set_id": rule_set_id},
                ScanIndexForward=True,  # Oldest first
            )

            items = response["Items"]
            logger.info(f"Found {len(items)} versions to fix")

            if not items:
                return {"status": "no_versions_found", "rule_set_id": rule_set_id}

            # Sort by version_timestamp to ensure proper ordering
            items.sort(key=lambda x: x["version_timestamp"])

            fixed_count = 0
            for index, item in enumerate(items):
                correct_version = index + 1  # Start from version 1
                current_version = item.get("audit_trail", {}).get("version", 0)

                if current_version != correct_version:
                    logger.info(
                        f"Fixing version: {current_version} -> {correct_version} for {item['version_timestamp']}"
                    )

                    # Update the version number in audit_trail
                    audit_trail = item.get("audit_trail", {})
                    audit_trail["version"] = correct_version

                    # Update the item in DynamoDB
                    self.pricing_rules_table.update_item(
                        Key={"rule_set_id": item["rule_set_id"], "version_timestamp": item["version_timestamp"]},
                        UpdateExpression="SET audit_trail = :audit_trail",
                        ExpressionAttributeValues={":audit_trail": audit_trail},
                    )
                    fixed_count += 1

            logger.info(f"Fixed {fixed_count} version numbers for rule_set_id: {rule_set_id}")

            return {
                "status": "success",
                "rule_set_id": rule_set_id,
                "total_versions": len(items),
                "fixed_versions": fixed_count,
            }

        except Exception as e:
            logger.error(f"Error fixing version numbering for {rule_set_id}: {e}")
            return {"status": "error", "rule_set_id": rule_set_id, "error": str(e)}
