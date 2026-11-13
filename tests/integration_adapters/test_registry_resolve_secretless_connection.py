"""Registry secret-resolution unit tests (GitLab #58).

Pins the distinction between the two reasons a Connection might have no usable secret, which the
original code conflated:

* **No ``secret_ref`` at all** — the Connection declares it needs no credential. Every built-in
  sink in the telemetry and access domains is in this class: ``dynamo_usage_events``,
  ``cloudwatch``, ``dynamo_analytics`` and ``aws_waf`` all authenticate with the execution role's
  IAM, and ``connections.py`` stores ``secret_ref = ""`` for them. These MUST resolve.
* **A ``secret_ref`` that is set but unreadable** — a misconfiguration (wrong name, missing IAM,
  deleted secret). These MUST fail closed (Requirement 6.6), because running an adapter with
  partial config is worse than not running it.

Before the #58 fix the Registry called the secret resolver unconditionally, so
``get_secret_value(SecretId="")`` failed for the first class and the adapter was treated as not
wired. The canonical usage sink was therefore unreachable no matter how it was configured, and
every billable partner read was dropped silently.

No AWS: the store and the secret resolver are both injected fakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from integrations.registry import DOMAIN_TELEMETRY, Registry


@dataclass
class _FakeRecord:
    """Satisfies the Registry's structural ``ConnectionRecord`` Protocol."""

    domain: str
    adapter: str
    brand: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    secret_ref: str = ""
    active: bool = True


class _FakeStore:
    def __init__(self, records: List[_FakeRecord]) -> None:
        self._records = records

    def list_active(self, domain: str, brand: Optional[str] = None) -> List[_FakeRecord]:
        return [r for r in self._records if r.domain == domain]


def _raising_resolver(secret_ref: str) -> str:
    raise RuntimeError(f"cannot read secret {secret_ref!r}")


# --------------------------------------------------------------------------- #
# A credential-less Connection resolves
# --------------------------------------------------------------------------- #
def test_empty_secret_ref_resolves_without_calling_the_resolver():
    """An IAM-authenticated sink with no secret_ref resolves, and no secret lookup is attempted.

    This is the #58 regression. Asserting the resolver is never CALLED (not merely that it did not
    raise) is what pins the fix: a resolver invoked with an empty SecretId would fail in a real
    deployment even though nothing here needs a credential.
    """
    calls: list[str] = []

    def tracking_resolver(secret_ref: str) -> str:
        calls.append(secret_ref)
        return "unused"

    registry = Registry(
        store=_FakeStore([_FakeRecord(domain=DOMAIN_TELEMETRY, adapter="dynamo_usage_events")]),
        secret_resolver=tracking_resolver,
    )

    sink = registry.resolve(DOMAIN_TELEMETRY)

    assert sink is not None, (
        "a telemetry Connection with no secret_ref must resolve — dynamo_usage_events "
        "authenticates with the execution role's IAM and has no credential to read (#58)"
    )
    assert calls == [], f"no secret lookup should be attempted for a secret-less Connection, got {calls}"


def test_empty_secret_ref_resolves_even_when_the_resolver_would_fail():
    """The sink resolves with a resolver that raises on any input — it must not be consulted.

    Mirrors the deployed failure exactly: `get_secret_value(SecretId="")` always errors, so before
    the fix a raising resolver and an empty secret_ref produced a silent "not wired".
    """
    registry = Registry(
        store=_FakeStore([_FakeRecord(domain=DOMAIN_TELEMETRY, adapter="dynamo_usage_events")]),
        secret_resolver=_raising_resolver,
    )

    assert registry.resolve(DOMAIN_TELEMETRY) is not None


# --------------------------------------------------------------------------- #
# A declared-but-unreadable secret still fails closed (Requirement 6.6)
# --------------------------------------------------------------------------- #
def test_unreadable_declared_secret_still_fails_closed():
    """A secret_ref that IS set but cannot be read must fail closed — the #58 fix must not relax this.

    The guard is `if record.secret_ref:`, so the only way this passes while the test above also
    passes is if the two cases are genuinely distinguished.
    """
    registry = Registry(
        store=_FakeStore(
            [
                _FakeRecord(
                    domain=DOMAIN_TELEMETRY,
                    adapter="dynamo_usage_events",
                    secret_ref="cm-connections/telemetry#dynamo_usage_events#default",
                )
            ]
        ),
        secret_resolver=_raising_resolver,
    )

    assert registry.resolve(DOMAIN_TELEMETRY) is None, (
        "a declared but unreadable secret_ref must fail closed (Requirement 6.6) — the #58 fix "
        "narrows the skip to an ABSENT secret_ref and must not weaken this case"
    )


@pytest.mark.parametrize("secret_ref", ["", None])
def test_falsy_secret_refs_are_all_treated_as_absent(secret_ref):
    """Both spellings of "no secret" resolve: `connections.py` writes "", and None is dropped on write."""
    registry = Registry(
        store=_FakeStore(
            [_FakeRecord(domain=DOMAIN_TELEMETRY, adapter="dynamo_usage_events", secret_ref=secret_ref)]
        ),
        secret_resolver=_raising_resolver,
    )

    assert registry.resolve(DOMAIN_TELEMETRY) is not None


def test_an_inactive_connection_is_still_not_wired():
    """The fix must not make an inactive Connection resolve — deactivation is an operator decision."""
    registry = Registry(
        store=_FakeStore(
            [_FakeRecord(domain=DOMAIN_TELEMETRY, adapter="dynamo_usage_events", active=False)]
        ),
        secret_resolver=_raising_resolver,
    )

    assert registry.resolve(DOMAIN_TELEMETRY) is None
