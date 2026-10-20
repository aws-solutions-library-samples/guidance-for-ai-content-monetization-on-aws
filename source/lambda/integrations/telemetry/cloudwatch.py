"""CloudWatch telemetry adapter (integration-adapters, GitLab #35, task 5.2).

Concrete :class:`~integrations.telemetry.port.UsageSinkPort` adapter that wraps the
existing ``access_logger`` Lambda module — the service that folds a content-access
event into the ``iab-access-logs`` DynamoDB table and publishes the corresponding
CloudWatch metrics (``AccessAttempts`` / ``SuccessfulAccess`` / ``Revenue`` …).

This adapter is a **thin delegation** (Requirement 3.2, 3.4): ``emit()`` maps the
canonical :class:`UsageEvent` onto ``access_logger.log_access_attempt(...)`` and adds
**no** new write-path semantics — the ``UsageEvent`` write path / ``emit()`` semantics
and the ``iab-access-logs`` fold are owned by the usage vertical (#3). Per
``refactoring-safety`` the wrapped module is neither relocated nor rewritten; the only
change is dependency inversion (callers depend on ``UsageSinkPort`` instead of calling
the module directly).

``test()``/``status()`` are safe, read-only connectivity checks: they issue a bounded
CloudWatch ``list_metrics`` describe against the access-logger metric namespace — no
event is emitted (Requirement 6.5, 7.3). The adapter delegates to the ``cloudwatch``
client the wrapped module already constructs rather than building its own; ``cloudwatch``
is general ``boto3`` and outside the guardrail's adapter-owned-client scope (Requirement
8.2a) regardless.
"""

from __future__ import annotations

import access_logger
from shared.utils.models import HealthStatus, TestResult, UsageEvent

# The CloudWatch namespace the wrapped access_logger publishes access metrics under.
_METRIC_NAMESPACE = "IAB/AccessLogger"


class CloudWatchUsageSink:
    """``UsageSinkPort`` adapter delegating to the ``access_logger`` module.

    Emits usage by folding the event into ``iab-access-logs`` + CloudWatch via the
    existing ``log_access_attempt`` service function; performs read-only CloudWatch
    describes for ``test()``/``status()``.
    """

    def emit(self, event: UsageEvent) -> None:
        """Emit a usage event by delegating to ``access_logger.log_access_attempt``.

        Thin mapping of the canonical :class:`UsageEvent` onto the wrapped service's
        parameters. No new write-path semantics are added here (owned by #3): the
        service performs the ``iab-access-logs`` fold and the CloudWatch metric
        publish exactly as it does for its own callers.
        """
        access_logger.log_access_attempt(
            partner_id=event.partner_id,
            request_type=event.content_type or "content_access",
            endpoint="content_access",
            response_code=200,
            content_id=event.content_id,
            price_charged=event.price_charged,
            currency=event.currency,
        )

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check — no event is emitted.

        Issues a bounded CloudWatch ``list_metrics`` describe against the
        access-logger namespace to confirm the sink is reachable and authorized,
        returning a fail-informative :class:`TestResult`.
        """
        try:
            access_logger.cloudwatch.list_metrics(Namespace=_METRIC_NAMESPACE)
            return TestResult(ok=True, message="CloudWatch reachable")
        except Exception as exc:  # noqa: BLE001 — fail-informative, never fail-silent
            return TestResult(ok=False, message=str(exc))

    def status(self) -> HealthStatus:
        """Ongoing health of the active telemetry Connection (read-only)."""
        try:
            access_logger.cloudwatch.list_metrics(Namespace=_METRIC_NAMESPACE)
            return HealthStatus(healthy=True, detail="CloudWatch reachable")
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))
