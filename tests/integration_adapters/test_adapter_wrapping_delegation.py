"""Wrapping/delegation + read-only ``test()`` unit tests for the first adapters
(integration-adapters, GitLab #35, task 5.4).

These are EXAMPLE-class tests (design "Testing Strategy" -> Example/unit tests: "Wrapping/
delegation" and "``test()`` is read-only per adapter") — plain pytest with mocks/fakes, no live
AWS. They complement the property tests in this directory (which cover Properties 1/2/6).

For each of the first adapters this asserts two things:

  1. **Implements + delegates (Requirements 3.1, 3.2, 3.3).** The adapter IMPLEMENTS its declared
     ``runtime_checkable`` Port (``isinstance`` against the Protocol) AND DELEGATES its primary
     operation to the wrapped service — the wrapped service's method is actually invoked (asserted
     via a mock/fake standing in for the wrapped service).

  2. **``test()`` is read-only (Requirements 6.5, 7.3).** Against a fake client/service, ``test()``
     performs ONLY read/describe calls and emits NO write/push/meter/listing-change:
       * WAF adapter -> describes the WebACL (``_get_web_acl_info``), NEVER ``update_web_acl``.
       * telemetry adapters -> ``list_metrics`` / ``Table.load`` only, and emit NO usage event
         (no ``log_access_attempt`` / ``record_content_access``).
       * marketplace adapter -> a benign read (``list_licenses``), NO create/update/delete.

Adapters covered (the three the wrapping tasks 5.1-5.3 built):
  * ``integrations.access.aws_waf.AwsWafAccessPolicyAdapter``          (AccessPolicyPort)
  * ``integrations.telemetry.cloudwatch.CloudWatchUsageSink``          (UsageSinkPort)
  * ``integrations.telemetry.dynamo_analytics.DynamoAnalyticsUsageSink`` (UsageSinkPort)
  * ``integrations.marketplace.internal_dynamo.InternalDynamoLicenseResolver`` (LicenseResolverPort)

Placement: gated unit tier (``tests/integration_adapters/``, ``-m "not integration"``);
deterministic, no live AWS.

Collection note (mirrors the sibling property/interface tests): this module does NOT import the
``TestResult`` model into its namespace — its ``Test*`` name matches pytest's collection glob and
would add a benign ``PytestCollectionWarning``. Adapter ``test()`` results are inspected by
attribute (``.ok`` / ``.message``) instead. Helper fakes/spies are named ``Fake*`` / ``Spy*`` /
``Recording*`` (never ``Test*``) so pytest does not try to collect them.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import MagicMock

from integrations.access.aws_waf import AwsWafAccessPolicyAdapter
from integrations.access.port import AccessPolicyPort
from integrations.marketplace.internal_dynamo import InternalDynamoLicenseResolver
from integrations.marketplace.port import LicenseResolverPort
from integrations.telemetry.cloudwatch import CloudWatchUsageSink
from integrations.telemetry.dynamo_analytics import DynamoAnalyticsUsageSink
from integrations.telemetry.port import UsageSinkPort
from shared.utils.models import AccessPolicy, AccessRule, UsageEvent

_WEB_ACL_ID = "fake-web-acl-id"
_WEB_ACL_NAME = "fake-web-acl"


# ================================================================================================
# WAF access adapter — implements AccessPolicyPort, delegates to WAFSyncController, test() read-only
# ================================================================================================


class RecordingWafv2Client:
    """In-memory spy for the ``wafv2`` client recording which operations were invoked.

    Records every call name so a test can assert that ``test()`` performs no write
    (``update_web_acl``) and ``push`` does. Backs a single WebACL rule store.
    """

    def __init__(self, initial_rules: List[Dict[str, Any]] | None = None) -> None:
        self._rules: List[Dict[str, Any]] = list(initial_rules or [])
        self.calls: List[str] = []

    def get_web_acl(self, *, Name: str, Scope: str, Id: str) -> Dict[str, Any]:  # noqa: N803 (AWS casing)
        self.calls.append("get_web_acl")
        return {
            "WebACL": {
                "Name": Name,
                "Id": Id,
                "Rules": list(self._rules),
                "DefaultAction": {"Allow": {}},
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "fakeWebAcl",
                },
                "LockToken": "lock-0",
            }
        }

    def update_web_acl(self, **kwargs: Any) -> Dict[str, Any]:  # noqa: ANN401
        self.calls.append("update_web_acl")
        self._rules = list(kwargs.get("Rules", []))
        return {"NextLockToken": "lock-1"}


class FakeWafController:
    """Fake ``WAFSyncController`` recording delegated calls.

    Supplies exactly what the adapter delegates to: ``_get_web_acl_info`` (WebACL identity /
    describe), ``_retry_operation`` (pass-through invoker) and ``get_waf_metrics`` (read-only
    metrics). Records call names so delegation can be asserted.
    """

    def __init__(self) -> None:
        self.calls: List[str] = []

    def _get_web_acl_info(self) -> Dict[str, Any]:
        self.calls.append("_get_web_acl_info")
        return {"Name": _WEB_ACL_NAME, "Id": _WEB_ACL_ID}

    def _retry_operation(self, operation_func, *args, **kwargs):
        self.calls.append("_retry_operation")
        return operation_func(*args, **kwargs)

    def get_waf_metrics(self, rule_name: str = None) -> Dict[str, Any]:
        self.calls.append("get_waf_metrics")
        return {"AccessAttempts": 0}


def _build_waf_adapter(initial_rules: List[Dict[str, Any]] | None = None):
    """Wire the real WAF adapter against the recording fakes (no live AWS)."""
    controller = FakeWafController()
    adapter = AwsWafAccessPolicyAdapter(
        web_acl_name=_WEB_ACL_NAME,
        web_acl_scope="REGIONAL",
        controller=controller,
    )
    client = RecordingWafv2Client(initial_rules=initial_rules)
    adapter.wafv2_client = client
    return adapter, controller, client


def test_waf_adapter_implements_access_policy_port():
    """AwsWafAccessPolicyAdapter implements the ratified AccessPolicyPort (Req 3.1)."""
    adapter, _controller, _client = _build_waf_adapter()
    assert isinstance(adapter, AccessPolicyPort)


def test_waf_adapter_push_delegates_to_wrapped_controller():
    """push delegates to the wrapped WAFSyncController (describe + retry-invoked update) (Req 3.1).

    The one authored rule lands via the controller's ``_get_web_acl_info`` (describe) and
    ``_retry_operation`` (which invokes the client's get/update) — proving delegation to the
    wrapped service rather than a rewrite.
    """
    adapter, controller, client = _build_waf_adapter()
    policy = AccessPolicy(
        policy_id="p1",
        name_prefix="iab-",
        rules=[AccessRule(rule_id="iab-r1", action="block", priority=10, statement={"x": 1})],
    )

    adapter.push(policy)

    # Delegated to the wrapped controller for WebACL identity + the retry-wrapped WAF calls.
    assert "_get_web_acl_info" in controller.calls
    assert "_retry_operation" in controller.calls
    # push IS a write: it describes then updates the WebACL through the delegated retry path.
    assert client.calls == ["get_web_acl", "update_web_acl"]


def test_waf_adapter_get_metrics_delegates_to_wrapped_controller():
    """get_metrics delegates to the wrapped controller's get_waf_metrics (read-only) (Req 3.1)."""
    adapter, controller, _client = _build_waf_adapter()
    adapter.get_metrics()
    assert "get_waf_metrics" in controller.calls


def test_waf_adapter_test_is_read_only_describe_no_update():
    """WAF test() describes the WebACL and performs NO write (no update_web_acl) (Req 6.5, 7.3)."""
    adapter, controller, client = _build_waf_adapter()

    result = adapter.test()

    assert result.ok is True
    # Describe only — the controller's WebACL-info lookup, no client mutation.
    assert controller.calls == ["_get_web_acl_info"]
    assert "update_web_acl" not in client.calls
    # No WAF client call at all during test() — it never even reads the client, only describes ACL.
    assert client.calls == []


# ================================================================================================
# CloudWatch telemetry adapter — implements UsageSinkPort, delegates to access_logger, test() r/o
# ================================================================================================


def _usage_event() -> UsageEvent:
    return UsageEvent(
        content_id="content-1",
        partner_id="partner-1",
        content_type="content_access",
        price_charged=0.01,
        currency="USD",
        license_id="lic-1",
    )


def test_cloudwatch_sink_implements_usage_sink_port():
    """CloudWatchUsageSink implements the ratified UsageSinkPort (Req 3.2)."""
    assert isinstance(CloudWatchUsageSink(), UsageSinkPort)


def test_cloudwatch_sink_emit_delegates_to_access_logger(monkeypatch):
    """emit() delegates to access_logger.log_access_attempt with the mapped event (Req 3.2)."""
    import access_logger

    spy = MagicMock()
    monkeypatch.setattr(access_logger, "log_access_attempt", spy)

    CloudWatchUsageSink().emit(_usage_event())

    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["partner_id"] == "partner-1"
    assert kwargs["content_id"] == "content-1"
    assert kwargs["price_charged"] == 0.01
    assert kwargs["currency"] == "USD"


def test_cloudwatch_sink_test_is_read_only_list_metrics_no_emit(monkeypatch):
    """CloudWatch test() calls list_metrics only and emits NO usage event (Req 6.5, 7.3)."""
    import access_logger

    fake_cloudwatch = MagicMock()
    emit_spy = MagicMock()
    monkeypatch.setattr(access_logger, "cloudwatch", fake_cloudwatch)
    monkeypatch.setattr(access_logger, "log_access_attempt", emit_spy)

    result = CloudWatchUsageSink().test()

    assert result.ok is True
    # Read-only describe against CloudWatch.
    fake_cloudwatch.list_metrics.assert_called_once()
    # No write-shaped CloudWatch call.
    assert not fake_cloudwatch.put_metric_data.called
    # No usage event was emitted by test().
    emit_spy.assert_not_called()


# ================================================================================================
# DynamoDB analytics telemetry adapter — implements UsageSinkPort, delegates to AnalyticsCollector
# ================================================================================================


class RecordingAnalyticsCollector:
    """Fake ``AnalyticsCollector`` recording delegated calls; exposes a spy ``table``.

    ``record_content_access`` is the write path; ``table.load()`` is the read/describe used by
    ``test()``/``status()``. Recording both lets a test assert delegation and read-only-ness.
    """

    def __init__(self) -> None:
        self.table_name = "fake-analytics-table"
        self.table = MagicMock()
        self.record_calls: List[Dict[str, Any]] = []

    def record_content_access(self, content_id: str, access_type: str, requester_info: Dict[str, Any]) -> bool:
        self.record_calls.append(
            {"content_id": content_id, "access_type": access_type, "requester_info": requester_info}
        )
        return True


def _build_analytics_sink() -> tuple:
    """Build the DynamoAnalyticsUsageSink with its wrapped collector replaced by a recording fake."""
    sink = DynamoAnalyticsUsageSink.__new__(DynamoAnalyticsUsageSink)
    collector = RecordingAnalyticsCollector()
    sink._collector = collector
    return sink, collector


def test_dynamo_analytics_sink_implements_usage_sink_port():
    """DynamoAnalyticsUsageSink implements the ratified UsageSinkPort (Req 3.2)."""
    sink, _collector = _build_analytics_sink()
    assert isinstance(sink, UsageSinkPort)


def test_dynamo_analytics_sink_emit_delegates_to_collector():
    """emit() delegates to AnalyticsCollector.record_content_access with the mapped event (Req 3.2)."""
    sink, collector = _build_analytics_sink()

    sink.emit(_usage_event())

    assert len(collector.record_calls) == 1
    call = collector.record_calls[0]
    assert call["content_id"] == "content-1"
    assert call["access_type"] == "content_access"
    assert call["requester_info"]["partner_id"] == "partner-1"
    assert call["requester_info"]["license_id"] == "lic-1"


def test_dynamo_analytics_sink_test_is_read_only_table_load_no_write():
    """Analytics test() calls Table.load() only and writes NO metric (Req 6.5, 7.3)."""
    sink, collector = _build_analytics_sink()

    result = sink.test()

    assert result.ok is True
    # Read-only describe of the analytics table.
    collector.table.load.assert_called_once()
    # No write-shaped DynamoDB call and no metric recorded by test().
    assert not collector.table.put_item.called
    assert collector.record_calls == []


# ================================================================================================
# Internal DynamoDB marketplace adapter — implements LicenseResolverPort, delegates to license_service
# ================================================================================================


def test_internal_dynamo_resolver_implements_license_resolver_port():
    """InternalDynamoLicenseResolver implements the ratified LicenseResolverPort (Req 3.3)."""
    assert isinstance(InternalDynamoLicenseResolver(), LicenseResolverPort)


def test_internal_dynamo_resolver_resolve_delegates_to_license_service(monkeypatch):
    """resolve() delegates to license_service.get_license and maps the license (Req 3.3)."""
    from services import license_service

    get_spy = MagicMock(
        return_value={
            "statusCode": 200,
            "body": json.dumps(
                {"license_id": "lic-42", "allowed_uses": [2, 3], "status": "active"}
            ),
        }
    )
    monkeypatch.setattr(license_service, "get_license", get_spy)

    entitlement = InternalDynamoLicenseResolver().resolve("lic-42")

    get_spy.assert_called_once_with("lic-42")
    assert entitlement.license_id == "lic-42"
    assert entitlement.allowed_uses == [2, 3]
    assert entitlement.active is True


def test_internal_dynamo_resolver_test_is_read_only_list_no_write(monkeypatch):
    """Marketplace test() performs a benign read (list_licenses) and NO create/update/delete
    (Req 6.5, 7.3)."""
    from services import license_service

    list_spy = MagicMock(
        return_value={"statusCode": 200, "body": json.dumps({"licenses": [], "count": 0})}
    )
    create_spy = MagicMock()
    update_spy = MagicMock()
    delete_spy = MagicMock()
    monkeypatch.setattr(license_service, "list_licenses", list_spy)
    # Guard: assert the write-path functions are never touched by test().
    monkeypatch.setattr(license_service, "create_license", create_spy, raising=False)
    monkeypatch.setattr(license_service, "update_license", update_spy, raising=False)
    monkeypatch.setattr(license_service, "delete_license", delete_spy, raising=False)

    result = InternalDynamoLicenseResolver().test()

    assert result.ok is True
    # Benign read only.
    list_spy.assert_called_once()
    # No mutating license_service call.
    create_spy.assert_not_called()
    update_spy.assert_not_called()
    delete_spy.assert_not_called()
