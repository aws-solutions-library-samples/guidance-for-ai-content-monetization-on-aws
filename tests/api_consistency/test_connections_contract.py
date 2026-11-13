"""
Contract-consistency tests for the internal Connections API (integration-adapters spec, #35).

Validates the spec ↔ API contract chain for the ``/connections*`` surface
(`.kiro/specs/integration-adapters/`, Requirements 6.7, 7.1, 4.3):

  * Req 6.7 — all internal ``/connections*`` paths are present in ``openapi-spec.yaml``.
  * Req 7.1 — every ``/connections*`` operation is marked ``x-audience: internal`` and the surface
    is NOT exposed on the partner API (never classified partner-facing).
  * Req 4.3 — the internal-only model types ``Entitlement`` / ``UsageEvent`` are below the wire and
    MUST NOT appear in the contract's component schemas.

Ordering note (test-first): task 8.1 (this module) is authored BEFORE task 8 adds the
``/connections*`` paths to ``openapi-spec.yaml``. This repo has no ``xfail`` precedent and the
gated unit suite MUST stay green (`spec-first-and-quality-gates`), so the presence/audience
assertions that depend on the not-yet-added paths are guarded to **activate only once the paths
exist** (``skip`` with a clear reason referencing task 8). They flip to enforcing automatically
when task 8 lands. The assertions that are true now AND must remain true forever — the
``Entitlement``/``UsageEvent`` schema exclusion, and the "never on the partner surface" guarantee —
run **unconditionally** so they enforce from this moment.
"""

import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = PROJECT_ROOT / "openapi-spec.yaml"

sys.path.insert(0, str(PROJECT_ROOT))

# HTTP methods considered "operations" (mirrors test_openapi_validation.py / test_route_reachability.py).
_OP_METHODS = ("get", "post", "put", "delete", "patch")

# The internal Connections surface, per the design's "Connections API (internal, Cognito)" table
# (`.kiro/specs/integration-adapters/design.md`). All are x-audience: internal (Req 7.1).
_EXPECTED_CONNECTIONS_PATHS = {
    "/connections/adapters",
    "/connections",
    "/connections/{id}",
    "/connections/{id}/test",
    "/connections/{id}/status",
    "/connections/mirrors/{domain}",
}

# Internal-only model types that live below the wire and MUST NOT be added to the contract (Req 4.3).
_INTERNAL_ONLY_SCHEMAS = ("Entitlement", "UsageEvent")

# Reason strings for the test-first conditional skips (task 8 has not run yet).
_SKIP_UNTIL_TASK_8 = (
    "integration-adapters task 8 (add /connections* to openapi-spec.yaml) not executed yet; "
    "this assertion enforces automatically once the paths are added"
)


def _load_spec() -> dict:
    with open(SPEC_PATH) as f:
        return yaml.safe_load(f)


def _connections_paths(paths: dict) -> dict:
    """Every contract path under the /connections* surface."""
    return {p: item for p, item in paths.items() if p == "/connections" or p.startswith("/connections/")}


def _operations(path_item: dict):
    """Yield (method, operation) for the real operations on a contract path item."""
    for method, op in path_item.items():
        if method in _OP_METHODS and isinstance(op, dict):
            yield method, op


@pytest.fixture(scope="module")
def spec() -> dict:
    assert SPEC_PATH.exists(), f"openapi-spec.yaml not found at {SPEC_PATH}"
    return _load_spec()


@pytest.fixture(scope="module")
def paths(spec) -> dict:
    return spec.get("paths", {}) or {}


class TestConnectionsPathsPresent:
    """Req 6.7 — the internal Connections paths are present in the contract (enforces after task 8)."""

    def test_all_connections_paths_present(self, paths):
        present = _connections_paths(paths)
        if not present:
            pytest.skip(_SKIP_UNTIL_TASK_8)
        missing = sorted(_EXPECTED_CONNECTIONS_PATHS - set(present))
        assert not missing, (
            "Connections API paths missing from openapi-spec.yaml (integration-adapters Req 6.7). "
            f"Add them in task 8:\n  " + "\n  ".join(missing)
        )

    def test_connections_surface_has_no_unexpected_paths(self, paths):
        """Any /connections* path present must be one of the designed paths (catch typos/drift)."""
        present = set(_connections_paths(paths))
        if not present:
            pytest.skip(_SKIP_UNTIL_TASK_8)
        unexpected = sorted(present - _EXPECTED_CONNECTIONS_PATHS)
        assert not unexpected, (
            "Unexpected /connections* path(s) in openapi-spec.yaml not in the integration-adapters "
            f"design's Connections API table:\n  " + "\n  ".join(unexpected)
        )


class TestConnectionsAudienceInternal:
    """Req 7.1 — every /connections* operation is internal, never partner-facing."""

    def test_every_connections_operation_is_internal(self, paths):
        present = _connections_paths(paths)
        if not present:
            pytest.skip(_SKIP_UNTIL_TASK_8)
        offenders = []
        for path, item in present.items():
            for method, op in _operations(item):
                if op.get("x-audience") != "internal":
                    offenders.append(f"{method.upper()} {path}: x-audience={op.get('x-audience')!r}")
        assert not offenders, (
            "Connections API operations must be x-audience: internal (integration-adapters Req 7.1); "
            f"offenders:\n  " + "\n  ".join(sorted(offenders))
        )

    def test_connections_never_on_partner_surface(self, paths):
        """
        Guarantee that must hold NOW and forever: no /connections* path is classified partner-facing.

        Reuses the SSOT partner-surface partition (#31) from test_openapi_validation. Runs
        unconditionally — with the paths absent the partner set simply contains none of them
        (vacuously safe), and once task 8 adds them it enforces that none leaked onto the partner
        surface. Also asserts no /connections* operation is explicitly tagged partner.
        """
        from tests.api_spec.test_openapi_validation import _PARTNER_PATHS

        partner_connections = sorted(p for p in _PARTNER_PATHS if p == "/connections" or p.startswith("/connections/"))
        assert not partner_connections, (
            "Connections API must never be on the partner surface (integration-adapters Req 7.1); "
            f"remove from _PARTNER_PATHS:\n  " + "\n  ".join(partner_connections)
        )

        for path, item in _connections_paths(paths).items():
            for method, op in _operations(item):
                assert op.get("x-audience") != "partner", (
                    f"{method.upper()} {path} must not be x-audience: partner (integration-adapters Req 7.1)"
                )


class TestInternalOnlyModelsAbsentFromContract:
    """Req 4.3 — internal-only Entitlement / UsageEvent are below the wire, not in the contract."""

    def test_internal_only_schemas_not_in_components(self, spec):
        schemas = spec.get("components", {}).get("schemas", {}) or {}
        present = [name for name in _INTERNAL_ONLY_SCHEMAS if name in schemas]
        assert not present, (
            "Internal-only model types must NOT appear in openapi-spec.yaml component schemas "
            "(integration-adapters Req 4.3 — they are below the wire):\n  " + "\n  ".join(sorted(present))
        )

    def test_internal_only_schemas_not_referenced_anywhere(self, spec):
        """No $ref points at an internal-only type (belt-and-suspenders against a nested definition)."""
        text = yaml.safe_dump(spec)
        offenders = [
            name for name in _INTERNAL_ONLY_SCHEMAS if f"#/components/schemas/{name}" in text
        ]
        assert not offenders, (
            "Contract must not $ref internal-only model types (integration-adapters Req 4.3):\n  "
            + "\n  ".join(sorted(offenders))
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
