"""Property test — AccessPolicyPort.push scoped projection preserves foreign rules.

Feature: integration-adapters, Property 2: AccessPolicyPort.push scoped projection preserves foreign rules

integration-adapters (GitLab #35), task 5.6.

Property 2 (design "Correctness Properties"):
    *For any* authored ``rule_set``, applying ``push(rule_set)`` against a target replaces only
    our authored partition and leaves every foreign rule outside our name-prefix and priority band
    exactly preserved, including AWS managed rule groups and the shared ``MonetizeRuleGroup``.

**Validates: Requirements 3.1**

This exercises the ratified ``AccessPolicyPort`` contract via its first concrete adapter,
``integrations.access.aws_waf.AwsWafAccessPolicyAdapter`` (task 5.1), against an **in-memory fake
WAF target** (no live AWS): a fake WebACL preloaded with foreign rules representing AWS managed
rule groups and the shared ``MonetizeRuleGroup``, plus a fake controller that supplies the
WebACL-info / retry plumbing the adapter delegates to. Deterministic, gated unit tier
(``tests/integration_adapters/``, ``-m "not integration"``).

Hypothesis, ``@settings(max_examples=100)`` (>= 100 generated examples). One property -> one test.

Note (pytest ``Test*`` collection): this module does NOT import the ``TestResult`` model (whose
name matches ``python_classes = Test*``) and names no helper ``Test*`` — the local fakes/helpers are
underscore- or ``_Fake``-prefixed — so no ``PytestCollectionWarning`` is added.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from shared.utils.models import AccessPolicy, AccessRule

# ── In-memory fake WAF target (no live AWS) ──────────────────────────────────────────────────────

_WEB_ACL_ID = "fake-webacl-id"
_WEB_ACL_NAME = "fake-webacl"


class _FakeWafClient:
    """Minimal in-memory stand-in for a boto3 ``wafv2`` client.

    Holds a single WebACL whose ``Rules`` list is mutated by ``update_web_acl``. Only the two
    operations the adapter's ``push`` calls are implemented: ``get_web_acl`` (returns the current
    state) and ``update_web_acl`` (replaces the rules and rotates the lock token).
    """

    def __init__(self, initial_rules: List[Dict[str, Any]]):
        self.web_acl: Dict[str, Any] = {
            "Name": _WEB_ACL_NAME,
            "Id": _WEB_ACL_ID,
            "DefaultAction": {"Allow": {}},
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": "fake_webacl",
            },
            "Rules": list(initial_rules),
            "LockToken": "lock-0",
        }
        self._lock_counter = 0

    def get_web_acl(self, **_kwargs: Any) -> Dict[str, Any]:
        # Return a deep-ish copy so callers can't mutate our stored state by reference.
        acl = dict(self.web_acl)
        acl["Rules"] = [dict(r) for r in self.web_acl["Rules"]]
        return {"WebACL": acl, "LockToken": self.web_acl["LockToken"]}

    def update_web_acl(self, **kwargs: Any) -> Dict[str, Any]:
        # WAFv2 requires the current LockToken; enforce it so the fake mirrors the real contract.
        if kwargs.get("LockToken") != self.web_acl["LockToken"]:
            raise AssertionError("stale LockToken passed to update_web_acl")
        self.web_acl["Rules"] = [dict(r) for r in kwargs["Rules"]]
        self._lock_counter += 1
        self.web_acl["LockToken"] = f"lock-{self._lock_counter}"
        return {"NextLockToken": self.web_acl["LockToken"]}


class _FakeController:
    """In-memory stand-in for ``WAFSyncController`` — supplies only what ``push`` delegates."""

    def _get_web_acl_info(self) -> Dict[str, Any]:
        return {"Name": _WEB_ACL_NAME, "Id": _WEB_ACL_ID}

    def _retry_operation(self, operation_func, *args: Any, **kwargs: Any):
        # No retry/backoff needed for the in-memory fake — just invoke.
        return operation_func(*args, **kwargs)


def _make_adapter(fake_client: _FakeWafClient):
    """Build the real adapter wired to the in-memory fakes (patches out ``boto3.client``)."""
    from integrations.access.aws_waf import AwsWafAccessPolicyAdapter

    with patch("integrations.access.aws_waf.boto3.client", return_value=fake_client):
        adapter = AwsWafAccessPolicyAdapter(
            web_acl_name=_WEB_ACL_NAME,
            controller=_FakeController(),
        )
    # Ensure the adapter uses our fake client for get/update (guard against constructor changes).
    adapter.wafv2_client = fake_client
    return adapter


def _rule_key(rule: Dict[str, Any]) -> tuple:
    """Comparable identity of a live WAF rule for exact-preservation assertions."""
    return (rule.get("Name"), rule.get("Priority"))


# ── Hypothesis strategies ────────────────────────────────────────────────────────────────────────

# Authored rules live under our name prefix; foreign rules deliberately never start with it.
_AUTHORED_PREFIX = "iab-"

# Names for authored rules: always carry the authored prefix so name-prefix scoping claims them.
_authored_names = st.builds(
    lambda s: f"{_AUTHORED_PREFIX}{s}",
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
)

_authored_rules = st.builds(
    lambda name, action, priority: AccessRule(
        rule_id=name,
        action=action,
        priority=priority,
        statement={"ByteMatchStatement": {"SearchString": name}},
    ),
    _authored_names,
    st.sampled_from(["allow", "block", "count"]),
    st.integers(min_value=0, max_value=50),
)


def _authored_policy_strategy():
    """An AccessPolicy whose rules all carry the authored name prefix (unique names)."""
    return st.lists(_authored_rules, min_size=0, max_size=6, unique_by=lambda r: r.rule_id).map(
        lambda rules: AccessPolicy(
            policy_id="policy-1",
            version="v1",
            name_prefix=_AUTHORED_PREFIX,
            rules=rules,
        )
    )


def _managed_rule_group(name: str, priority: int) -> Dict[str, Any]:
    """A live WAF rule representing an AWS managed rule group (a foreign rule)."""
    return {
        "Name": name,
        "Priority": priority,
        "Statement": {"ManagedRuleGroupStatement": {"VendorName": "AWS", "Name": name}},
        "OverrideAction": {"None": {}},
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": name.replace("-", "_"),
        },
    }


def _monetize_rule_group(priority: int) -> Dict[str, Any]:
    """A live WAF rule representing the shared ``MonetizeRuleGroup`` (a foreign rule)."""
    return {
        "Name": "MonetizeRuleGroup",
        "Priority": priority,
        "Statement": {"RuleGroupReferenceStatement": {"ARN": "arn:aws:wafv2:::rulegroup/MonetizeRuleGroup"}},
        "OverrideAction": {"None": {}},
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": "MonetizeRuleGroup",
        },
    }


def _generic_foreign_rule(name: str, priority: int) -> Dict[str, Any]:
    """A live WAF rule outside our authored partition (no authored prefix)."""
    return {
        "Name": name,
        "Priority": priority,
        "Statement": {"RateBasedStatement": {"Limit": 1000, "AggregateKeyType": "IP"}},
        "Action": {"Block": {}},
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": name.replace("-", "_"),
        },
    }


# Foreign-rule names must never collide with the authored prefix, and must use a priority band
# clearly ABOVE the authored rules' 0..50 band so they fall outside both scope dimensions.
_foreign_names = st.builds(
    lambda s: f"foreign-{s}",
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=8),
)


def _foreign_rules_strategy():
    """A list of foreign live rules: always the two required exemplars (managed group +
    MonetizeRuleGroup) plus 0..4 additional generic foreign rules, all at priorities >= 100
    (outside the authored 0..50 band) with unique names."""
    extra = st.lists(
        st.builds(lambda name, pr: _generic_foreign_rule(name, pr), _foreign_names, st.integers(200, 400)),
        min_size=0,
        max_size=4,
        unique_by=lambda r: r["Name"],
    )
    return extra.map(
        lambda extras: [
            _managed_rule_group("AWSManagedRulesCommonRuleSet", 100),
            _monetize_rule_group(101),
            *extras,
        ]
    )


# ── The property test ──────────────────────────────────────────────────────────────────────────


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(policy=_authored_policy_strategy(), foreign_rules=_foreign_rules_strategy())
def test_push_scoped_projection_preserves_foreign_rules(policy, foreign_rules):
    """Property 2 — push replaces only our authored partition; every foreign rule is preserved.

    Feature: integration-adapters, Property 2: AccessPolicyPort.push scoped projection preserves foreign rules
    Validates: Requirements 3.1
    """
    fake_client = _FakeWafClient(initial_rules=foreign_rules)
    adapter = _make_adapter(fake_client)

    foreign_keys_before = {_rule_key(r) for r in foreign_rules}
    # Sanity on the fixture: the two required exemplars are present as foreign rules.
    names_before = {r["Name"] for r in foreign_rules}
    assert "MonetizeRuleGroup" in names_before
    assert any(n.startswith("AWSManagedRules") for n in names_before)

    adapter.push(policy)

    resulting_rules = fake_client.web_acl["Rules"]
    resulting_keys = {_rule_key(r) for r in resulting_rules}

    # 1) Every foreign rule is preserved EXACTLY (same Name + Priority) after push.
    assert foreign_keys_before <= resulting_keys, (
        "push must preserve every foreign rule outside our authored partition; "
        f"missing after push: {foreign_keys_before - resulting_keys}"
    )

    # 1a) The foreign rules' full definitions are byte-for-byte preserved (not just their keys).
    resulting_by_key = {_rule_key(r): r for r in resulting_rules}
    for foreign in foreign_rules:
        assert resulting_by_key[_rule_key(foreign)] == foreign, (
            f"foreign rule {_rule_key(foreign)} was mutated by push (must be preserved exactly)"
        )

    # 2) Our authored partition is present after push (only our rules were (re)projected).
    authored_keys = {(r.rule_id, r.priority) for r in policy.rules}
    assert authored_keys <= resulting_keys, (
        f"authored rules must be projected onto the target; missing: {authored_keys - resulting_keys}"
    )

    # 3) The resulting rule set is EXACTLY foreign ∪ authored — no foreign rule dropped and no
    #    stray rule introduced. (Authored rule ids carry the prefix, so they can't collide with
    #    foreign keys by construction.)
    assert resulting_keys == foreign_keys_before | authored_keys, (
        "push must yield exactly the foreign rules plus the authored partition, nothing else"
    )
