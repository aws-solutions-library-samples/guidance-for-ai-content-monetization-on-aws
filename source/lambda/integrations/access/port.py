"""Access-domain port (integration-adapters, GitLab #35, task 3.1).

Holds the ratified outbound ``AccessPolicyPort`` — a **one-way projection** port
(the fix for the WAF two-way-sync bug, entity-model eval §3.9).

The port exposes ``push`` plus read-only ``get_metrics`` / ``detect_drift``, and
deliberately has **no** ``pull_rules()`` or ``write_back()``: authoring lives in
our source of truth, the target (a WAF WebACL) is a disposable render target, and
the only reads back are metrics + drift-detection (reported, never auto-adopted).

``push`` is a **scoped** overwrite of our authored rule partition (name-prefix /
priority band): it replaces only the rules our policy owns and preserves every
foreign rule on the same target (AWS managed rule groups, the shared
``MonetizeRuleGroup``).

The port imports nothing external (no ``boto3``/vendor SDK, no cross-domain
import) — the hexagonal dependency direction, enforced by
``tests/import_guard/test_port_import_direction.py`` (Requirement 1.2). Concrete
adapters (``access/aws_waf.py``, owned by #29/#7) are the only place ``wafv2``
appears.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shared.utils.models import (
    AccessMetrics,
    AccessPolicy,
    DriftReport,
    HealthStatus,
    TargetConfig,
    TestResult,
)


@runtime_checkable
class AccessPolicyPort(Protocol):
    """Outbound, one-way projection of an authored access ruleset onto a target.

    Ratified contract (Requirements 2.1, 2.2, 2.9, 6.8). Implementations MUST NOT
    add a method that reads the target's live rule-state as authoring input or
    writes the target's state back into the source of truth — that is the
    two-way-sync class of bug this port forecloses at the interface.
    """

    def push(self, rule_set: AccessPolicy) -> TargetConfig:
        """Project the authored ``rule_set`` onto the target (declarative, idempotent).

        A **scoped** overwrite of our authored rule partition (``rule_set.name_prefix``
        / priority band): replaces only the rules our policy owns and preserves foreign
        rules on the same target (AWS managed rule groups, the shared
        ``MonetizeRuleGroup``). Applying the same ``rule_set`` twice yields the same
        target configuration as applying it once; the authored source is never mutated.
        """
        ...

    def get_metrics(self) -> AccessMetrics:
        """Read-only metrics from the target (never writes)."""
        ...

    def detect_drift(self) -> DriftReport:
        """Read-only drift detection between the authored source and the target's live
        state — reported, never auto-adopted."""
        ...

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check (e.g. describe the WebACL, not push)."""
        ...

    def status(self) -> HealthStatus:
        """Ongoing health of an active Connection (distinct from ``test()``)."""
        ...
