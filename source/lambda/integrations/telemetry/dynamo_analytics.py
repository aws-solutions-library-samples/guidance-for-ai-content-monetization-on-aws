"""DynamoDB analytics telemetry adapter (integration-adapters, GitLab #35, task 5.2).

Concrete :class:`~integrations.telemetry.port.UsageSinkPort` adapter that wraps the
existing ``services.analytics_collector.AnalyticsCollector`` — the service that records
content-access / usage metrics into the analytics DynamoDB table.

This adapter is a **thin delegation** (Requirement 3.2, 3.4): ``emit()`` maps the
canonical :class:`UsageEvent` onto ``AnalyticsCollector.record_content_access(...)`` and
adds **no** new write-path semantics — the ``UsageEvent`` write path / ``emit()``
semantics are owned by the usage vertical (#3). Per ``refactoring-safety`` the wrapped
service is neither relocated nor rewritten; the only change is dependency inversion
(callers depend on ``UsageSinkPort`` instead of constructing the collector directly).

``test()``/``status()`` are safe, read-only checks against the analytics table (a bounded
``Table.load()`` describe) — no metric is written (Requirement 6.5, 7.3). The adapter
delegates to the DynamoDB resource the wrapped collector already holds rather than
constructing its own client; ``dynamodb`` is general ``boto3`` and outside the guardrail's
adapter-owned-client scope (Requirement 8.2a) regardless.
"""

from __future__ import annotations

from typing import Optional

from services.analytics_collector import AnalyticsCollector
from shared.utils.models import HealthStatus, TestResult, UsageEvent


class DynamoAnalyticsUsageSink:
    """``UsageSinkPort`` adapter delegating to ``AnalyticsCollector``.

    Emits usage by recording a content-access metric through the existing
    collector service; performs a read-only table describe for ``test()``/
    ``status()``.
    """

    def __init__(self, table_name: Optional[str] = None) -> None:
        """Wrap an :class:`AnalyticsCollector`.

        :param table_name: optional analytics table name forwarded to the wrapped
            collector; when omitted the collector resolves it from configuration
            (env var / default) exactly as it does for its own callers.
        """
        self._collector = AnalyticsCollector(table_name)

    def emit(self, event: UsageEvent) -> None:
        """Emit a usage event by delegating to ``AnalyticsCollector.record_content_access``.

        Thin mapping of the canonical :class:`UsageEvent` onto the wrapped service.
        No new write-path semantics are added here (owned by #3): the collector
        records the content-access metric exactly as it does for its own callers.
        """
        self._collector.record_content_access(
            content_id=event.content_id,
            access_type=event.content_type or "content_access",
            requester_info={
                "partner_id": event.partner_id,
                "price_charged": event.price_charged,
                "currency": event.currency,
                "license_id": event.license_id,
            },
        )

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check — no metric is written.

        Issues a bounded ``Table.load()`` describe against the analytics table to
        confirm the sink is reachable and authorized, returning a fail-informative
        :class:`TestResult`.
        """
        try:
            self._collector.table.load()
            return TestResult(ok=True, message=f"Analytics table reachable: {self._collector.table_name}")
        except Exception as exc:  # noqa: BLE001 — fail-informative, never fail-silent
            return TestResult(ok=False, message=str(exc))

    def status(self) -> HealthStatus:
        """Ongoing health of the active telemetry Connection (read-only)."""
        try:
            self._collector.table.load()
            return HealthStatus(healthy=True, detail=f"Analytics table reachable: {self._collector.table_name}")
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))
