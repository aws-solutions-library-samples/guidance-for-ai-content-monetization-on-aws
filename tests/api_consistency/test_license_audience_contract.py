"""
Contract-consistency guard for the license-operation ``x-audience`` partition (#3, task 3.4).

Validates Link 1 of the contract chain (`.kiro/specs/license-lifecycle-usage-events/design.md`
"§2 Contract chain → Link 1", "A3. Two-plane boundary") against ``openapi-spec.yaml``:

  * Req 4.4 — every license operation carries the ``x-audience`` value matching Req 4.1/4.2:
    ``internal`` for the writes and the collection/version reads, ``partner`` for the public
    license-document read and validate. Asserted per ``(path, method)``, because
    ``/licenses/{license_id}`` is **mixed-audience** (GET partner, PUT/DELETE internal) and a
    path-level partition cannot express that.
  * Req 12.6 — the spec derived by the **real** ``derive_partner_spec`` from the **real** contract
    contains zero license-mutating operations and exactly the two partner license operations, so a
    partner key has no route to a license write *by construction*.

**Fail-closed (the load-bearing property).** The set of license operations is derived from the
contract itself and asserted equal to the expected classification below. A future ``/licenses*``
operation added to ``openapi-spec.yaml`` without being classified here therefore FAILS — the guard
cannot be outgrown by adding paths or methods.

This module is the dedicated boundary guard; it does not restate the generic #31 partition checks in
``tests/api_spec/test_openapi_validation.py`` (every-op-has-an-audience, whole-contract partition).
Instead it **strengthens** them: it pins the license half per-operation, ties itself to that same
SSOT partition so the two cannot silently disagree, and adds the derived-spec and audience/security
coherence assertions those checks do not make.
"""

import sys
from pathlib import Path
from typing import Dict, Iterator, Set, Tuple

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = PROJECT_ROOT / "openapi-spec.yaml"

sys.path.insert(0, str(PROJECT_ROOT))

from shared.utils.openapi_filter import derive_partner_spec  # noqa: E402
from tests.api_spec.test_openapi_validation import (  # noqa: E402
    _INTERNAL_PATHS,
    _MIXED_AUDIENCE_OPERATIONS,
    _OP_METHODS,
    _PARTNER_PATHS,
)

Operation = Tuple[str, str]

# The expected audience of every license operation, per (path, method) — Req 4.1/4.2/4.4 and the
# design's Link 1 table. This is the classified set the contract is asserted to equal exactly.
_EXPECTED_LICENSE_AUDIENCE: Dict[Operation, str] = {
    # Writes + internal reads → internal plane (Cognito), dropped from the partner spec (Req 4.1).
    ("/licenses", "get"): "internal",
    ("/licenses", "post"): "internal",
    ("/licenses/{license_id}", "put"): "internal",
    ("/licenses/{license_id}", "delete"): "internal",
    ("/licenses/{license_id}/renew", "post"): "internal",
    ("/licenses/{license_id}/versions", "get"): "internal",
    # The only two partner license operations (Req 4.2): the public licenseUrl an agent
    # dereferences, and native-key validate.
    ("/licenses/{license_id}", "get"): "partner",
    ("/licenses/{license_id}/validate", "post"): "partner",
}

_EXPECTED_INTERNAL_OPERATIONS: Set[Operation] = {
    op for op, audience in _EXPECTED_LICENSE_AUDIENCE.items() if audience == "internal"
}
_EXPECTED_PARTNER_OPERATIONS: Set[Operation] = {
    op for op, audience in _EXPECTED_LICENSE_AUDIENCE.items() if audience == "partner"
}

# HTTP methods that mutate state. ``POST /licenses/{id}/validate`` is the one partner POST that does
# not mutate a license (it evaluates an entitlement), so it is excluded explicitly rather than by
# being overlooked.
_MUTATING_METHODS = frozenset({"post", "put", "delete", "patch"})
_NON_MUTATING_PARTNER_POSTS: Set[Operation] = {("/licenses/{license_id}/validate", "post")}


def _is_license_path(path: str) -> bool:
    return path == "/licenses" or path.startswith("/licenses/")


def _license_operations(paths: dict) -> Iterator[Tuple[str, str, dict]]:
    """Yield (path, method, operation) for every license operation present in a spec's paths."""
    for path, item in paths.items():
        if not _is_license_path(path):
            continue
        for method, operation in item.items():
            if method in _OP_METHODS and isinstance(operation, dict):
                yield path, method, operation


def _security_schemes(operation: dict) -> Set[str]:
    """The security scheme names an operation declares (an empty requirement means public)."""
    return {name for requirement in operation.get("security") or [] if requirement for name in requirement}


def _fmt(operations) -> str:
    return "\n  ".join(f"{method.upper()} {path}" for path, method in sorted(operations))


@pytest.fixture(scope="module")
def spec() -> dict:
    assert SPEC_PATH.exists(), f"openapi-spec.yaml not found at {SPEC_PATH}"
    with open(SPEC_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def paths(spec) -> dict:
    return spec.get("paths", {}) or {}


@pytest.fixture(scope="module")
def partner_spec(spec) -> dict:
    """The real partner view of the real contract (no fixtures, no stubs) — Req 12.6."""
    return derive_partner_spec(spec)


class TestLicenseOperationSetIsFullyClassified:
    """Fail-closed: the contract's license surface must equal the classified set exactly."""

    def test_license_operations_match_expected_partition_exactly(self, paths):
        contract_operations = {(path, method) for path, method, _ in _license_operations(paths)}
        expected = set(_EXPECTED_LICENSE_AUDIENCE)

        unclassified = contract_operations - expected
        assert not unclassified, (
            "Unclassified license operation(s) in openapi-spec.yaml. Every /licenses* operation must "
            "be deliberately assigned to a plane (#3 Req 4.1/4.2/4.4): add it to "
            f"_EXPECTED_LICENSE_AUDIENCE with its audience, and to the #31 partition:\n  "
            + _fmt(unclassified)
        )

        stale = expected - contract_operations
        assert not stale, (
            "Expected license operation(s) missing from openapi-spec.yaml — the contract lost an "
            f"operation this guard pins (#3 Req 4.4):\n  " + _fmt(stale)
        )


class TestLicenseAudiencePartition:
    """Req 4.4 — per-(path, method) audience, including the mixed-audience path."""

    def test_every_license_write_and_internal_read_is_internal(self, paths):
        declared = {(path, method): op.get("x-audience") for path, method, op in _license_operations(paths)}
        offenders = {op for op in _EXPECTED_INTERNAL_OPERATIONS if declared.get(op) != "internal"}
        assert not offenders, (
            "License writes and internal reads must be x-audience: internal so the derived partner "
            "spec excludes them by construction (#3 Req 4.1/4.4). Offending operations "
            f"(declared audience in parentheses):\n  "
            + "\n  ".join(f"{m.upper()} {p} ({declared.get((p, m))!r})" for p, m in sorted(offenders))
        )

    def test_the_two_partner_license_operations_are_partner(self, paths):
        declared = {(path, method): op.get("x-audience") for path, method, op in _license_operations(paths)}
        offenders = {op for op in _EXPECTED_PARTNER_OPERATIONS if declared.get(op) != "partner"}
        assert not offenders, (
            "The public license-document read and native-key validate are the only partner license "
            f"operations and must be x-audience: partner (#3 Req 4.2/4.4):\n  "
            + "\n  ".join(f"{m.upper()} {p} ({declared.get((p, m))!r})" for p, m in sorted(offenders))
        )

    def test_agrees_with_the_ssot_audience_partition(self):
        """This guard and the #31 whole-contract partition must classify the licenses identically.

        Keeps the two from drifting apart: a license operation reclassified in only one place fails
        here rather than leaving two tests quietly disagreeing.
        """
        mismatches = []
        for (path, method), expected in _EXPECTED_LICENSE_AUDIENCE.items():
            if (path, method) in _MIXED_AUDIENCE_OPERATIONS:
                ssot = _MIXED_AUDIENCE_OPERATIONS[(path, method)]
            elif path in _INTERNAL_PATHS:
                ssot = "internal"
            elif path in _PARTNER_PATHS:
                ssot = "partner"
            else:
                ssot = None
            if ssot != expected:
                mismatches.append(f"{method.upper()} {path}: this guard={expected!r}, #31 partition={ssot!r}")
        assert not mismatches, (
            "License audience disagrees with the #31 partition in "
            f"tests/api_spec/test_openapi_validation.py:\n  " + "\n  ".join(sorted(mismatches))
        )


class TestAudienceSecurityCoherence:
    """An operation's audience and its declared security must tell the same story."""

    def test_cognito_only_license_operations_are_internal(self, paths):
        offenders = []
        for path, method, operation in _license_operations(paths):
            if _security_schemes(operation) == {"CognitoAuth"} and operation.get("x-audience") != "internal":
                offenders.append(f"{method.upper()} {path} (x-audience={operation.get('x-audience')!r})")
        assert not offenders, (
            "A license operation secured by CognitoAuth alone is internal-plane only and must be "
            f"x-audience: internal (#3 Req 4.1/4.4):\n  " + "\n  ".join(sorted(offenders))
        )

    def test_no_internal_license_operation_declares_partner_key_auth(self, paths):
        offenders = []
        for path, method, operation in _license_operations(paths):
            if operation.get("x-audience") == "internal" and "PartnerKeyAuth" in _security_schemes(operation):
                offenders.append(f"{method.upper()} {path}")
        assert not offenders, (
            "An internal license operation must not accept a partner key — a partner key must have no "
            f"route to a license write (#3 Req 4.1/4.3):\n  " + "\n  ".join(sorted(offenders))
        )


class TestDerivedPartnerSpecExcludesLicenseWrites:
    """Req 12.6 — the derived partner document, from the real contract via the real filter."""

    def test_partner_spec_license_surface_is_exactly_the_two_partner_operations(self, partner_spec):
        derived = {(path, method) for path, method, _ in _license_operations(partner_spec.get("paths", {}) or {})}
        assert derived == _EXPECTED_PARTNER_OPERATIONS, (
            "The derived partner spec must carry exactly the two partner license operations "
            f"(#3 Req 4.2/12.6).\n  unexpected: {sorted(derived - _EXPECTED_PARTNER_OPERATIONS)}"
            f"\n  missing: {sorted(_EXPECTED_PARTNER_OPERATIONS - derived)}"
        )

    def test_partner_spec_contains_no_license_mutating_operation(self, partner_spec):
        offenders = [
            (path, method)
            for path, method, _ in _license_operations(partner_spec.get("paths", {}) or {})
            if method in _MUTATING_METHODS and (path, method) not in _NON_MUTATING_PARTNER_POSTS
        ]
        assert not offenders, (
            "The derived partner spec must contain zero operations that mutate a license — a partner "
            f"key has no route to a license write (#3 Req 4.3/12.6):\n  " + _fmt(offenders)
        )

    def test_no_internal_license_operation_survives_the_filter(self, partner_spec):
        derived = {(path, method) for path, method, _ in _license_operations(partner_spec.get("paths", {}) or {})}
        leaked = derived & _EXPECTED_INTERNAL_OPERATIONS
        assert not leaked, (
            "Internal license operation(s) leaked into the derived partner spec (#3 Req 4.1/12.6):\n  "
            + _fmt(leaked)
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
