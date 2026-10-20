"""Property test — Registry resolves the unique active connection per (domain, brand).

Feature: integration-adapters, Property 3: Registry resolves the unique active connection per (domain, brand)

integration-adapters (GitLab #35), task 6.2.

Property 3 (design "Correctness Properties"):
    *For any* set of ``Connection`` records varying by domain, brand, adapter, and active flag,
    ``Registry.resolve(domain, brand)`` returns the single active connection matching that
    ``(domain, brand)`` — never one belonging to a different brand or domain — and returns
    ``None`` when no active connection matches (an adapter with no active Connection is not
    wired).

**Validates: Requirements 6.3a, 6.6**

This exercises the real :class:`integrations.registry.Registry` selection logic against an
**in-memory FAKE ConnectionStore** (implementing ``list_active`` over a generated record set) and
an **injected secret_resolver** (a plain callable returning a dummy secret — no AWS, no Secrets
Manager). Deterministic, gated unit tier (``tests/integration_adapters/``, ``-m "not integration"``).

Determinism / no-AWS note: the Registry's built-in adapter factories construct *real* adapter
instances, and the ``aws_waf`` factory builds a ``wafv2`` boto3 client at construction. To keep
this property fully AWS-free and to assert on *which* record was resolved without depending on any
adapter's internals, the built-in factories are patched to return a lightweight sentinel that
records the ``(domain, adapter)`` it was built for. This is a test-local patch of the registry
module's factory index; ``registry.py`` itself is not modified. The selection logic under test
(match by ``(domain, brand)`` + ``active``, cross-brand/cross-domain isolation, ``None`` when no
match) is entirely the Registry's own.

Hypothesis, ``@settings(max_examples=100)`` (>= 100 generated examples). One property -> one test.

Collection-warning note: this module does NOT import ``TestResult`` (or any ``Test*``-named model)
into its namespace, and names no helper ``Test*`` — the local fakes/helpers are underscore- or
``_Fake``/``Fake``-prefixed dataclasses — so pytest adds no ``PytestCollectionWarning`` for a
``Test*`` collection match.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from hypothesis import given, settings
from hypothesis import strategies as st

import integrations.registry as registry_module
from integrations.registry import (
    DOMAIN_ACCESS,
    DOMAIN_CONTENT,
    DOMAIN_MARKETPLACE,
    DOMAIN_TELEMETRY,
    Registry,
    _BuiltinAdapter,
)

# The known built-in (domain, adapter) pairs the Registry can resolve — read from
# registry._BUILTIN_ADAPTERS so the "resolves" cases use valid adapter names (task guidance).
_KNOWN_BUILTINS: List[tuple[str, str]] = [
    (b.domain, b.adapter) for b in registry_module._BUILTIN_ADAPTERS
]

# All four integration domains (content currently has no built-in adapter — its port is
# Proposed-only — so a content record always exercises the unknown-adapter None path).
_ALL_DOMAINS = [DOMAIN_CONTENT, DOMAIN_MARKETPLACE, DOMAIN_TELEMETRY, DOMAIN_ACCESS]

# A small pool of brands including None (single-brand / brand-agnostic), per Requirement 6.3a.
_BRANDS: List[Optional[str]] = [None, "acme", "globex", "initech"]

_DUMMY_SECRET = "dummy-secret-value"  # noqa: S105 - test placeholder, not a real credential


def _dummy_secret_resolver(secret_ref: str) -> str:
    """Injected secret resolver — returns a fixed dummy value, never touches AWS."""
    return _DUMMY_SECRET


# ─────────────────────────────────────────────────────────────────────────────
# Local fake ConnectionRecord + in-memory ConnectionStore (no persistence, no AWS)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FakeConnectionRecord:
    """In-memory record exposing exactly the attributes the ``ConnectionRecord`` Protocol reads."""

    domain: str
    adapter: str
    brand: Optional[str]
    config: Dict[str, Any]
    secret_ref: str
    active: bool


@dataclass
class FakeConnectionStore:
    """In-memory ``ConnectionStore`` over a fixed set of generated records.

    ``list_active`` mirrors the contract of the real store (task 10.1): return records that are
    ``active`` and match the requested ``(domain, brand)`` — where the ``brand`` argument selects
    per-brand (``None`` selects the brand-agnostic connection). The Registry re-applies the same
    predicate defensively, so this fake is faithful whether it over- or exactly-filters.
    """

    records: List[FakeConnectionRecord] = field(default_factory=list)

    def list_active(
        self, domain: str, brand: Optional[str] = None
    ) -> List[FakeConnectionRecord]:
        return [
            r
            for r in self.records
            if r.active and r.domain == domain and r.brand == brand
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Sentinel adapter factory patch — keeps resolution AWS-free and lets us assert on
# WHICH record's adapter was constructed, without touching any adapter internals.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _SentinelAdapter:
    """What a patched built-in factory returns: records the (domain, adapter) it was built for."""

    domain: str
    adapter: str
    secret: str


def _patched_builtin_index() -> Dict[tuple[str, str], _BuiltinAdapter]:
    """Replacement ``_BUILTIN_BY_KEY`` whose factories build sentinels (no boto3, no AWS)."""
    patched: Dict[tuple[str, str], _BuiltinAdapter] = {}
    for domain, adapter in _KNOWN_BUILTINS:
        # Bind domain/adapter per-entry via default args to avoid late-binding closure capture.
        def _factory(config, secret, _d=domain, _a=adapter):  # noqa: ANN001, ANN202
            return _SentinelAdapter(domain=_d, adapter=_a, secret=secret)

        patched[(domain, adapter)] = _BuiltinAdapter(
            domain=domain, adapter=adapter, factory=_factory
        )
    return patched


# ─────────────────────────────────────────────────────────────────────────────
# Record generation
# ─────────────────────────────────────────────────────────────────────────────


@st.composite
def _connection_records(draw: st.DrawFn) -> List[FakeConnectionRecord]:
    """Generate a set of Connection records across domains, brands, adapters, active flags.

    Adapters are drawn from a pool that mixes valid built-ins (so the "resolves" cases hit a
    known adapter) and an ``unknown`` name (so the unknown-adapter None path is exercised).

    Per-brand-unique invariant (the precondition Property 3 is stated over)
    ----------------------------------------------------------------------
    The design treats "**at most one active connection per (domain, brand)**" as a *system
    invariant* (Requirement 6.3a; the ``ConnectionStore.list_active`` docstring; ``resolve``'s
    "there should be exactly one active connection" note). ``registry.resolve`` deliberately
    does NOT return ``None`` when that invariant is violated — it logs a warning and returns the
    first record. So a generator that emits two active records for the same ``(domain, brand)``
    with a known adapter is generating an **out-of-spec** input, not a valid one, and the
    single-active oracle rightly no longer describes ``resolve``'s behaviour.

    To keep the property faithful to the invariant it validates, we ENFORCE the precondition
    after free generation: within each ``(domain, brand)`` group, at most one record stays
    ``active`` (the extras are demoted to inactive). This still exercises the full input space
    the property cares about — multiple *inactive* records per group, cross-brand and
    cross-domain records, the unknown-adapter path, and the no-active-match ``None`` path — while
    the "exactly one active known-adapter match -> resolve returns it; otherwise None" oracle
    holds exactly.
    """
    adapter_pool = [a for (_d, a) in _KNOWN_BUILTINS] + ["unknown_adapter"]

    def _one() -> st.SearchStrategy[FakeConnectionRecord]:
        return st.builds(
            FakeConnectionRecord,
            domain=st.sampled_from(_ALL_DOMAINS),
            adapter=st.sampled_from(adapter_pool),
            brand=st.sampled_from(_BRANDS),
            config=st.just({}),
            secret_ref=st.sampled_from(["ref-a", "ref-b", "ref-c"]),
            active=st.booleans(),
        )

    generated = draw(st.lists(_one(), min_size=0, max_size=12))

    # Enforce the per-(domain, brand) at-most-one-active system invariant: keep the first active
    # record in each group active, demote any subsequent active in the same group to inactive.
    seen_active_groups: set[tuple[str, Optional[str]]] = set()
    enforced: List[FakeConnectionRecord] = []
    for record in generated:
        if record.active:
            group = (record.domain, record.brand)
            if group in seen_active_groups:
                record = replace(record, active=False)
            else:
                seen_active_groups.add(group)
        enforced.append(record)
    return enforced


def _unique_active_match(
    records: List[FakeConnectionRecord], domain: str, brand: Optional[str]
) -> Optional[FakeConnectionRecord]:
    """The active record matching (domain, brand) iff exactly one exists whose adapter is a
    known built-in; otherwise None. Mirrors the Registry's expected single-active contract."""
    matches = [
        r for r in records if r.active and r.domain == domain and r.brand == brand
    ]
    if len(matches) != 1:
        return None
    only = matches[0]
    if (only.domain, only.adapter) not in registry_module._BUILTIN_BY_KEY:
        return None
    return only


@settings(max_examples=100)
@given(
    records=_connection_records(),
    query_domain=st.sampled_from(_ALL_DOMAINS),
    query_brand=st.sampled_from(_BRANDS),
)
def test_registry_resolves_unique_active_connection_per_domain_brand(
    records: List[FakeConnectionRecord],
    query_domain: str,
    query_brand: Optional[str],
) -> None:
    """Feature: integration-adapters, Property 3: Registry resolves the unique active connection
    per (domain, brand). Validates: Requirements 6.3a, 6.6.

    For a queried ``(domain, brand)``: ``resolve`` returns an adapter iff exactly one active
    record with a known built-in adapter matches, that adapter corresponds to the matching
    record's ``adapter`` + ``domain``, and returns ``None`` when no such active record matches —
    with cross-brand / cross-domain isolation (a record for a different brand or domain is never
    returned).
    """
    store = FakeConnectionStore(records=list(records))
    registry = Registry(store=store, secret_resolver=_dummy_secret_resolver)

    # Keep resolution AWS-free and assertable: patch the factory index to build sentinels.
    # A context-manager patch (not the monkeypatch fixture) so it composes cleanly with @given.
    with patch.object(registry_module, "_BUILTIN_BY_KEY", _patched_builtin_index()):
        resolved = registry.resolve(query_domain, query_brand)

    expected = _unique_active_match(records, query_domain, query_brand)

    if expected is None:
        # No unique active known-adapter match -> not wired.
        assert resolved is None
    else:
        # Resolved the single active matching connection, and it is that record's adapter+domain.
        assert isinstance(resolved, _SentinelAdapter)
        # Cross-domain isolation: resolved domain is exactly the queried domain (== the match's).
        assert resolved.domain == query_domain == expected.domain
        # The resolved adapter corresponds to the matching record's adapter.
        assert resolved.adapter == expected.adapter
        # Cross-brand isolation: the matched record itself belongs to the queried brand, so the
        # resolved adapter is never one built from a foreign-brand record.
        assert expected.brand == query_brand
        # The injected (dummy) secret was passed through to the factory, never AWS.
        assert resolved.secret == _DUMMY_SECRET


@settings(max_examples=100)
@given(
    records=_connection_records(),
    query_domain=st.sampled_from(_ALL_DOMAINS),
    query_brand=st.sampled_from(_BRANDS),
)
def test_registry_never_returns_foreign_brand_or_domain(
    records: List[FakeConnectionRecord],
    query_domain: str,
    query_brand: Optional[str],
) -> None:
    """Feature: integration-adapters, Property 3: Registry resolves the unique active connection
    per (domain, brand). Validates: Requirements 6.3a, 6.6.

    Isolation half of Property 3: whatever ``resolve(domain, brand)`` returns, it is never an
    adapter constructed from a record of a *different* brand or a *different* domain. Verified by
    querying a (domain, brand) that has NO active known-adapter match while foreign-brand /
    foreign-domain active records exist — resolve must be ``None``, not a leak.
    """

    # Force isolation scenario: strip any active known-adapter record for the exact queried
    # (domain, brand), leaving foreign-brand/foreign-domain records untouched. Built-in keys are
    # identical between the real and sentinel index, so membership is well-defined here.
    def _is_local_match(r: FakeConnectionRecord) -> bool:
        return (
            r.active
            and r.domain == query_domain
            and r.brand == query_brand
            and (r.domain, r.adapter) in registry_module._BUILTIN_BY_KEY
        )

    isolated = [r if not _is_local_match(r) else replace(r, active=False) for r in records]

    store = FakeConnectionStore(records=isolated)
    registry = Registry(store=store, secret_resolver=_dummy_secret_resolver)

    with patch.object(registry_module, "_BUILTIN_BY_KEY", _patched_builtin_index()):
        resolved = registry.resolve(query_domain, query_brand)

    # No active match for the queried (domain, brand) -> never a foreign record leaks through.
    assert resolved is None
