"""Adapter-owned external-client conformance guardrail (integration-adapters, GitLab #35,
task 14.1, Requirement 8).

Land-green static AST scan asserting the **adapter-owned** external-system clients are
constructed only inside their domain adapter, and that each concrete adapter implements
its declared Port. This is the runtime analogue of #26's route-reachability guardrail
(``tests/api_consistency/test_route_reachability.py``): a deterministic, gated-unit-tier
check (no live AWS) that enforces the integration contract rather than merely documenting
it.

Scope — the four adapter-owned clients ONLY
-------------------------------------------
The guardrail is scoped **strictly** to the clients an adapter owns (Requirement 8.2):

  * ``wafv2``                          -> ``integrations/access/`` (the WAF adapter)
  * ``marketplaceentitlementservice``  -> ``integrations/marketplace/`` (Entitlement Service)
  * ``meteringmarketplace``            -> ``integrations/marketplace/`` (Metering Service)
  * ``marketplace-catalog``            -> ``integrations/marketplace/`` (Catalog API)

It MUST NOT flag general ``boto3`` — ``dynamodb``, ``s3``, ``bedrock-runtime``,
``cloudwatch``, ``secretsmanager``, ``sts``, etc. — which is legitimate and pervasive
across services / pipeline / infra / tests (Requirement 8.2). Read-only **observability**
(e.g. CloudWatch metrics for WAF) is likewise NOT in scope (Requirement 8.2a): the
guardrail targets *construction of the adapter-owned clients* outside the adapter, not
how a client is used. HOW a client is used (one-way projection vs. read-modify-write) is
enforced by the Port interface + code review, not by this static check.

Land-green (hard constraint, Requirement 8.3)
---------------------------------------------
This guardrail passes CI **from the moment it is introduced**. Known direct-construction
sites not yet migrated to an adapter are carried on an explicit, reviewed, issue-annotated
``PLANNED_UNROUTED``-style allowlist (:data:`PLANNED_UNROUTED_CLIENTS`) — **reported, not
silently skipped** (see :class:`TestPlannedUnroutedReported`). As each vertical migrates,
it removes its entry so the allowlist ratchets toward empty; a stale entry (one that no
longer corresponds to a real construction) fails
(:meth:`TestPlannedUnroutedReported.test_planned_unrouted_are_real_constructions`).

Honest limit (Requirement 8.5)
------------------------------
This is a static import/AST check: it catches port-bypass via the **known adapter-owned
client names** constructed through recognizable ``boto3.client("<name>")`` /
``session.client("<name>")`` call shapes. Calls assembled via **raw dynamic clients**
(e.g. ``boto3.client(service_var)`` where the service string is computed at runtime, or a
pre-built client passed in) are **not** caught here — those are governed by steering +
code review. The check is a tripwire against the common, static bypass, not a proof of
absence.

Scan boundary
-------------
The scan targets the deployed Lambda runtime layer, ``source/lambda/`` (the home of the
``integrations/`` adapters and the ``services/`` they wrap). Deployment-time scripts under
``deployment/`` and test fixtures under ``tests/`` are outside the Lambda runtime and are
not scanned — a ``wafv2`` client built in a one-off ``deployment/data/`` migration script
or faked in a test is not a runtime port-bypass.
"""

from __future__ import annotations

import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAMBDA_ROOT = os.path.join(REPO_ROOT, "source", "lambda")
INTEGRATIONS_ROOT = os.path.join(LAMBDA_ROOT, "integrations")

# ── Scope: the adapter-owned clients and the domain dir each is allowed to live in ──
# Keys are the exact boto3 service names; values are the integrations/<domain>/ prefix
# (relative to source/lambda) within which construction of that client is permitted.
ADAPTER_OWNED_CLIENTS: dict[str, str] = {
    "wafv2": os.path.join("integrations", "access"),
    "marketplaceentitlementservice": os.path.join("integrations", "marketplace"),
    "meteringmarketplace": os.path.join("integrations", "marketplace"),
    "marketplace-catalog": os.path.join("integrations", "marketplace"),
}

# ── Land-green allowlist (PLANNED_UNROUTED pattern, #26) ─────────────────────────────
# Known direct-construction sites NOT yet migrated behind an adapter, each mapped to its
# owning issue. These are REPORTED (TestPlannedUnroutedReported), never silently skipped,
# and each MUST correspond to a real construction the scan finds (stale entries fail), so
# the list ratchets toward empty as verticals migrate.
#
# Path is relative to source/lambda. The tuple value is (client_name, owning-issue-note).
#
#   services/waf_sync_controller.py constructs a ``wafv2`` client. Task 5.1's adapter
#   (integrations/access/aws_waf.py) WRAPS this controller for its stabilized read/
#   describe plumbing but, per refactoring-safety, does NOT relocate it — so the
#   controller's own ``wafv2`` construction remains OUTSIDE the adapter dir until the WAF
#   vertical (#29/#7) migrates it. Carried here so the guardrail lands green.
PLANNED_UNROUTED_CLIENTS: dict[tuple[str, str], str] = {
    (os.path.join("services", "waf_sync_controller.py"), "wafv2"): "#29/#7",
}

# ── Concrete adapters and the Port each must implement (Requirement 8.2) ─────────────
# (module path relative to source/lambda, adapter class name, port module, port class).
# The runtime-checkable Protocol conformance is asserted structurally (isinstance) so a
# missing/renamed port method fails. Only adapters in the two adapter-owned domains are
# listed; the AWS marketplace client adapters (aws_entitlement/aws_metering/aws_catalog)
# are owned by the verticals (#3/#15) and not built yet — this list grows as they land.
ADAPTER_PORT_BINDINGS: tuple[tuple[str, str, str, str], ...] = (
    (
        "integrations.access.aws_waf",
        "AwsWafAccessPolicyAdapter",
        "integrations.access.port",
        "AccessPolicyPort",
    ),
    (
        "integrations.marketplace.internal_dynamo",
        "InternalDynamoLicenseResolver",
        "integrations.marketplace.port",
        "LicenseResolverPort",
    ),
)


# ─────────────────────────────────────────────────────────────────────────────────────
# AST scan
# ─────────────────────────────────────────────────────────────────────────────────────


def _first_string_arg(node: ast.Call) -> str | None:
    """Return the first positional string-literal argument of a call, else ``None``."""
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def find_adapter_owned_client_constructions(source: str, filename: str) -> list[tuple[int, str]]:
    """Return (lineno, client_name) for every adapter-owned client construction (AST-only).

    Recognizes the static ``boto3``-style construction shapes:
      * ``boto3.client("wafv2")`` / ``boto3.client('marketplace-catalog')``
      * ``session.client("wafv2")`` / ``<anything>.client("wafv2")`` (e.g. a boto3.Session)

    Only the ``.client(...)`` attribute call with a **string-literal** first argument that
    is one of :data:`ADAPTER_OWNED_CLIENTS` is flagged. ``.resource(...)`` is never an
    adapter-owned client (those services are all clients), and a computed/non-literal
    service name is deliberately NOT flagged (the honest-limit boundary, Requirement 8.5).
    """
    matches: list[tuple[int, str]] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match ``<x>.client(...)`` — boto3.client / session.client / self._session.client.
        if not (isinstance(func, ast.Attribute) and func.attr == "client"):
            continue
        name = _first_string_arg(node)
        if name in ADAPTER_OWNED_CLIENTS:
            matches.append((node.lineno, name))
    return matches


def _iter_lambda_py_files():
    """Yield absolute paths to every ``.py`` under source/lambda (excluding caches/vendored)."""
    exclude = {
        os.path.join(LAMBDA_ROOT, "python"),
        os.path.join(LAMBDA_ROOT, "utils_deprecated"),
    }
    for dirpath, dirnames, filenames in os.walk(LAMBDA_ROOT):
        if any(dirpath == ex or dirpath.startswith(ex + os.sep) for ex in exclude):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(".py"):
                yield os.path.join(dirpath, fn)


def _scan_all_constructions() -> list[tuple[str, int, str]]:
    """Scan source/lambda; return (rel_path, lineno, client_name) for every construction found.

    ``rel_path`` is relative to source/lambda (matching the allowlist / domain-dir keys),
    with OS-native separators.
    """
    found: list[tuple[str, int, str]] = []
    for path in _iter_lambda_py_files():
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        rel = os.path.relpath(path, LAMBDA_ROOT)
        for lineno, name in find_adapter_owned_client_constructions(source, path):
            found.append((rel, lineno, name))
    return found


def _is_allowed_location(rel_path: str, client_name: str) -> bool:
    """Whether ``client_name`` may be constructed at ``rel_path`` (relative to source/lambda).

    Allowed iff the file lives under the client's owning ``integrations/<domain>/`` dir, OR
    the (path, client) pair is carried on the issue-annotated ``PLANNED_UNROUTED`` allowlist.
    """
    allowed_dir = ADAPTER_OWNED_CLIENTS[client_name]
    if rel_path == allowed_dir or rel_path.startswith(allowed_dir + os.sep):
        return True
    if (rel_path, client_name) in PLANNED_UNROUTED_CLIENTS:
        return True
    return False


@pytest.fixture(scope="module")
def constructions() -> list[tuple[str, int, str]]:
    return _scan_all_constructions()


class TestAdapterOwnedClientsScopedToAdapters:
    """The four adapter-owned clients are constructed only inside their domain adapter."""

    def test_no_adapter_owned_client_outside_its_adapter(self, constructions):
        offenders = []
        for rel_path, lineno, client_name in constructions:
            if not _is_allowed_location(rel_path, client_name):
                allowed_dir = ADAPTER_OWNED_CLIENTS[client_name]
                offenders.append(
                    f"{rel_path}:{lineno}: '{client_name}' constructed outside its adapter "
                    f"(allowed only under source/lambda/{allowed_dir}/). Route it through the "
                    f"domain port/adapter, or add it to PLANNED_UNROUTED_CLIENTS with an owning issue."
                )
        assert not offenders, (
            "Adapter-owned external clients constructed outside their domain adapter "
            "(integration-adapters #35 Req 8.2):\n  " + "\n  ".join(sorted(offenders))
        )


class TestAdaptersImplementDeclaredPort:
    """Each concrete adapter structurally implements its declared (runtime-checkable) Port."""

    @pytest.mark.parametrize(
        "adapter_module,adapter_cls,port_module,port_cls",
        ADAPTER_PORT_BINDINGS,
        ids=[b[1] for b in ADAPTER_PORT_BINDINGS],
    )
    def test_adapter_implements_port(self, adapter_module, adapter_cls, port_module, port_cls):
        import importlib

        adapter_type = getattr(importlib.import_module(adapter_module), adapter_cls)
        port_type = getattr(importlib.import_module(port_module), port_cls)
        # Structural (construct-free) check: every declared port method exists on the adapter
        # class — the same conformance a runtime_checkable Protocol isinstance() enforces, but
        # without instantiating the adapter (which would build a boto3 client).
        port_methods = [
            name
            for name in dir(port_type)
            if callable(getattr(port_type, name, None)) and not name.startswith("_")
        ]
        missing = [m for m in port_methods if not hasattr(adapter_type, m)]
        assert not missing, (
            f"{adapter_module}.{adapter_cls} does not implement {port_cls} — missing method(s): "
            f"{missing} (integration-adapters #35 Req 8.2)"
        )


class TestPlannedUnroutedReported:
    """PLANNED_UNROUTED entries are surfaced (not silently skipped) and are not stale."""

    def test_planned_unrouted_are_real_constructions(self, constructions):
        """A stale allowlist entry — one that no longer corresponds to a real construction —
        fails, so the list ratchets toward empty (Requirement 8.3)."""
        actual = {(rel_path, client_name) for rel_path, _lineno, client_name in constructions}
        stale = [entry for entry in PLANNED_UNROUTED_CLIENTS if entry not in actual]
        assert not stale, (
            "PLANNED_UNROUTED_CLIENTS lists (path, client) pairs with no matching construction "
            "(stale allowlist entries — the site migrated behind an adapter; remove them):\n  "
            + "\n  ".join(f"{path} -> {client}" for path, client in sorted(stale))
        )

    def test_report_planned_unrouted(self, capsys):
        """Visibility: emit the allowlist so an unmigrated site never lingers unnoticed."""
        with capsys.disabled():
            print("\n[adapter-owned-clients] PLANNED_UNROUTED_CLIENTS (unmigrated, tracked):")
            for (path, client), issue in sorted(PLANNED_UNROUTED_CLIENTS.items()):
                print(f"  source/lambda/{path}: '{client}' -> tracked in {issue}")


# ─────────────────────────────────────────────────────────────────────────────────────
# Example / edge-case tests for the scanner itself (structured so task 14.2 can extend).
# ─────────────────────────────────────────────────────────────────────────────────────


def test_scanner_flags_boto3_client_wafv2():
    v = find_adapter_owned_client_constructions('import boto3\nc = boto3.client("wafv2")\n', "x.py")
    assert v == [(2, "wafv2")]


def test_scanner_flags_session_client_marketplace():
    v = find_adapter_owned_client_constructions(
        'c = session.client("marketplaceentitlementservice")', "x.py"
    )
    assert v == [(1, "marketplaceentitlementservice")]


def test_scanner_flags_hyphenated_catalog_client():
    v = find_adapter_owned_client_constructions('boto3.client("marketplace-catalog")', "x.py")
    assert v == [(1, "marketplace-catalog")]


def test_scanner_ignores_general_boto3_clients():
    # dynamodb/s3/bedrock-runtime/cloudwatch/secretsmanager/sts are legitimate + pervasive.
    src = (
        'boto3.client("dynamodb")\n'
        'boto3.client("s3")\n'
        'boto3.client("bedrock-runtime")\n'
        'boto3.client("cloudwatch")\n'
        'boto3.client("secretsmanager")\n'
        'boto3.client("sts")\n'
        'boto3.resource("dynamodb")\n'
    )
    assert find_adapter_owned_client_constructions(src, "x.py") == []


def test_scanner_ignores_dynamic_client_name():
    # Honest-limit boundary: a computed service name is NOT flagged (Requirement 8.5).
    v = find_adapter_owned_client_constructions('svc = "wafv2"\nc = boto3.client(svc)\n', "x.py")
    assert v == []
