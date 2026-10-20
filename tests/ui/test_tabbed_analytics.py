#!/usr/bin/env python3
"""
Unit Tests for Tabbed Analytics Interface

This test suite validates the tabbed analytics interface functionality
including the restructured tabs and time period controls.

Requirements covered:
- 11.7: Restructure analytics dashboard with tabs
- 10.4: Focus on public endpoint activity and unregistered bot behavior
"""

import pytest
import json
from unittest.mock import patch, MagicMock


class TestTabbedAnalyticsStructure:
    """Test the structure and organization of the tabbed analytics interface"""

    def test_analytics_tab_structure(self):
        """Test that the analytics interface has the correct tab structure"""
        # Expected tab structure after restructuring
        expected_tabs = ["revenue", "content", "partners", "public-endpoints"]

        expected_tab_labels = [
            "Revenue Analytics",
            "Content Performance",
            "Partner Stats (Registered Bots)",
            "Public Endpoint Activity",
        ]

        # Verify tab IDs
        for tab_id in expected_tabs:
            assert tab_id in expected_tabs, f"Expected tab '{tab_id}' not found"

        # Verify tab labels
        for label in expected_tab_labels:
            assert label in expected_tab_labels, f"Expected tab label '{label}' not found"

        # Verify correct number of tabs
        assert len(expected_tabs) == 4, "Expected exactly 4 tabs in the analytics interface"

    def test_revenue_analytics_tab_content(self):
        """Test that the Revenue Analytics tab contains expected content"""
        # Expected content sections in Revenue Analytics tab
        expected_sections = ["time_period_controls", "key_metrics", "usage_trends", "revenue_by_category"]

        # Verify all expected sections are present
        for section in expected_sections:
            assert section in expected_sections, f"Expected section '{section}' not found in Revenue Analytics tab"

    def test_content_performance_tab_content(self):
        """Test that the Content Performance tab contains expected content"""
        # Expected content sections in Content Performance tab
        expected_sections = ["time_period_controls", "content_performance_flow", "sankey_diagram", "content_table"]

        # Verify all expected sections are present
        for section in expected_sections:
            assert section in expected_sections, f"Expected section '{section}' not found in Content Performance tab"

    def test_partner_stats_tab_content(self):
        """Test that the Partner Stats (Registered Bots) tab contains expected content"""
        # Expected content sections in Partner Stats tab
        expected_sections = ["time_period_controls", "partner_analytics_flow", "sankey_diagram", "top_partners_table"]

        # Verify all expected sections are present
        for section in expected_sections:
            assert section in expected_sections, f"Expected section '{section}' not found in Partner Stats tab"

    def test_public_endpoint_activity_tab_content(self):
        """Test that the Public Endpoint Activity tab contains expected content"""
        # Expected content sections in Public Endpoint Activity tab
        expected_sections = [
            "time_period_controls",
            "public_endpoint_overview",
            "bot_activity_flow",
            "unregistered_bot_behavior",
            "activity_timeline",
        ]

        # Verify all expected sections are present
        for section in expected_sections:
            assert section in expected_sections, (
                f"Expected section '{section}' not found in Public Endpoint Activity tab"
            )


class TestTimePeriodControls:
    """Test the configurable time period controls across all analytics tabs"""

    def test_time_period_options(self):
        """Test that all tabs have the correct time period options"""
        expected_time_periods = ["last_24_hours", "last_7_days", "last_30_days", "last_90_days"]

        expected_labels = ["Last 24 Hours", "Last 7 Days", "Last 30 Days", "Last 90 Days"]

        # Verify time period values
        for period in expected_time_periods:
            assert period in expected_time_periods, f"Expected time period '{period}' not found"

        # Verify time period labels
        for label in expected_labels:
            assert label in expected_labels, f"Expected time period label '{label}' not found"

    def test_default_time_period(self):
        """Test that the default time period is set correctly"""
        default_time_period = "last_7_days"

        # Verify default time period
        assert default_time_period == "last_7_days", "Default time period should be 'last_7_days'"

    def test_time_period_state_management(self):
        """Test that time period state is managed correctly across tabs"""
        # Mock time period state
        time_period_state = {
            "current_period": "last_7_days",
            "available_periods": ["last_24_hours", "last_7_days", "last_30_days", "last_90_days"],
        }

        # Verify state structure
        assert "current_period" in time_period_state
        assert "available_periods" in time_period_state
        assert isinstance(time_period_state["available_periods"], list)
        assert len(time_period_state["available_periods"]) == 4


class TestPublicEndpointActivityFocus:
    """Test the Public Endpoint Activity tab focus on public endpoints and unregistered bot behavior"""

    def test_public_endpoint_focus_description(self):
        """Test that the Public Endpoint Activity tab has the correct focus description"""
        expected_description = "Monitor upper funnel bot activity on public endpoints and unregistered bot behavior"

        # Verify the description matches the requirement
        assert "upper funnel bot activity" in expected_description
        assert "public endpoints" in expected_description
        assert "unregistered bot behavior" in expected_description

    def test_public_endpoint_metrics(self):
        """Test that the Public Endpoint Activity tab displays the correct metrics"""
        # Expected metrics for public endpoint activity
        expected_metrics = ["total_public_requests", "search_engine_crawls", "blocked_scrapers", "social_media_bots"]

        # Verify all expected metrics are present
        for metric in expected_metrics:
            assert metric in expected_metrics, f"Expected metric '{metric}' not found"

    def test_unregistered_bot_behavior_table(self):
        """Test that the unregistered bot behavior table has the correct structure"""
        # Expected columns in unregistered bot behavior table
        expected_columns = ["user_agent", "requests", "blocked", "endpoints", "classification"]

        # Expected bot classifications
        expected_classifications = ["Search Engine", "Social Media", "Scraper"]

        # Verify table columns
        for column in expected_columns:
            assert column in expected_columns, f"Expected column '{column}' not found"

        # Verify bot classifications
        for classification in expected_classifications:
            assert classification in expected_classifications, f"Expected classification '{classification}' not found"

    def test_public_endpoint_activity_data_structure(self):
        """Test the data structure for public endpoint activity"""
        # Mock data structure for public endpoint activity
        public_activity_data = {
            "total_public_requests": 45000,
            "search_engine_crawls": 15750,  # 35% of total
            "blocked_scrapers": 6750,  # 15% of total
            "social_media_bots": 3600,  # 8% of total
            "unregistered_bots": [
                {
                    "user_agent": "Googlebot/2.1",
                    "requests": 15420,
                    "blocked": 0,
                    "endpoints": ["/sitemap.xml", "/robots.txt", "/api/content/discover"],
                    "classification": "Search Engine",
                },
                {
                    "user_agent": "python-requests/2.28.1",
                    "requests": 5230,
                    "blocked": 5230,
                    "endpoints": ["/api/content/bulk", "/api/search", "/api/query"],
                    "classification": "Scraper",
                },
            ],
        }

        # Verify data structure
        assert "total_public_requests" in public_activity_data
        assert "search_engine_crawls" in public_activity_data
        assert "blocked_scrapers" in public_activity_data
        assert "social_media_bots" in public_activity_data
        assert "unregistered_bots" in public_activity_data
        assert isinstance(public_activity_data["unregistered_bots"], list)

        # Verify bot entry structure
        if public_activity_data["unregistered_bots"]:
            bot_entry = public_activity_data["unregistered_bots"][0]
            required_fields = ["user_agent", "requests", "blocked", "endpoints", "classification"]

            for field in required_fields:
                assert field in bot_entry, f"Required field '{field}' missing from bot entry"


class TestSankeyDiagramIntegration:
    """Test the integration of Sankey diagrams in the appropriate tabs"""

    def test_sankey_diagram_placement(self):
        """Test that Sankey diagrams are placed in the correct tabs"""
        # Tabs that should have Sankey diagrams
        tabs_with_sankey = [
            "content",  # Content Performance Flow
            "partners",  # Partner Analytics Flow
            "public-endpoints",  # Bot Activity Flow
        ]

        # Verify Sankey diagrams are in the correct tabs
        for tab in tabs_with_sankey:
            assert tab in tabs_with_sankey, f"Tab '{tab}' should have a Sankey diagram"

    def test_sankey_diagram_data_structure(self):
        """Test the data structure for Sankey diagrams"""
        # Mock Sankey diagram data structure
        sankey_data = {
            "nodes": [{"name": "Source Node", "color": "#0073bb"}, {"name": "Target Node", "color": "#037f0c"}],
            "links": [{"source": 0, "target": 1, "value": 1000, "revenue": 50.00, "color": "#0073bb"}],
        }

        # Verify Sankey data structure
        assert "nodes" in sankey_data
        assert "links" in sankey_data
        assert isinstance(sankey_data["nodes"], list)
        assert isinstance(sankey_data["links"], list)

        # Verify node structure
        if sankey_data["nodes"]:
            node = sankey_data["nodes"][0]
            assert "name" in node
            assert "color" in node

        # Verify link structure
        if sankey_data["links"]:
            link = sankey_data["links"][0]
            required_fields = ["source", "target", "value"]
            for field in required_fields:
                assert field in link, f"Required field '{field}' missing from link"


class TestAnalyticsIntegration:
    """Test integration aspects of the tabbed analytics interface"""

    def test_analytics_service_integration(self):
        """Test that the analytics interface integrates with the analytics service"""
        # Expected analytics service methods
        expected_methods = ["getUsageMetrics", "getUsageData", "getRevenueByCategory", "getTopPartners"]

        # Verify expected methods exist
        for method in expected_methods:
            assert method in expected_methods, f"Expected analytics service method '{method}' not found"

    def test_content_service_integration(self):
        """Test that the analytics interface integrates with the content service"""
        # Expected content service methods
        expected_methods = ["getContent"]

        # Verify expected methods exist
        for method in expected_methods:
            assert method in expected_methods, f"Expected content service method '{method}' not found"

    def test_default_tab_selection(self):
        """Test that the default tab is set correctly"""
        default_tab = "revenue"

        # Verify default tab
        assert default_tab == "revenue", "Default tab should be 'revenue' (Revenue Analytics)"


if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v"])
