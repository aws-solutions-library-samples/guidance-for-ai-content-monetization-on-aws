"""Property-based test — AccessPolicyPort.push idempotence + no write-back
(integration-adapters, GitLab #35, task 5.5).

Feature: integration-adapters, Property 1: AccessPolicyPort.push is idempotent and never writes back to the source

Validates: Requirements 2.2

Property 1 (design "Correctness Properties"): *For any* authored ``rule_set``,
applying ``push(rule_set)`` twice against a target produces the same resulting
target configuration as applying it once, AND the authored source (the SSOT
``rule_set`` object) is unchanged after ``push`` — the port never writes the
target's live state back into the source.

This runs against an **in-memory fake WAF target** (a simple rule store), with no
live AWS: the real ``AwsWafAccessPolicyAdapter`` (task 5.1) is wired against a
``FakeWafv2Client`` (in-memory rule store keyed by WebACL id) and a
``FakeWafController`` (supplies the WebACL identity + a pass-through
``_retry_operation``). The adapter is the ratified ``AccessPolicyPort``
implementation, so exercising ``adapter.push`` validates the port contract
deterministically.

Collection note: this module deliberately does NOT import the ``TestResult``
model (its ``Test*`` name triggers a benign pytest collection warning); it only
needs the authored ``AccessPolicy``/``AccessRule`` model types and inspects the
``TargetConfig`` the adapter returns by attribute. Helper fakes are named
``Fake*`` (never ``Test*``) so pytest does not try to collect them.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Dict, List

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from integrations.access.aws_waf import AwsWafAccessPolicyAdapter
from shared.utils.models import AccessPolicy, AccessRule

# --- in-memory fake WAF target -----------------------------------------------------------------

_WEB_ACL_ID = "fake-web-acl-id"
_WEB_ACL_NAME = "fake-web-acl"


class FakeWafv2Client:
    """In-memory fake of the ``wafv2`` client used by the adapter's ``push``.

    Backs a single WebACL as an in-memory rule store (a list of WAF rule dicts).
    Implements exactly the two operations ``push`` invokes — ``get_web_acl`` and
    ``update_web_acl`` — plus a monotonically-changing ``LockToken`` so repeated
    updates behave like the real optimistic-locking contract.
    """

    def __init__(self, initial_rules: List[Dict[str, Any]] | None = None) -> None:
        self._rules: List[Dict[str, Any]] = list(initial_rules or [])
        self._lock_counter = 0

    def _lock_token(self) -> str:
        return f"lock-{self._lock_counter}"

    def get_web_acl(self, *, Name: str, Scope: str, Id: str) -> Dict[str, Any]:  # noqa: N803 (AWS API casing)
        return {
            "WebACL": {
                "Name": Name,
                "Id": Id,
                "Rules": copy.deepcopy(self._rules),
                "DefaultAction": {"Allow": {}},
                "VisibilityConfig": {
                    "SampledRequestsEnabled": True,
                    "CloudWatchMetricsEnabled": True,
                    "MetricName": "fakeWebAcl",
                },
                "LockToken": self._lock_token(),
            }
        }

    def update_web_acl(  # noqa: N803 (AWS API casing)
        self,
        *,
        Scope: str,
        Id: str,
        Name: str,
        DefaultAction: Dict[str, Any],
        Rules: List[Dict[str, Any]],
        VisibilityConfig: Dict[str, Any],
        LockToken: str,
    ) -> Dict[str, Any]:
        # Whole-ACL replacement, exactly like the real update_web_acl contract.
        self._rules = copy.deepcopy(Rules)
        self._lock_counter += 1
        return {"NextLockToken": self._lock_token()}


class FakeWafController:
    """In-memory fake of ``WAFSyncController`` supplying only what ``push`` needs.

    ``push`` calls ``_get_web_acl_info()`` (WebACL identity) and
    ``_retry_operation(func, *args, **kwargs)`` (a pass-through invoker). No
    DynamoDB, no write-back path — the adapter deliberately never calls the
    controller's write-back CRUD.
    """

    def _get_web_acl_info(self) -> Dict[str, Any]:
        return {"Name": _WEB_ACL_NAME, "Id": _WEB_ACL_ID}

    def _retry_operation(self, operation_func, *args, **kwargs):
        return operation_func(*args, **kwargs)


def _build_adapter(initial_rules: List[Dict[str, Any]] | None = None) -> AwsWafAccessPolicyAdapter:
    """Wire the real adapter against the in-memory fakes (no live AWS).

    ``AwsWafAccessPolicyAdapter.__init__`` constructs a ``boto3.client("wafv2")``
    (a local client object — no network call); we then replace it with the fake
    in-memory client so ``push`` operates entirely on the fake target.
    """
    adapter = AwsWafAccessPolicyAdapter(
        web_acl_name=_WEB_ACL_NAME,
        web_acl_scope="REGIONAL",
        controller=FakeWafController(),
    )
    adapter.wafv2_client = FakeWafv2Client(initial_rules=initial_rules)
    return adapter


def _target_config_tuple(tc: Any) -> tuple:
    """Normalize a ``TargetConfig`` for equality comparison across two pushes."""
    return (tc.target_id, tc.name_prefix, tuple(tc.applied_rules))


# --- Hypothesis strategies ----------------------------------------------------------------------

_action = st.sampled_from(["allow", "block", "count"])


@st.composite
def _access_policies(draw) -> AccessPolicy:
    """Generate an authored ``AccessPolicy`` with a name-prefix and unique-named,
    unique-priority rules (WAF rule names/priorities are unique within a WebACL)."""
    prefix = draw(st.sampled_from(["iab-", "cm-authored-", "policy-"]))
    n = draw(st.integers(min_value=0, max_value=6))
    # Unique priorities within the authored band.
    priorities = draw(
        st.lists(st.integers(min_value=1, max_value=200), min_size=n, max_size=n, unique=True)
    )
    rules = [
        AccessRule(
            rule_id=f"{prefix}rule-{i}",
            action=draw(_action),
            priority=priorities[i],
            statement={"ByteMatchStatement": {"idx": i}},
        )
        for i in range(n)
    ]
    return AccessPolicy(
        policy_id=draw(st.text(min_size=1, max_size=12)),
        version=draw(st.sampled_from(["", "v1", "v2"])),
        name_prefix=prefix,
        rules=rules,
    )


# --- Property 1 ---------------------------------------------------------------------------------


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(rule_set=_access_policies())
def test_push_is_idempotent_and_never_writes_back(rule_set: AccessPolicy):
    """Feature: integration-adapters, Property 1: AccessPolicyPort.push is idempotent and never writes back to the source.

    Validates: Requirements 2.2

    For any authored rule_set:
      (a) push twice == push once, both in the returned TargetConfig and in the
          resulting target (fake WebACL) rule store — push is idempotent; and
      (b) the authored source object (rule_set and its rules) is byte-for-byte
          unchanged after push — the port never writes the target's live state
          back into the source of truth.
    """
    adapter = _build_adapter()

    # Snapshot the authored source BEFORE any push to prove it is never mutated.
    source_before = copy.deepcopy(rule_set)

    # --- first push ---
    tc1 = adapter.push(rule_set)
    target_after_first = copy.deepcopy(adapter.wafv2_client._rules)

    # (b) the authored source is unchanged by push (no write-back into the SSOT).
    assert rule_set == source_before, "push must not mutate the authored rule_set (no write-back)"

    # --- second push (same authored rule_set) ---
    tc2 = adapter.push(rule_set)
    target_after_second = copy.deepcopy(adapter.wafv2_client._rules)

    # (a) idempotence: the resulting target configuration is identical after one vs two pushes.
    assert target_after_second == target_after_first, (
        "push must be idempotent: applying it twice yields the same target rule store as once"
    )
    assert _target_config_tuple(tc2) == _target_config_tuple(tc1), (
        "push must return an equivalent TargetConfig on the second application (idempotent)"
    )

    # The authored source remains unchanged after the second push too.
    assert rule_set == source_before, "push must not mutate the authored rule_set on repeat (no write-back)"

    # Sanity: every authored rule id is present exactly once in the target (scoped projection landed).
    applied_names = [r["Name"] for r in target_after_first]
    for rule in rule_set.rules:
        assert applied_names.count(rule.rule_id) == 1


@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(rule_set=_access_policies())
def test_push_idempotent_when_target_prepopulated_with_stale_authored_rules(rule_set: AccessPolicy):
    """Idempotence holds even when the target already holds a *stale* copy of our
    authored partition: a fresh push converges to the same state as pushing onto
    an empty target, and a second push is a no-op change. (Reinforces Property 1.)

    Validates: Requirements 2.2
    """
    # Pre-seed the fake target with an out-of-date render of our own partition
    # (same names, different priorities/actions) plus a foreign rule.
    stale = [
        {
            "Name": r.rule_id,
            "Priority": 900 + i,
            "Statement": {},
            "Action": {"Count": {}},
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": "stale",
            },
        }
        for i, r in enumerate(rule_set.rules)
    ]
    foreign = {
        "Name": "AWS-AWSManagedRulesCommonRuleSet",
        "Priority": 5000,
        "Statement": {},
        "Action": {"Block": {}},
        "VisibilityConfig": {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": "managed",
        },
    }
    adapter = _build_adapter(initial_rules=stale + [foreign])

    source_before = copy.deepcopy(rule_set)

    adapter.push(rule_set)
    after_first = copy.deepcopy(adapter.wafv2_client._rules)
    adapter.push(rule_set)
    after_second = copy.deepcopy(adapter.wafv2_client._rules)

    assert after_second == after_first, "push must converge (idempotent) even from a stale target"
    assert rule_set == source_before, "push must not mutate the authored source (no write-back)"


def test_push_does_not_bind_authored_rule_objects_into_source_mutation():
    """Guard (example): mutating the TargetConfig the adapter returns must not
    reach back into the authored rule_set — the projection is a copy-out, not a
    shared reference the target could write through.

    Validates: Requirements 2.2
    """
    policy = AccessPolicy(
        policy_id="p1",
        name_prefix="iab-",
        rules=[AccessRule(rule_id="iab-r1", action="block", priority=10, statement={"x": 1})],
    )
    adapter = _build_adapter()
    source_before = copy.deepcopy(policy)

    tc = adapter.push(policy)
    # Mutate the returned applied_rules list; the authored source must be unaffected.
    tc.applied_rules.append("iab-injected")
    tc = replace(tc, name_prefix="tampered")

    assert policy == source_before, "mutating push output must not write back into the authored source"
