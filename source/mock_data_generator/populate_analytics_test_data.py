#!/usr/bin/env python3
"""
Populate Analytics Test Data

This script populates the analytics DynamoDB table with test bot access data
to enable proper Sankey diagram visualization and debugging.
"""

import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3


def populate_analytics_test_data():
    """
    Populate the analytics table with test bot access data
    """

    # Initialize DynamoDB
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

    # Get table name from environment or use default
    table_name = os.environ.get("ANALYTICS_TABLE_NAME", "cm-analytics")

    try:
        table = dynamodb.Table(table_name)
        print(f"📊 Populating analytics table: {table_name}")
    except Exception as e:
        print(f"❌ Failed to connect to table {table_name}: {e}")
        return False

    # Generate test data for the last 7 days
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=7)

    # Bot types and their characteristics
    bot_types = [
        {"bot_id": "gptbot_001", "user_agent": "GPTBot/1.0", "access_method": "direct_api"},
        {"bot_id": "claude_web_001", "user_agent": "Claude-Web/1.0", "access_method": "direct_api"},
        {"bot_id": "chatgpt_user_001", "user_agent": "ChatGPT-User/1.0", "access_method": "direct_api"},
        {"bot_id": "mcp_agent_001", "user_agent": "MCP-Agent/1.0", "access_method": "mcp_server"},
        {"bot_id": "mcp_agent_002", "user_agent": "MCP-Agent/1.0", "access_method": "mcp_server"},
        {"bot_id": "research_bot_001", "user_agent": "ResearchBot/2.0", "access_method": "direct_api"},
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

    # MCP tools
    mcp_tools = ["search_content", "get_content", "list_categories", "access_content"]

    total_records = 0

    # Generate data for each day
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        print(f"📅 Generating data for {date_str}")

        # Generate 10-50 bot access records per day
        daily_records = random.randint(10, 50)

        for _ in range(daily_records):
            # Random timestamp within the day
            random_hour = random.randint(0, 23)
            random_minute = random.randint(0, 59)
            random_second = random.randint(0, 59)

            timestamp = current_date.replace(hour=random_hour, minute=random_minute, second=random_second).isoformat()

            # Select random bot
            bot = random.choice(bot_types)

            # Generate bot access record
            content_accessed = random.sample(content_ids, random.randint(1, 3))

            bot_access_item = {
                "metric_date": date_str,
                "metric_type_timestamp": f"bot_access_{bot['bot_id']}_{timestamp}",
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
                },
            }

            try:
                table.put_item(Item=bot_access_item)
                total_records += 1
            except Exception as e:
                print(f"❌ Failed to insert bot access record: {e}")

            # Generate MCP tool usage if it's an MCP bot
            if bot["access_method"] == "mcp_server":
                tool_name = random.choice(mcp_tools)

                mcp_item = {
                    "metric_date": date_str,
                    "metric_type_timestamp": f"mcp_tool_usage_{timestamp}",
                    "metric_type": "mcp_tool_usage",
                    "tool_name": tool_name,
                    "agent_id": bot["bot_id"],
                    "usage_count": 1,
                    "content_accessed_count": len(content_accessed),
                    "value": len(content_accessed),
                    "timestamp": timestamp,
                    "metadata": {
                        "tool_name": tool_name,
                        "agent_id": bot["bot_id"],
                        "content_accessed": content_accessed,
                        "business_metrics": {
                            "content_accessed": content_accessed,
                            "response_size": random.randint(1, 10),
                        },
                    },
                }

                try:
                    table.put_item(Item=mcp_item)
                    total_records += 1
                except Exception as e:
                    print(f"❌ Failed to insert MCP record: {e}")

            # Generate general API request record
            api_request_item = {
                "metric_date": date_str,
                "metric_type_timestamp": f"api_requests_{timestamp}",
                "metric_type": "api_requests",
                "value": 1,
                "timestamp": timestamp,
                "metadata": {
                    "endpoint": "/query",
                    "user_id": bot["bot_id"],
                    "user_agent": bot["user_agent"],
                    "is_bot": True,
                    "is_mcp": bot["access_method"] == "mcp_server",
                },
            }

            try:
                table.put_item(Item=api_request_item)
                total_records += 1
            except Exception as e:
                print(f"❌ Failed to insert API request record: {e}")

            # Generate content access records
            for content_id in content_accessed:
                content_access_item = {
                    "metric_date": date_str,
                    "metric_type_timestamp": f"content_access_{timestamp}_{content_id}",
                    "metric_type": "content_access",
                    "value": 1,
                    "timestamp": timestamp,
                    "metadata": {
                        "content_id": content_id,
                        "access_type": "query",
                        "requester_info": {
                            "user_agent": bot["user_agent"],
                            "user_id": bot["bot_id"],
                            "authentication_method": "api_key",
                        },
                    },
                }

                try:
                    table.put_item(Item=content_access_item)
                    total_records += 1
                except Exception as e:
                    print(f"❌ Failed to insert content access record: {e}")

        current_date += timedelta(days=1)

    print(f"\n✅ Successfully populated {total_records} analytics records")
    print(f"📊 Data covers {(end_date - start_date).days + 1} days")
    print(f"🤖 Bot types: {len(bot_types)}")
    print(f"📄 Content items: {len(content_ids)}")
    print("\n🔄 You can now refresh the dashboard to see updated bot statistics")

    return True


if __name__ == "__main__":
    print("🚀 Starting analytics test data population...")

    # Set AWS profile if needed
    if "AWS_PROFILE" not in os.environ:
        os.environ["AWS_PROFILE"] = "genai"

    success = populate_analytics_test_data()

    if success:
        print("\n✅ Analytics test data population completed successfully!")
    else:
        print("\n❌ Analytics test data population failed!")
        exit(1)
