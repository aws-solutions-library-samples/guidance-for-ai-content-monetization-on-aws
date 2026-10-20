#!/usr/bin/env python3
"""
Generate Simple Bot Data

This script generates bot access data in the correct format expected by
the analytics aggregation lambda (regular Python objects, not DynamoDB format).
"""

import json
import os
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


def generate_simple_bot_data():
    """
    Generate simple bot access data in the correct format
    """

    print("🚀 Starting simple bot data generation...")

    # Initialize DynamoDB
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

    # Get table name
    table_name = "cm-analytics"

    try:
        table = dynamodb.Table(table_name)
        print(f"📊 Using analytics table: {table_name}")
    except Exception as e:
        print(f"❌ Failed to connect to table {table_name}: {e}")
        return False

    # Clear existing bot_access data first
    print("\n🧹 Clearing existing bot_access data...")
    try:
        # Scan for bot_access records
        response = table.scan(FilterExpression="metric_type = :mt", ExpressionAttributeValues={":mt": "bot_access"})

        items_to_delete = response.get("Items", [])
        if items_to_delete:
            print(f"🗑️  Deleting {len(items_to_delete)} existing bot_access records...")

            with table.batch_writer() as batch:
                for item in items_to_delete:
                    batch.delete_item(
                        Key={"metric_date": item["metric_date"], "metric_type_timestamp": item["metric_type_timestamp"]}
                    )
            print("✅ Existing bot_access data cleared")
        else:
            print("ℹ️  No existing bot_access data to clear")

    except Exception as e:
        print(f"⚠️  Warning: Could not clear existing data: {e}")

    # Generate data for the last 7 days
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=6)  # 7 days total

    # Bot types
    bot_types = [
        {"bot_id": "gptbot_001", "user_agent": "GPTBot/1.0", "access_method": "direct_api"},
        {"bot_id": "claude_web_001", "user_agent": "Claude-Web/1.0", "access_method": "direct_api"},
        {"bot_id": "mcp_agent_001", "user_agent": "MCP-Agent/1.0", "access_method": "mcp_server"},
        {"bot_id": "bedrock_agent", "user_agent": "Bedrock-Agent/1.0", "access_method": "mcp_server"},
    ]

    # Content IDs
    content_ids = ["art_tech_001", "art_health_002", "rpt_market_001", "doc_guide_001"]

    total_records = 0

    # Generate data for each day
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        print(f"📅 Generating bot data for {date_str}")

        # Generate 5-15 bot access records per day
        daily_records = random.randint(5, 15)

        for i in range(daily_records):
            # Random timestamp within the day
            random_hour = random.randint(0, 23)
            random_minute = random.randint(0, 59)
            random_second = random.randint(0, 59)

            timestamp = current_date.replace(hour=random_hour, minute=random_minute, second=random_second).isoformat()

            # Select random bot
            bot = random.choice(bot_types)

            # Generate content accessed
            content_accessed = random.sample(content_ids, random.randint(1, 3))

            # Create bot access record in the format expected by analytics aggregation
            bot_access_item = {
                "metric_date": date_str,
                "metric_type_timestamp": f"bot_access_{bot['bot_id']}_{timestamp}_{i}",
                "metric_type": "bot_access",
                "bot_id": bot["bot_id"],  # Direct string, not DynamoDB format
                "access_method": bot["access_method"],
                "content_accessed": content_accessed,  # Direct list, not DynamoDB format
                "timestamp": timestamp,
                "value": 1,
                "metadata": {
                    "bot_id": bot["bot_id"],
                    "user_agent": bot["user_agent"],
                    "access_method": bot["access_method"],
                    "ip_address": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
                    "status": "allowed",
                },
            }

            try:
                table.put_item(Item=bot_access_item)
                total_records += 1
            except Exception as e:
                print(f"❌ Failed to insert bot access record: {e}")

        current_date += timedelta(days=1)

    print(f"\n✅ Successfully generated {total_records} bot access records")
    print(f"📊 Data covers last 7 days: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    print(f"🤖 Bot types: {len(bot_types)}")
    print(f"📄 Content items: {len(content_ids)}")
    print("\n🔄 You can now refresh the dashboard to see updated bot statistics")

    return True


if __name__ == "__main__":
    print("🚀 Starting simple bot data generation...")

    # Set AWS profile if needed
    if "AWS_PROFILE" not in os.environ:
        os.environ["AWS_PROFILE"] = "genai"

    success = generate_simple_bot_data()

    if success:
        print("\n✅ Simple bot data generation completed successfully!")
    else:
        print("\n❌ Simple bot data generation failed!")
        exit(1)
