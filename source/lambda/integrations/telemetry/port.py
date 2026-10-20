"""Telemetry-domain port (integration-adapters, GitLab #35, task 3.2).

Defines the ratified ``UsageSinkPort`` — the single choke point through which all
content-access usage events flow out to a telemetry sink (Requirement 2.3). It is an
outbound fan-out port: core logic ``emit()``s one canonical :class:`UsageEvent`, and the
concrete adapter (``telemetry/cloudwatch.py`` / ``telemetry/dynamo_analytics.py``, task 5.2)
translates it to the backing system. ``emit()`` write-path semantics and the
``iab-access-logs`` fold are owned by the usage vertical (#3); this file commits only the
interface.

Every ratified port also exposes the common operational surface (Requirements 2.9, 6.8):
  * ``test()``  — a safe, read-only connectivity/auth check the Connections page runs to
    validate a config before/while activating a Connection (no real domain operation).
  * ``status()`` — the ongoing health of an already-active Connection.

Per the hexagonal dependency direction (Requirement 1.2), this port depends on **nothing
external** — only the Canonical_Model types in ``shared.utils.models`` and stdlib typing.
The port-import-direction guard (``tests/import_guard/test_port_import_direction.py``)
enforces that no ``boto3``/vendor SDK or cross-domain module is imported here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shared.utils.models import HealthStatus, TestResult, UsageEvent


@runtime_checkable
class UsageSinkPort(Protocol):
    """Outbound telemetry port: the single choke point for usage events.

    All content-access usage flows through :meth:`emit`; callers depend on this
    interface rather than a specific telemetry backend, so the sink is swappable
    (CloudWatch, a DynamoDB analytics store, etc.) behind an active Connection.
    """

    def emit(self, event: UsageEvent) -> None:
        """Emit a single usage event to the telemetry sink (the choke point).

        :param event: the canonical :class:`UsageEvent`, carrying the
            values-at-time-of-access stamped at emit (price, currency, license,
            content type). Population/emit semantics are owned by #3.
        :returns: nothing — emission is fire-and-forward; adapters translate the
            event to the backing system and surface failures as raised errors.
        """
        ...

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check for a UI-configured Connection.

        Validates that the sink is reachable and authorized **without** emitting a
        real event, returning ``TestResult{ok, message}`` (fail-informative).
        """
        ...

    def status(self) -> HealthStatus:
        """Report the ongoing health of an active telemetry Connection."""
        ...
