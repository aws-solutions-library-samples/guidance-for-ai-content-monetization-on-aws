"""
Unit tests for services/internal_logging_service.py (wave0 Phase 6, Req 19-21).

The service class writes usage/access logs to a DynamoDB analytics table and
publishes CloudWatch metrics. Tests drive the DynamoDB side with moto and stub
the module-level ``cloudwatch`` client so no real AWS calls are made. Covered:

- ``log_partner_usage`` — persists the row, publishes metrics, returns log_id;
  and the failure path re-raises when the table write fails.
- ``log_ui_test_usage`` — persists a not-billable row.
- ``log_api_access`` — billable vs. not-billable classification by auth_type.
- ``get_usage_logs`` — filter-expression assembly (partner_id / log_type /
  date range) and the failure path.
- ``_publish_usage_metrics`` / ``_publish_access_metrics`` — CloudWatch payloads,
  and that metric failures are swallowed (non-critical).
"""

from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

import services.internal_logging_service as ils
from services.internal_logging_service import InternalLoggingService

ANALYTICS_TABLE = "cm-analytics"


@pytest.fixture
def analytics_env(monkeypatch):
    monkeypatch.setenv("ANALYTICS_TABLE_NAME", ANALYTICS_TABLE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")


@pytest.fixture
def stub_cloudwatch(monkeypatch):
    cw = MagicMock()
    monkeypatch.setattr(ils, "cloudwatch", cw)
    return cw


def _make_analytics_table():
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    dynamodb.create_table(
        TableName=ANALYTICS_TABLE,
        KeySchema=[{"AttributeName": "log_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "log_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return dynamodb.Table(ANALYTICS_TABLE)


def _service():
    """Build a service whose analytics_table points at the moto table.

    The module-level ``dynamodb`` resource is created at import (before moto is
    active), so rebind the instance's table to a moto-backed resource.
    """
    svc = InternalLoggingService()
    svc.analytics_table = boto3.resource("dynamodb", region_name="us-west-2").Table(ANALYTICS_TABLE)
    return svc


# --------------------------------------------------------------------------- #
# log_partner_usage
# --------------------------------------------------------------------------- #
@mock_aws
def test_log_partner_usage_persists_and_publishes(analytics_env, stub_cloudwatch):
    table = _make_analytics_table()
    svc = _service()

    result = svc.log_partner_usage(
        partner_id="partner123", usage_data={"content_ids": ["a", "b"], "endpoint": "/query"}
    )

    assert result["status"] == "logged"
    assert result["log_id"].startswith("internal_log_")

    stored = table.get_item(Key={"log_id": result["log_id"]})["Item"]
    assert stored["partner_id"] == "partner123"
    assert stored["log_type"] == "partner_usage"
    assert stored["billing_status"] == "pending"

    # Metrics published: PartnerUsage + ContentAccessed (content_ids present).
    stub_cloudwatch.put_metric_data.assert_called_once()
    metric_names = {m["MetricName"] for m in stub_cloudwatch.put_metric_data.call_args.kwargs["MetricData"]}
    assert metric_names == {"PartnerUsage", "ContentAccessed"}


@mock_aws
def test_log_partner_usage_reraises_on_write_failure(analytics_env, stub_cloudwatch, monkeypatch):
    _make_analytics_table()
    svc = _service()
    failing = MagicMock()
    failing.put_item.side_effect = Exception("write failed")
    svc.analytics_table = failing

    with pytest.raises(Exception, match="write failed"):
        svc.log_partner_usage(partner_id="p", usage_data={})


# --------------------------------------------------------------------------- #
# log_ui_test_usage
# --------------------------------------------------------------------------- #
@mock_aws
def test_log_ui_test_usage_persists_not_billable(analytics_env, stub_cloudwatch):
    table = _make_analytics_table()
    svc = _service()

    result = svc.log_ui_test_usage(
        user_id="user1", publisher_id="pub1", test_usage_data={"test_query": "ai"}
    )

    assert result["status"] == "logged"
    assert result["log_id"].startswith("ui_test_log_")
    assert result["note"] == "UI test usage - not billable"
    stored = table.get_item(Key={"log_id": result["log_id"]})["Item"]
    assert stored["billing_status"] == "not_billable"
    assert stored["publisher_id"] == "pub1"


# --------------------------------------------------------------------------- #
# log_api_access
# --------------------------------------------------------------------------- #
@mock_aws
def test_log_api_access_partner_key_is_billable(analytics_env, stub_cloudwatch):
    table = _make_analytics_table()
    svc = _service()
    result = svc.log_api_access(
        endpoint="/query", user_id="partner123", auth_type="partner_key", metadata={"ip": "1.2.3.4"}
    )
    stored = table.get_item(Key={"log_id": result["log_id"]})["Item"]
    assert stored["billing_status"] == "billable"
    stub_cloudwatch.put_metric_data.assert_called_once()


@mock_aws
def test_log_api_access_cognito_is_not_billable(analytics_env, stub_cloudwatch):
    table = _make_analytics_table()
    svc = _service()
    result = svc.log_api_access(
        endpoint="/content", user_id="user1", auth_type="cognito", metadata={}
    )
    stored = table.get_item(Key={"log_id": result["log_id"]})["Item"]
    assert stored["billing_status"] == "not_billable"


# --------------------------------------------------------------------------- #
# get_usage_logs — filter assembly
# --------------------------------------------------------------------------- #
@mock_aws
def test_get_usage_logs_builds_filter_expression(analytics_env, stub_cloudwatch):
    _make_analytics_table()
    svc = _service()
    captured = {}

    def _capture_scan(**kwargs):
        captured.update(kwargs)
        return {"Items": [{"log_id": "x"}]}

    svc.analytics_table = MagicMock()
    svc.analytics_table.scan.side_effect = _capture_scan

    logs = svc.get_usage_logs(
        filters={"partner_id": "p1", "log_type": "partner_usage", "start_date": "2025-01-01T00:00:00Z"}, limit=25
    )

    assert logs == [{"log_id": "x"}]
    assert captured["Limit"] == 25
    assert "partner_id = :partner_id" in captured["FilterExpression"]
    assert "log_type = :log_type" in captured["FilterExpression"]
    assert "#timestamp >= :start_date" in captured["FilterExpression"]
    assert captured["ExpressionAttributeValues"][":partner_id"] == "p1"
    assert captured["ExpressionAttributeNames"] == {"#timestamp": "timestamp"}


@mock_aws
def test_get_usage_logs_reraises_on_scan_error(analytics_env, stub_cloudwatch):
    _make_analytics_table()
    svc = _service()
    svc.analytics_table = MagicMock()
    svc.analytics_table.scan.side_effect = Exception("scan boom")
    with pytest.raises(Exception, match="scan boom"):
        svc.get_usage_logs(filters={}, limit=10)


# --------------------------------------------------------------------------- #
# metric helpers — failures are swallowed
# --------------------------------------------------------------------------- #
@mock_aws
def test_publish_usage_metrics_swallows_failure(analytics_env, stub_cloudwatch):
    _make_analytics_table()
    svc = _service()
    stub_cloudwatch.put_metric_data.side_effect = Exception("cw down")
    # Should NOT raise — metric publication is non-critical.
    svc._publish_usage_metrics("partner123", {"content_ids": ["a"]})


@mock_aws
def test_publish_access_metrics_payload(analytics_env, stub_cloudwatch):
    _make_analytics_table()
    svc = _service()
    svc._publish_access_metrics("/query", "partner_key")
    kwargs = stub_cloudwatch.put_metric_data.call_args.kwargs
    assert kwargs["Namespace"] == "IAB/InternalLogging"
    assert kwargs["MetricData"][0]["MetricName"] == "APIAccess"
