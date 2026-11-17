"""
Unit tests for processing status Lambda function.

Tests the processing status functionality including real data integration
as part of task 2.2 - Replace mock processing status with real job data.
"""

import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add lambda directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../source/lambda"))

# NOTE (task 4.4): the vestigial ``lambda_handler`` shim was removed from ``processing_status.py``
# (Requirement 7.7 — the module is not CDK-wired and the ui_backend dispatcher now routes
# ``/processing/status`` to ``ui_backend/routes/processing.py``). Its handler-shim tests were removed
# with it; the retained data functions (``handle_processing_status``, ``get_real_processing_status``,
# and the metric helpers) are still exercised below.
from services.processing_jobs_service import (
    handle_processing_status,
    get_real_processing_status,
    calculate_queue_statistics,
    calculate_performance_metrics,
    generate_alerts,
    get_user_from_token,
)


class TestProcessingStatusUnit:
    """Unit tests for processing status functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.user_info = {"user_id": "test_user_001", "email": "test@example.com", "username": "test_user"}

        self.mock_event = {
            "httpMethod": "GET",
            "path": "/processing/status",
            "headers": {"Authorization": "Bearer test_token"},
        }

        self.mock_context = Mock()

    @patch("services.processing_jobs_service.get_real_processing_status")
    def test_handle_processing_status_real_data(self, mock_get_real):
        """Test handle_processing_status reads real data (wave0 Req 9 — real-only)."""
        mock_get_real.return_value = {
            "active_jobs": 5,
            "queued_jobs": 10,
            "completed_today": 50,
            "failed_today": 2,
            "system_status": "healthy",
            "services": {},
            "recent_jobs": [],
            "queue_statistics": {},
            "performance_metrics": {},
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        result = handle_processing_status(self.user_info)

        assert result["statusCode"] == 200
        response_body = json.loads(result["body"])
        assert response_body["active_jobs"] == 5
        assert response_body["queued_jobs"] == 10
        mock_get_real.assert_called_once()

    @patch("services.processing_jobs_service.get_real_processing_status")
    def test_handle_processing_status_error_returns_500(self, mock_get_real):
        """Real-source failure surfaces as a 500 error (wave0 Req 9.3 — no mock fallback)."""
        mock_get_real.side_effect = Exception("DynamoDB error")

        result = handle_processing_status(self.user_info)

        assert result["statusCode"] == 500


    def test_calculate_queue_statistics(self):
        """Test queue statistics calculation."""
        active_jobs = [
            {"job_type": "content_processing"},
            {"job_type": "content_reprocessing"},
            {"job_type": "content_processing"},
        ]

        queued_jobs = [
            {"job_type": "content_processing"},
            {"job_type": "bulk_operations"},
            {"job_type": "content_processing"},
        ]

        stats = calculate_queue_statistics(active_jobs, queued_jobs)

        assert stats["content_processing"]["processing"] == 2
        assert stats["content_processing"]["queued"] == 2
        assert stats["content_reprocessing"]["processing"] == 1
        assert stats["content_reprocessing"]["queued"] == 0
        assert stats["bulk_operations"]["processing"] == 0
        assert stats["bulk_operations"]["queued"] == 1

    def test_calculate_performance_metrics(self):
        """Test performance metrics calculation."""
        now = datetime.now(timezone.utc)
        two_hours_ago = now - timedelta(hours=2)  # Within last 24 hours
        yesterday = now - timedelta(days=1, hours=1)  # Just outside 24 hours
        week_ago = now - timedelta(days=7)

        completed_jobs = [
            {"created_at": now.isoformat(), "completed_at": (now + timedelta(seconds=30)).isoformat()},
            {
                "created_at": two_hours_ago.isoformat(),
                "completed_at": (two_hours_ago + timedelta(seconds=45)).isoformat(),
            },
            {"created_at": yesterday.isoformat(), "completed_at": (yesterday + timedelta(seconds=60)).isoformat()},
            {"created_at": week_ago.isoformat(), "completed_at": (week_ago + timedelta(seconds=60)).isoformat()},
        ]

        failed_jobs = [{"created_at": now.isoformat()}, {"created_at": week_ago.isoformat()}]

        metrics = calculate_performance_metrics(completed_jobs, failed_jobs)

        assert "last_24_hours" in metrics
        assert "last_7_days" in metrics
        assert metrics["last_24_hours"]["total_jobs"] == 3  # 2 completed + 1 failed in last 24h
        assert metrics["last_24_hours"]["successful_jobs"] == 2
        assert metrics["last_24_hours"]["failed_jobs"] == 1  # 1 failed in last 24h
        # For 7 days: 2 completed jobs within 7 days (now, two_hours_ago) + 2 failed jobs within 7 days (now, week_ago) = 4 total
        # The yesterday job is outside 24h but within 7 days, so it should be included
        # Actually, let me check: yesterday is 25 hours ago, week_ago is 7*24=168 hours ago
        # So within 7 days we should have: now, two_hours_ago, yesterday completed jobs (3) + now failed job (1) = 4 total
        assert metrics["last_7_days"]["total_jobs"] == 4  # Jobs within last 7 days
        assert metrics["last_7_days"]["successful_jobs"] == 3  # 3 completed jobs within 7 days
        assert metrics["last_7_days"]["failed_jobs"] == 1  # 1 failed job within 7 days (now)

    def test_generate_alerts_high_failures(self):
        """Test alert generation for high failure rate."""
        failed_today = [{"job_id": f"job_{i}"} for i in range(6)]  # 6 failures

        alerts = generate_alerts(failed_today, 3000)

        assert len(alerts) == 1
        assert alerts[0]["severity"] == "error"
        assert "High failure rate" in alerts[0]["message"]
        assert "6 jobs failed today" in alerts[0]["message"]

    def test_generate_alerts_high_processing_time(self):
        """Test alert generation for high processing time."""
        failed_today = []  # No failures

        alerts = generate_alerts(failed_today, 6000)  # High processing time

        assert len(alerts) == 1
        assert alerts[0]["severity"] == "warning"
        assert "Processing time above threshold" in alerts[0]["message"]
        assert "6000ms average" in alerts[0]["message"]

    def test_generate_alerts_no_issues(self):
        """Test alert generation with no issues."""
        failed_today = [{"job_id": "job_1"}]  # Only 1 failure

        alerts = generate_alerts(failed_today, 3000)  # Normal processing time

        assert len(alerts) == 0


if __name__ == "__main__":
    pytest.main([__file__])
