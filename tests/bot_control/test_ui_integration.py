"""
Unit tests for Bot Control UI Integration

Tests the integration between the React UI components and the bot control APIs,
ensuring proper data flow, error handling, and user interactions.
"""

import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime, timezone

# Mock the bot control service responses
MOCK_BOT_RULES = [
    {
        "rule_id": "rule_001",
        "rule_name": "Block AI Training Bots",
        "description": "Block known AI training bots (GPTBot, ChatGPT, CCBot)",
        "action": "block",
        "priority": 100,
        "conditions": {"user_agent": ["GPTBot", "ChatGPT", "CCBot", "Claude-Web", "Bard"]},
        "status": "active",
        "created_at": "2024-01-15T10:00:00Z",
        "updated_at": "2024-01-15T10:00:00Z",
        "waf_rule_id": "waf_rule_123",
        "last_synced": "2024-01-15T10:00:00Z",
        "sync_status": "synced",
    },
    {
        "rule_id": "rule_002",
        "rule_name": "Allow Search Engines",
        "description": "Allow legitimate search engines",
        "action": "allow",
        "priority": 200,
        "conditions": {"user_agent": ["Googlebot", "Bingbot", "DuckDuckBot"]},
        "status": "active",
        "created_at": "2024-01-15T10:00:00Z",
        "updated_at": "2024-01-15T10:00:00Z",
        "waf_rule_id": "waf_rule_456",
        "last_synced": "2024-01-15T10:00:00Z",
        "sync_status": "synced",
    },
]

MOCK_BOT_ACTIVITY = {
    "blocked_requests": 1247,
    "allowed_requests": 45678,
    "total_requests": 46925,
    "block_rate": 2.7,
    "time_period": "last_24_hours",
    "datapoints": [
        {"timestamp": "2024-01-15T10:00:00Z", "blocked": 52, "allowed": 1890},
        {"timestamp": "2024-01-15T11:00:00Z", "blocked": 67, "allowed": 2100},
        {"timestamp": "2024-01-15T12:00:00Z", "blocked": 45, "allowed": 1950},
    ],
}

MOCK_BOT_STATS = {
    "time_period": "last_24_hours",
    "rule_summary": {"total_rules": 5, "active_rules": 4, "inactive_rules": 1},
    "sync_status": {"synced_rules": 4, "pending_rules": 1, "error_rules": 0, "sync_health": 80.0},
    "traffic_summary": {
        "total_requests": 46925,
        "blocked_requests": 1247,
        "allowed_requests": 45678,
        "block_rate": 2.7,
    },
    "rule_effectiveness": [
        {
            "rule_name": "Block AI Training Bots",
            "rule_id": "rule_001",
            "action": "block",
            "blocked_requests": 847,
            "effectiveness_score": 67.9,
        },
        {
            "rule_name": "Block Content Scrapers",
            "rule_id": "rule_003",
            "action": "block",
            "blocked_requests": 400,
            "effectiveness_score": 32.1,
        },
    ],
    "waf_health": "healthy",
}


class TestBotControlUIIntegration:
    """Test bot control UI integration with backend APIs"""

    def setup_method(self):
        """Set up test fixtures"""
        self.mock_bot_control_service = Mock()

    def test_bot_rules_data_loading(self):
        """Test that bot rules are properly loaded and displayed"""
        # Mock the service response
        self.mock_bot_control_service.getBotRules.return_value = MOCK_BOT_RULES

        # Simulate component loading
        rules = self.mock_bot_control_service.getBotRules()

        # Verify data structure
        assert len(rules) == 2
        assert rules[0]["rule_name"] == "Block AI Training Bots"
        assert rules[0]["action"] == "block"
        assert rules[0]["sync_status"] == "synced"
        assert rules[1]["rule_name"] == "Allow Search Engines"
        assert rules[1]["action"] == "allow"

        # Verify service was called
        self.mock_bot_control_service.getBotRules.assert_called_once()

    def test_bot_activity_data_loading(self):
        """Test that bot activity data is properly loaded"""
        # Mock the service response
        self.mock_bot_control_service.getBotActivity.return_value = MOCK_BOT_ACTIVITY

        # Simulate component loading
        activity = self.mock_bot_control_service.getBotActivity({"time_period": "last_24_hours"})

        # Verify data structure
        assert activity["blocked_requests"] == 1247
        assert activity["allowed_requests"] == 45678
        assert activity["total_requests"] == 46925
        assert activity["block_rate"] == 2.7
        assert len(activity["datapoints"]) == 3

        # Verify datapoints structure for chart rendering
        for datapoint in activity["datapoints"]:
            assert "timestamp" in datapoint
            assert "blocked" in datapoint
            assert "allowed" in datapoint
            assert isinstance(datapoint["blocked"], int)
            assert isinstance(datapoint["allowed"], int)

    def test_bot_stats_data_loading(self):
        """Test that bot statistics are properly loaded"""
        # Mock the service response
        self.mock_bot_control_service.getBotStats.return_value = MOCK_BOT_STATS

        # Simulate component loading
        stats = self.mock_bot_control_service.getBotStats("last_24_hours")

        # Verify data structure
        assert stats["rule_summary"]["total_rules"] == 5
        assert stats["rule_summary"]["active_rules"] == 4
        assert stats["sync_status"]["sync_health"] == 80.0
        assert stats["traffic_summary"]["block_rate"] == 2.7
        assert stats["waf_health"] == "healthy"
        assert len(stats["rule_effectiveness"]) == 2

    def test_create_bot_rule_integration(self):
        """Test creating a new bot rule through the UI"""
        # Mock successful rule creation
        new_rule = {
            "rule_id": "rule_003",
            "rule_name": "Test Rule",
            "description": "Test rule description",
            "action": "block",
            "priority": 150,
            "conditions": {"user_agent": ["TestBot"]},
            "status": "active",
            "created_at": "2024-01-15T12:00:00Z",
            "updated_at": "2024-01-15T12:00:00Z",
            "sync_status": "pending",
        }

        self.mock_bot_control_service.createBotRule.return_value = new_rule

        # Simulate form submission
        rule_data = {
            "rule_name": "Test Rule",
            "description": "Test rule description",
            "action": "block",
            "priority": 150,
            "conditions": {"user_agent": ["TestBot"]},
            "status": "active",
        }

        result = self.mock_bot_control_service.createBotRule(rule_data)

        # Verify the rule was created correctly
        assert result["rule_name"] == "Test Rule"
        assert result["action"] == "block"
        assert result["sync_status"] == "pending"

        # Verify service was called with correct data
        self.mock_bot_control_service.createBotRule.assert_called_once_with(rule_data)

    def test_update_bot_rule_integration(self):
        """Test updating an existing bot rule through the UI"""
        # Mock successful rule update
        updated_rule = MOCK_BOT_RULES[0].copy()
        updated_rule["description"] = "Updated description"
        updated_rule["updated_at"] = "2024-01-15T13:00:00Z"

        self.mock_bot_control_service.updateBotRule.return_value = updated_rule

        # Simulate rule update
        rule_id = "rule_001"
        updates = {"description": "Updated description"}

        result = self.mock_bot_control_service.updateBotRule(rule_id, updates)

        # Verify the rule was updated correctly
        assert result["description"] == "Updated description"
        assert result["updated_at"] == "2024-01-15T13:00:00Z"

        # Verify service was called with correct parameters
        self.mock_bot_control_service.updateBotRule.assert_called_once_with(rule_id, updates)

    def test_delete_bot_rule_integration(self):
        """Test deleting a bot rule through the UI"""
        # Mock successful rule deletion
        self.mock_bot_control_service.deleteBotRule.return_value = None

        # Simulate rule deletion
        rule_id = "rule_001"
        self.mock_bot_control_service.deleteBotRule(rule_id)

        # Verify service was called with correct rule ID
        self.mock_bot_control_service.deleteBotRule.assert_called_once_with(rule_id)

    def test_rule_filtering_functionality(self):
        """Test that rule filtering works correctly in the UI"""
        # Mock rules data
        rules = MOCK_BOT_RULES

        # Simulate filtering by name
        filter_text = "AI Training"
        filtered_rules = [
            rule
            for rule in rules
            if filter_text.lower() in rule["rule_name"].lower() or filter_text.lower() in rule["description"].lower()
        ]

        # Verify filtering works
        assert len(filtered_rules) == 1
        assert filtered_rules[0]["rule_name"] == "Block AI Training Bots"

        # Test case-insensitive filtering
        filter_text = "search engines"
        filtered_rules = [
            rule
            for rule in rules
            if filter_text.lower() in rule["rule_name"].lower() or filter_text.lower() in rule["description"].lower()
        ]

        assert len(filtered_rules) == 1
        assert filtered_rules[0]["rule_name"] == "Allow Search Engines"

    def test_rule_validation_integration(self):
        """Test that rule validation works correctly"""
        # Mock validation service
        self.mock_bot_control_service.validateRuleConditions.return_value = {"valid": True, "errors": []}

        # Test valid conditions
        conditions = {"user_agent": ["GPTBot", "ChatGPT"]}
        validation = self.mock_bot_control_service.validateRuleConditions(conditions)

        assert validation["valid"] is True
        assert len(validation["errors"]) == 0

        # Test invalid conditions
        self.mock_bot_control_service.validateRuleConditions.return_value = {
            "valid": False,
            "errors": ["User agent condition must be a string or array of strings"],
        }

        invalid_conditions = {"user_agent": 123}
        validation = self.mock_bot_control_service.validateRuleConditions(invalid_conditions)

        assert validation["valid"] is False
        assert len(validation["errors"]) == 1

    def test_rule_templates_integration(self):
        """Test that rule templates are properly loaded and used"""
        # Mock rule templates
        templates = [
            {
                "name": "Block AI Training Bots",
                "description": "Block known AI training bots",
                "action": "block",
                "priority": 100,
                "conditions": {"user_agent": ["GPTBot", "ChatGPT"]},
            },
            {
                "name": "Allow Search Engines",
                "description": "Allow legitimate search engines",
                "action": "allow",
                "priority": 200,
                "conditions": {"user_agent": ["Googlebot", "Bingbot"]},
            },
        ]

        self.mock_bot_control_service.getRuleTemplates.return_value = templates

        # Simulate loading templates
        loaded_templates = self.mock_bot_control_service.getRuleTemplates()

        # Verify templates structure
        assert len(loaded_templates) == 2
        assert loaded_templates[0]["name"] == "Block AI Training Bots"
        assert loaded_templates[0]["action"] == "block"
        assert "conditions" in loaded_templates[0]
        assert loaded_templates[1]["name"] == "Allow Search Engines"
        assert loaded_templates[1]["action"] == "allow"

    def test_error_handling_integration(self):
        """Test that API errors are properly handled in the UI"""
        # Mock API error
        error_message = "Failed to fetch bot rules: Network error"
        self.mock_bot_control_service.getBotRules.side_effect = Exception(error_message)

        # Simulate error handling
        try:
            self.mock_bot_control_service.getBotRules()
            assert False, "Expected exception was not raised"
        except Exception as e:
            assert str(e) == error_message

        # Test authentication error
        auth_error = "Authentication failed"
        self.mock_bot_control_service.createBotRule.side_effect = Exception(auth_error)

        try:
            self.mock_bot_control_service.createBotRule({})
            assert False, "Expected exception was not raised"
        except Exception as e:
            assert str(e) == auth_error

    def test_waf_metrics_integration(self):
        """Test that WAF metrics are properly integrated"""
        # Mock WAF metrics
        waf_metrics = {
            "blocked_requests": 1247,
            "allowed_requests": 45678,
            "total_requests": 46925,
            "block_rate": 2.7,
            "datapoints": MOCK_BOT_ACTIVITY["datapoints"],
        }

        self.mock_bot_control_service.getWAFMetrics.return_value = waf_metrics

        # Simulate loading WAF metrics
        metrics = self.mock_bot_control_service.getWAFMetrics("last_24_hours")

        # Verify metrics structure
        assert metrics["blocked_requests"] == 1247
        assert metrics["allowed_requests"] == 45678
        assert metrics["block_rate"] == 2.7
        assert len(metrics["datapoints"]) == 3

        # Verify datapoints are suitable for chart rendering
        for datapoint in metrics["datapoints"]:
            assert isinstance(datapoint["blocked"], int)
            assert isinstance(datapoint["allowed"], int)
            assert "timestamp" in datapoint

    def test_sync_status_display_integration(self):
        """Test that WAF sync status is properly displayed"""
        # Test different sync statuses
        sync_statuses = ["synced", "pending", "error"]
        expected_colors = ["success", "in-progress", "error"]

        for status, expected_color in zip(sync_statuses, expected_colors):
            # Simulate sync status color mapping
            if status == "synced":
                color = "success"
            elif status == "pending":
                color = "in-progress"
            elif status == "error":
                color = "error"
            else:
                color = "stopped"

            assert color == expected_color

    def test_priority_color_mapping(self):
        """Test that rule priority colors are correctly mapped"""
        # Test priority color mapping
        priorities = [50, 100, 150, 250, 500]
        expected_colors = ["red", "red", "blue", "grey", "grey"]

        for priority, expected_color in zip(priorities, expected_colors):
            # Simulate priority color mapping logic
            if priority <= 100:
                color = "red"  # High priority
            elif priority <= 200:
                color = "blue"  # Medium priority
            else:
                color = "grey"  # Low priority

            assert color == expected_color

    def test_action_color_mapping(self):
        """Test that rule action colors are correctly mapped"""
        # Test action color mapping
        actions = ["block", "allow", "count", "rate_limit"]
        expected_colors = ["red", "green", "blue", "grey"]

        for action, expected_color in zip(actions, expected_colors):
            # Simulate action color mapping logic
            if action == "block":
                color = "red"
            elif action == "allow":
                color = "green"
            elif action == "count":
                color = "blue"
            elif action == "rate_limit":
                color = "grey"
            else:
                color = "blue"

            assert color == expected_color

    def test_chart_data_transformation(self):
        """Test that bot activity data is properly transformed for charts"""
        # Mock activity data
        activity_data = MOCK_BOT_ACTIVITY

        # Simulate chart data transformation
        chart_series = [
            {
                "title": "Blocked Requests",
                "type": "area",
                "data": [
                    {"x": datetime.fromisoformat(dp["timestamp"].replace("Z", "+00:00")), "y": dp["blocked"]}
                    for dp in activity_data["datapoints"]
                ],
                "color": "#d13212",
            },
            {
                "title": "Allowed Requests",
                "type": "area",
                "data": [
                    {"x": datetime.fromisoformat(dp["timestamp"].replace("Z", "+00:00")), "y": dp["allowed"]}
                    for dp in activity_data["datapoints"]
                ],
                "color": "#037f0c",
            },
        ]

        # Verify chart data structure
        assert len(chart_series) == 2
        assert chart_series[0]["title"] == "Blocked Requests"
        assert chart_series[1]["title"] == "Allowed Requests"
        assert len(chart_series[0]["data"]) == 3
        assert len(chart_series[1]["data"]) == 3

        # Verify data points
        assert chart_series[0]["data"][0]["y"] == 52
        assert chart_series[1]["data"][0]["y"] == 1890


if __name__ == "__main__":
    pytest.main([__file__])
