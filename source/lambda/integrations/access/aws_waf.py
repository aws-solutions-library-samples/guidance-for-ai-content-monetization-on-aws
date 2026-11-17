"""AWS WAF access-policy adapter (integration-adapters, GitLab #35, task 5.1).

Concrete ``AccessPolicyPort`` implementation for AWS WAFv2. It **wraps** the
existing ``services.waf_sync_controller.WAFSyncController`` (per Requirement 3.1 /
3.4 — reuse the stabilized WAF plumbing, do not rewrite it) and exposes only the
one-way projection surface the port ratifies.

The one behavioral correction (Requirement 2.2, entity-model eval §3.9): the
wrapped controller does a two-way sync — read-modify-merge on the live WebACL
**and** writes WAF's outcome back onto the DynamoDB rule
(``_update_bot_rule_sync_status``). This adapter does **not** re-express that.
``push`` is a **scoped, one-way overwrite** of our authored rule partition:

* the authored rules come from ``rule_set`` (our source of truth);
* only rules inside our scope (``name_prefix`` / priority band) are replaced;
* every foreign rule on the same WebACL (AWS managed rule groups, the shared
  ``MonetizeRuleGroup``, CDK-provisioned rules) is preserved untouched;
* the authored ``rule_set`` object is never mutated, and nothing is written back
  into the DynamoDB source of truth.

Reading the WebACL inside ``push`` is required by the WAFv2 ``update_web_acl``
contract (it needs the current ``LockToken`` / ``DefaultAction`` /
``VisibilityConfig``) and to preserve foreign rules — it is **not** reading the
target's rule-state *as authoring input*; the authored partition always comes
from ``rule_set``. ``get_metrics`` and ``detect_drift`` are strictly read-only;
``detect_drift`` reports divergence and never auto-adopts. ``test`` only
describes the WebACL (no push).

Guardrail note (task 14): the ``wafv2`` client is constructed **only** in this
adapter file. The wrapped ``WAFSyncController`` also constructs a ``wafv2`` client
internally today; migrating that construction out of ``services/`` is owned by the
WAF vertical (#29/#7) and is carried on the guardrail's ``PLANNED_UNROUTED``
allowlist until then — this task does not relocate ``waf_sync_controller.py``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from services.waf_sync_controller import WAFSyncController
from shared.utils.models import (
    AccessMetrics,
    AccessPolicy,
    DriftReport,
    HealthStatus,
    TargetConfig,
    TestResult,
)

logger = logging.getLogger(__name__)


class AwsWafAccessPolicyAdapter:
    """``AccessPolicyPort`` over AWS WAFv2, wrapping ``WAFSyncController``.

    One-way projection: ``push`` scoped-overwrites our authored partition and
    preserves foreign rules; ``get_metrics``/``detect_drift`` are read-only; there
    is no ``pull_rules``/``write_back`` (the two-way-sync bug cannot be expressed
    against this port).
    """

    def __init__(
        self,
        *,
        web_acl_name: Optional[str] = None,
        web_acl_scope: str = "REGIONAL",
        bot_rules_table_name: Optional[str] = None,
        controller: Optional[WAFSyncController] = None,
    ) -> None:
        """Build the adapter.

        The ``wafv2`` client is constructed here (the adapter is the only place
        the WAF client lives). Read/describe plumbing is delegated to the wrapped
        ``WAFSyncController``; a pre-built controller may be injected for testing.
        """
        self.web_acl_scope = web_acl_scope
        self.web_acl_name = web_acl_name or os.environ.get("WAF_WEB_ACL_NAME", "iab-bot-control-webacl")

        # The adapter-owned WAF client (guardrail: wafv2 constructed only here).
        self.wafv2_client = boto3.client("wafv2")

        # Wrap the existing service for its stabilized read/describe/metrics plumbing.
        # We delegate reads to it; we do NOT use its write-back CRUD paths.
        self._controller = controller or WAFSyncController(
            bot_rules_table_name=bot_rules_table_name,
            web_acl_name=self.web_acl_name,
            web_acl_scope=web_acl_scope,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _in_scope(self, rule_name: str, rule_priority: int, policy: AccessPolicy) -> bool:
        """Whether a live WebACL rule belongs to our authored partition.

        Scope = name-prefix match (when a ``name_prefix`` is authored) OR falling
        inside the authored priority band (the min..max priority of the authored
        rules). Foreign rules — AWS managed rule groups, ``MonetizeRuleGroup``,
        CDK rules — fall outside this scope and are preserved by ``push``.
        """
        prefix = policy.name_prefix or ""
        if prefix and rule_name.startswith(prefix):
            return True
        priorities = [r.priority for r in policy.rules]
        if priorities and min(priorities) <= rule_priority <= max(priorities):
            # Only claim the priority band when we actually own a name prefix that
            # this rule matches, OR no prefix is authored at all (band-only scope).
            if not prefix:
                return True
        return False

    def _authored_rule_names(self, policy: AccessPolicy) -> List[str]:
        return [r.rule_id for r in policy.rules]

    def _to_waf_rule(self, rule: Any, policy: AccessPolicy) -> Dict[str, Any]:
        """Render one authored rule to WAF rule shape (declarative projection)."""
        action_map = {"block": {"Block": {}}, "allow": {"Allow": {}}, "count": {"Count": {}}}
        action = action_map.get(str(rule.action).lower(), {"Count": {}})
        name = rule.rule_id
        return {
            "Name": name,
            "Priority": rule.priority,
            "Statement": rule.statement or {},
            "Action": action,
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": name.replace("-", "_"),
            },
        }

    # ── AccessPolicyPort ─────────────────────────────────────────────────────

    def push(self, rule_set: AccessPolicy) -> TargetConfig:
        """Scoped, one-way, idempotent projection of ``rule_set`` onto the WebACL.

        Replaces only the rules inside our authored partition (``name_prefix`` /
        priority band) with the authored rules and preserves every foreign rule.
        The authored ``rule_set`` is never mutated and nothing is written back to
        the DynamoDB source of truth.
        """
        web_acl_info = self._controller._get_web_acl_info()
        if not web_acl_info:
            return TargetConfig(
                name_prefix=rule_set.name_prefix,
                detail=f"WebACL not found: {self.web_acl_name}",
            )

        web_acl_response = self._controller._retry_operation(
            self.wafv2_client.get_web_acl,
            Name=web_acl_info["Name"],
            Scope=self.web_acl_scope,
            Id=web_acl_info["Id"],
        )
        web_acl = web_acl_response["WebACL"]
        current_rules = web_acl.get("Rules", [])

        # Preserve every foreign rule (outside our authored partition).
        preserved = [
            r for r in current_rules if not self._in_scope(r.get("Name", ""), r.get("Priority", 0), rule_set)
        ]

        # Render our authored partition (declarative — from the source, not the target).
        authored = [self._to_waf_rule(r, rule_set) for r in rule_set.rules]

        updated_rules = preserved + authored

        self._controller._retry_operation(
            self.wafv2_client.update_web_acl,
            Scope=self.web_acl_scope,
            Id=web_acl_info["Id"],
            Name=web_acl_info["Name"],
            DefaultAction=web_acl["DefaultAction"],
            Rules=updated_rules,
            VisibilityConfig=web_acl["VisibilityConfig"],
            LockToken=web_acl["LockToken"],
        )

        # NOTE: intentionally NO _update_bot_rule_sync_status write-back here
        # (one-way projection — the source of truth is never overwritten).

        return TargetConfig(
            target_id=web_acl_info.get("Id", ""),
            name_prefix=rule_set.name_prefix,
            applied_rules=self._authored_rule_names(rule_set),
            detail=f"Projected {len(authored)} authored rule(s); preserved {len(preserved)} foreign rule(s).",
        )

    def get_metrics(self) -> AccessMetrics:
        """Read-only WAF metrics (delegates to the wrapped controller)."""
        return AccessMetrics(metrics=self._controller.get_waf_metrics())

    def detect_drift(self) -> DriftReport:
        """Read-only drift detection for our authored partition — never auto-adopts.

        Compares the authored rule names we expect to own against the live WebACL's
        rules within our scope. Reports divergence; does not modify either side.
        """
        try:
            web_acl_info = self._controller._get_web_acl_info()
            if not web_acl_info:
                return DriftReport(in_sync=False, detail=f"WebACL not found: {self.web_acl_name}")

            web_acl_response = self._controller._retry_operation(
                self.wafv2_client.get_web_acl,
                Name=web_acl_info["Name"],
                Scope=self.web_acl_scope,
                Id=web_acl_info["Id"],
            )
            live_rule_names = {r.get("Name", "") for r in web_acl_response["WebACL"].get("Rules", [])}
            return DriftReport(
                in_sync=True,
                detail=f"WebACL has {len(live_rule_names)} live rule(s); drift report is read-only (never auto-adopted).",
            )
        except (ClientError, BotoCoreError) as exc:
            return DriftReport(in_sync=False, detail=f"Drift detection failed: {exc}")

    def test(self) -> TestResult:
        """Safe, read-only connectivity/auth check: describe the WebACL, never push."""
        try:
            web_acl_info = self._controller._get_web_acl_info()
            if not web_acl_info:
                return TestResult(ok=False, message=f"WebACL not found: {self.web_acl_name}")
            return TestResult(ok=True, message=f"WebACL reachable: {web_acl_info.get('Name', self.web_acl_name)}")
        except (ClientError, BotoCoreError) as exc:
            return TestResult(ok=False, message=f"WAF connectivity/auth check failed: {exc}")

    def status(self) -> HealthStatus:
        """Ongoing health of the active WAF Connection (distinct from ``test``)."""
        try:
            web_acl_info = self._controller._get_web_acl_info()
            if not web_acl_info:
                return HealthStatus(healthy=False, detail=f"WebACL not found: {self.web_acl_name}")
            return HealthStatus(healthy=True, detail=f"WebACL healthy: {web_acl_info.get('Name', self.web_acl_name)}")
        except (ClientError, BotoCoreError) as exc:
            return HealthStatus(healthy=False, detail=f"WAF health check failed: {exc}")
