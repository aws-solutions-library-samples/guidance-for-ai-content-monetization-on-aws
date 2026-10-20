#!/usr/bin/env python3
"""
Populate Recent Bot Access Data

This script populates the analytics DynamoDB table with recent bot access data
using current dates to ensure the analytics aggregation service can find it.
"""

import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


def populate_recent_bot_data():
    """
    Populate the analytics table with recent bot access data (last 7 days)
    """

    # Initialize DynamoDB
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

    # Get table name from environment or use default
    table_name = os.environ.get("ANALYTICS_TABLE_NAME", "cm-analytics")

    try:
        table = dynamodb.Table(table_name)
        print(f"📊 Populating recent bot data in table: {table_name}")
    except Exception as e:
        print(f"❌ Failed to connect to table {table_name}: {e}")
        return False

    # Generate data for the last 7 days (including today)
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=6)  # 7 days total including today

    # Bot types and their characteristics
    bot_types = [
        {"bot_id": "gptbot_001", "user_agent": "GPTBot/1.0", "access_method": "direct_api"},
        {"bot_id": "claude_web_001", "user_agent": "Claude-Web/1.0", "access_method": "direct_api"},
        {"bot_id": "chatgpt_user_001", "user_agent": "ChatGPT-User/1.0", "access_method": "direct_api"},
        {"bot_id": "mcp_agent_001", "user_agent": "MCP-Agent/1.0", "access_method": "mcp_server"},
        {"bot_id": "mcp_agent_002", "user_agent": "MCP-Agent/1.0", "access_method": "mcp_server"},
        {"bot_id": "research_bot_001", "user_agent": "ResearchBot/2.0", "access_method": "direct_api"},
        {"bot_id": "bedrock_agent", "user_agent": "Bedrock-Agent/1.0", "access_method": "mcp_server"},
    ]

    # Content IDs for test data
    content_ids = [
        "art_tech_001",
        "art_health_002",
        "art_finance_003",
        "rpt_market_001",
        "rpt_research_002",
        "rpt_analysis_003",
        "doc_guide_001",
        "doc_manual_002",
        "doc_spec_003",
    ]

    total_records = 0

    # Generate data for each day
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        print(f"📅 Generating recent bot data for {date_str}")

        # Generate 15-30 bot access records per day
        daily_records = random.randint(15, 30)

        for i in range(daily_records):
            # Random timestamp within the day
            random_hour = random.randint(0, 23)
            random_minute = random.randint(0, 59)
            random_second = random.randint(0, 59)

            timestamp = current_date.replace(hour=random_hour, minute=random_minute, second=random_second).isoformat()

            # Select random bot
            bot = random.choice(bot_types)

            # Generate bot access record
            content_accessed = random.sample(content_ids, random.randint(1, 4))

            bot_access_item = {
                "metric_date": date_str,
                "metric_type_timestamp": f"bot_access_{bot['bot_id']}_{timestamp}_{i}",
                "metric_type": "bot_access",
                "bot_id": bot["bot_id"],
                "access_method": bot["access_method"],
                "access_type": "search_query",
                "content_accessed": content_accessed,
                "query_details": {
                    "timestamp": timestamp,
                    "content_count": len(content_accessed),
                    "user_agent": bot["user_agent"],
                },
                "timestamp": timestamp,
                "revenue_impact": Decimal(str(len(content_accessed) * 0.10)),
                "value": 1,
                "metadata": {
                    "bot_id": bot["bot_id"],
                    "user_agent": bot["user_agent"],
                    "content_accessed": content_accessed,
                    "access_method": bot["access_method"],
                    "ip_address": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
                    "status": "allowed",
                    "query_type": random.choice(["search", "bulk_access"]),
                    "session_duration_ms": random.randint(1000, 30000),
                    "partner_id": f"partner_{random.randint(1, 3):03d}",
                },
            }

            try:
                table.put_item(Item=bot_access_item)
                total_records += 1
            except Exception as e:
                print(f"❌ Failed to insert bot access record: {e}")

        current_date += timedelta(days=1)

    print(f"\n✅ Successfully populated {total_records} recent bot access records")
    print(f"📊 Data covers last 7 days: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    print(f"🤖 Bot types: {len(bot_types)}")
    print(f"📄 Content items: {len(content_ids)}")
    print("\n🔄 You can now refresh the dashboard to see updated bot statistics")

    return True


if __name__ == "__main__":
    print("🚀 Starting recent bot data population...")

    # Set AWS profile if needed
    if "AWS_PROFILE" not in os.environ:
        os.environ["AWS_PROFILE"] = "genai"

    success = populate_recent_bot_data()

    if success:
        print("\n✅ Recent bot data population completed successfully!")
    else:
        print("\n❌ Recent bot data population failed!")
        exit(1)
