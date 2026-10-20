"""Property test for Property 5 (integration-adapters, GitLab #35, task 10.2).

Feature: integration-adapters, Property 5: Persisted-dataclass to_item() round-trip is
DynamoDB-safe and lossless

Validates: Requirements 4.1.

*For any* instance of a persisted port-facing dataclass (the ``Connection`` record — the one
entity this spec persists via the #33 ``to_item()`` convention), ``to_item()`` produces a dict
that is DynamoDB-safe (no Python ``float`` anywhere — all numerics are ``Decimal`` — and no
``None`` values anywhere, per ``_to_dynamo``'s float->Decimal + drop-None behavior), and
reconstructing a ``Connection`` from that dict (the way the store's ``_record_from_item`` does it)
yields a value equal to the original **modulo** the dropped ``None``/default fields.

Deterministic, gated unit tier, no live AWS — the fake is the in-memory ``_to_dynamo``/dataclass
round-trip itself (design "Testing Strategy" -> property-based tests, fakes-not-AWS). Hypothesis
runs >=100 examples (``@settings(max_examples=100)``). One property -> one property-based test.

float->Decimal semantics (handled precisely, not loosely): ``_to_dynamo`` converts every
``float`` to ``Decimal(str(value))`` and drops every ``None`` (recursively, inside ``config``'s
nested dicts too). So the original record and a record reconstructed from its item are NOT
``==``-equal when the config carried floats or ``None`` — the types/keys change by design. The
equality assertion therefore compares the two records **after** projecting both through the same
DynamoDB-safe normalization (``to_item()``): reconstructing from ``to_item()`` and re-serializing
must reproduce the exact same item (idempotent, lossless up to the intended Decimal/drop-None
transform). The non-``config`` scalar fields are additionally asserted field-by-field to be exactly
preserved (they carry no floats/None post-normalization).

Collection-warning note: this module does NOT import ``TestResult`` (or any ``Test*``-named model)
into its namespace, avoiding the benign ``PytestCollectionWarning`` pytest raises for dataclasses
matching its ``Test*`` collection glob.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

# Reuse the store's canonical reconstruction so the round-trip mirrors production exactly
# (dependency inversion: the test asserts against the real reconstruction seam, not a re-impl).
from services.connections_store import _record_from_item
from shared.utils.models import Connection


def _contains_float(value: Any) -> bool:
    """True if a Python ``float`` appears anywhere in ``value`` (recursively)."""
    # bool is a subclass of int (not float) — no special handling needed.
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_float(v) for v in value)
    return False


def _contains_none(value: Any) -> bool:
    """True if a ``None`` value appears anywhere in ``value`` (recursively).

    Note ``_to_dynamo`` drops ``None`` only at dict-value level; a ``None`` element inside a list
    is not dropped. The generators below therefore do not produce list-level ``None`` (matching
    what a real Connection config would hold), so this recursive check is a true invariant.
    """
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_none(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_none(v) for v in value)
    return False


# ── generators ──────────────────────────────────────────────────────────────

# Config leaf values: strings, ints, bools, floats (which MUST become Decimal), and None
# (which MUST be dropped). Floats and None are deliberately included so the property exercises
# the float->Decimal and drop-None transforms.
_config_leaf = st.one_of(
    st.text(max_size=16),
    st.integers(min_value=-(10**6), max_value=10**6),
    st.booleans(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.none(),
)


def _config_dicts() -> st.SearchStrategy[dict]:
    """Config dicts possibly containing nested dicts with floats/None (drop-None + Decimal cases).

    Values are dict-nested (never list-nested None) so drop-None is fully recursive over the
    generated space — see ``_contains_none`` note.
    """
    return st.dictionaries(
        keys=st.text(min_size=1, max_size=12),
        values=st.one_of(
            _config_leaf,
            st.dictionaries(
                keys=st.text(min_size=1, max_size=12),
                values=_config_leaf,
                max_size=4,
            ),
        ),
        max_size=6,
    )


def _connections() -> st.SearchStrategy[Connection]:
    """Generate varied Connection instances across the persisted input space."""
    return st.builds(
        Connection,
        connection_id=st.text(min_size=1, max_size=24),
        domain=st.sampled_from(["content", "marketplace", "telemetry", "access"]),
        adapter=st.text(min_size=0, max_size=24),
        brand=st.one_of(st.none(), st.text(min_size=1, max_size=16)),
        config=_config_dicts(),
        secret_ref=st.text(min_size=0, max_size=48),
        active=st.booleans(),
        created_at=st.text(min_size=0, max_size=32),
        updated_at=st.text(min_size=0, max_size=32),
    )


@settings(max_examples=100)
@given(connection=_connections())
def test_persisted_to_item_roundtrip_is_dynamo_safe_and_lossless(connection: Connection) -> None:
    """Feature: integration-adapters, Property 5: Persisted-dataclass to_item() round-trip is
    DynamoDB-safe and lossless. Validates: Requirements 4.1.

    (a) no Python ``float`` anywhere in ``to_item()`` output (all numerics are ``Decimal``);
    (b) no ``None`` anywhere in the output (drop-None); (c) reconstructing a Connection from the
    item and re-serializing reproduces the exact same item (lossless up to the intended
    Decimal/drop-None transform), and the non-config scalar fields are preserved exactly.
    """
    item = connection.to_item()

    # (a) DynamoDB-safe numerics: no Python float survives; any numeric in config is a Decimal.
    assert not _contains_float(item)

    # (b) drop-None: no None value anywhere in the produced item.
    assert not _contains_none(item)

    # (c) lossless reconstruction (modulo dropped None/defaults): reconstruct via the store's
    # canonical _record_from_item, then re-serialize. The transform is idempotent — a second
    # to_item() over the reconstructed record equals the first item exactly. This is the precise
    # equality (not a loose compare): it accounts for float->Decimal and dropped None by comparing
    # both records in their already-normalized (DynamoDB-safe) form.
    reconstructed = _record_from_item(item)
    assert reconstructed.to_item() == item

    # Non-config scalar fields carry no float/None post-normalization, so they round-trip verbatim.
    assert reconstructed.connection_id == connection.connection_id
    assert reconstructed.domain == connection.domain
    assert reconstructed.adapter == connection.adapter
    assert reconstructed.brand == connection.brand  # None dropped from item -> reconstructed None
    assert reconstructed.secret_ref == connection.secret_ref
    assert reconstructed.active == connection.active
    assert reconstructed.created_at == connection.created_at
    assert reconstructed.updated_at == connection.updated_at

    # And the config specifically: every original float is now a Decimal, every None is gone,
    # and every surviving (non-None) scalar is preserved by value.
    def _normalize(value: Any) -> Any:
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, dict):
            return {k: _normalize(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [_normalize(v) for v in value]
        return value

    assert reconstructed.config == _normalize(connection.config)
