#!/usr/bin/env python3
"""
Populate Pricing Data Script

This script populates the pricing rules and transactions tables with sample data
to ensure revenue calculations work with real database values.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


def populate_pricing_rules():
    """Populate pricing rules table with active pricing data"""

    # Initialize DynamoDB
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    pricing_rules_table = dynamodb.Table("cm-pricing-rules")

    # Current timestamp
    now = datetime.now(timezone.utc)
    version_timestamp = now.isoformat()
    effective_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")  # Effective 30 days ago

    # Sample pricing rules
    pricing_rule = {
        "rule_set_id": "default-pricing",
        "version_timestamp": version_timestamp,
        "effective_date": effective_date,
        "status": "active",
        "pricing_rules": {
            "per_access": {"article": "0.05", "report": "0.25", "video": "0.15", "document": "0.10", "default": "0.05"},
            "mcp_tools": {
                "search_content": "0.02",
                "get_content_details": "0.05",
                "access_content": "0.10",
                "list_categories": "0.01",
                "get_pricing_info": "0.01",
                "default": "0.02",
            },
            "subscription": {"basic": "10.00", "premium": "25.00", "enterprise": "100.00"},
        },
        "created_by": "system",
        "created_at": version_timestamp,
        "description": "Default pricing rules for content access and MCP tools",
    }

    try:
        pricing_rules_table.put_item(Item=pricing_rule)
        print(f"✅ Created pricing rule: {pricing_rule['rule_set_id']} v{version_timestamp}")
        return True
    except Exception as e:
        print(f"❌ Failed to create pricing rule: {str(e)}")
        return False


def populate_sample_transactions():
    """Populate pricing transactions table with sample transaction data"""

    # Initialize DynamoDB
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    pricing_transactions_table = dynamodb.Table("cm-pricing-transactions")

    # Generate sample transactions for the last 7 days
    base_date = datetime.now(timezone.utc) - timedelta(days=7)

    sample_transactions = []

    # Content types and their pricing
    content_types = {
        "article": Decimal("0.05"),
        "report": Decimal("0.25"),
        "video": Decimal("0.15"),
        "document": Decimal("0.10"),
    }

    # MCP tools and their pricing
    mcp_tools = {
        "search_content": Decimal("0.02"),
        "get_content_details": Decimal("0.05"),
        "access_content": Decimal("0.10"),
        "list_categories": Decimal("0.01"),
    }

    # Generate content access transactions
    for day in range(7):
        transaction_date = (base_date + timedelta(days=day)).strftime("%Y-%m-%d")

        # Generate 10-20 content transactions per day
        for i in range(10, 21):
            content_type = list(content_types.keys())[i % len(content_types)]
            content_id = f"{content_type[:3]}_{uuid.uuid4().hex[:8]}"

            transaction = {
                "transaction_date": transaction_date,
                "transaction_id": str(uuid.uuid4()),
                "transaction_timestamp": (base_date + timedelta(days=day, hours=i)).isoformat(),
                "content_id": content_id,
                "content_type": content_type,
                "amount_charged": str(content_types[content_type]),
                "currency": "USD",
                "access_method": "direct_api",
                "partner_id": f"partner_{(i % 5) + 1}",
                "pricing_rule_version": "default-pricing",
                "transaction_type": "content_access",
            }

            sample_transactions.append(transaction)

        # Generate 5-10 MCP tool transactions per day
        for i in range(5, 11):
            tool_name = list(mcp_tools.keys())[i % len(mcp_tools)]

            transaction = {
                "transaction_date": transaction_date,
                "transaction_id": str(uuid.uuid4()),
                "transaction_timestamp": (base_date + timedelta(days=day, hours=i + 12)).isoformat(),
                "tool_name": tool_name,
                "amount_charged": str(mcp_tools[tool_name]),
                "currency": "USD",
                "access_method": "mcp_server",
                "agent_id": f"agent_{(i % 3) + 1}",
                "pricing_rule_version": "default-pricing",
                "transaction_type": "mcp_tool_usage",
                "content_accessed_count": i % 5 + 1,
            }

            sample_transactions.append(transaction)

    # Batch write transactions
    success_count = 0
    error_count = 0

    # Write in batches of 25 (DynamoDB limit)
    for i in range(0, len(sample_transactions), 25):
        batch = sample_transactions[i : i + 25]

        try:
            with pricing_transactions_table.batch_writer() as batch_writer:
                for transaction in batch:
                    batch_writer.put_item(Item=transaction)

            success_count += len(batch)
            print(f"✅ Wrote batch of {len(batch)} transactions")

        except Exception as e:
            error_count += len(batch)
            print(f"❌ Failed to write batch: {str(e)}")

    print(f"📊 Transaction summary: {success_count} successful, {error_count} errors")
    return success_count > 0


def main():
    """Main function to populate pricing data"""
    print("🚀 Populating pricing data for real revenue calculations...")

    try:
        # Populate pricing rules
        print("\n📋 Creating pricing rules...")
        rules_success = populate_pricing_rules()

        # Populate sample transactions
        print("\n💰 Creating sample transactions...")
        transactions_success = populate_sample_transactions()

        if rules_success and transactions_success:
            print("\n✅ Successfully populated pricing data!")
            print("💡 Revenue calculations will now use real database values")
        else:
            print("\n⚠️  Some operations failed. Check the logs above.")

    except Exception as e:
        print(f"\n❌ Failed to populate pricing data: {str(e)}")
        return False

    return True


if __name__ == "__main__":
    main()
