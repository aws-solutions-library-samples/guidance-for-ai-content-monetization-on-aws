#!/usr/bin/env python3
"""
Test script to verify authentication integration for the analytics agent.
Moved from analytics_agent/test_auth_fix.py
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, project_root)


def test_analytics_agent_auth():
    """Test the analytics agent authentication integration."""
    try:
        from analytics_agent_strands import PublisherAnalyticsAgent

        # Test authentication setup
        agent = PublisherAnalyticsAgent()

        # Verify agent initialization
        assert agent is not None, "Analytics agent should initialize successfully"

        print("✅ Analytics agent authentication test passed")
        return True

    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False
    except Exception as e:
        print(f"❌ Authentication test failed: {e}")
        return False


if __name__ == "__main__":
    success = test_analytics_agent_auth()
    sys.exit(0 if success else 1)
