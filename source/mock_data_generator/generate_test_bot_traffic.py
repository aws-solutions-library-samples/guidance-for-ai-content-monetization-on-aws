#!/usr/bin/env python3
"""
Generate Test Bot Traffic

This script generates test bot traffic to populate WAF metrics for Sankey diagram visualization.
It makes requests with different bot user agents to trigger WAF rules.
"""

import random
import time
from datetime import datetime

import requests


def generate_test_bot_traffic(api_url: str, num_requests: int = 50):
    """
    Generate test bot traffic with various user agents

    Args:
        api_url: Base API URL to send requests to
        num_requests: Number of test requests to generate
    """

    # Bot user agents that should trigger different WAF rules
    bot_user_agents = [
        "GPTBot/1.0",  # Should be blocked
        "ChatGPT-User/1.0",  # Should be blocked
        "CCBot/2.0",  # Should be blocked
        "Claude-Web/1.0",  # Should be blocked
        "python-requests/2.28.1",  # Should be blocked (scraper)
        "Scrapy/2.5.1",  # Should be blocked (scraper)
        "Googlebot/2.1 (+http://www.google.com/bot.html)",  # Should be allowed
        "Bingbot/2.0 (+http://www.bing.com/bingbot.htm)",  # Should be allowed
        "facebookexternalhit/1.1",  # Should be allowed (social media)
        "Twitterbot/1.0",  # Should be allowed (social media)
    ]

    # Test endpoints to hit
    test_endpoints = ["/info", "/query", "/bid", "/content/search"]

    print(f"🤖 Generating {num_requests} test bot requests to {api_url}")
    print("This will populate WAF metrics for Sankey diagram visualization")

    successful_requests = 0
    blocked_requests = 0

    for i in range(num_requests):
        try:
            # Random user agent and endpoint
            user_agent = random.choice(bot_user_agents)
            endpoint = random.choice(test_endpoints)
            url = f"{api_url.rstrip('/')}{endpoint}"

            # Make request with bot user agent
            headers = {"User-Agent": user_agent, "Content-Type": "application/json"}

            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 403:
                blocked_requests += 1
                print(f"🚫 Blocked: {user_agent} -> {endpoint} (403)")
            else:
                successful_requests += 1
                print(f"✅ Allowed: {user_agent} -> {endpoint} ({response.status_code})")

            # Small delay between requests
            time.sleep(0.5)  # nosemgrep: arbitrary-sleep - simulate realistic bot traffic intervals

        except requests.exceptions.RequestException as e:
            print(f"❌ Request failed: {e}")

        # Progress indicator
        if (i + 1) % 10 == 0:
            print(f"Progress: {i + 1}/{num_requests} requests sent")

    print("\n📊 Test Traffic Summary:")
    print(f"   Total Requests: {num_requests}")
    print(f"   Successful: {successful_requests}")
    print(f"   Blocked: {blocked_requests}")
    print(f"   Success Rate: {(successful_requests / num_requests) * 100:.1f}%")
    print("\n⏰ Wait 5-10 minutes for CloudWatch metrics to update")
    print("   Then refresh the dashboard to see updated Sankey diagrams")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python generate_test_bot_traffic.py <API_URL>")
        print("Example: python generate_test_bot_traffic.py https://example123.execute-api.us-west-2.amazonaws.com/v1")
        sys.exit(1)

    api_url = sys.argv[1]
    generate_test_bot_traffic(api_url, 30)  # Generate 30 test requests
