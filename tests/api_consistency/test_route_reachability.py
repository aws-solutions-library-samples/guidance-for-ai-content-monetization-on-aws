"""
Bidirectional API contract <-> runtime routing reachability guardrail (GitLab #26).

Asserts the two API definitions cannot silently diverge:

  Direction A (contract -> runtime): every path in ``openapi-spec.yaml`` is reachable via
    {partner_api explicit paths} U {ui_backend ROUTE_TABLE proxy routes} U {edge behaviors},
    OR is an explicitly-listed PLANNED_UNROUTED path (documented-ahead-of-wiring, tracked to an
    owning issue and REPORTED, not silently skipped).

  Direction B (runtime -> contract): every ui_backend ROUTE_TABLE entry resolves to at least one
    documented contract path (no reachable route is undocumented).

Design notes (see .kiro/specs/api-contract-ssot/design.md):
  * Reuses the REAL ``resolve_route`` from ``ui_backend.routes`` (imported as ``routes`` at the
    zip root) so the test and the Lambda dispatcher can never disagree.
  * Deliberately does NOT run a strict OpenAPI-3.0 validator over the contract: it would
    false-fail on ``{proxy+}`` and ``x-amazon-apigateway-*`` extensions, which are valid at
    API Gateway import (spike-verified) but rejected by vanilla OpenAPI validators.
  * Deterministic, no live AWS, gated unit tier.
"""

import os
from pathlib import Path

import pytest
import yaml

# ``source/lambda`` is on sys.path via tests/conftest.py. In the repo the routes package is
# canonically ``ui_backend.routes`` (it is hoisted to the zip root ``routes`` only at packaging).
from ui_backend.routes import META_ROUTE_NAMES, ROUTE_TABLE, resolve_route

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = PROJECT_ROOT / "openapi-spec.yaml"

# --- Classification constants (single source; the MonetizationApi generator imports the same) ---

# Paths integrated directly to the partner_api Lambda (public discovery + partner query).
PARTNER_EXPLICIT = {"/info", "/query", "/taxonomy", "/llms.txt"}

# Contract paths served at the edge (CloudFront Function / S3 origin) rather than API GW -> Lambda.
# Empty for the control plane today; wired to edge-resolution-strategy when those land.
EDGE_BEHAVIORS: set[str] = set()

# Contract paths intentionally documented ahead of wiring, each mapped to its owning issue.
# These are REPORTED (see test_planned_unrouted_reported), never silently skipped.
# A key is either a bare path (every method unrouted) or ``"METHOD /path"`` (only that method
# unrouted) so a path that is partly live stays guarded for the methods that are.
PLANNED_UNROUTED = {
    "/licenses": "#3",
    "/licenses/{license_id}": "#3",
    "/licenses/{license_id}/versions": "#3",
    "/licenses/{license_id}/validate": "#3",
    "/content/{content_id}/metadata": "#8",
    "/.well-known/comp.json": "#3",  # CoMP discovery
}


def _load_contract_paths() -> dict:
    with open(SPEC_PATH) as f:
        spec = yaml.safe_load(f)
    return spec.get("paths", {}) or {}


def _to_runtime_path(contract_path: str) -> str:
    """Turn an OpenAPI path template into a concrete probe path for resolve_route.

    ``/partners/{id}`` -> ``/partners/x``; the greedy ``/{proxy+}`` is not probed here (it is
    the catch-all itself, handled in Direction B).
    """
    parts = []
    for seg in contract_path.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            parts.append("x")
        else:
            parts.append(seg)
    return "/".join(parts)


def _methods_for(path_item: dict) -> list[str]:
    """HTTP methods declared on a contract path item (upper-cased), ignoring extensions/params."""
    verbs = {"get", "put", "post", "delete", "patch", "head", "options"}
    methods = [m.upper() for m in path_item.keys() if m.lower() in verbs]
    # x-amazon-apigateway-any-method means "any" -> represent as GET for a reachability probe.
    if any(k.lower() == "x-amazon-apigateway-any-method" for k in path_item.keys()):
        methods.append("GET")
    return methods or ["GET"]


def _reachable(path: str, method: str) -> bool:
    if path in PARTNER_EXPLICIT:
        return True
    if path in EDGE_BEHAVIORS:
        return True
    if path in PLANNED_UNROUTED or f"{method} {path}" in PLANNED_UNROUTED:
        return True
    return resolve_route(_to_runtime_path(path), method) is not None


@pytest.fixture(scope="module")
def contract_paths():
    assert SPEC_PATH.exists(), f"openapi-spec.yaml not found at {SPEC_PATH}"
    return _load_contract_paths()


class TestContractToRuntimeReachability:
    """Direction A: every contract path is reachable (or explicitly planned-unrouted)."""

    def test_every_contract_path_reachable(self, contract_paths):
        unreachable = []
        for path, item in contract_paths.items():
            for method in _methods_for(item):
                if not _reachable(path, method):
                    unreachable.append(f"{method} {path}")
        assert not unreachable, (
            "Contract paths not reachable via {partner_explicit} U {ROUTE_TABLE} U {edge} U "
            "{planned_unrouted} (Direction A drift). Either wire/route them, add to "
            f"PLANNED_UNROUTED with an owning issue, or prune from the contract:\n  "
            + "\n  ".join(sorted(unreachable))
        )


class TestRuntimeToContractDocumented:
    """Direction B: every ROUTE_TABLE entry maps to >=1 documented contract path."""

    def test_every_route_table_entry_documented(self, contract_paths):
        # For each route, confirm at least one contract path resolves to that route name.
        documented_routes = set()
        for path, item in contract_paths.items():
            for method in _methods_for(item):
                name = resolve_route(_to_runtime_path(path), method)
                if name:
                    documented_routes.add(name)

        undocumented = []
        for route in ROUTE_TABLE:
            if route.name in META_ROUTE_NAMES:
                continue  # meta routes (e.g. /openapi) serve the API, they are not part of it
            if route.name not in documented_routes:
                undocumented.append(f"{route.name} ({route.prefix})")
        assert not undocumented, (
            "ROUTE_TABLE entries with no documenting contract path (Direction B / reverse drift). "
            "Document these live routes in openapi-spec.yaml:\n  " + "\n  ".join(sorted(undocumented))
        )


class TestPlannedUnroutedReported:
    """PLANNED_UNROUTED entries are surfaced (not silently skipped) and are actually in the contract."""

    def test_planned_unrouted_are_in_contract(self, contract_paths):
        stale = []
        for key in PLANNED_UNROUTED:
            method, _, path = key.rpartition(" ")
            if path not in contract_paths or (method and method not in _methods_for(contract_paths[path])):
                stale.append(key)
        assert not stale, (
            "PLANNED_UNROUTED lists paths not present in the contract (stale allowlist entries — "
            "remove them):\n  " + "\n  ".join(sorted(stale))
        )

    def test_report_planned_unrouted(self, capsys):
        # Visibility: emit the planned-unrouted set so it never lingers unnoticed.
        with capsys.disabled():
            print("\n[reachability] PLANNED_UNROUTED (documented, not-yet-wired):")
            for path, issue in sorted(PLANNED_UNROUTED.items()):
                print(f"  {path}  -> tracked in {issue}")
